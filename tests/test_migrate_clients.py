"""
Тесты разового скрипта миграции carwash_clients.json -> БД (GAP-DB1,
этап 6). Изоляция через тот же DATA_DIR, что и sessions_mod (см.
conftest.py) — migrate_clients_to_db.py читает DATA_DIR тем же способом,
что и миграции users/advances/branches/payments/archive.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_clients_to_db
    module = importlib.reload(migrate_clients_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_client_card(sessions_mod):
    legacy_data = {
        "79990000001": {
            "phone": "79990000001",
            "name": "Иван",
            "cars": ["А001АА"],
            "visits": [
                {"date": "01.08.2026", "branch": "Тестовый филиал", "car": "А001АА",
                 "total": 500, "car_num": 1, "service": "Мойка", "time": "10:00",
                 "paid": 500, "status": "done"},
            ],
            "discount_percent": 15,
        }
    }
    with open(sessions_mod.CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    client = sessions_mod.find_client("79990000001")
    assert client["name"] == "Иван"
    assert client["cars"] == ["А001АА"]
    assert client["discount_percent"] == 15
    assert client["visit_count"] == 1
    assert client["total_spent"] == 500
    assert client["last_visit"] == "01.08.2026"


def test_migrate_client_without_discount_stays_none(sessions_mod):
    legacy_data = {
        "79990000002": {"phone": "79990000002", "name": "Пётр", "cars": [], "visits": []},
    }
    with open(sessions_mod.CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    client = sessions_mod.find_client("79990000002")
    assert client["discount_percent"] is None


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {
        "79990000003": {"phone": "79990000003", "name": "Анна", "cars": [], "visits": []},
    }
    with open(sessions_mod.CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    clients = sessions_mod.load_clients()
    assert list(clients.keys()) == ["79990000003"]  # не задублировалось


def test_migrate_skips_malformed_client_entry(sessions_mod, capsys):
    legacy_data = {"79990000004": "не объект"}
    with open(sessions_mod.CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущена" in out


def test_migrate_skips_client_with_non_list_visits(sessions_mod, capsys):
    legacy_data = {"79990000005": {"phone": "79990000005", "name": "", "cars": [], "visits": "не список"}}
    with open(sessions_mod.CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущена" in out
