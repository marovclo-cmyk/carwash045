"""
Тесты разового скрипта миграции carwash_archive.json -> БД (GAP-DB1,
этап 5). Изоляция через тот же DATA_DIR, что и sessions_mod (см.
conftest.py) — migrate_archive_to_db.py читает DATA_DIR тем же способом,
что и миграции users/advances/branches/payments.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_archive_to_db
    module = importlib.reload(migrate_archive_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_day(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {
            "01.08.2026": {
                "date": "01.08.2026",
                "branch": "Тестовый филиал",
                "cars": [{"num": 1, "price": 500}],
                "products": [{"name": "Ароматизатор", "price": 100}],
                "expenses": [{"amount": 50, "comment": "мыло"}],
                "incomes": [],
                "loyalty": [{"car_num": 1, "discount": 25}],
                "admin_percent": 10,
                "admin_name": "Иван",
                "fixed_rates": {"Пётр": 1500},
                "admin_fixed_rate": 800,
            }
        }
    }
    with open(sessions_mod.ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    archive = sessions_mod.load_archive()
    day = archive["Тестовый филиал"]["01.08.2026"]
    assert day["cars"] == [{"num": 1, "price": 500}]
    assert day["admin_name"] == "Иван"
    assert day["fixed_rates"] == {"Пётр": 1500}
    assert day["admin_fixed_rate"] == 800


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {
            "02.08.2026": {"date": "02.08.2026", "branch": "Тестовый филиал", "cars": []},
        }
    }
    with open(sessions_mod.ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    archive = sessions_mod.load_archive()
    assert list(archive["Тестовый филиал"].keys()) == ["02.08.2026"]  # не задублировалось


def test_migrate_skips_malformed_branch_entry(sessions_mod, capsys):
    legacy_data = {"Сломанный филиал": "не объект"}
    with open(sessions_mod.ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущен" in out


def test_migrate_skips_malformed_day_entry(sessions_mod, capsys):
    legacy_data = {"Тестовый филиал": {"03.08.2026": "не объект"}}
    with open(sessions_mod.ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущен" in out
