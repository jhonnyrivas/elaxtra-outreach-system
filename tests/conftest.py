"""Test fixtures: in-memory SQLite DB and mocked external clients."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Force test env before importing src.config
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("AGENTMAIL_API_KEY", "am-test")
os.environ.setdefault("AGENTMAIL_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("COMPANY_PROFILE_FILE_ID", "file_test")
os.environ.setdefault("AGENTMAIL_INBOX_ID", "inbox_test")
os.environ.setdefault("ENVIRONMENT_ID", "env_test")
os.environ.setdefault("VAULT_ID", "vlt_test")
os.environ.setdefault("COMPOSER_AGENT_ID", "agent_composer_test")
os.environ.setdefault("RESPONDER_AGENT_ID", "agent_responder_test")
os.environ.setdefault("SCHEDULER_AGENT_ID", "agent_scheduler_test")

from src.db.engine import get_engine, dispose_engine  # noqa: E402
from src.db.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def db():
    """Fresh in-memory SQLite schema for each test."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await dispose_engine()


@pytest.fixture
def mock_anthropic_client():
    """Mock anthropic.AsyncAnthropic with a working sessions.events.stream."""
    client = MagicMock()

    # Session create returns an object with .id
    session_obj = MagicMock()
    session_obj.id = "sesn_test_123"
    session_obj.status = "running"
    client.beta.sessions.create = AsyncMock(return_value=session_obj)

    # Event send is a no-op
    client.beta.sessions.events.send = AsyncMock()

    # Stream yields a predictable event sequence; tests can override
    async def _noop_stream(session_id: str):
        class _Stream:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration
        return _Stream()

    client.beta.sessions.events.stream = _noop_stream
    return client


@pytest.fixture
def mock_agentmail_client():
    """Mock AgentMailClient with no-op async methods."""
    am = MagicMock()
    am.send_outbound = AsyncMock(
        return_value={"thread_id": "thd_test", "message_id": "<msg@test>"}
    )
    am.reply = AsyncMock(return_value={"thread_id": "thd_test", "message_id": "<reply@test>"})
    am.get_thread = AsyncMock(return_value={"thread_id": "thd_test", "messages": []})
    am.register_webhook = AsyncMock(return_value={"id": "wh_test", "url": "https://test"})
    am.get_or_create_inbox = AsyncMock(
        return_value={"id": "inbox_test", "address": "elaxtra@agentmail.to"}
    )
    return am
