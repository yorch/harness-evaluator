"""Tests for the CSV parser (simple cases without quotes)."""

from src.solution import parse_csv_line


def test_simple_fields():
    assert parse_csv_line("a,b,c") == ["a", "b", "c"]


def test_single_field():
    assert parse_csv_line("hello") == ["hello"]


def test_empty_fields():
    assert parse_csv_line("a,,c") == ["a", "", "c"]


def test_empty_string():
    assert parse_csv_line("") == [""]


def test_custom_delimiter():
    assert parse_csv_line("a|b|c", delimiter="|") == ["a", "b", "c"]
