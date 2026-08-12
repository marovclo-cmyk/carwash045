"""
Тесты разового скрипта миграции carwash_branches.json -> БД (GAP-DB1,
этап 3). Изоляция через тот же DATA_DIR, что и sessions_mod (см.
conftest.py) — migrate_branches_to_db.py читает DATA_DIR тем же способом,
что и миграции users/advances.
"""
import importlib
import json


def _run_migration(sessions_mod):
    import migrate_branches_to_db
    module = importlib.reload(migrate_branches_to_db)
    module.main()


def test_migrate_noop_when_json_absent(sessions_mod, capsys):
    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "не найден" in out  # ничего не упало — миграция тихо пропущена


def test_migrate_transfers_full_branch_config(sessions_mod):
    legacy_data = {
        "Тестовый филиал": {
            "admin": 12345,
            "workers": ["Иван", "Пётр"],
            "admin_names": ["Иван"],
            "boxes": [{"id": 1, "name": "Бокс 1"}, {"id": 2, "name": "Бокс 2"}],
            "boxes_next_id": 3,
            "stock": {"olympea": {"qty": 10, "min_qty": 2}},
            "schedules": {"Иван": {"work": 3, "rest": 1, "start": "2026-01-01"}},
        }
    }
    with open(sessions_mod.BRANCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    cfg = sessions_mod.get_branch_config("Тестовый филиал")
    assert cfg["admin"] == 12345
    assert cfg["workers"] == ["Иван", "Пётр"]
    assert cfg["admin_names"] == ["Иван"]
    assert cfg["boxes"] == [{"id": 1, "name": "Бокс 1"}, {"id": 2, "name": "Бокс 2"}]
    assert cfg["boxes_next_id"] == 3
    assert cfg["stock"] == {"olympea": {"qty": 10, "min_qty": 2}}
    assert cfg["schedules"] == {"Иван": {"work": 3, "rest": 1, "start": "2026-01-01"}}


def test_migrate_computes_boxes_next_id_when_missing(sessions_mod):
    """Тот же регресс, что раньше проверял JSON-вариант load_branches_config:
    если у старых данных нет boxes_next_id, миграция должна вычислить его
    как max(текущих id боксов) + 1, а не начать заново с 1."""
    legacy_data = {
        "Тестовый филиал": {
            "admin": 0, "workers": [], "admin_names": [],
            "boxes": [{"id": 1, "name": "Бокс 1"}, {"id": 3, "name": "Бокс 3"}],
            # boxes_next_id намеренно отсутствует
        }
    }
    with open(sessions_mod.BRANCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)

    new_box = sessions_mod.add_branch_box("Тестовый филиал", "Бокс 4")
    assert new_box["id"] == 4  # не переиспользует id=2 (никогда не выданный)


def test_migrate_is_idempotent(sessions_mod):
    legacy_data = {"Тестовый филиал": {"admin": 1, "workers": ["Иван"], "admin_names": []}}
    with open(sessions_mod.BRANCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    _run_migration(sessions_mod)

    cfg = sessions_mod.get_branch_config("Тестовый филиал")
    assert cfg["workers"] == ["Иван"]  # не задублировалось


def test_migrate_skips_malformed_branch_entry(sessions_mod, capsys):
    legacy_data = {"Сломанный филиал": "не объект"}
    with open(sessions_mod.BRANCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    _run_migration(sessions_mod)
    out = capsys.readouterr().out
    assert "Пропущен" in out
