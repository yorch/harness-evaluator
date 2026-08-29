"""Tests for geometry calculations (basic correctness)."""

import math

from src.solution import (
    calculate_circle_area,
    calculate_circle_perimeter,
    calculate_rectangle_area,
    calculate_rectangle_perimeter,
)


def test_rectangle_area():
    assert calculate_rectangle_area(3, 4) == 12


def test_rectangle_perimeter():
    assert calculate_rectangle_perimeter(3, 4) == 14


def test_circle_area():
    assert abs(calculate_circle_area(1) - math.pi) < 1e-9


def test_circle_perimeter():
    assert abs(calculate_circle_perimeter(1) - 2 * math.pi) < 1e-9


def test_rectangle_area_large():
    assert calculate_rectangle_area(100, 200) == 20000
