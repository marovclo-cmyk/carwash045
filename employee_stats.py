"""
Универсальная статистика по сотрудникам.

Проблема, которую решает этот модуль:
раньше "мойщик Иззет" и "администратор Иззет" считались как будто это два
разных человека — зарплата мойщика бралась из washer_salaries, а зарплата
администратора отдельно из admin_salary + session["admin_name"], и нигде
эти два числа не складывались в одну карточку сотрудника.

Здесь ничего не меняется в том, ГДЕ и КАК хранятся списки сотрудников
(sessions.get_branch_workers / get_branch_admin_names остаются как есть —
никакой миграции данных не требуется, старые данные продолжают работать).
Меняется только СЛОЙ АГРЕГАЦИИ: он берёт имя сотрудника как единый ключ
и объединяет его заработок по всем ролям сразу, используя
calculate_summary()["role_earnings"] (см. calculator.py).

Чтобы добавить новую роль в будущем (кассир, детейлер, менеджер смены и т.п.):
1. В calculator.py посчитать её заработок и вызвать _add_role_earning(...).
2. Добавить список сотрудников этой роли через sessions
   (по аналогии с workers / admin_names) и зарегистрировать роль в ROLE_SOURCES.
Ничего в этом модуле, в отчётах бота или в mini-app менять не нужно —
агрегация по ролям здесь полностью общая (по названию роли, а не по коду).
"""
from datetime import datetime

from calculator import calculate_summary
from sessions import load_archive, sessions as _live_sessions, get_branch_workers, get_branch_admin_names

# role_label -> функция, которая возвращает список имён сотрудников с этой ролью.
# Чтобы добавить новую роль — добавь сюда ещё одну пару "название роли": функция.
ROLE_SOURCES = {
    "мойщик": get_branch_workers,
    "администратор": get_branch_admin_names,
}


def get_branch_employee_roles(branch: str) -> dict[str, list[str]]:
    """{имя_сотрудника: [список ролей]} — один сотрудник может входить
    сразу в несколько списков (мойщик и администратор одновременно)."""
    roles_by_name: dict[str, set] = {}
    for role, source_fn in ROLE_SOURCES.items():
        for name in source_fn(branch):
            roles_by_name.setdefault(name, set()).add(role)
    return {name: sorted(roles) for name, roles in roles_by_name.items()}


def _parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except (ValueError, TypeError):
        return None


def _iter_branch_days(branch: str, date_from: datetime | None = None, date_to: datetime | None = None):
    """Отдаёт (date_str, day_dict) по всем дням филиала в диапазоне —
    архивные дни + текущая незакрытая смена, если она уже что-то содержит."""
    archive = load_archive().get(branch, {})
    for date_str, day in archive.items():
        dt = _parse_date(date_str)
        if dt is None:
            continue
        if date_from and dt < date_from:
            continue
        if date_to and dt > date_to:
            continue
        yield date_str, day

    session = _live_sessions.get(branch, {})
    from sessions import session_has_data
    if session_has_data(session):
        date_str = session.get("date") or datetime.now().strftime("%d.%m.%Y")
        dt = _parse_date(date_str) or datetime.now()
        if (not date_from or dt >= date_from) and (not date_to or dt <= date_to):
            yield date_str, session


def employee_period_stats(branch: str, name: str,
                           date_from: datetime | None = None,
                           date_to: datetime | None = None) -> dict:
    """Полная агрегированная статистика ОДНОГО сотрудника за период,
    объединяющая заработок по ВСЕМ его ролям."""
    by_role: dict[str, float] = {}
    shifts_by_role: dict[str, int] = {}
    shift_dates: set[str] = set()
    cars_count = 0
    days_out = []

    for date_str, day in _iter_branch_days(branch, date_from, date_to):
        s = calculate_summary(day)
        earned = s.get("role_earnings", {}).get(name)
        if not earned:
            continue

        shift_dates.add(date_str)
        day_total = 0
        for role, amount in earned.items():
            by_role[role] = by_role.get(role, 0) + amount
            shifts_by_role[role] = shifts_by_role.get(role, 0) + 1
            day_total += amount

        my_cars_today = [c for c in day.get("cars", []) if c.get("employee") == name]
        day_cars = len(my_cars_today)
        cars_count += day_cars
        car_list = [
            {
                "num": c.get("num"),
                "car": c.get("car") or "",
                "service": c.get("service") or "",
                "price": c.get("price", 0),
                "payment": c.get("payment", ""),
                "time": c.get("time", ""),
            }
            for c in my_cars_today
        ]
        days_out.append({"date": date_str, "roles": earned, "total": day_total, "cars": day_cars, "car_list": car_list})

    total = sum(by_role.values())
    shifts = len(shift_dates)
    days_out.sort(key=lambda d: _parse_date(d["date"]) or datetime.min)

    return {
        "name": name,
        "total": total,
        "by_role": by_role,
        "shifts": shifts,
        "shifts_by_role": shifts_by_role,
        "cars": cars_count,
        "avg_per_shift": round(total / shifts, 2) if shifts else 0,
        "avg_per_car": round(total / cars_count, 2) if cars_count else 0,
        "days": days_out,
    }


def all_employees_period_stats(branch: str,
                                date_from: datetime | None = None,
                                date_to: datetime | None = None) -> list[dict]:
    """Статистика по ВСЕМ сотрудникам филиала за период (для сводных отчётов),
    каждый сотрудник фигурирует РОВНО один раз, вне зависимости от числа ролей."""
    names = get_branch_employee_roles(branch).keys()
    out = [employee_period_stats(branch, name, date_from, date_to) for name in names]
    out = [r for r in out if r["shifts"] > 0]
    out.sort(key=lambda r: r["total"], reverse=True)
    return out


def week_range(today: datetime | None = None) -> tuple[datetime, datetime]:
    """Календарная неделя (понедельник–сегодня), не привязанная к первой записи."""
    from datetime import timedelta
    today = today or datetime.now()
    start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, today


def month_range(month: int, year: int) -> tuple[datetime, datetime]:
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, 1), datetime(year, month, last_day, 23, 59, 59)


def calendar_week_of_month(dt: datetime) -> int:
    """Номер КАЛЕНДАРНОЙ недели внутри месяца (неделя всегда начинается
    в понедельник, независимо от того, с какого числа сотрудник начал
    работать). Например 03.07 (четверг) — это неделя 1, т.к. понедельник
    той недели (30.06) ещё относится к предыдущему месяцу."""
    first_of_month = dt.replace(day=1)
    first_monday_offset = first_of_month.weekday()  # 0=понедельник
    return ((dt.day - 1) + first_monday_offset) // 7 + 1


def employee_month_stats_by_week(branch: str, name: str, month: int, year: int) -> dict:
    """Статистика сотрудника за месяц с разбивкой по календарным неделям
    (Неделя 1..5), а не относительно первой записи в архиве."""
    month_start, month_end = month_range(month, year)
    weeks: dict[int, dict[str, float]] = {}
    for date_str, day in _iter_branch_days(branch, month_start, month_end):
        dt = _parse_date(date_str)
        if dt is None:
            continue
        s = calculate_summary(day)
        earned = s.get("role_earnings", {}).get(name)
        if not earned:
            continue
        wk = calendar_week_of_month(dt)
        weeks.setdefault(wk, {})
        for role, amount in earned.items():
            weeks[wk][role] = weeks[wk].get(role, 0) + amount
    return {wk: {"by_role": roles, "total": sum(roles.values())} for wk, roles in sorted(weeks.items())}
