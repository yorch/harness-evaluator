"""Tests for the Cache class (basic operations, no eviction)."""

from src.solution import Cache


def test_basic_get_put():
    cache = Cache(max_size=10)
    cache.put("a", 1)
    assert cache.get("a") == 1


def test_overwrite():
    cache = Cache(max_size=10)
    cache.put("a", 1)
    cache.put("a", 2)
    assert cache.get("a") == 2


def test_missing_key():
    cache = Cache(max_size=10)
    assert cache.get("missing") is None


def test_contains():
    cache = Cache(max_size=10)
    cache.put("x", 100)
    assert "x" in cache
    assert "y" not in cache


def test_len():
    cache = Cache(max_size=10)
    cache.put("a", 1)
    cache.put("b", 2)
    assert len(cache) == 2


def test_default_max_size():
    cache = Cache()
    assert cache.max_size == 100
