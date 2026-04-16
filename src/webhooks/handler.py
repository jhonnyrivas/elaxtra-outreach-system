"""Webhook background handlers — spawn responder/scheduler agent sessions."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import anthropic

from src.agents.sessions import AgentSessionError, run_agent_session
from src.config import settings
from src.db import queries as q
from src.db.engine import session_scope
from src.services.agentmail_client import get_agentmail_client
from src.services.excel import update_row_status
from src.services.file_upload import company_profile_content_block
from src.utils.logging import get_logger

log = get_logger(__name__)


# Classification → new contact outreach_status
_STATUS_BY_CLASSIFICATION = {
    "INTERESTED": "INTERESTED",
    "OBJECTION": "REPLIED",
    "OPT_OUT": "OPTED_OUT",
    "QUESTION": "REPLIED",
    "AUTO_REPLY": "REPLIED",
    "REFERRAL": "REPLIED",
}


async def handle_incoming_reply(event_id: str, message: dict[str, Any]) -> None:
    """Process a message.received webhook event.

    Flow: dedupe event → find contact by thread → run Responder agent →
    send reply via AgentMail → update DB + Excel. If classification is
    INTERESTED with next_action=SCHEDULE_CALL, trigger the Scheduler agent.
    """
    thread_id = message.get("thread_id")
    incoming_message_id = message.get("message_id")
    from_addrs = message.get("from_") or message.get("from") or []
    sender_email = (from_addrs[0] if isinstance(from_addrs, list) else from_addrs) or ""

    # Idempotency — atomic insert-or-skip
    async with session_scope() as session:
        newly_inserted = await q.mark_event_processed(
            session,
            event_id=event_id,
            event_type="message.received",
            payload=message,
        )
    if not newly_inserted:
        log.info("webhook_duplicate_skipped", event_id=event_id)
        return

    if not thread_id:
        log.warning("webhook_missing_thread_id", event_id=event_id)
        return

    # Find the contact by thread ID
    async with session_scope() as session:
        contact = await q.get_contact_by_thread_id(session, thread_id)

    if not contact:
        log.info(
            "webhook_unknown_thread",
            event_id=event_id,
            thread_id=thread_id,
            sender=sender_email,
        )
        return

    if contact.outreach_status == "OPTED_OUT":
        log.info("webhook_opted_out_contact_ignored", contact_id=contact.id)
        return

    # Record the inbound email
    async with session_scope() as session:
        await q.record_email(
            session,
            contact_id=contact.id,
            direction="INBOUND",
            thread_id=thread_id,
            message_id=incoming_message_id,
            subject=message.get("subject"),
            body_text=message.get("text"),
            body_html=message.get("html"),
        )
        await q.update_contact_fields(
            session, contact.id, {"last_reply_date": datetime.utcnow()}
        )

    # Pull full thread for context
    am = get_agentmail_client()
    thread = await am.get_thread(settings.AGENTMAIL_INBOX_ID, thread_id)

    # Run Responder agent
    try:
        classification_result = await _run_responder(contact, thread, message)
    except AgentSessionError as e:
        log.error("responder_failed", contact_id=contact.id, error=str(e))
        return

    classification = classification_result.get("classification", "QUESTION")
    should_reply = bool(classification_result.get("should_reply"))
    next_action = classification_result.get("next_action", "NONE")

    # Update contact status based on classification
    new_status = _STATUS_BY_CLASSIFICATION.get(classification, "REPLIED")
    async with session_scope() as session:
        await q.update_contact_fields(
            session,
            contact.id,
            {
                "outreach_status": new_status,
                "reply_classification": classification,
                "conversation_summary": classification_result.get("notes"),
            },
        )
    if contact.excel_row_number:
        try:
            update_row_status(
                settings.CONTACTS_EXCEL_PATH,
                contact.excel_row_number,
                status=new_status,
            )
        except Exception as e:
            log.warning("excel_update_failed", error=str(e))

    # Send reply if applicable
    if should_reply and incoming_message_id:
        try:
            await am.reply(
                inbox_id=settings.AGENTMAIL_INBOX_ID,
                thread_id=thread_id,
                in_reply_to_message_id=incoming_message_id,
                subject=classification_result.get("reply_subject") or f"Re: {message.get('subject', '')}",
                text=classification_result.get("reply_body_text", ""),
                html=classification_result.get("reply_body_html", ""),
            )
            async with session_scope() as session:
                await q.record_email(
                    session,
                    contact_id=contact.id,
                    direction="OUTBOUND",
                    thread_id=thread_id,
                    message_id=None,
                    subject=classification_result.get("reply_subject"),
                    body_text=classification_result.get("reply_body_text"),
                    body_html=classification_result.get("reply_body_html"),
                    classification=classification,
                )
        except Exception as e:
            log.error("reply_send_failed", contact_id=contact.id, error=str(e))

    # Branch to scheduler if interested
    if classification == "INTERESTED" and next_action == "SCHEDULE_CALL":
        try:
            await _run_scheduler(contact, thread)
        except AgentSessionError as e:
            log.error("scheduler_failed", contact_id=contact.id, error=str(e))

    log.info(
        "webhook_processed",
        event_id=event_id,
        contact_id=contact.id,
        classification=classification,
    )


async def _run_responder(
    contact, thread: dict, new_message: dict
) -> dict[str, Any]:
    """Invoke the Responder agent and return the parsed JSON."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    user_payload = {
        "contact": {
            "name": contact.contact_name,
            "email": contact.contact_email,
            "company": contact.company_name,
            "role": contact.contact_role,
        },
        "new_reply": {
            "from": new_message.get("from_") or new_message.get("from"),
            "subject": new_message.get("subject"),
            "text": new_message.get("text"),
        },
        "thread_history": thread.get("messages", []),
    }

    user_content = [
        company_profile_content_block(settings.COMPANY_PROFILE_FILE_ID),
        {
            "type": "text",
            "text": (
                "Classify and respond to the new reply in this thread. "
                "Use the company profile for any factual details about Elaxtra.\n\n"
                f"CONTEXT:\n{json.dumps(user_payload, indent=2, default=str)}"
            ),
        },
    ]

    result = await run_agent_session(
        client=client,
        agent_id=settings.RESPONDER_AGENT_ID,
        environment_id=settings.ENVIRONMENT_ID,
        vault_ids=[settings.VAULT_ID] if settings.VAULT_ID else None,
        user_content=user_content,
        contact_id=contact.id,
        agent_type="responder",
    )
    return result.parsed or {}


async def _run_scheduler(contact, thread: dict) -> dict[str, Any]:
    """Invoke the Scheduler agent to propose times and send the scheduling email."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    user_payload = {
        "contact": {
            "name": contact.contact_name,
            "email": contact.contact_email,
            "company": contact.company_name,
            "country": contact.country,
        },
        "thread_history": thread.get("messages", []),
    }
    user_content = [
        company_profile_content_block(settings.COMPANY_PROFILE_FILE_ID),
        {
            "type": "text",
            "text": (
                "The contact agreed to a discovery call. Check calendar availability "
                "via the MS 365 MCP tool, propose 3 options, and draft the scheduling email.\n\n"
                f"CONTEXT:\n{json.dumps(user_payload, indent=2, default=str)}"
            ),
        },
    ]

    result = await run_agent_session(
        client=client,
        agent_id=settings.SCHEDULER_AGENT_ID,
        environment_id=settings.ENVIRONMENT_ID,
        vault_ids=[settings.VAULT_ID] if settings.VAULT_ID else None,
        user_content=user_content,
        contact_id=contact.id,
        agent_type="scheduler",
    )
    scheduler_output = result.parsed or {}

    # Send the proposal email if present
    subject = scheduler_output.get("email_subject")
    text = scheduler_output.get("email_body_text")
    html = scheduler_output.get("email_body_html")
    if subject and text and contact.agentmail_thread_id:
        am = get_agentmail_client()
        # Find the latest message in the thread to reply to
        messages = thread.get("messages", [])
        last_message_id = messages[-1]["message_id"] if messages else None
        if last_message_id:
            try:
                await am.reply(
                    inbox_id=settings.AGENTMAIL_INBOX_ID,
                    thread_id=contact.agentmail_thread_id,
                    in_reply_to_message_id=last_message_id,
                    subject=subject,
                    text=text,
                    html=html or f"<p>{text}</p>",
                )
                async with session_scope() as session:
                    await q.update_contact_fields(
                        session, contact.id, {"outreach_status": "CALL_PROPOSED"}
                    )
            except Exception as e:
                log.error("scheduler_send_failed", contact_id=contact.id, error=str(e))

    return scheduler_output


async def handle_bounce(event_id: str, bounce: dict[str, Any]) -> None:
    """Mark contact BOUNCED."""
    async with session_scope() as session:
        newly = await q.mark_event_processed(
            session, event_id=event_id, event_type="message.bounced", payload=bounce
        )
    if not newly:
        return

    recipient = (bounce.get("recipient") or bounce.get("email") or "").lower()
    if not recipient:
        return
    async with session_scope() as session:
        contact = await q.get_contact_by_email(session, recipient)
        if contact:
            await q.update_contact_fields(
                session, contact.id, {"outreach_status": "BOUNCED"}
            )
    log.info("bounce_recorded", recipient=recipient)


async def handle_complaint(event_id: str, complaint: dict[str, Any]) -> None:
    """Mark contact OPTED_OUT on spam complaint."""
    async with session_scope() as session:
        newly = await q.mark_event_processed(
            session,
            event_id=event_id,
            event_type="message.complained",
            payload=complaint,
        )
    if not newly:
        return

    recipient = (complaint.get("recipient") or complaint.get("email") or "").lower()
    if not recipient:
        return
    async with session_scope() as session:
        contact = await q.get_contact_by_email(session, recipient)
        if contact:
            await q.update_contact_fields(
                session, contact.id, {"outreach_status": "OPTED_OUT"}
            )
    log.info("complaint_recorded", recipient=recipient)
