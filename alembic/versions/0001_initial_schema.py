"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=False, unique=True),
        sa.Column("contact_role", sa.Text()),
        sa.Column("company_website", sa.Text()),
        sa.Column("headcount", sa.Integer()),
        sa.Column("service_type", sa.Text()),
        sa.Column("linkedin_person_url", sa.Text()),
        sa.Column("linkedin_company_url", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("outreach_status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("outreach_date", sa.DateTime(timezone=True)),
        sa.Column("last_reply_date", sa.DateTime(timezone=True)),
        sa.Column("next_follow_up_date", sa.DateTime(timezone=True)),
        sa.Column("reply_classification", sa.Text()),
        sa.Column("conversation_summary", sa.Text()),
        sa.Column("agentmail_thread_id", sa.Text()),
        sa.Column("agentmail_inbox_id", sa.Text()),
        sa.Column("excel_row_number", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "outreach_status IN ('PENDING','SENT','REPLIED','INTERESTED',"
            "'CALL_PROPOSED','CALL_BOOKED','OPTED_OUT','BOUNCED','ERROR')",
            name="ck_contacts_outreach_status",
        ),
    )
    op.create_index("idx_contacts_status", "contacts", ["outreach_status"])
    op.create_index("idx_contacts_thread", "contacts", ["agentmail_thread_id"])
    op.create_index("idx_contacts_email", "contacts", ["contact_email"])

    op.create_table(
        "email_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("agentmail_thread_id", sa.Text()),
        sa.Column("agentmail_message_id", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("body_text", sa.Text()),
        sa.Column("body_html", sa.Text()),
        sa.Column("classification", sa.Text()),
        sa.Column("agent_session_id", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("direction IN ('OUTBOUND','INBOUND')", name="ck_email_history_direction"),
    )
    op.create_index("idx_email_history_contact", "email_history", ["contact_id"])
    op.create_index("idx_email_history_thread", "email_history", ["agentmail_thread_id"])

    op.create_table(
        "send_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sent_date", sa.Date(), nullable=False),
        sa.Column("sent_hour", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("sent_date", "sent_hour", name="uq_send_log_date_hour"),
    )

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("managed_session_id", sa.Text(), nullable=False),
        sa.Column("agent_type", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id")),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("tokens_input", sa.BigInteger()),
        sa.Column("tokens_output", sa.BigInteger()),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "agent_type IN ('composer','responder','scheduler')",
            name="ck_agent_sessions_type",
        ),
    )

    op.create_table(
        "processed_webhook_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", sa.JSON()),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
    op.drop_table("agent_sessions")
    op.drop_table("send_log")
    op.drop_index("idx_email_history_thread", table_name="email_history")
    op.drop_index("idx_email_history_contact", table_name="email_history")
    op.drop_table("email_history")
    op.drop_index("idx_contacts_email", table_name="contacts")
    op.drop_index("idx_contacts_thread", table_name="contacts")
    op.drop_index("idx_contacts_status", table_name="contacts")
    op.drop_table("contacts")
