"""pass2pass: invariants that hold for `add` BEFORE and AFTER the fix (a-b == a+b when b == 0)."""
from calc import add


def test_add_zero_right_identity():
    assert add(7, 0) == 7        # 7 - 0 == 7 + 0


def test_add_zero_zero():
    assert add(0, 0) == 0
