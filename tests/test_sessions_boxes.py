"""
Тесты сущности «Бокс» sessions.py (GAP-BOX1).

Ключевое поведение по спецификации GAP-BOX1: id бокса сквозной по
филиалу и НЕ переиспользуется после удаления — это важно, потому что
старые записи (booking.box) ссылаются на id, и переиспользование id
привело бы к тому, что старая запись стала бы указывать на новый бокс.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]


def test_new_branch_has_no_boxes_by_default(sessions_mod):
    assert sessions_mod.get_branch_boxes(BRANCH) == []


def test_add_branch_box_assigns_sequential_id(sessions_mod):
    box1 = sessions_mod.add_branch_box(BRANCH, "Бокс А")
    box2 = sessions_mod.add_branch_box(BRANCH, "Бокс Б")
    assert box1["id"] == 1
    assert box2["id"] == 2
    assert [b["name"] for b in sessions_mod.get_branch_boxes(BRANCH)] == ["Бокс А", "Бокс Б"]


def test_add_branch_box_blank_name_gets_default_label(sessions_mod):
    box = sessions_mod.add_branch_box(BRANCH, "   ")
    assert box["name"] == f"Бокс {box['id']}"


def test_rename_branch_box(sessions_mod):
    box = sessions_mod.add_branch_box(BRANCH, "Старое имя")
    ok = sessions_mod.rename_branch_box(BRANCH, box["id"], "Новое имя")
    assert ok is True
    boxes = sessions_mod.get_branch_boxes(BRANCH)
    assert boxes[0]["name"] == "Новое имя"


def test_rename_nonexistent_box_returns_false(sessions_mod):
    assert sessions_mod.rename_branch_box(BRANCH, 999, "Имя") is False


def test_remove_branch_box(sessions_mod):
    box = sessions_mod.add_branch_box(BRANCH, "Бокс А")
    ok = sessions_mod.remove_branch_box(BRANCH, box["id"])
    assert ok is True
    assert sessions_mod.get_branch_boxes(BRANCH) == []


def test_remove_nonexistent_box_returns_false(sessions_mod):
    assert sessions_mod.remove_branch_box(BRANCH, 999) is False


def test_box_id_not_reused_after_removal(sessions_mod):
    """Ключевая гарантия GAP-BOX1: id не переиспользуется, даже если это
    освобождает "дырку" в последовательности."""
    box1 = sessions_mod.add_branch_box(BRANCH, "Бокс 1")  # id=1
    box2 = sessions_mod.add_branch_box(BRANCH, "Бокс 2")  # id=2
    sessions_mod.remove_branch_box(BRANCH, box2["id"])    # освобождаем id=2

    box3 = sessions_mod.add_branch_box(BRANCH, "Бокс 3")
    assert box3["id"] == 3  # НЕ переиспользует освободившийся id=2
    assert {b["box"] for b in sessions_mod.get_branch_boxes(BRANCH)} == {box1["id"], box3["id"]}


def test_get_branch_boxes_sorted_by_id(sessions_mod):
    sessions_mod.add_branch_box(BRANCH, "Второй")
    sessions_mod.add_branch_box(BRANCH, "Третий")
    boxes = sessions_mod.get_branch_boxes(BRANCH)
    assert [b["box"] for b in boxes] == sorted(b["box"] for b in boxes)


def test_boxes_are_isolated_per_branch(sessions_mod):
    other_branch = BRANCHES[1]
    sessions_mod.add_branch_box(BRANCH, "Бокс филиала 1")
    assert sessions_mod.get_branch_boxes(other_branch) == []


# Регресс "boxes_next_id отсутствует у старых данных → не начинать заново
# с 1" теперь актуален только на границе миграции JSON → БД (колонка в БД
# NOT NULL с дефолтом 1, поэтому внутри приложения это состояние больше не
# достижимо) — см. tests/test_migrate_branches.py::
# test_migrate_computes_boxes_next_id_when_missing.
