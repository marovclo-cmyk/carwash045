"""
Тесты складской логики sessions.py (GAP-P1: учёт остатков товаров).

Ключевое поведение по спецификации GAP-P1:
- товар без записи в stock — НЕ отслеживается, продажа без ограничений;
- как только для товара задан остаток (set_branch_stock) — он начинает
  списываться и может заблокировать продажу при нехватке;
- уведомление о низком остатке должно сработать РОВНО один раз в момент
  пересечения порога, а не при каждой последующей продаже.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]


def test_untracked_product_has_no_limit(sessions_mod):
    """Товар, для которого ни разу не вызывался set_branch_stock, продаётся
    без ограничений (как было до GAP-P1)."""
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 5)
    assert (ok, qty, crossed) == (True, None, False)
    assert sessions_mod.get_branch_stock(BRANCH) == {}


def test_set_branch_stock_creates_tracking_entry(sessions_mod):
    entry = sessions_mod.set_branch_stock(BRANCH, "olympea", qty=10, min_qty=2)
    assert entry == {"qty": 10, "min_qty": 2}
    assert sessions_mod.get_branch_stock(BRANCH) == {"olympea": {"qty": 10, "min_qty": 2}}


def test_set_branch_stock_is_absolute_not_delta(sessions_mod):
    """Повторный вызов set_branch_stock задаёт АБСОЛЮТНОЕ значение
    (калибровка), а не прибавляет к текущему остатку."""
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=10, min_qty=2)
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=3)
    assert sessions_mod.get_branch_stock(BRANCH)["olympea"]["qty"] == 3
    # min_qty не переданный — сохраняется прежним
    assert sessions_mod.get_branch_stock(BRANCH)["olympea"]["min_qty"] == 2


def test_decrement_tracked_product_success(sessions_mod):
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=5, min_qty=1)
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 2)
    assert ok is True
    assert qty == 3
    assert crossed is False
    assert sessions_mod.get_branch_stock(BRANCH)["olympea"]["qty"] == 3


def test_decrement_blocks_when_insufficient(sessions_mod):
    """При нехватке остатка списание не проходит и остаток НЕ меняется."""
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=1, min_qty=0)
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 5)
    assert ok is False
    assert qty == 1  # остаток не тронут
    assert crossed is False
    assert sessions_mod.get_branch_stock(BRANCH)["olympea"]["qty"] == 1


def test_decrement_crosses_threshold_only_once(sessions_mod):
    """crossed=True должно сработать только в момент ПЕРВОГО пересечения
    порога min_qty, а не при каждой последующей продаже ниже порога."""
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=3, min_qty=2)

    # 3 -> 2: остаток впервые опустился до порога -> crossed=True
    ok1, qty1, crossed1 = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 1)
    assert (ok1, qty1, crossed1) == (True, 2, True)

    # 2 -> 1: остаток уже был <= порога до этой продажи -> crossed=False
    ok2, qty2, crossed2 = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 1)
    assert (ok2, qty2, crossed2) == (True, 1, False)


def test_decrement_does_not_cross_threshold_when_staying_above(sessions_mod):
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=10, min_qty=2)
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 3)
    assert (ok, qty, crossed) == (True, 7, False)


def test_increment_restocks_tracked_product(sessions_mod):
    """Возврат товара на склад (например, при удалении продажи) —
    используется для отмены списания."""
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=5, min_qty=1)
    sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 3)  # 5 -> 2
    new_qty = sessions_mod.increment_branch_stock(BRANCH, "olympea", 3)
    assert new_qty == 5
    assert sessions_mod.get_branch_stock(BRANCH)["olympea"]["qty"] == 5


def test_increment_is_noop_for_untracked_product(sessions_mod):
    """Возврат на склад товара, который никогда не отслеживался, не
    заводит запись — метод по-прежнему считается неограниченным."""
    result = sessions_mod.increment_branch_stock(BRANCH, "olympea", 3)
    assert result is None
    assert sessions_mod.get_branch_stock(BRANCH) == {}


def test_clear_branch_stock_removes_tracking(sessions_mod):
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=5, min_qty=1)
    cleared = sessions_mod.clear_branch_stock(BRANCH, "olympea")
    assert cleared is True
    assert sessions_mod.get_branch_stock(BRANCH) == {}
    # после снятия с учёта товар снова продаётся без ограничений
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(BRANCH, "olympea", 999)
    assert (ok, qty, crossed) == (True, None, False)


def test_clear_branch_stock_returns_false_when_not_tracked(sessions_mod):
    assert sessions_mod.clear_branch_stock(BRANCH, "olympea") is False


def test_stock_is_isolated_per_branch(sessions_mod):
    """Остаток одного филиала не должен быть виден/списываться в другом."""
    other_branch = BRANCHES[1]
    sessions_mod.set_branch_stock(BRANCH, "olympea", qty=5, min_qty=1)
    assert sessions_mod.get_branch_stock(other_branch) == {}
    ok, qty, crossed = sessions_mod.try_decrement_branch_stock(other_branch, "olympea", 1)
    assert (ok, qty, crossed) == (True, None, False)  # в другом филиале товар не отслеживается
