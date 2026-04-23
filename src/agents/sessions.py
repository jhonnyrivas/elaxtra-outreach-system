"""Managed Agent session helper.

Pattern (from Anthropic Managed Agents docs):
1. Create session referencing pre-created agent ID
2. Open the SSE stream BEFORE sending the kickoff event
3. Send user message with content
4. Iterate the stream collecting agent.message text blocks
5. Break on session.status_terminated or on session.status_idle with a
   terminal stop_reason (NOT on `requires_action` — that means the agent
   needs something from us, and we'd deadlock if we broke)
6. Parse JSON from the collected text
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import anthropic

from src.db import queries as q
from src.db.engine import session_scope
from src.utils.logging import get_logger

log = get_logger(__name__)


class AgentSessionError(RuntimeError):
    pass


@dataclass
class AgentRunResult:
    session_id: str
    raw_text: str
    parsed: dict[str, Any] | None
    tokens_input: int | None
    tokens_output: int | None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of the text.

    The agent is instructed to emit ONLY JSON, but occasionally adds stray
    whitespace or markdown fences. Strip those, then try parsing.
    """
    if not text:
        return None
    cleaned = text.strip()
    # Remove ```json ... ``` fences if present
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Last-resort: grab the outermost {...}
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


async def run_agent_session(
    *,
    client: anthropic.AsyncAnthropic,
    agent_id: str,
    environment_id: str,
    user_content: list[dict],
    vault_ids: list[str] | None = None,
    contact_id: int | None = None,
    agent_type: str = "composer",
    timeout_seconds: float = 300.0,
) -> AgentRunResult:
    """Run one session to completion and return the parsed JSON.

    timeout_seconds caps the whole operation — the SSE stream can hang if
    something goes wrong server-side, and we don't want an orchestrator
    worker blocked indefinitely.
    """
    try:
        return await asyncio.wait_for(
            _run_agent_session_inner(
                client=client,
                agent_id=agent_id,
                environment_id=environment_id,
                user_content=user_content,
                vault_ids=vault_ids,
                contact_id=contact_id,
                agent_type=agent_type,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as e:
        log.error("agent_session_timeout", agent_type=agent_type, timeout=timeout_seconds)
        raise AgentSessionError(f"Agent session timed out after {timeout_seconds}s") from e


async def _run_agent_session_inner(
    *,
    client: anthropic.AsyncAnthropic,
    agent_id: str,
    environment_id: str,
    user_content: list[dict],
    vault_ids: list[str] | None,
    contact_id: int | None,
    agent_type: str,
) -> AgentRunResult:
    create_kwargs: dict[str, Any] = {
        "agent": agent_id,  # string shorthand = latest version
        "environment_id": environment_id,
    }
    if vault_ids:
        create_kwargs["vault_ids"] = vault_ids

    session = await client.beta.sessions.create(**create_kwargs)
    session_id = session.id

    # Record the session in our DB for cost/usage tracking
    async with session_scope() as db:
        await q.record_agent_session(
            db,
            managed_session_id=session_id,
            agent_type=agent_type,
            contact_id=contact_id,
        )

    log.info(
        "agent_session_created",
        session_id=session_id,
        agent_type=agent_type,
        agent_id=agent_id,
    )

    # Open the stream BEFORE sending the kickoff event (stream-first rule)
    full_text = ""
    tokens_input: int | None = None
    tokens_output: int | None = None
    error: str | None = None

    try:
        # In current anthropic SDK (>=0.96), `events.stream()` is `async def`
        # and returns an AsyncStream; we must await the call before using it
        # as an async context manager.
        stream_cm = await client.beta.sessions.events.stream(session_id=session_id)
        async with stream_cm as stream:
            # Now send the kickoff user message
            await client.beta.sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": user_content}],
            )

            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "agent.message":
                    for block in getattr(event, "content", []) or []:
                        if getattr(block, "type", None) == "text":
                            full_text += block.text

                elif event_type == "span.model_request_end":
                    # Accumulate token usage from each model request
                    usage = getattr(event, "model_usage", None)
                    if usage:
                        tokens_input = (tokens_input or 0) + (
                            getattr(usage, "input_tokens", 0) or 0
                        )
                        tokens_output = (tokens_output or 0) + (
                            getattr(usage, "output_tokens", 0) or 0
                        )

                elif event_type == "session.status_terminated":
                    log.info("session_terminated", session_id=session_id)
                    break

                elif event_type == "session.status_idle":
                    stop_reason = getattr(event, "stop_reason", None)
                    reason_type = (
                        getattr(stop_reason, "type", None)
                        if stop_reason
                        else None
                    )
                    if reason_type == "requires_action":
                        # Agent is waiting on a tool confirmation or custom tool result.
                        # Our agents don't use always_ask or custom tools, so this shouldn't
                        # happen — if it does, log and keep streaming (don't deadlock).
                        log.warning(
                            "session_idle_requires_action",
                            session_id=session_id,
                            stop_reason=str(stop_reason),
                        )
                        continue
                    # end_turn or retries_exhausted — both terminal
                    log.info(
                        "session_idle_terminal",
                        session_id=session_id,
                        stop_reason=reason_type,
                    )
                    break

                elif event_type == "session.error":
                    error = str(getattr(event, "error", event))
                    log.error("session_error", session_id=session_id, error=error)
                    break

    except Exception as e:
        error = str(e)
        log.error("agent_session_stream_failed", session_id=session_id, error=error)
        raise
    finally:
        async with session_scope() as db:
            await q.complete_agent_session(
                db,
                managed_session_id=session_id,
                status="ERROR" if error else "COMPLETED",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                error_message=error,
            )

    if error:
        raise AgentSessionError(f"Session {session_id} failed: {error}")

    parsed = _extract_json(full_text)
    if parsed is None:
        log.error(
            "agent_json_parse_failed",
            session_id=session_id,
            raw_preview=full_text[:500],
        )
        raise AgentSessionError(
            f"Failed to parse JSON from agent response. Raw text: {full_text[:500]!r}"
        )

    return AgentRunResult(
        session_id=session_id,
        raw_text=full_text,
        parsed=parsed,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )
