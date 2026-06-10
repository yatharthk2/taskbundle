"""fail2pass: real addition — FAILS on the baseline (a-b), PASSES once `add` returns a+b."""
from calc import add


def test_add_positive():
    assert add(2, 3) == 5        # baseline: 2 - 3 == -1


def test_add_larger():
    assert add(10, 5) == 15      # baseline: 10 - 5 == 5
