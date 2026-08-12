"""
Тесты онлайн-оплаты в sessions.py (GAP-PAY1).

Провайдер в тестах всегда мок (sessions_mod фикстура не трогает
YOOKASSA_*-переменные окружения, а payment_provider.get_provider() без
них отдаёт MockYooKassaProvider — см. test_payment_provider.py) — это
позволяет проверять полный цикл создание → подтверждение → побочный
эффект без сети.

Ключевое поведение по спецификации GAP-PAY1:
- purpose="advance" — успешная оплата помечает booking["prepayment"], но
  НЕ трогает кассу смены напрямую (это делает конвертация записи в
  машину, см. webapp/server.py:_maybe_convert_booking_to_car — там
  отдельная интеграционная логика, не покрыта здесь);
- purpose="car" — успешная оплата добавляет payment_split машине методом
  "онлайн", сумма не может превысить цену машины (car["price"]), остаток
  уходит прежним способом оплаты (или "нал" по умолчанию);
- apply_payment_success идемпотентна: повторный вызов для уже
  применённого платежа не повторяет побочный эффект.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]


def test_create_payment_returns_pending_mock_record(sessions_mod):
    record = sessions_mod.create_payment(BRANCH, "advance", 500, "Тестовая предоплата")
    assert record["status"] == "pending"
    assert record["provider"] == "mock_yookassa"
    assert record["amount"] == 500
    assert record["applied"] is False
    assert record["id"].startswith("mock_")
    assert sessions_mod.get_payment(record["id"]) == record


def test_get_payment_missing_returns_none(sessions_mod):
    assert sessions_mod.get_payment("does-not-exist") is None


def test_apply_payment_success_advance_sets_booking_prepayment(sessions_mod):
    booking = sessions_mod.create_booking(
        BRANCH, "2026-08-15", 1, "10:00", "10:30",
        employee="Иван", price=1000, payment="нал", client_name="Пётр",
    )
    record = sessions_mod.create_payment(BRANCH, "advance", 400, booking_id=booking["id"])

    updated = sessions_mod.apply_payment_success(record["id"])

    assert updated["status"] == "succeeded"
    assert updated["applied"] is True
    fresh_booking = sessions_mod.get_booking(booking["id"])
    assert fresh_booking["prepayment"] == {
        "amount": 400, "status": "paid",
        "payment_id": record["id"], "paid_at": updated["paid_at"],
    }


def test_apply_payment_success_is_idempotent(sessions_mod):
    booking = sessions_mod.create_booking(BRANCH, "2026-08-15", 1, "10:00", "10:30", price=1000)
    record = sessions_mod.create_payment(BRANCH, "advance", 400, booking_id=booking["id"])

    first = sessions_mod.apply_payment_success(record["id"])
    second = sessions_mod.apply_payment_success(record["id"])

    assert first["paid_at"] == second["paid_at"]
    # booking.prepayment не пересоздаётся вторым вызовом
    fresh_booking = sessions_mod.get_booking(booking["id"])
    assert fresh_booking["prepayment"]["payment_id"] == record["id"]


def test_apply_payment_success_car_sets_payment_split(sessions_mod):
    session = sessions_mod.get_session(BRANCH)
    session["cars"].append({"num": 1, "employee": "Иван", "price": 1000, "payment": "нал"})
    sessions_mod.save_sessions()

    record = sessions_mod.create_payment(BRANCH, "car", 1000, car_num=1)
    sessions_mod.apply_payment_success(record["id"])

    session = sessions_mod.get_session(BRANCH)
    car = next(c for c in session["cars"] if c["num"] == 1)
    assert car["payment_split"] == {"онлайн": 1000}


def test_apply_payment_success_car_caps_at_price_and_keeps_remainder_method(sessions_mod):
    """Онлайн-доплата на сумму БОЛЬШЕ цены машины не может увеличить
    payment_split сверх car['price'] (защита от опечатки в сумме);
    доплата МЕНЬШЕ цены оставляет остаток на прежнем способе оплаты."""
    session = sessions_mod.get_session(BRANCH)
    session["cars"].append({"num": 2, "employee": "Иван", "price": 1000, "payment": "visa"})
    sessions_mod.save_sessions()

    record = sessions_mod.create_payment(BRANCH, "car", 600, car_num=2)
    sessions_mod.apply_payment_success(record["id"])

    session = sessions_mod.get_session(BRANCH)
    car = next(c for c in session["cars"] if c["num"] == 2)
    assert car["payment_split"] == {"онлайн": 600, "visa": 400}


def test_apply_payment_success_car_does_not_overwrite_existing_split(sessions_mod):
    session = sessions_mod.get_session(BRANCH)
    session["cars"].append({"num": 3, "employee": "Иван", "price": 1000,
                             "payment": "нал", "payment_split": {"нал": 500, "visa": 500}})
    sessions_mod.save_sessions()

    record = sessions_mod.create_payment(BRANCH, "car", 300, car_num=3)
    sessions_mod.apply_payment_success(record["id"])

    session = sessions_mod.get_session(BRANCH)
    car = next(c for c in session["cars"] if c["num"] == 3)
    assert car["payment_split"] == {"нал": 500, "visa": 500}  # не тронут


def test_apply_payment_success_missing_payment_returns_none(sessions_mod):
    assert sessions_mod.apply_payment_success("does-not-exist") is None


def test_mark_payment_canceled(sessions_mod):
    record = sessions_mod.create_payment(BRANCH, "advance", 200)
    canceled = sessions_mod.mark_payment_canceled(record["id"])
    assert canceled["status"] == "canceled"
    assert sessions_mod.get_payment(record["id"])["status"] == "canceled"


def test_mark_payment_canceled_ignores_already_applied(sessions_mod):
    booking = sessions_mod.create_booking(BRANCH, "2026-08-15", 1, "10:00", "10:30", price=1000)
    record = sessions_mod.create_payment(BRANCH, "advance", 400, booking_id=booking["id"])
    sessions_mod.apply_payment_success(record["id"])

    result = sessions_mod.mark_payment_canceled(record["id"])

    assert result["status"] == "succeeded"  # уже применённый платёж отменить нельзя
