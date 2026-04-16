"""Orchestrator respects rate limits and setup guard."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents import orchestrator
from src.config import settings


@pytest.mark.asyncio
async def test_orchestrator_skips_when_no_pending_contacts(db) -> None:
    result = await orchestrator.run_outreach_batch()
    assert result["sent"] == 0
    assert result["reason"] == "no_pending"


@pytest.mark.asyncio
async def test_orchestrator_requires_setup(db) -> None:
    with patch.object(type(settings), "setup_complete", property(lambda self: False)):
        with pytest.raises(RuntimeError, match="Setup not complete"):
            await orchestrator.run_outreach_batch()


@pytest.mark.asyncio
async def test_orchestrator_skips_when_rate_limited(db) -> None:
    # Consume the hourly budget
    from src.services.rate_limiter import RateLimiter

    rl = RateLimiter()
    for _ in range(settings.MAX_EMAILS_PER_HOUR):
        await rl.record_send()
    for _ in range(settings.MAX_EMAILS_PER_DAY):
        await rl.record_send()

    result = await orchestrator.run_outreach_batch()
    assert result["reason"] == "rate_limit"
