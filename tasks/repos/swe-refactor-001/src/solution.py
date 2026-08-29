"""Geometry calculations with duplicated validation logic.

The input validation (checking for non-negative values) is duplicated
across every function. This should be refactored to remove the duplication
while keeping the public API and behavior identical.
"""

import math


def calculate_rectangle_area(length, width):
    """Calculate the area of a rectangle."""
    if length < 0 or width < 0:
        raise ValueError("Dimensions must be non-negative")
    return length * width


def calculate_rectangle_perimeter(length, width):
    """Calculate the perimeter of a rectangle."""
    if length < 0 or width < 0:
        raise ValueError("Dimensions must be non-negative")
    return 2 * (length + width)


def calculate_circle_area(radius):
    """Calculate the area of a circle."""
    if radius < 0:
        raise ValueError("Radius must be non-negative")
    return math.pi * radius**2


def calculate_circle_perimeter(radius):
    """Calculate the perimeter (circumference) of a circle."""
    if radius < 0:
        raise ValueError("Radius must be non-negative")
    return 2 * math.pi * radius
