"""Async token-bucket rate limiter."""

import asyncio
import time


class RateLimiter:
    """A simple async token-bucket rate limiter.

    Args:
        rate: Tokens added per second.
        capacity: Maximum number of tokens the bucket can hold.
    """

    def __init__(self, rate, capacity):
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    async def acquire(self, n=1):
        """Acquire ``n`` tokens, waiting for a refill if necessary."""
        while True:
            self._refill()
            if self._tokens >= n:
                # Yield so other waiting tasks get a chance to proceed.
                await asyncio.sleep(0)
                # BUG: the check above and this decrement are not protected
                # by a lock. Multiple concurrent callers can all observe
                # enough tokens before any of them decrements, causing the
                # bucket to be over-spent (tokens going negative).
                self._tokens -= n
                return
            needed = n - self._tokens
            wait = needed / self.rate
            await asyncio.sleep(wait)
