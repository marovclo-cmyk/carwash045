"""
Тесты разового скрипта миграции carwash_bookings.json -> БД (GAP-DB1,
этап 7). Изоляция через тот же DATA_DIR, что и sessions_mod (см.
conftest.py) — migrate_bookings_to_db.py читает DATA_DIR тем же способом,
что и миграции users/advances/branches/payments/archive/clients.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_bookings_to_db
    module = importlib.reload(migrate_bookings_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_booking(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {
            "01.08.2026": [
                {
                    "id": 1, "branch": "Тестовый филиал", "date": "01.08.2026",
                    "box": 2, "start_time": "10:00", "end_time": "11:00",
                    "employee": "Иван", "body_type": "sedan", "car": "А001АА",
                    "service_keys": ["wash"], "custom_services": [{"name": "Доп", "price": 100, "percent": 0}],
                    "product_keys": ["shampoo"], "price": 600, "price_calc": 600,
                    "price_override": None, "payment": "нал", "payment_split": None,
                    "comment": "коммент", "phone": "79990000001", "client_name": "Пётр",
                    "status": "waiting", "car_num": None,
                    "created_at": "2026-08-01T09:00:00", "updated_at": "2026-08-01T09:00:00",
                },
            ]
        }
    }
    with open(sessions_mod.BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    booking = sessions_mod.get_booking(1)
    assert booking["branch"] == "Тестовый филиал"
    assert booking["date"] == "01.08.2026"
    assert booking["box"] == 2
    assert booking["start_time"] == "10:00"
    assert booking["end_time"] == "11:00"
    assert booking["service_keys"] == ["wash"]
    assert booking["custom_services"] == [{"name": "Доп", "price": 100, "percent": 0}]
    assert booking["product_keys"] == ["shampoo"]
    assert booking["price"] == 600
    assert booking["phone"] == "79990000001"
    assert booking["client_name"] == "Пётр"
    assert booking["status"] == "waiting"


def test_migrate_booking_without_optional_fields_uses_defaults(sessions_mod):
    legacy_data = {
        "Филиал": {
            "02.08.2026": [
                {"id": 2, "branch": "Филиал", "date": "02.08.2026", "box": 1,
                 "start_time": "12:00", "end_time": "13:00"},
            ]
        }
    }
    with open(sessions_mod.BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    booking = sessions_mod.get_booking(2)
    assert booking["employee"] == ""
    assert booking["service_keys"] == []
    assert booking["price_override"] is None
    assert booking["payment_split"] is None
    assert booking["car_num"] is None
    assert booking["status"] == "waiting"


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {
        "Филиал": {
            "03.08.2026": [
                {"id": 3, "branch": "Филиал", "date": "03.08.2026", "box": 1,
                 "start_time": "09:00", "end_time": "10:00", "status": "waiting"},
            ]
        }
    }
    with open(sessions_mod.BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    bookings = sessions_mod.get_bookings("Филиал", "03.08.2026")
    assert [b["id"] for b in bookings] == [3]  # не задублировалось


def test_migrate_skips_entry_without_numeric_id(sessions_mod, capsys):
    legacy_data = {
        "Филиал": {
            "04.08.2026": [
                {"branch": "Филиал", "date": "04.08.2026", "box": 1,
                 "start_time": "09:00", "end_time": "10:00"},  # нет id
            ]
        }
    }
    with open(sessions_mod.BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущена" in out


def test_migrate_skips_malformed_day(sessions_mod, capsys):
    legacy_data = {"Филиал": {"05.08.2026": "не список"}}
    with open(sessions_mod.BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущен" in out
