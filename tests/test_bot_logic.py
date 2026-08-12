"""
Тесты чистой (не требующей Telegram Update/Bot) логики бота —
handlers/admin.py (доступ, роли, текущий филиал) и handlers/cars.py
(разбор машины из свободного текста).

До этого прохода handlers/* тестами не были покрыты (GAP-TEST1 явно
исключал "роуты webapp/server.py, handlers/* (бот) и весь UI" —
см. "Известная граница" в PROJECT_STATE.md). Это ПЕРВЫЙ проход: он
покрывает функции, которые не требуют реального объекта
telegram.Update/Context (это отдельная, более тяжёлая работа с
unittest.mock — намеренно вне охвата этого прохода, см. отчёт по этапу).

Фикстуры bot_admin_mod / bot_cars_mod (см. conftest.py) перезагружают
handlers/admin.py и handlers/cars.py ПОСЛЕ изоляции sessions.py — оба
модуля делают `from sessions import get_session, load_users,
get_branch_workers, ...`, то есть привязывают функции sessions.py на
момент импорта.
"""
from config import BRANCHES, OWNER_ID, SERVICES

BRANCH = BRANCHES[0]


class _FakeContext:
    """Минимальная замена telegram.ext.ContextTypes.DEFAULT_TYPE для
    функций, которым нужен только context.user_data (обычный dict)."""

    def __init__(self, user_data: dict | None = None):
        self.user_data = user_data if user_data is not None else {}


# ── is_allowed / роль пользователя ──────────────────────────────────────

def test_is_allowed_owner_always_true(bot_admin_mod):
    assert bot_admin_mod.is_allowed(OWNER_ID) is True


def test_is_allowed_unknown_user_false(bot_admin_mod):
    assert bot_admin_mod.is_allowed(999999999) is False


def test_is_allowed_whitelisted_user_true(bot_admin_mod, sessions_mod):
    sessions_mod.add_user(555, "Тест Юзер")
    assert bot_admin_mod.is_allowed(555) is True


def test_is_allowed_revoked_after_remove_user(bot_admin_mod, sessions_mod):
    """Как только владелец убирает пользователя из белого списка, доступ
    должен пропасть немедленно (та же гарантия, что и is_whitelisted в
    webapp/server.py — см. его docstring)."""
    sessions_mod.add_user(556, "Тест Юзер 2")
    assert bot_admin_mod.is_allowed(556) is True
    sessions_mod.remove_user(556)
    assert bot_admin_mod.is_allowed(556) is False


def test_get_role_owner(bot_admin_mod):
    assert bot_admin_mod.get_role(OWNER_ID, BRANCH) == "owner"


def test_get_role_admin_of_this_branch(bot_admin_mod, sessions_mod):
    sessions_mod.set_branch_admin(BRANCH, 777)
    assert bot_admin_mod.get_role(777, BRANCH) == "admin"


def test_get_role_admin_of_other_branch_is_not_admin_here(bot_admin_mod, sessions_mod):
    """Роль не должна "утекать" с одного филиала на другой — см. docstring
    sessions.get_role()."""
    other_branch = BRANCHES[1]
    sessions_mod.set_branch_admin(other_branch, 778)
    assert bot_admin_mod.get_role(778, BRANCH) == "worker"


def test_get_role_default_worker(bot_admin_mod):
    assert bot_admin_mod.get_role(123, BRANCH) == "worker"


# ── текущий филиал пользователя (per-user context, не глобальный) ──────────

def test_get_current_branch_none_by_default(bot_admin_mod):
    ctx = _FakeContext()
    assert bot_admin_mod.get_current_branch(ctx) is None


def test_get_current_branch_returns_selected(bot_admin_mod):
    ctx = _FakeContext({"current_branch": BRANCH})
    assert bot_admin_mod.get_current_branch(ctx) == BRANCH


# ── parse_car_from_text (быстрый ввод машины одной строкой в чат) ──────────

def test_parse_car_from_text_basic(bot_cars_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)

    car = bot_cars_mod.parse_car_from_text("Иван комплекс нал Camry", session, BRANCH)

    assert car is not None
    assert car["employee"] == "Иван"
    assert car["service_keys"] == ["комплекс"]
    assert car["payment"] == "нал"
    assert car["price"] == SERVICES["комплекс"]["prices"]["sedan"]  # тип кузова по умолчанию — седан
    assert car["car"] == "Camry"
    assert car["num"] == 1


def test_parse_car_from_text_unknown_employee_returns_none(bot_cars_mod, sessions_mod):
    session = sessions_mod.get_session(BRANCH)  # ни одного сотрудника не зарегистрировано
    car = bot_cars_mod.parse_car_from_text("Иван комплекс нал", session, BRANCH)
    assert car is None


def test_parse_car_from_text_no_matching_service_returns_none(bot_cars_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)
    car = bot_cars_mod.parse_car_from_text("Иван нал Camry", session, BRANCH)
    assert car is None


def test_parse_car_from_text_defaults_payment_to_cash(bot_cars_mod, sessions_mod):
    """Если способ оплаты не указан в тексте — берётся 'нал' по умолчанию
    (см. parse_car_from_text: `next(..., "нал")`)."""
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)
    car = bot_cars_mod.parse_car_from_text("Иван комплекс", session, BRANCH)
    assert car is not None
    assert car["payment"] == "нал"


def test_parse_car_from_text_detects_body_type_and_adjusts_price(bot_cars_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)

    car = bot_cars_mod.parse_car_from_text("Иван комплекс внедорожник visa", session, BRANCH)

    assert car["body_type"] == "suv"
    assert car["price"] == SERVICES["комплекс"]["prices"]["suv"]
    assert car["payment"] == "visa"


def test_parse_car_from_text_combo_services_sum_prices(bot_cars_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)

    car = bot_cars_mod.parse_car_from_text("Иван комплекс+воск нал", session, BRANCH)

    assert set(car["service_keys"]) == {"комплекс", "воск"}
    expected = SERVICES["комплекс"]["prices"]["sedan"] + SERVICES["воск"]["prices"]["sedan"]
    assert car["price"] == expected


def test_parse_car_from_text_num_increments_from_existing_cars(bot_cars_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    session = sessions_mod.get_session(BRANCH)
    session["cars"].append({"num": 1, "employee": "Иван", "price": 1000, "payment": "нал"})

    car = bot_cars_mod.parse_car_from_text("Иван комплекс нал", session, BRANCH)

    assert car["num"] == 2
