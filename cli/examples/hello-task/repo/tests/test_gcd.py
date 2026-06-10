"""Visible test — the solver sees this; it is NOT a pass2pass / fail2pass test."""
from mathx.core import gcd


def test_gcd_basic():
    assert gcd(12, 8) == 4
    assert gcd(17, 5) == 1
