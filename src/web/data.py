"""Data fetchers for the dashboard. Keeps route handlers slim.

Every function here returns a plain dict/list that's easy to render from
Jinja2 (for HTML pages) or serialize as JSON (for the /api endpoints).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import anthropic
from sqlalchemy import desc, select

from src.agents.assistant import ASSISTANT_NAME, build_assistant_agent_config
from src.agents.composer import COMPOSER_NAME, build_composer_agent_config
from src.agents.responder import RESPONDER_NAME, build_responder_agent_config
from src.agents.scheduler_agent import SCHEDULER_NAME, build_scheduler_agent_config
from src.config import settings
from src.db import queries as q
from src.db.engine import session_scope
from src.db.models import AgentSession, Contact, EmailHistory
from src.services.system_stats import build_system_snapshot
from src.utils.logging import get_logger

log = get_logger(__name__)


# --- Human-readable metadata for MCP servers. Augments the bare URL/name --- #
MCP_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "apollo": {
        "purpose": "B2B contact & company enrichment. Look up a domain or a person and get firmographics, headcount, tech stack, and more.",
        "auth": "OAuth 2.0 (credential stored in vault)",
    },
    "apify": {
        "purpose": "Web / LinkedIn scraping actors. Used sparingly to fill in a contact's role or recent posts when Apollo data is thin.",
        "auth": "OAuth 2.0 (credential stored in vault)",
    },
    "hubspot": {
        "purpose": "Sync outreach activity into HubSpot CRM — contacts, deals, tasks — so Andrew can see everything inside the CRM.",
        "auth": "OAuth 2.0 (credential stored in vault)",
    },
    "ms365": {
        "purpose": "Microsoft 365 calendar. The Scheduler agent checks Andrew's availability and proposes specific call times.",
        "auth": "OAuth 2.0 (credential stored in vault)",
    },
    "agentmail": {
        "purpose": "Inbox + threads + delivery. The agents send, reply, and read threads through this MCP when appropriate.",
        "auth": "API key (AGENTMAIL_API_KEY)",
    },
}


# ---------------- Agents ---------------- #


AGENT_DESCRIPTORS = [
    {
        "key": "composer",
        "display": COMPOSER_NAME,
        "role": "Writes one personalized outbound cold email per target contact.",
        "id_setting": "COMPOSER_AGENT_ID",
        "version_setting": "COMPOSER_AGENT_VERSION",
        "model_setting": "COMPOSER_MODEL",
        "config_builder": build_composer_agent_config,
    },
    {
        "key": "responder",
        "display": RESPONDER_NAME,
        "role": "Classifies inbound replies (INTERESTED / QUESTION / OPT_OUT / ...) and drafts a response.",
        "id_setting": "RESPONDER_AGENT_ID",
        "version_setting": "RESPONDER_AGENT_VERSION",
        "model_setting": "RESPONDER_MODEL",
        "config_builder": build_responder_agent_config,
    },
    {
        "key": "scheduler",
        "display": SCHEDULER_NAME,
        "role": "When a contact agrees to a call, checks Andrew's M365 calendar and proposes specific times.",
        "id_setting": "SCHEDULER_AGENT_ID",
        "version_setting": "SCHEDULER_AGENT_VERSION",
        "model_setting": "SCHEDULER_MODEL",
        "config_builder": build_scheduler_agent_config,
    },
    {
        "key": "assistant",
        "display": ASSISTANT_NAME,
        "role": "Internal read-only concierge. Answers status/Q&A email queries from Andrew and authorized staff.",
        "id_setting": "ASSISTANT_AGENT_ID",
        "version_setting": "ASSISTANT_AGENT_VERSION",
        "model_setting": "ASSISTANT_MODEL",
        "config_builder": build_assistant_agent_config,
    },
]


async def get_agents_overview() -> list[dict[str, Any]]:
    """Combine the local agent descriptors with a live fetch from Anthropic.

    Degrades gracefully: if the Anthropic call fails (e.g. missing key in
    dev), we still show the static descriptors with `live_status="unavailable"`.
    """
    client: anthropic.AsyncAnthropic | None = None
    if settings.ANTHROPIC_API_KEY:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    rows: list[dict[str, Any]] = []
    for desc in AGENT_DESCRIPTORS:
        agent_id = getattr(settings, desc["id_setting"])
        version = getattr(settings, desc["version_setting"])
        model = getattr(settings, desc["model_setting"])
        config = desc["config_builder"]()

        row: dict[str, Any] = {
            "key": desc["key"],
            "display": desc["display"],
            "role": desc["role"],
            "agent_id": agent_id,
            "version": version,
            "model": model,
            "description": config.get("description", ""),
            "tools": [t.get("type") or t.get("mcp_server_name") for t in config.get("tools", [])],
            "mcp_servers": [
                {"name": s.get("name"), "url": s.get("url")}
                for s in config.get("mcp_servers", []) or []
            ],
            "live_status": "not_configured",
            "live_name": None,
            "live_description": None,
        }

        if client and agent_id:
            try:
                live = await client.beta.agents.retrieve(agent_id=agent_id)
                row["live_status"] = "ok"
                row["live_name"] = getattr(live, "name", None)
                row["live_description"] = getattr(live, "description", None)
                row["version"] = str(getattr(live, "version", version))
            except Exception as e:
                log.warning("dashboard_agent_retrieve_failed", agent_id=agent_id, error=str(e))
                row["live_status"] = "unavailable"

        rows.append(row)

    return rows


# ---------------- MCP Servers ---------------- #


async def get_mcp_servers_overview() -> list[dict[str, Any]]:
    """Union of MCP servers declared across all agent configs.

    For each server we surface: name, url, list of agents that declare it,
    and the human-readable purpose/auth metadata from MCP_DESCRIPTIONS.
    """
    servers: dict[str, dict[str, Any]] = {}
    for desc in AGENT_DESCRIPTORS:
        config = desc["config_builder"]()
        for s in config.get("mcp_servers", []) or []:
            name = s.get("name") or "unknown"
            url = s.get("url") or ""
            server = servers.setdefault(
                name,
                {
                    "name": name,
                    "url": url,
                    "agents": [],
                    "purpose": MCP_DESCRIPTIONS.get(name, {}).get(
                        "purpose", "No description available."
                    ),
                    "auth": MCP_DESCRIPTIONS.get(name, {}).get(
                        "auth", "Credentials not configured"
                    ),
                },
            )
            if desc["display"] not in server["agents"]:
                server["agents"].append(desc["display"])

    # AgentMail isn't declared in agent configs but is a first-class MCP we use
    # out-of-band for inbox/webhooks. Surface it so the demo can mention it.
    if "agentmail" not in servers:
        servers["agentmail"] = {
            "name": "agentmail",
            "url": settings.MCP_AGENTMAIL_URL,
            "agents": ["(used out-of-band by the system)"],
            "purpose": MCP_DESCRIPTIONS["agentmail"]["purpose"],
            "auth": MCP_DESCRIPTIONS["agentmail"]["auth"],
        }

    return sorted(servers.values(), key=lambda s: s["name"])


# ---------------- Activity feed ---------------- #


async def get_activity_feed(limit: int = 30) -> list[dict[str, Any]]:
    """Unified timeline: outbound/inbound emails + agent sessions, newest first."""
    items: list[dict[str, Any]] = []

    async with session_scope() as session:
        email_q = await session.execute(
            select(EmailHistory, Contact)
            .join(Contact, Contact.id == EmailHistory.contact_id)
            .order_by(desc(EmailHistory.created_at))
            .limit(limit)
        )
        for email, contact in email_q.all():
            items.append(
                {
                    "type": "email_outbound" if email.direction == "OUTBOUND" else "email_inbound",
                    "at": email.created_at,
                    "subject": email.subject or "(no subject)",
                    "classification": email.classification,
                    "contact_name": contact.contact_name,
                    "contact_email": contact.contact_email,
                    "company_name": contact.company_name,
                }
            )

        session_q = await session.execute(
            select(AgentSession, Contact)
            .outerjoin(Contact, Contact.id == AgentSession.contact_id)
            .order_by(desc(AgentSession.started_at))
            .limit(limit)
        )
        for agent_session, contact in session_q.all():
            items.append(
                {
                    "type": "agent_session",
                    "at": agent_session.started_at,
                    "agent_type": agent_session.agent_type,
                    "managed_session_id": agent_session.managed_session_id,
                    "status": agent_session.status,
                    "tokens_input": agent_session.tokens_input,
                    "tokens_output": agent_session.tokens_output,
                    "contact_name": contact.contact_name if contact else None,
                    "company_name": contact.company_name if contact else None,
                }
            )

    items.sort(key=lambda x: x["at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return items[:limit]


# ---------------- Contacts ---------------- #


async def list_contacts(
    *, limit: int = 50, offset: int = 0, search: str | None = None
) -> dict[str, Any]:
    """Return contacts rows for the table + a total count for pagination."""
    from sqlalchemy import func, or_

    async with session_scope() as session:
        q_filter = []
        if search:
            needle = f"%{search.lower()}%"
            q_filter.append(
                or_(
                    func.lower(Contact.company_name).like(needle),
                    func.lower(Contact.contact_name).like(needle),
                    func.lower(Contact.contact_email).like(needle),
                )
            )

        total = (
            await session.execute(
                select(func.count(Contact.id)).where(*q_filter) if q_filter else select(func.count(Contact.id))
            )
        ).scalar_one()

        stmt = select(Contact)
        if q_filter:
            stmt = stmt.where(*q_filter)
        stmt = stmt.order_by(desc(Contact.updated_at)).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()

        row_list = [
            {
                "id": c.id,
                "company_name": c.company_name,
                "contact_name": c.contact_name,
                "contact_email": c.contact_email,
                "contact_role": c.contact_role,
                "outreach_status": c.outreach_status,
                "reply_classification": c.reply_classification,
                "last_reply_date": c.last_reply_date,
                "country": c.country,
                "headcount": c.headcount,
                "updated_at": c.updated_at,
            }
            for c in rows
        ]

    # NOTE: key must not collide with dict method names like `items` — Jinja
    # resolves `obj.attr` by attribute first, then key, and would return the
    # bound method `dict.items` instead of our row list.
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "rows": row_list,
    }


# ---------------- Overview ---------------- #


async def get_overview() -> dict[str, Any]:
    """Bundle snapshot + agent count + mcp count + recent activity for the landing page."""
    snapshot = await build_system_snapshot(recent_limit=5)
    agents = await get_agents_overview()
    mcp_servers = await get_mcp_servers_overview()
    activity = await get_activity_feed(limit=10)

    configured_agents = [a for a in agents if a["agent_id"]]
    return {
        "snapshot": snapshot,
        "agents_total": len(agents),
        "agents_configured": len(configured_agents),
        "mcp_total": len(mcp_servers),
        "agents_preview": agents,
        "mcp_preview": mcp_servers,
        "activity_preview": activity,
        "setup_complete": settings.setup_complete,
        "public_url": settings.PUBLIC_WEBHOOK_URL,
        "inbox_address": settings.AGENTMAIL_INBOX_ADDRESS,
        "allowed_assistant_senders": sorted(settings.assistant_allowed_senders),
    }
