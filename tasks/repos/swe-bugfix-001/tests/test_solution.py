"""Tests for the pagination utility."""

from src.solution import get_page


def test_get_page_short_list():
    """A list shorter than page_size returns all items."""
    assert get_page([1, 2, 3], 1, 10) == [1, 2, 3]


def test_get_page_empty_list():
    """An empty list returns an empty page."""
    assert get_page([], 1, 10) == []


def test_get_page_beyond_end():
    """Requesting a page beyond the end returns an empty list."""
    assert get_page([1, 2, 3], 5, 10) == []


def test_get_page_default_size():
    """Default page_size is 10."""
    items = list(range(5))
    assert get_page(items, 1) == items
