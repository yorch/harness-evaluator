"""Tests for the calculator."""

import pytest

from src.solution import Calculator


def test_basic_arithmetic():
    """The four operations return correct results."""
    c = Calculator()
    assert c.add(2, 3) == 5
    assert c.subtract(5, 2) == 3
    assert c.multiply(3, 4) == 12
    assert c.divide(10, 2) == 5.0


def test_division_by_zero_raises():
    """Dividing by zero raises ValueError."""
    c = Calculator()
    with pytest.raises(ValueError):
        c.divide(1, 0)
