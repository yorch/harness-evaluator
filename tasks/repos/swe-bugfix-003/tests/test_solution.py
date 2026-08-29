"""Tests for the nested dict traversal utility."""

from src.solution import deep_get


def test_basic_nested_access():
    """Traversing existing keys returns the leaf value."""
    data = {"a": {"b": {"c": 42}}}
    assert deep_get(data, ["a", "b", "c"]) == 42


def test_single_key_access():
    """A single-key path returns the corresponding value."""
    assert deep_get({"a": 1}, ["a"]) == 1


def test_default_param_unused_when_found():
    """The default is only returned when a key is missing."""
    assert deep_get({"a": 1}, ["a"], default="missing") == 1
