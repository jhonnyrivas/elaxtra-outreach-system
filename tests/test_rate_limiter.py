"""RateLimiter enforces hourly + daily caps."""
from __future__ import annotations

import pytest

from src.config import settings
from src.services.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_sends_under_cap(db) -> None:
    rl = RateLimiter()
    assert await rl.can_send() is True
    assert await rl.remaining_today() == settings.MAX_EMAILS_PER_DAY


@pytest.mark.asyncio
async def test_rate_limiter_blocks_at_hourly_cap(db) -> None:
    rl = RateLimiter()
    for _ in range(settings.MAX_EMAILS_PER_HOUR):
        await rl.record_send()
    assert await rl.can_send() is False
    assert await rl.remaining_this_hour() == 0


@pytest.mark.asyncio
async def test_rate_limiter_decrements_remaining(db) -> None:
    rl = RateLimiter()
    before = await rl.remaining_today()
    await rl.record_send()
    after = await rl.remaining_today()
    assert after == before - 1
