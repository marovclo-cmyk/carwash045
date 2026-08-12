"""
Тесты разового скрипта миграции carwash_sessions.json -> БД (GAP-DB1,
этап 8, финальный домен). Изоляция через тот же DATA_DIR, что и
sessions_mod (см. conftest.py) — migrate_sessions_to_db.py читает DATA_DIR
тем же способом, что и миграции остальных 7 доменов.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_sessions_to_db
    module = importlib.reload(migrate_sessions_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_session(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {
            "date": "01.08.2026",
            "branch": "Тестовый филиал",
            "cars": [{"num": 1, "price": 500}],
            "products": [{"name": "Ароматизатор", "price": 100}],
            "expenses": [{"amount": 50, "comment": "мыло"}],
            "incomes": [],
            "loyalty": [{"car_num": 1, "discount": 25}],
            "admin_percent": 10,
            "admin_name": "Иван",
            "day_open": True,
            "fixed_rates": {"Пётр": 1500},
            "admin_fixed_rate": 800,
        }
    }
    with open(sessions_mod.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    # load_sessions() перечитывает кэш из БД в память процесса — тот же
    # шаг, что выполняет run_all.py/bot.py при старте после реальной
    # миграции на деплое (см. докстринг migrate_sessions_to_db.py).
    sessions_mod.load_sessions()
    session = sessions_mod.get_session("Тестовый филиал")
    assert session["cars"] == [{"num": 1, "price": 500}]
    assert session["admin_name"] == "Иван"
    assert session["day_open"] is True
    assert session["fixed_rates"] == {"Пётр": 1500}
    assert session["admin_fixed_rate"] == 800


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {"date": "02.08.2026", "branch": "Тестовый филиал", "cars": [], "day_open": False},
    }
    with open(sessions_mod.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    sessions_mod.load_sessions()
    assert list(sessions_mod.sessions.keys()) == ["Тестовый филиал"]  # не задублировалось


def test_migrate_skips_malformed_session_entry(sessions_mod, capsys):
    legacy_data = {"Сломанный филиал": "не объект"}
    with open(sessions_mod.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущена" in out


def test_migrated_session_visible_via_live_sessions_alias(sessions_mod):
    """employee_stats.py/handlers/reports.py читают `sessions.sessions`
    напрямую (не только через get_session()) — проверяем, что после
    load_sessions() перенесённая смена видна и через этот alias."""
    legacy_data = {
        "Тестовый филиал": {"date": "03.08.2026", "branch": "Тестовый филиал", "cars": [{"num": 1}], "day_open": True},
    }
    with open(sessions_mod.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    sessions_mod.load_sessions()

    assert sessions_mod.sessions["Тестовый филиал"]["cars"] == [{"num": 1}]
