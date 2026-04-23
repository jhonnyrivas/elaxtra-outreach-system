"""allow 'assistant' in agent_sessions.agent_type check constraint

Revision ID: 0002_allow_assistant
Revises: 0001_initial
Create Date: 2026-04-23 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_allow_assistant"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_sessions_type", "agent_sessions", type_="check")
    op.create_check_constraint(
        "ck_agent_sessions_type",
        "agent_sessions",
        "agent_type IN ('composer','responder','scheduler','assistant')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_sessions_type", "agent_sessions", type_="check")
    op.create_check_constraint(
        "ck_agent_sessions_type",
        "agent_sessions",
        "agent_type IN ('composer','responder','scheduler')",
    )
