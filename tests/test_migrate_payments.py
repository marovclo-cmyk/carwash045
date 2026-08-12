"""
Тесты разового скрипта миграции carwash_payments.json -> БД (GAP-DB1,
этап 4). Изоляция через тот же DATA_DIR, что и sessions_mod (см.
conftest.py) — migrate_payments_to_db.py читает DATA_DIR тем же способом,
что и миграции users/advances/branches.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_payments_to_db
    module = importlib.reload(migrate_payments_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_payment_record(sessions_mod):
    legacy_data = {
        "mock_abc123": {
            "id": "mock_abc123",
            "branch": "Тестовый филиал",
            "purpose": "advance",
            "booking_id": 7,
            "car_num": None,
            "amount": 500,
            "description": "Предоплата",
            "phone": "+79990001122",
            "client_name": "Иван",
            "status": "succeeded",
            "provider": "mock",
            "confirmation_url": "https://example.test/pay/mock_abc123",
            "applied": True,
            "created_at": "2026-08-12T10:00:00",
            "updated_at": "2026-08-12T10:05:00",
            "paid_at": "2026-08-12T10:05:00",
        }
    }
    with open(sessions_mod.PAYMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    record = sessions_mod.get_payment("mock_abc123")
    assert record["branch"] == "Тестовый филиал"
    assert record["purpose"] == "advance"
    assert record["booking_id"] == 7
    assert record["car_num"] is None
    assert record["amount"] == 500
    assert record["phone"] == "+79990001122"
    assert record["status"] == "succeeded"
    assert record["applied"] is True
    assert record["paid_at"] == "2026-08-12T10:05:00"


def test_migrate_handles_pending_payment_without_paid_at(sessions_mod):
    legacy_data = {
        "mock_xyz789": {
            "id": "mock_xyz789",
            "branch": "Тестовый филиал",
            "purpose": "car",
            "booking_id": None,
            "car_num": 3,
            "amount": 1000,
            "description": "",
            "phone": "",
            "client_name": "",
            "status": "pending",
            "provider": "mock",
            "confirmation_url": "https://example.test/pay/mock_xyz789",
            "applied": False,
            "created_at": "2026-08-12T11:00:00",
            "updated_at": "2026-08-12T11:00:00",
            "paid_at": None,
        }
    }
    with open(sessions_mod.PAYMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    record = sessions_mod.get_payment("mock_xyz789")
    assert record["purpose"] == "car"
    assert record["car_num"] == 3
    assert record["applied"] is False
    assert record["paid_at"] is None


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {
        "mock_dup1": {
            "id": "mock_dup1", "branch": "Тестовый филиал", "purpose": "advance",
            "booking_id": 1, "car_num": None, "amount": 300, "description": "",
            "phone": "", "client_name": "", "status": "pending", "provider": "mock",
            "confirmation_url": "", "applied": False,
            "created_at": "2026-08-12T12:00:00", "updated_at": "2026-08-12T12:00:00",
            "paid_at": None,
        }
    }
    with open(sessions_mod.PAYMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    assert list(sessions_mod.load_payments().keys()) == ["mock_dup1"]  # не задублировалось


def test_migrate_skips_malformed_payment_entry(sessions_mod, capsys):
    legacy_data = {"broken": "не объект"}
    with open(sessions_mod.PAYMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущен" in out
