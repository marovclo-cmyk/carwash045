"""
Тесты авансов сотрудников после переноса на БД (GAP-DB1, этап 2).

Изоляция та же, что и у test_sessions_users.py: sessions_mod подменяет
DATA_DIR, db.py пересчитывает engine по нему при каждом обращении —
отдельная DB-фикстура не нужна.

Контракт функций (форма записи {"idx","date","amount","ts"}, idx —
сквозной счётчик В ПРЕДЕЛАХ (branch, name)) идентичен дореформенному
JSON-хранилищу.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]
BRANCH2 = BRANCHES[1]


def test_get_employee_advances_empty_by_default(sessions_mod):
    assert sessions_mod.get_employee_advances(BRANCH, "Иван") == []


def test_add_advance_returns_entry(sessions_mod):
    entry = sessions_mod.add_advance(BRANCH, "Иван", 1000)
    assert entry["idx"] == 0
    assert entry["amount"] == 1000
    assert "date" in entry and "ts" in entry


def test_add_advance_then_list(sessions_mod):
    sessions_mod.add_advance(BRANCH, "Иван", 1000)
    entries = sessions_mod.get_employee_advances(BRANCH, "Иван")
    assert len(entries) == 1
    assert entries[0]["amount"] == 1000


def test_idx_increments_per_branch_and_name(sessions_mod):
    e1 = sessions_mod.add_advance(BRANCH, "Иван", 500)
    e2 = sessions_mod.add_advance(BRANCH, "Иван", 700)
    assert (e1["idx"], e2["idx"]) == (0, 1)


def test_idx_independent_per_employee(sessions_mod):
    """У разных сотрудников (даже в одном филиале) свой собственный
    счётчик idx, начинающийся с 0 — как и было в JSON-версии
    ({branch: {name: [...]}})."""
    e_ivan = sessions_mod.add_advance(BRANCH, "Иван", 100)
    e_petr = sessions_mod.add_advance(BRANCH, "Пётр", 200)
    assert e_ivan["idx"] == 0
    assert e_petr["idx"] == 0


def test_idx_independent_per_branch(sessions_mod):
    """Тот же сотрудник в разных филиалах — тоже независимые счётчики."""
    sessions_mod.add_advance(BRANCH, "Иван", 100)
    e2 = sessions_mod.add_advance(BRANCH2, "Иван", 200)
    assert e2["idx"] == 0
    assert len(sessions_mod.get_employee_advances(BRANCH, "Иван")) == 1
    assert len(sessions_mod.get_employee_advances(BRANCH2, "Иван")) == 1


def test_delete_advance_returns_true_when_existed(sessions_mod):
    sessions_mod.add_advance(BRANCH, "Иван", 300)
    assert sessions_mod.delete_advance(BRANCH, "Иван", 0) is True
    assert sessions_mod.get_employee_advances(BRANCH, "Иван") == []


def test_delete_advance_returns_false_when_absent(sessions_mod):
    assert sessions_mod.delete_advance(BRANCH, "Иван", 99) is False


def test_delete_one_leaves_others_intact(sessions_mod):
    sessions_mod.add_advance(BRANCH, "Иван", 100)
    sessions_mod.add_advance(BRANCH, "Иван", 200)
    sessions_mod.delete_advance(BRANCH, "Иван", 0)
    entries = sessions_mod.get_employee_advances(BRANCH, "Иван")
    assert len(entries) == 1
    assert entries[0]["amount"] == 200
    assert entries[0]["idx"] == 1


def test_date_filter_excludes_out_of_range(sessions_mod):
    from datetime import datetime, timedelta
    sessions_mod.add_advance(BRANCH, "Иван", 100)  # дата "сегодня"
    tomorrow = datetime.now() + timedelta(days=1)
    assert sessions_mod.get_employee_advances(BRANCH, "Иван", date_from=tomorrow) == []
    yesterday = datetime.now() - timedelta(days=1)
    assert len(sessions_mod.get_employee_advances(BRANCH, "Иван", date_from=yesterday)) == 1
