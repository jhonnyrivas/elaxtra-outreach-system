"""Build a `system_snapshot` — the live read-only view of the outreach
system that we hand to the Assistant agent (and later to the dashboard).

Centralizing the snapshot here means the Assistant and the dashboard see
the same numbers and any bug is fixed in one place.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import settings
from src.db import queries as q
from src.db.engine import session_scope
from src.services.rate_limiter import RateLimiter


async def build_system_snapshot(*, recent_limit: int = 10) -> dict[str, Any]:
    """Gather pipeline counts, rate-limit budget, and recent activity.

    Shape (stable — the Assistant's system prompt knows this structure):
    {
        "generated_at": ISO8601 UTC,
        "window": {"today": ISO date, "last_24h_from": ISO8601},
        "pipeline": {
            "contacts_by_status": {"PENDING": N, "SENT": N, ...},
            "contacts_by_reply_classification": {"INTERESTED": N, ...},
            "total_contacts": N,
        },
        "activity_last_24h": {"outbound_sends": N, "inbound_replies": N},
        "rate_limit": {
            "max_per_day": N, "remaining_today": N,
            "max_per_hour": N, "remaining_this_hour": N,
        },
        "recent_inbound_replies": [ {...}, ... ],
        "recent_outbound_sends": [ {...}, ... ],
        "config": {"dry_run": bool, "signer_name": str, "signer_title": str,
                   "bcc_email": str, "inbox_address": str},
    }
    """
    now = datetime.now(UTC)
    since_24h = now - timedelta(hours=24)

    rl = RateLimiter()
    async with session_scope() as session:
        by_status = await q.count_contacts_by_status(session)
        by_classification = await q.count_contacts_by_classification(session)
        activity = await q.count_emails_by_direction_since(session, since_24h)
        recent_in = await q.list_recent_inbound_replies(session, limit=recent_limit)
        recent_out = await q.list_recent_outbound_sends(session, limit=recent_limit)

    return {
        "generated_at": now.isoformat(),
        "window": {
            "today": now.date().isoformat(),
            "last_24h_from": since_24h.isoformat(),
        },
        "pipeline": {
            "contacts_by_status": by_status,
            "contacts_by_reply_classification": by_classification,
            "total_contacts": sum(by_status.values()),
        },
        "activity_last_24h": {
            "outbound_sends": int(activity.get("OUTBOUND", 0)),
            "inbound_replies": int(activity.get("INBOUND", 0)),
        },
        "rate_limit": {
            "max_per_day": settings.MAX_EMAILS_PER_DAY,
            "remaining_today": await rl.remaining_today(),
            "max_per_hour": settings.MAX_EMAILS_PER_HOUR,
            "remaining_this_hour": await rl.remaining_this_hour(),
        },
        "recent_inbound_replies": recent_in,
        "recent_outbound_sends": recent_out,
        "config": {
            "dry_run": settings.DRY_RUN,
            "signer_name": settings.SIGNER_NAME,
            "signer_title": settings.SIGNER_TITLE,
            "bcc_email": settings.BCC_EMAIL,
            "inbox_address": settings.AGENTMAIL_INBOX_ADDRESS,
        },
    }
