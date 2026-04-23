"""AgentMail SDK wrapper — send, reply, inbox + webhook management."""
from __future__ import annotations

from typing import Any

from agentmail import AgentMail, AsyncAgentMail

from src.config import settings
from src.utils.logging import get_logger

log = get_logger(__name__)


class AgentMailClient:
    """Thin wrapper around the async AgentMail SDK.

    Centralizes the BCC rule — every outbound send goes to andrew.burgert.
    """

    def __init__(self) -> None:
        self._client = AsyncAgentMail(api_key=settings.AGENTMAIL_API_KEY)

    @property
    def raw(self) -> AsyncAgentMail:
        return self._client

    # ---- Inbox management ----

    async def get_or_create_inbox(self, address: str) -> dict[str, Any]:
        """Find an inbox by address, or create one.

        SDK notes (agentmail 0.4.x):
        - `inboxes.list()` returns a ListInboxesResponse with `.inboxes` list;
          each Inbox exposes `.email` (NOT `.address`) and `.inbox_id`.
        - `inboxes.create()` takes `request=CreateInboxRequest(...)` — kwargs
          like `username=` on the top-level call are no longer supported.
        """
        try:
            resp = await self._client.inboxes.list()
            inbox_list = getattr(resp, "inboxes", None) or list(resp)
            for inbox in inbox_list:
                email = getattr(inbox, "email", None) or getattr(inbox, "address", None)
                if email == address:
                    return {"id": inbox.inbox_id, "address": address}
        except Exception as e:
            log.warning("inbox_list_failed", error=str(e))

        from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest

        username, _, domain = address.partition("@")
        req = CreateInboxRequest(
            username=username or None,
            domain=domain or None,
        )
        created = await self._client.inboxes.create(request=req)
        return {"id": created.inbox_id, "address": address}

    # ---- Outbound ----

    async def send_outbound(
        self,
        *,
        inbox_id: str,
        to_email: str,
        to_name: str | None,
        subject: str,
        text: str,
        html: str,
    ) -> dict[str, Any]:
        """Send an outbound (new-thread) email. BCC Andrew enforced here.

        SDK note (agentmail 0.4.x): `to`/`bcc` accept RFC-5322 strings (or
        lists of them), not dicts. Include the display name inline.
        """
        if settings.DRY_RUN:
            log.info("dry_run_send", to=to_email, subject=subject)
            return {"dry_run": True, "thread_id": "dry-thread", "message_id": "dry-msg"}

        to_addr = f'"{to_name}" <{to_email}>' if to_name else to_email
        result = await self._client.inboxes.messages.send(
            inbox_id,
            to=to_addr,
            bcc=settings.BCC_EMAIL,
            subject=subject,
            text=text,
            html=html,
        )
        return {
            "thread_id": getattr(result, "thread_id", None),
            "message_id": getattr(result, "message_id", None),
        }

    async def reply(
        self,
        *,
        inbox_id: str,
        thread_id: str,
        in_reply_to_message_id: str,
        subject: str,
        text: str,
        html: str,
    ) -> dict[str, Any]:
        """Reply within an existing thread (preserves threading headers).

        SDK note (agentmail 0.4.x): `reply` takes `inbox_id, message_id` as
        positional args and NO `subject` kwarg (subject is carried from the
        parent thread). `bcc` is a string/list of strings.
        """
        if settings.DRY_RUN:
            log.info("dry_run_reply", thread_id=thread_id, subject=subject)
            return {"dry_run": True, "message_id": "dry-msg"}

        result = await self._client.inboxes.messages.reply(
            inbox_id,
            in_reply_to_message_id,
            bcc=settings.BCC_EMAIL,
            text=text,
            html=html,
        )
        return {
            "thread_id": getattr(result, "thread_id", thread_id),
            "message_id": getattr(result, "message_id", None),
        }

    # ---- Reading threads ----

    async def get_thread(self, inbox_id: str, thread_id: str) -> dict[str, Any]:
        """Fetch the full thread (list of messages) — used to reconstruct context."""
        try:
            thread = await self._client.inboxes.threads.get(inbox_id, thread_id)
            messages = getattr(thread, "messages", []) or []
            return {
                "thread_id": thread_id,
                "messages": [
                    {
                        "message_id": getattr(m, "message_id", None),
                        "from": getattr(m, "from_", None),
                        "to": getattr(m, "to", None),
                        "subject": getattr(m, "subject", None),
                        "text": getattr(m, "text", None),
                        "html": getattr(m, "html", None),
                        "timestamp": getattr(m, "timestamp", None),
                    }
                    for m in messages
                ],
            }
        except Exception as e:
            log.warning("get_thread_failed", thread_id=thread_id, error=str(e))
            return {"thread_id": thread_id, "messages": []}

    # ---- Webhook management ----

    async def register_webhook(self, url: str, inbox_id: str) -> dict[str, Any]:
        """Register a webhook for message.received/bounced/complained events."""
        try:
            result = await self._client.webhooks.create(
                url=url,
                event_types=["message.received", "message.bounced", "message.complained"],
                inbox_ids=[inbox_id],
            )
            return {"id": getattr(result, "webhook_id", None), "url": url}
        except AttributeError:
            # Older SDKs may expose a sync namespace; fall through to sync client.
            sync = AgentMail(api_key=settings.AGENTMAIL_API_KEY)
            result = sync.webhooks.create(
                url=url,
                event_types=["message.received", "message.bounced", "message.complained"],
                inbox_ids=[inbox_id],
            )
            return {"id": getattr(result, "webhook_id", None), "url": url}


_client: AgentMailClient | None = None


def get_agentmail_client() -> AgentMailClient:
    global _client
    if _client is None:
        _client = AgentMailClient()
    return _client
