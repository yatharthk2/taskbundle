"""pass2pass: base cases that must hold BEFORE and AFTER the golden patch."""
from mathx.core import factorial


def test_factorial_zero():
    assert factorial(0) == 1


def test_factorial_one():
    assert factorial(1) == 1
