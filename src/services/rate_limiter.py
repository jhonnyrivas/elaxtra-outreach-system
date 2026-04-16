"""DB-backed async rate limiter for outbound email sends."""
from __future__ import annotations

from datetime import UTC, date, datetime

from src.config import settings
from src.db import queries as q
from src.db.engine import session_scope


class RateLimiter:
    """Enforces MAX_EMAILS_PER_DAY, MAX_EMAILS_PER_HOUR.

    Persisted in the `send_log` table so multiple processes share state.
    """

    async def can_send(self) -> bool:
        today = date.today()
        hour = datetime.now(UTC).hour
        async with session_scope() as session:
            day_count = await q.get_send_count_for_date(session, today)
            hour_count = await q.get_send_count_for_hour(session, today, hour)
        return (
            day_count < settings.MAX_EMAILS_PER_DAY
            and hour_count < settings.MAX_EMAILS_PER_HOUR
        )

    async def record_send(self) -> None:
        today = date.today()
        hour = datetime.now(UTC).hour
        async with session_scope() as session:
            await q.increment_send_count(session, today, hour)

    async def remaining_today(self) -> int:
        today = date.today()
        async with session_scope() as session:
            count = await q.get_send_count_for_date(session, today)
        return max(0, settings.MAX_EMAILS_PER_DAY - count)

    async def remaining_this_hour(self) -> int:
        today = date.today()
        hour = datetime.now(UTC).hour
        async with session_scope() as session:
            count = await q.get_send_count_for_hour(session, today, hour)
        return max(0, settings.MAX_EMAILS_PER_HOUR - count)
