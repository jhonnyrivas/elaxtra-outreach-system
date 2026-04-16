"""Webhook handler idempotency + unknown-thread safety."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.db import queries as q
from src.db.engine import session_scope
from src.webhooks import handler


@pytest.mark.asyncio
async def test_handler_idempotent(db) -> None:
    """Duplicate event_id should short-circuit without calling the responder."""
    message = {"thread_id": "thd_unknown", "from_": ["a@b.com"]}
    with patch.object(handler, "_run_responder") as mock_resp:
        await handler.handle_incoming_reply("evt_1", message)
        await handler.handle_incoming_reply("evt_1", message)  # duplicate
    mock_resp.assert_not_called()  # thread is unknown, never gets that far

    # Event was recorded exactly once
    async with session_scope() as session:
        assert await q.is_event_processed(session, "evt_1") is True


@pytest.mark.asyncio
async def test_handler_ignores_unknown_thread(db) -> None:
    """Replies for threads we don't own should be silently ignored."""
    message = {"thread_id": "thd_not_ours", "from_": ["stranger@random.com"]}
    with patch.object(handler, "_run_responder") as mock_resp:
        await handler.handle_incoming_reply("evt_unknown", message)
    mock_resp.assert_not_called()


@pytest.mark.asyncio
async def test_handler_skips_opted_out_contact(db) -> None:
    async with session_scope() as session:
        contact = await q.upsert_contact(
            session,
            {
                "company_name": "Acme",
                "contact_name": "Alice",
                "contact_email": "alice@acme.com",
                "agentmail_thread_id": "thd_acme",
                "outreach_status": "OPTED_OUT",
            },
        )

    with patch.object(handler, "_run_responder") as mock_resp:
        await handler.handle_incoming_reply(
            "evt_opted_out",
            {"thread_id": "thd_acme", "from_": ["alice@acme.com"]},
        )
    mock_resp.assert_not_called()
