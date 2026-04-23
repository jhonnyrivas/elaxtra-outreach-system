"""All database queries as async functions."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AgentSession,
    Contact,
    EmailHistory,
    ProcessedWebhookEvent,
    SendLog,
)


def _upsert_insert(session: AsyncSession):
    """Pick the right dialect-specific insert for ON CONFLICT support.

    We key off the DATABASE_URL so this works in both async+sync paths.
    Postgres is prod; SQLite is tests.
    """
    from src.config import settings  # local import to avoid cycle at import-time

    if "sqlite" in settings.DATABASE_URL:
        return sqlite_insert
    return pg_insert


# ---------- Contacts ----------


async def upsert_contact(session: AsyncSession, data: dict[str, Any]) -> Contact:
    """Insert or update by email."""
    email = data["contact_email"]
    existing = await get_contact_by_email(session, email)
    if existing:
        for k, v in data.items():
            if k != "id" and hasattr(existing, k):
                setattr(existing, k, v)
        await session.flush()
        return existing
    contact = Contact(**data)
    session.add(contact)
    await session.flush()
    return contact


async def get_contact_by_email(session: AsyncSession, email: str) -> Contact | None:
    result = await session.execute(select(Contact).where(Contact.contact_email == email))
    return result.scalar_one_or_none()


async def get_contact_by_thread_id(session: AsyncSession, thread_id: str) -> Contact | None:
    result = await session.execute(
        select(Contact).where(Contact.agentmail_thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def list_pending_contacts(session: AsyncSession, limit: int) -> list[Contact]:
    """Contacts ready for outbound — PENDING and never sent."""
    result = await session.execute(
        select(Contact)
        .where(Contact.outreach_status == "PENDING")
        .order_by(Contact.id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_contact_fields(
    session: AsyncSession, contact_id: int, fields: dict[str, Any]
) -> None:
    await session.execute(update(Contact).where(Contact.id == contact_id).values(**fields))


async def count_contacts_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(Contact.outreach_status, func.count(Contact.id)).group_by(Contact.outreach_status)
    )
    return {status: count for status, count in result.all()}


# ---------- Email history ----------


async def record_email(
    session: AsyncSession,
    *,
    contact_id: int,
    direction: str,
    thread_id: str | None,
    message_id: str | None,
    subject: str | None,
    body_text: str | None,
    body_html: str | None,
    classification: str | None = None,
    agent_session_id: str | None = None,
) -> EmailHistory:
    entry = EmailHistory(
        contact_id=contact_id,
        direction=direction,
        agentmail_thread_id=thread_id,
        agentmail_message_id=message_id,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        classification=classification,
        agent_session_id=agent_session_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_thread_history(
    session: AsyncSession, thread_id: str
) -> list[EmailHistory]:
    result = await session.execute(
        select(EmailHistory)
        .where(EmailHistory.agentmail_thread_id == thread_id)
        .order_by(EmailHistory.created_at.asc())
    )
    return list(result.scalars().all())


async def list_recent_inbound_replies(
    session: AsyncSession, *, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the most recent INBOUND messages joined with contact info.

    Used to answer "who replied this week / today" style questions.
    """
    result = await session.execute(
        select(
            EmailHistory.id,
            EmailHistory.subject,
            EmailHistory.classification,
            EmailHistory.created_at,
            Contact.contact_name,
            Contact.contact_email,
            Contact.company_name,
            Contact.outreach_status,
        )
        .join(Contact, Contact.id == EmailHistory.contact_id)
        .where(EmailHistory.direction == "INBOUND")
        .order_by(EmailHistory.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "email_id": row.id,
            "subject": row.subject,
            "classification": row.classification,
            "received_at": row.created_at.isoformat() if row.created_at else None,
            "contact_name": row.contact_name,
            "contact_email": row.contact_email,
            "company_name": row.company_name,
            "outreach_status": row.outreach_status,
        }
        for row in result.all()
    ]


async def list_recent_outbound_sends(
    session: AsyncSession, *, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the most recent OUTBOUND messages joined with contact info."""
    result = await session.execute(
        select(
            EmailHistory.id,
            EmailHistory.subject,
            EmailHistory.created_at,
            Contact.contact_name,
            Contact.contact_email,
            Contact.company_name,
            Contact.outreach_status,
        )
        .join(Contact, Contact.id == EmailHistory.contact_id)
        .where(EmailHistory.direction == "OUTBOUND")
        .order_by(EmailHistory.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "email_id": row.id,
            "subject": row.subject,
            "sent_at": row.created_at.isoformat() if row.created_at else None,
            "contact_name": row.contact_name,
            "contact_email": row.contact_email,
            "company_name": row.company_name,
            "outreach_status": row.outreach_status,
        }
        for row in result.all()
    ]


async def count_emails_by_direction_since(
    session: AsyncSession, since: datetime
) -> dict[str, int]:
    """{'OUTBOUND': N, 'INBOUND': M} for emails recorded since a timestamp."""
    result = await session.execute(
        select(EmailHistory.direction, func.count(EmailHistory.id))
        .where(EmailHistory.created_at >= since)
        .group_by(EmailHistory.direction)
    )
    return {direction: int(count) for direction, count in result.all()}


async def count_contacts_by_classification(
    session: AsyncSession,
) -> dict[str, int]:
    """Count contacts grouped by their latest reply_classification."""
    result = await session.execute(
        select(Contact.reply_classification, func.count(Contact.id))
        .where(Contact.reply_classification.is_not(None))
        .group_by(Contact.reply_classification)
    )
    return {cls: int(count) for cls, count in result.all()}


# ---------- Rate limiter ----------


async def get_send_count_for_date(session: AsyncSession, day: date) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(SendLog.count), 0)).where(SendLog.sent_date == day)
    )
    return int(result.scalar_one() or 0)


async def get_send_count_for_hour(session: AsyncSession, day: date, hour: int) -> int:
    result = await session.execute(
        select(func.coalesce(SendLog.count, 0)).where(
            SendLog.sent_date == day, SendLog.sent_hour == hour
        )
    )
    val = result.scalar_one_or_none()
    return int(val or 0)


async def increment_send_count(session: AsyncSession, day: date, hour: int) -> None:
    """Upsert: increment (day, hour) count by 1."""
    insert_fn = _upsert_insert(session)
    stmt = (
        insert_fn(SendLog)
        .values(sent_date=day, sent_hour=hour, count=1)
        .on_conflict_do_update(
            index_elements=["sent_date", "sent_hour"],
            set_={"count": SendLog.count + 1},
        )
    )
    await session.execute(stmt)


# ---------- Agent sessions (cost tracking) ----------


async def record_agent_session(
    session: AsyncSession,
    *,
    managed_session_id: str,
    agent_type: str,
    contact_id: int | None,
    status: str = "RUNNING",
) -> AgentSession:
    record = AgentSession(
        managed_session_id=managed_session_id,
        agent_type=agent_type,
        contact_id=contact_id,
        status=status,
    )
    session.add(record)
    await session.flush()
    return record


async def complete_agent_session(
    session: AsyncSession,
    *,
    managed_session_id: str,
    status: str,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    error_message: str | None = None,
) -> None:
    await session.execute(
        update(AgentSession)
        .where(AgentSession.managed_session_id == managed_session_id)
        .values(
            status=status,
            completed_at=datetime.utcnow(),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            error_message=error_message,
        )
    )


# ---------- Webhook idempotency ----------


async def is_event_processed(session: AsyncSession, event_id: str) -> bool:
    result = await session.execute(
        select(ProcessedWebhookEvent.event_id).where(
            ProcessedWebhookEvent.event_id == event_id
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_event_processed(
    session: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    payload: dict | None = None,
) -> bool:
    """Insert event_id atomically. Returns True if newly inserted, False if duplicate."""
    insert_fn = _upsert_insert(session)
    stmt = (
        insert_fn(ProcessedWebhookEvent)
        .values(event_id=event_id, event_type=event_type, payload=payload)
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    result = await session.execute(stmt)
    # rowcount > 0 means a row was inserted; 0 means conflict (already existed)
    return result.rowcount > 0
