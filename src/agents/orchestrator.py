"""Batch outreach orchestrator.

Pure Python (NOT a Managed Agent) — reads Excel, picks contacts, dispatches
composer sessions in parallel, sends via AgentMail, updates DB + Excel.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import anthropic

from src.config import settings
from src.db import queries as q
from src.db.engine import session_scope
from src.db.models import Contact
from src.services.agentmail_client import get_agentmail_client
from src.services.excel import read_contacts, update_row_status
from src.services.file_upload import company_profile_content_block
from src.services.rate_limiter import RateLimiter
from src.agents.sessions import AgentSessionError, run_agent_session
from src.utils.logging import get_logger

log = get_logger(__name__)


# --------- Composer invocation ---------


def _first_name(full_name: str) -> str:
    return (full_name or "").strip().split(" ")[0] or "there"


async def _compose_email(
    client: anthropic.AsyncAnthropic, contact: Contact
) -> dict:
    """Run the composer agent for one contact, return the parsed email JSON."""
    contact_payload = {
        "first_name": _first_name(contact.contact_name),
        "full_name": contact.contact_name,
        "email": contact.contact_email,
        "role": contact.contact_role,
        "company_name": contact.company_name,
        "company_website": contact.company_website,
        "headcount": contact.headcount,
        "service_type": contact.service_type,
        "linkedin_person_url": contact.linkedin_person_url,
        "linkedin_company_url": contact.linkedin_company_url,
        "country": contact.country,
    }
    user_content: list[dict] = [
        company_profile_content_block(settings.COMPANY_PROFILE_FILE_ID),
        {
            "type": "text",
            "text": (
                "Compose a personalized outreach email for this contact. "
                "Use the company profile PDF as context for Elaxtra's positioning.\n\n"
                f"CONTACT:\n{json.dumps(contact_payload, indent=2)}"
            ),
        },
    ]

    result = await run_agent_session(
        client=client,
        agent_id=settings.COMPOSER_AGENT_ID,
        environment_id=settings.ENVIRONMENT_ID,
        vault_ids=[settings.VAULT_ID] if settings.VAULT_ID else None,
        user_content=user_content,
        contact_id=contact.id,
        agent_type="composer",
    )
    email = result.parsed or {}
    for required_key in ("subject", "body_text", "body_html"):
        if not email.get(required_key):
            raise AgentSessionError(f"Composer output missing '{required_key}'")
    return email


# --------- Batch driver ---------


async def run_outreach_batch() -> dict:
    """Select + send one batch of outreach emails."""
    if not settings.setup_complete:
        raise RuntimeError("Setup not complete — run `python -m src.main setup` first.")

    rate_limiter = RateLimiter()
    remaining_day = await rate_limiter.remaining_today()
    remaining_hour = await rate_limiter.remaining_this_hour()
    budget = min(remaining_day, remaining_hour, settings.OUTREACH_BATCH_SIZE)

    if budget <= 0:
        log.info("batch_skipped_no_budget", remaining_day=remaining_day, remaining_hour=remaining_hour)
        return {"sent": 0, "skipped": 0, "errors": 0, "reason": "rate_limit"}

    async with session_scope() as session:
        contacts = await q.list_pending_contacts(session, budget)

    if not contacts:
        log.info("batch_skipped_no_contacts")
        return {"sent": 0, "skipped": 0, "errors": 0, "reason": "no_pending"}

    log.info(
        "batch_start",
        selected=len(contacts),
        budget=budget,
        dry_run=settings.DRY_RUN,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    am = get_agentmail_client()
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_AGENT_SESSIONS)

    async def _process_one(contact: Contact) -> str:
        async with semaphore:
            # Re-check rate limit per-send (budget might be consumed by others)
            if not await rate_limiter.can_send():
                log.info("contact_skipped_rate_limit", email=contact.contact_email)
                return "rate_limited"

            try:
                email = await _compose_email(client, contact)
            except AgentSessionError as e:
                log.error("composer_failed", email=contact.contact_email, error=str(e))
                await _mark_error(contact.id, str(e))
                return "error"

            try:
                send_result = await am.send_outbound(
                    inbox_id=settings.AGENTMAIL_INBOX_ID,
                    to_email=contact.contact_email,
                    to_name=contact.contact_name,
                    subject=email["subject"],
                    text=email["body_text"],
                    html=email["body_html"],
                )
            except Exception as e:
                log.error("agentmail_send_failed", email=contact.contact_email, error=str(e))
                await _mark_error(contact.id, f"send_failed: {e}")
                return "error"

            # Record + update
            await rate_limiter.record_send()
            now = datetime.utcnow()
            async with session_scope() as session:
                await q.update_contact_fields(
                    session,
                    contact.id,
                    {
                        "outreach_status": "SENT",
                        "outreach_date": now,
                        "agentmail_thread_id": send_result.get("thread_id"),
                        "agentmail_inbox_id": settings.AGENTMAIL_INBOX_ID,
                    },
                )
                await q.record_email(
                    session,
                    contact_id=contact.id,
                    direction="OUTBOUND",
                    thread_id=send_result.get("thread_id"),
                    message_id=send_result.get("message_id"),
                    subject=email["subject"],
                    body_text=email["body_text"],
                    body_html=email["body_html"],
                )

            if contact.excel_row_number:
                try:
                    update_row_status(
                        settings.CONTACTS_EXCEL_PATH,
                        contact.excel_row_number,
                        status="SENT",
                        outreach_date=now,
                    )
                except Exception as e:
                    log.warning("excel_update_failed", error=str(e))

            log.info("outreach_sent", email=contact.contact_email)

            # Respect MIN_DELAY_BETWEEN_EMAILS_SECONDS — yield the semaphore
            # *after* the delay so parallel workers don't stack sends
            await asyncio.sleep(settings.MIN_DELAY_BETWEEN_EMAILS_SECONDS)
            return "sent"

    results = await asyncio.gather(
        *[_process_one(c) for c in contacts], return_exceptions=True
    )

    summary = {
        "sent": sum(1 for r in results if r == "sent"),
        "rate_limited": sum(1 for r in results if r == "rate_limited"),
        "errors": sum(1 for r in results if r == "error" or isinstance(r, Exception)),
        "total": len(results),
    }
    log.info("batch_complete", **summary)
    return summary


async def _mark_error(contact_id: int, message: str) -> None:
    async with session_scope() as session:
        await q.update_contact_fields(
            session, contact_id, {"outreach_status": "ERROR", "conversation_summary": message[:500]}
        )
