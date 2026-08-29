"""Tests for the duplicate detection utility."""

from src.solution import find_duplicates


def test_basic_duplicate_finding():
    """Items appearing more than once are reported in first-occurrence order."""
    assert find_duplicates([1, 2, 3, 2, 1]) == [1, 2]


def test_no_duplicates_returns_empty():
    """A list with all unique items returns an empty list."""
    assert find_duplicates([1, 2, 3, 4]) == []
