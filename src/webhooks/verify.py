"""Svix webhook signature verification for AgentMail webhooks."""
from __future__ import annotations

from svix.webhooks import Webhook, WebhookVerificationError

from src.config import settings


class WebhookSignatureError(Exception):
    pass


def verify_webhook_signature(raw_body: bytes, headers: dict[str, str]) -> None:
    """Raise WebhookSignatureError if the request signature is invalid.

    AgentMail uses Svix to sign webhooks. Headers we care about:
      - svix-id
      - svix-timestamp
      - svix-signature
    """
    secret = settings.AGENTMAIL_WEBHOOK_SECRET
    if not secret:
        raise WebhookSignatureError(
            "AGENTMAIL_WEBHOOK_SECRET is not configured — refusing to accept webhook"
        )

    # Svix library expects lowercase header keys and the raw body string
    svix_headers = {
        "svix-id": headers.get("svix-id", ""),
        "svix-timestamp": headers.get("svix-timestamp", ""),
        "svix-signature": headers.get("svix-signature", ""),
    }
    if not all(svix_headers.values()):
        raise WebhookSignatureError(
            f"Missing required Svix headers: {[k for k, v in svix_headers.items() if not v]}"
        )

    try:
        wh = Webhook(secret)
        wh.verify(raw_body.decode("utf-8"), svix_headers)
    except WebhookVerificationError as e:
        raise WebhookSignatureError(str(e)) from e
