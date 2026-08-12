"""
Тесты единой модели скидок (GAP-M12).

Раньше постоянная скидка клиента (discount_percent) и разовая скидка на
машину (session["loyalty"]) были двумя не связанными механизмами: первая
молча урезала total_price машины (значит и зарплату мойщика, и базу %
администратора — обе считаются от car["price"]), вторая — отдельная
запись, которая не трогает car["price"], а вычитается только из реально
принятых денег и видна отдельной строкой «Лояльность».

По решению владельца (GAP-M12) обе скидки сведены в одну модель: базой
для зарплаты/базы % остаётся car["price"] всегда, а постоянная скидка
клиента применяется через тот же механизм session["loyalty"], что и
разовая — см. sessions.apply_client_loyalty_discount().
"""
from config import BRANCHES

BRANCH = BRANCHES[0]


def test_apply_client_loyalty_discount_no_client_returns_zero(sessions_mod):
    session = sessions_mod.get_session(BRANCH)
    applied = sessions_mod.apply_client_loyalty_discount(session, "+79990000000", 1, 1000)
    assert applied == 0
    assert session.get("loyalty", []) == []


def test_apply_client_loyalty_discount_client_without_discount_returns_zero(sessions_mod):
    client = sessions_mod.upsert_client_visit("+79990000001", "Иван", BRANCH, "А001АА", 500)
    assert client["discount_percent"] is None
    session = sessions_mod.get_session(BRANCH)
    applied = sessions_mod.apply_client_loyalty_discount(session, "+79990000001", 1, 1000)
    assert applied == 0
    assert session.get("loyalty", []) == []


def test_apply_client_loyalty_discount_creates_loyalty_entry(sessions_mod):
    sessions_mod.upsert_client_visit("+79990000002", "Пётр", BRANCH, "В002ВВ", 500)
    sessions_mod.set_client_discount("+79990000002", 20)
    session = sessions_mod.get_session(BRANCH)

    applied = sessions_mod.apply_client_loyalty_discount(session, "+79990000002", 3, 1000)

    assert applied == 200  # 20% от 1000
    assert session["loyalty"] == [
        {"car_num": 3, "discount": 200, "auto": True, "percent": 20}
    ]


def test_apply_client_loyalty_discount_does_not_touch_base_price(sessions_mod):
    """Ключевая гарантия GAP-M12: скидка не уменьшает переданную car["price"] —
    она только добавляется отдельной записью в session["loyalty"], поэтому
    зарплата мойщика/база % администратора (считаются от car["price"] в
    calculator.py) скидку клиента не видят вовсе."""
    sessions_mod.upsert_client_visit("+79990000003", "Анна", BRANCH, "С003СС", 500)
    sessions_mod.set_client_discount("+79990000003", 50)
    session = sessions_mod.get_session(BRANCH)
    car_price = 1000
    car = {"num": 1, "price": car_price, "payment": "нал"}
    session["cars"].append(car)

    sessions_mod.apply_client_loyalty_discount(session, "+79990000003", car["num"], car_price)

    assert car["price"] == car_price  # цена машины не изменилась
    assert session["loyalty"][0]["discount"] == 500


def test_apply_client_loyalty_discount_rounds_to_nearest_ruble(sessions_mod):
    sessions_mod.upsert_client_visit("+79990000004", "Олег", BRANCH, "Д004ДД", 500)
    sessions_mod.set_client_discount("+79990000004", 15)
    session = sessions_mod.get_session(BRANCH)

    applied = sessions_mod.apply_client_loyalty_discount(session, "+79990000004", 1, 333)

    assert applied == round(333 * 15 / 100)


def test_apply_client_loyalty_discount_cleared_discount_stops_applying(sessions_mod):
    sessions_mod.upsert_client_visit("+79990000005", "Мария", BRANCH, "Е005ЕЕ", 500)
    sessions_mod.set_client_discount("+79990000005", 10)
    sessions_mod.clear_client_discount("+79990000005")
    session = sessions_mod.get_session(BRANCH)

    applied = sessions_mod.apply_client_loyalty_discount(session, "+79990000005", 1, 1000)

    assert applied == 0
    assert session.get("loyalty", []) == []


def test_apply_client_loyalty_discount_affects_calculator_cash_not_salary_base(sessions_mod):
    """Интеграционная проверка сквозь calculator.py: скидка клиента должна
    вести себя ТОЧНО как разовая ручная скидка — уменьшать фактически
    принятый нал, но не влиять на car["price"] (базу зарплаты/процента)."""
    from calculator import calculate_summary

    sessions_mod.upsert_client_visit("+79990000006", "Сергей", BRANCH, "Ж006ЖЖ", 500)
    sessions_mod.set_client_discount("+79990000006", 10)
    session = sessions_mod.get_session(BRANCH)
    car = {
        "num": 1, "price": 1000, "payment": "нал", "employee": "Тест",
        "price_breakdown": {"wash": {"name": "Мойка", "price": 1000, "percent": 0.4}},
        "status": "in_progress",
    }
    session["cars"].append(car)
    sessions_mod.apply_client_loyalty_discount(session, "+79990000006", car["num"], car["price"])

    summary = calculate_summary(session)

    assert summary["total_loyalty"] == 100          # 10% от 1000 ушло в «Лояльность»
    assert summary["cash"] == 900                    # фактически принятый нал уменьшен
    assert car["price"] == 1000                       # база для зарплаты/% не тронута
