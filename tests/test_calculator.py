"""
Тесты денежной логики calculator.py.

calculate_summary() — центральная функция расчёта дневной кассы: из неё
берутся суммы для отчётов, зарплаты сотрудников и остаток в кассе. Ошибка
здесь напрямую бьёт по деньгам, поэтому это первое, что нужно тестами
закрыть (GAP-TEST1).
"""
import pytest

from calculator import calculate_summary, round_salary


# ── round_salary ─────────────────────────────────────────────────────────
# Правило (см. docstring round_salary):
#   0–30  → вниз до XX00
#   31–50 → вверх до XX50
#   51–70 → вниз до XX50
#   71–99 → вверх до (XX+1)00
@pytest.mark.parametrize("raw,expected", [
    (1800, 1800),  # rem=0  -> вниз до XX00 (без изменений)
    (1830, 1800),  # rem=30 -> вниз до XX00
    (1831, 1850),  # rem=31 -> вверх до XX50
    (1850, 1850),  # rem=50 -> вверх до XX50 (без изменений)
    (1861, 1850),  # rem=61 -> вниз до XX50
    (1870, 1850),  # rem=70 -> вниз до XX50
    (1871, 1900),  # rem=71 -> вверх до (XX+1)00
    (1899, 1900),  # rem=99 -> вверх до (XX+1)00
    (1900, 1900),  # rem=0  -> вниз до XX00 (без изменений)
    (0, 0),
])
def test_round_salary(raw, expected):
    assert round_salary(raw) == expected


def _empty_session(**overrides):
    session = {
        "cars": [], "products": [], "expenses": [], "incomes": [], "loyalty": [],
    }
    session.update(overrides)
    return session


# ── calculate_summary: комплексный сценарий ─────────────────────────────
def test_calculate_summary_full_scenario():
    """Один сквозной сценарий, покрывающий разом: несколько машин одного
    мойщика, скидку лояльности (вычитается из кассы, но НЕ из зарплаты
    мойщика — она считается от полной цены), товар, доп. доход, расход
    и процент администратора от общей выручки (машины + товары)."""
    session = _empty_session(
        cars=[
            {"num": 1, "employee": "Иван", "price": 2000, "payment": "нал",
             "service_keys": ["комплекс"]},
            {"num": 2, "employee": "Иван", "price": 3000, "payment": "visa",
             "service_keys": ["комплекс"]},
        ],
        products=[{"price": 500, "payment": "нал"}],
        expenses=[{"name": "мыло", "amount": 200}],
        incomes=[{"name": "доп", "amount": 100, "payment": "нал"}],
        loyalty=[{"car_num": 1, "discount": 100}],
        admin_name="Салим",
    )
    s = calculate_summary(session)

    # Касса реально поступивших денег: цена машин минус скидка, плюс товар,
    # плюс доп. доход.
    assert s["cash"] == 2500     # (2000 - 100 скидка) + 500 товар + 100 доход
    assert s["visa"] == 3000
    assert s["beznal"] == 0
    assert s["total"] == 5500
    assert s["grand_total"] == 5600  # total + скидка (сколько было "на бумаге" до скидки)
    assert s["total_loyalty"] == 100
    assert s["loyalty_cash"] == 100
    assert s["total_products"] == 500

    # Зарплата мойщика считается от ПОЛНОЙ цены (2000+3000), скидка её не
    # уменьшает — это сознательное поведение (см. комментарий в calculator.py).
    assert s["washer_totals"] == {"Иван": 5000}
    assert s["washer_salaries"] == {"Иван": 1500}  # (2000+3000) * 0.30

    # Админ получает % от (выручка машин + товары), округлённый по правилу.
    # (5000 + 500) * 0.10 = 550 -> round_salary(550) = 550
    assert s["admin_salary"] == 550
    assert s["admin_fixed_rate"] == 0

    assert s["role_earnings"] == {
        "Иван": {"мойщик": 1500},
        "Салим": {"администратор": 550},
    }

    assert s["total_expenses"] == 200
    assert s["expenses_str"] == "мыло - 200"
    assert s["total_incomes"] == 100
    assert s["incomes_str"] == "доп - 100"
    assert s["income_cash"] == 100
    assert s["remainder"] == 2300  # cash(2500) - expenses(200)


def test_calculate_summary_payment_split():
    """Одна машина, оплаченная частично наличными, частично картой —
    сумма должна разойтись по обоим методам оплаты."""
    session = _empty_session(cars=[
        {"num": 1, "employee": "Пётр", "price": 2000,
         "payment_split": {"нал": 1200, "visa": 800},
         "service_keys": ["комплекс"]},
    ])
    s = calculate_summary(session)
    assert s["cash"] == 1200
    assert s["visa"] == 800
    assert s["beznal"] == 0
    assert s["total"] == 2000
    # Зарплата считается от полной цены машины, а не от долей оплаты.
    assert s["washer_salaries"] == {"Пётр": round_salary(2000 * 0.30)}


def test_calculate_summary_price_breakdown_combo_service():
    """Комбо-услуга с разбивкой цены/процента по каждой составляющей
    (price_breakdown) — зарплата считается по сумме частей, а не по
    среднему проценту от полной цены."""
    session = _empty_session(cars=[
        {"num": 1, "employee": "Иван", "price": 1500, "payment": "нал",
         "price_breakdown": {
             "комплекс": {"price": 1000, "percent": 0.30},
             "воск":     {"price": 500,  "percent": 0.30},
         }},
    ])
    s = calculate_summary(session)
    # 1000*0.30 + 500*0.30 = 450
    assert s["washer_salaries"] == {"Иван": round_salary(450)}


def test_calculate_summary_fixed_rate_not_rounded_and_not_in_admin_base():
    """Фиксированная ставка (`fixed_rates`) добавляется мойщику СВЕРХУ,
    без повторного округления, и не должна попадать в базу для расчёта
    % администратора (это не реальная выручка от машины)."""
    session = _empty_session(
        fixed_rates={"Петя": 837},  # намеренно "некруглое" число
        admin_name="Салим",
    )
    s = calculate_summary(session)
    assert s["washer_salaries"]["Петя"] == 837  # не прогоняется через round_salary
    assert s["washer_totals"]["Петя"] == 0       # нет реальных машин
    assert s["admin_salary"] == 0                # база 0 -> round_salary(0) = 0
    assert s["role_earnings"]["Петя"] == {"мойщик": 837}


def test_calculate_summary_admin_fixed_rate_added_on_top():
    """`admin_fixed_rate` (фикс-ставка администратора за пустую смену)
    добавляется поверх процента, а не заменяет его."""
    session = _empty_session(admin_fixed_rate=1000)
    s = calculate_summary(session)
    assert s["admin_salary"] == 1000  # round_salary(0*0.10) + 1000


def test_calculate_summary_same_person_multiple_roles_combined():
    """Один и тот же человек как мойщик и как администратор в один день —
    заработок по обеим ролям должен агрегироваться в role_earnings под
    одним и тем же именем, а не создавать двух разных "сотрудников"."""
    session = _empty_session(
        cars=[
            {"num": 1, "employee": "Иззет", "price": 2000, "payment": "нал",
             "service_keys": ["комплекс"]},
        ],
        admin_name="Иззет",
    )
    s = calculate_summary(session)
    assert set(s["role_earnings"]["Иззет"].keys()) == {"мойщик", "администратор"}


def test_calculate_summary_empty_session_all_zero():
    s = calculate_summary(_empty_session())
    assert s["total"] == 0
    assert s["grand_total"] == 0
    assert s["cash"] == s["visa"] == s["beznal"] == 0
    assert s["washer_totals"] == {}
    assert s["washer_salaries"] == {}
    assert s["admin_salary"] == 0
    assert s["remainder"] == 0
