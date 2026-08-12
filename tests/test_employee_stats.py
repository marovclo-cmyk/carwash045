"""
Тесты агрегации заработка сотрудников по периодам (employee_stats.py).

До этого прохода модуль тестами не был покрыт (см. "Известная граница" в
GAP-TEST1 / PROJECT_STATE.md) — он не тестировался в связке с sessions.py
тем же паттерном изоляции DATA_DIR, что и остальные тесты. Здесь это
делается через фикстуру `employee_stats_mod` (см. conftest.py): она
перезагружает employee_stats.py ПОСЛЕ изоляции sessions.py, потому что
employee_stats делает `from sessions import ... sessions as _live_sessions,
...` — прямую привязку объектов модуля sessions на момент импорта.

Покрывается:
- объединение заработка одного человека по нескольким ролям в один ключ
  (мойщик + администратор в один день — ключевая идея модуля, см. его
  docstring);
- агрегация по архивным дням + текущей незакрытой смене вместе;
- фильтрация по датам (date_from/date_to);
- вычитание авансов из заработка (get_employee_advances);
- список всех сотрудников филиала (all_employees_period_stats) —
  сортировка по убыванию заработка и исключение тех, у кого 0 смен;
- разбивка по календарным неделям месяца (employee_month_stats_by_week /
  calendar_week_of_month).
"""
from datetime import datetime

from config import BRANCHES

BRANCH = BRANCHES[0]


def _archive_day(sessions_mod, date_str: str, cars: list, admin_name: str = "", admin_percent: float = 0.10):
    """Записывает один день сразу в архив филиала (в обход текущей смены) —
    employee_stats читает архив через load_archive(), поэтому для тестов
    периодов удобнее наполнять его напрямую, а не открывать/закрывать смену."""
    day = {
        "date": date_str, "branch": BRANCH,
        "cars": cars, "products": [], "expenses": [], "incomes": [], "loyalty": [],
        "admin_percent": admin_percent, "admin_name": admin_name,
        "fixed_rates": {}, "admin_fixed_rate": 0,
    }
    sessions_mod.overwrite_archive_day(BRANCH, date_str, day)


def _car(num, employee, price=1000, percent=0.30, payment="нал"):
    return {
        "num": num, "employee": employee, "price": price, "payment": payment,
        "price_breakdown": {"wash": {"name": "Мойка", "price": price, "percent": percent}},
        "status": "in_progress",
    }


# ── get_branch_employee_roles ──────────────────────────────────────────────

def test_get_branch_employee_roles_combines_roles_for_same_name(employee_stats_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    sessions_mod.add_branch_admin_name(BRANCH, "Иван")
    sessions_mod.add_branch_worker(BRANCH, "Пётр")

    roles = employee_stats_mod.get_branch_employee_roles(BRANCH)

    assert roles["Иван"] == ["администратор", "мойщик"]
    assert roles["Пётр"] == ["мойщик"]


# ── employee_period_stats: базовая агрегация ────────────────────────────────

def test_employee_period_stats_no_data_returns_zeros(employee_stats_mod):
    stats = employee_stats_mod.employee_period_stats(BRANCH, "Никто")
    assert stats["total"] == 0
    assert stats["shifts"] == 0
    assert stats["cars"] == 0
    assert stats["avg_per_shift"] == 0
    assert stats["avg_per_car"] == 0
    assert stats["days"] == []


def test_employee_period_stats_reads_from_archive(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000, percent=0.30)])

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["shifts"] == 1
    assert stats["cars"] == 1
    assert stats["by_role"]["мойщик"] == 300  # round_salary(1000*0.30)
    assert stats["total"] == 300


def test_employee_period_stats_combines_washer_and_admin_earnings_same_name(employee_stats_mod, sessions_mod):
    """Ключевая гарантия модуля (см. docstring employee_stats.py): "мойщик
    Иван" и "администратор Иван" — один сотрудник, оба заработка должны
    сложиться в один total, а не остаться раздельными записями."""
    _archive_day(
        sessions_mod, "01.07.2026",
        [_car(1, "Иван", price=1000, percent=0.30)],
        admin_name="Иван", admin_percent=0.10,
    )

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert set(stats["by_role"].keys()) == {"мойщик", "администратор"}
    assert stats["total"] == stats["by_role"]["мойщик"] + stats["by_role"]["администратор"]
    assert stats["shifts"] == 1  # один день = одна смена, а не две


def test_employee_period_stats_aggregates_across_multiple_days(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000, percent=0.30)])
    _archive_day(sessions_mod, "02.07.2026", [_car(1, "Иван", price=2000, percent=0.30)])

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["shifts"] == 2
    assert stats["cars"] == 2
    assert stats["total"] == 300 + 600  # round_salary(300) + round_salary(600)
    assert len(stats["days"]) == 2
    # дни отсортированы по дате
    assert [d["date"] for d in stats["days"]] == ["01.07.2026", "02.07.2026"]


def test_employee_period_stats_ignores_other_employees_cars(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [
        _car(1, "Иван", price=1000, percent=0.30),
        _car(2, "Пётр", price=5000, percent=0.30),
    ])

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["cars"] == 1
    assert stats["total"] == 300


def test_employee_period_stats_car_list_details(employee_stats_mod, sessions_mod):
    car = _car(1, "Иван", price=1000, percent=0.30, payment="visa")
    car["car"] = "А123АА"
    car["service"] = "Комплекс"
    car["time"] = "10:00"
    _archive_day(sessions_mod, "01.07.2026", [car])

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["days"][0]["car_list"] == [{
        "num": 1, "car": "А123АА", "service": "Комплекс",
        "price": 1000, "payment": "visa", "time": "10:00",
    }]


# ── фильтрация по датам ─────────────────────────────────────────────────────

def test_employee_period_stats_date_range_filters_days(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000)])
    _archive_day(sessions_mod, "15.07.2026", [_car(1, "Иван", price=1000)])
    _archive_day(sessions_mod, "01.08.2026", [_car(1, "Иван", price=1000)])

    stats = employee_stats_mod.employee_period_stats(
        BRANCH, "Иван",
        date_from=datetime(2026, 7, 1), date_to=datetime(2026, 7, 31),
    )

    assert stats["shifts"] == 2
    assert {d["date"] for d in stats["days"]} == {"01.07.2026", "15.07.2026"}


def test_employee_period_stats_includes_current_open_session(employee_stats_mod, sessions_mod):
    """_iter_branch_days должен видеть и архив, и текущую незакрытую смену —
    иначе сегодняшний, ещё не закрытый день выпадал бы из статистики."""
    session = sessions_mod.get_session(BRANCH)
    session["cars"].append(_car(1, "Иван", price=1000, percent=0.30))

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["shifts"] == 1
    assert stats["total"] == 300


def test_employee_period_stats_empty_current_session_not_counted(employee_stats_mod, sessions_mod):
    """Пустая (ещё не начатая) текущая смена не должна создавать
    "нулевую" смену в статистике — session_has_data() отсекает её."""
    sessions_mod.get_session(BRANCH)  # создаёт пустую сессию, ничего не добавляя

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["shifts"] == 0


# ── авансы ───────────────────────────────────────────────────────────────

def test_employee_period_stats_subtracts_advances(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000, percent=0.30)])
    sessions_mod.add_advance(BRANCH, "Иван", 100)

    stats = employee_stats_mod.employee_period_stats(BRANCH, "Иван")

    assert stats["total"] == 300
    assert stats["advance"] == 100
    assert stats["remaining"] == 200
    assert len(stats["advances"]) == 1


def test_employee_period_stats_advances_filtered_by_same_date_range(employee_stats_mod, sessions_mod, monkeypatch):
    """get_employee_advances получает тот же date_from/date_to, что и сама
    выборка дней — авансы вне периода не должны попадать в остаток."""
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000, percent=0.30)])
    sessions_mod.add_advance(BRANCH, "Иван", 100)  # аванс "сегодня" (реальная дата теста)

    stats = employee_stats_mod.employee_period_stats(
        BRANCH, "Иван",
        date_from=datetime(2026, 7, 1), date_to=datetime(2026, 7, 31),
    )

    # аванс выдан не в июле 2026 → не должен вычитаться из заработка за июль
    assert stats["advance"] == 0
    assert stats["remaining"] == stats["total"]


# ── all_employees_period_stats ──────────────────────────────────────────────

def test_all_employees_period_stats_sorted_and_excludes_zero_shifts(employee_stats_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    sessions_mod.add_branch_worker(BRANCH, "Пётр")
    sessions_mod.add_branch_worker(BRANCH, "Без смен")  # числится, но не работал
    _archive_day(sessions_mod, "01.07.2026", [
        _car(1, "Иван", price=1000, percent=0.30),
        _car(2, "Пётр", price=5000, percent=0.30),
    ])

    result = employee_stats_mod.all_employees_period_stats(BRANCH)
    names = [r["name"] for r in result]

    assert names == ["Пётр", "Иван"]  # по убыванию заработка
    assert "Без смен" not in names


def test_all_employees_period_stats_one_entry_per_person_multi_role(employee_stats_mod, sessions_mod):
    sessions_mod.add_branch_worker(BRANCH, "Иван")
    sessions_mod.add_branch_admin_name(BRANCH, "Иван")
    _archive_day(
        sessions_mod, "01.07.2026",
        [_car(1, "Иван", price=1000, percent=0.30)],
        admin_name="Иван",
    )

    result = employee_stats_mod.all_employees_period_stats(BRANCH)

    assert len([r for r in result if r["name"] == "Иван"]) == 1


# ── недельная/месячная разбивка ──────────────────────────────────────────

def test_calendar_week_of_month_matches_docstring_example(employee_stats_mod):
    # 03.07.2026 — четверг; понедельник той недели (30.06) относится к июню,
    # поэтому это всё равно неделя 1 месяца (см. docstring функции).
    assert employee_stats_mod.calendar_week_of_month(datetime(2026, 7, 3)) == 1


def test_employee_month_stats_by_week_groups_by_calendar_week(employee_stats_mod, sessions_mod):
    _archive_day(sessions_mod, "01.07.2026", [_car(1, "Иван", price=1000, percent=0.30)])  # неделя 1
    _archive_day(sessions_mod, "08.07.2026", [_car(1, "Иван", price=2000, percent=0.30)])  # неделя 2

    weeks = employee_stats_mod.employee_month_stats_by_week(BRANCH, "Иван", month=7, year=2026)

    assert set(weeks.keys()) == {1, 2}
    assert weeks[1]["total"] == 300
    assert weeks[2]["total"] == 600


def test_week_range_starts_on_monday(employee_stats_mod):
    # 12.08.2026 — среда; понедельник той недели — 10.08.2026.
    start, end = employee_stats_mod.week_range(datetime(2026, 8, 12, 15, 30))
    assert start == datetime(2026, 8, 10, 0, 0, 0)
    assert end == datetime(2026, 8, 12, 15, 30)
