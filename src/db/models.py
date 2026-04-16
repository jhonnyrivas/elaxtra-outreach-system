"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    contact_role: Mapped[str | None] = mapped_column(Text)
    company_website: Mapped[str | None] = mapped_column(Text)
    headcount: Mapped[int | None] = mapped_column(Integer)
    service_type: Mapped[str | None] = mapped_column(Text)
    linkedin_person_url: Mapped[str | None] = mapped_column(Text)
    linkedin_company_url: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)

    # Outreach state machine
    outreach_status: Mapped[str] = mapped_column(Text, default="PENDING", nullable=False)
    outreach_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reply_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_follow_up_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_classification: Mapped[str | None] = mapped_column(Text)
    conversation_summary: Mapped[str | None] = mapped_column(Text)

    # AgentMail refs
    agentmail_thread_id: Mapped[str | None] = mapped_column(Text)
    agentmail_inbox_id: Mapped[str | None] = mapped_column(Text)

    excel_row_number: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    emails: Mapped[list["EmailHistory"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_contacts_status", "outreach_status"),
        Index("idx_contacts_thread", "agentmail_thread_id"),
        Index("idx_contacts_email", "contact_email"),
        CheckConstraint(
            "outreach_status IN ('PENDING','SENT','REPLIED','INTERESTED',"
            "'CALL_PROPOSED','CALL_BOOKED','OPTED_OUT','BOUNCED','ERROR')",
            name="ck_contacts_outreach_status",
        ),
    )


class EmailHistory(Base):
    __tablename__ = "email_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    agentmail_thread_id: Mapped[str | None] = mapped_column(Text)
    agentmail_message_id: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str | None] = mapped_column(Text)
    agent_session_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    contact: Mapped["Contact"] = relationship(back_populates="emails")

    __table_args__ = (
        Index("idx_email_history_contact", "contact_id"),
        Index("idx_email_history_thread", "agentmail_thread_id"),
        CheckConstraint(
            "direction IN ('OUTBOUND','INBOUND')", name="ck_email_history_direction"
        ),
    )


class SendLog(Base):
    """Rate limiter tracking — one row per (date, hour) with count."""

    __tablename__ = "send_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_date: Mapped[date] = mapped_column(Date, nullable=False)
    sent_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (UniqueConstraint("sent_date", "sent_hour", name="uq_send_log_date_hour"),)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    managed_session_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_type: Mapped[str] = mapped_column(Text, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    status: Mapped[str] = mapped_column(Text, default="RUNNING", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tokens_input: Mapped[int | None] = mapped_column(BigInteger)
    tokens_output: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "agent_type IN ('composer','responder','scheduler')", name="ck_agent_sessions_type"
        ),
    )


class ProcessedWebhookEvent(Base):
    """Idempotency — store seen webhook event IDs to skip duplicates."""

    __tablename__ = "processed_webhook_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict | None] = mapped_column(JSON)
