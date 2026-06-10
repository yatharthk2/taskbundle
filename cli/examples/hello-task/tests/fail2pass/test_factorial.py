"""fail2pass: must FAIL on the buggy baseline, PASS after the golden patch."""
from mathx.core import factorial


def test_factorial_five():
    assert factorial(5) == 120


def test_factorial_six():
    assert factorial(6) == 720
