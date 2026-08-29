"""HTTP client wrapper around the ``requests`` library."""

import time

import requests


class HttpClient:
    """A thin wrapper around ``requests`` for a fixed base URL.

    Args:
        base_url: The base URL prefix for all requests.
        timeout: Per-request timeout in seconds.
    """

    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path, **kwargs):
        url = f"{self.base_url}/{path.lstrip('/')}"
        return requests.get(url, timeout=self.timeout, **kwargs)

    def post(self, path, **kwargs):
        url = f"{self.base_url}/{path.lstrip('/')}"
        return requests.post(url, timeout=self.timeout, **kwargs)
