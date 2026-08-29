"""Tests for the HTTP client wrapper."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.solution import HttpClient


def test_successful_request():
    """A 200 response is returned unchanged."""
    client = HttpClient("http://example.com")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("src.solution.requests.get", return_value=mock_resp):
        resp = client.get("/users")
    assert resp.status_code == 200


def test_timeout_raises():
    """A timeout surfaces as a requests.Timeout."""
    client = HttpClient("http://example.com")
    with patch("src.solution.requests.get", side_effect=requests.Timeout("timed out")):
        with patch("src.solution.time.sleep"):
            with pytest.raises(requests.Timeout):
                client.get("/users")
