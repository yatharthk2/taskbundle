"""Tiny math helpers used by the getting-started task."""


def factorial(n):
    """Return n! — the product of the integers 1 through n (factorial(0) == 1)."""
    result = 1
    for i in range(1, n):
        result *= i
    return result


def gcd(a, b):
    """Greatest common divisor via the Euclidean algorithm."""
    while b:
        a, b = b, a % b
    return a
