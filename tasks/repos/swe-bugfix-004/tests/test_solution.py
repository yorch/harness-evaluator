"""Tests for the async rate limiter."""

import time

import pytest

from src.solution import RateLimiter


@pytest.mark.asyncio
async def test_basic_acquire():
    """Acquiring tokens when available decrements the bucket."""
    rl = RateLimiter(rate=1, capacity=5)
    await rl.acquire(3)
    assert rl._tokens <= 2.1


@pytest.mark.asyncio
async def test_acquire_waits_for_refill():
    """Acquiring beyond capacity waits for tokens to refill."""
    rl = RateLimiter(rate=10, capacity=1)
    await rl.acquire(1)
    start = time.monotonic()
    await rl.acquire(1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05
