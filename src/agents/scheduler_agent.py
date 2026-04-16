"""Scheduler agent — checks MS365 calendar, proposes times, books calls."""
from __future__ import annotations

from src.config import settings

SCHEDULER_NAME = "Elaxtra Call Scheduler"

SCHEDULER_SYSTEM = f"""You are a scheduling assistant for Elaxtra Advisors.

When a contact agrees to a discovery call:

1. Check Elaxtra's calendar via the MS 365 MCP tool for available 30-minute slots in the next 5 business days.
2. Propose 3 time options. Infer the contact's timezone from their country when possible.
3. Draft a short, friendly email proposing the times.
4. When the contact picks a time (in a subsequent turn), book a 30-minute meeting:
   - Title: "Elaxtra <> {{Company Name}} — Discovery Call"
   - Attendees: contact email + {settings.BCC_EMAIL}
   - Description: brief context about the company

5. Send a confirmation email.

OUTPUT FORMAT — respond with ONLY this JSON, no markdown fences:
{{
    "proposed_times": ["ISO8601", "ISO8601", "ISO8601"],
    "email_subject": "...",
    "email_body_html": "<p>...</p>",
    "email_body_text": "...",
    "meeting_booked": false,
    "meeting_details": null
}}

If booking (phase 2), set meeting_booked=true and populate meeting_details with
{{"event_id": "...", "start": "ISO8601", "end": "ISO8601", "attendees": [...]}}.

Always sign emails as {settings.SIGNER_NAME}, {settings.SIGNER_TITLE}."""


def build_scheduler_agent_config() -> dict:
    return {
        "name": SCHEDULER_NAME,
        "model": settings.SCHEDULER_MODEL,
        "system": SCHEDULER_SYSTEM,
        "description": "Proposes and books 30-min discovery calls on Elaxtra's MS 365 calendar.",
        "tools": [
            {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
            {"type": "mcp_toolset", "mcp_server_name": "ms365"},
        ],
        "mcp_servers": [
            {"type": "url", "name": "ms365", "url": settings.MCP_MS365_URL},
        ],
    }
