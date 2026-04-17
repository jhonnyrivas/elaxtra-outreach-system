"""FastAPI webhook router. Returns 200 immediately; processing is async."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.config import settings
from src.utils.logging import get_logger
from src.webhooks.handler import handle_bounce, handle_complaint, handle_incoming_reply
from src.webhooks.verify import WebhookSignatureError, verify_webhook_signature

log = get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/webhooks/agentmail")
async def agentmail_webhook(request: Request):
    """Receives AgentMail webhook events.

    CRITICAL: return 200 immediately; queue processing on the event loop.
    AgentMail retries on non-200, which would spam duplicate agent sessions.

    We use `asyncio.create_task` instead of FastAPI's `BackgroundTasks`
    because BackgroundTasks runs AFTER the response is returned but within
    the same request context — for long-running agent sessions we want to
    hand off to the event loop and let the response ship instantly.
    """
    if not settings.setup_complete:
        log.warning("webhook_rejected_setup_incomplete")
        return JSONResponse(
            status_code=503,
            content={"error": "system not configured"},
        )

    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    try:
        verify_webhook_signature(raw_body, headers)
    except WebhookSignatureError as e:
        log.warning("webhook_signature_invalid", error=str(e))
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from e

    try:
        payload = await request.json()
    except Exception as e:
        log.warning("webhook_bad_json", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON body") from e

    event_type = payload.get("event_type")
    event_id = payload.get("event_id") or f"evt_synthetic_{uuid.uuid4().hex}"

    log.info("webhook_received", event_type=event_type, event_id=event_id)

    if event_type == "message.received":
        asyncio.create_task(_safe_handle(handle_incoming_reply, event_id, payload.get("message", {})))
    elif event_type == "message.bounced":
        asyncio.create_task(_safe_handle(handle_bounce, event_id, payload.get("bounce", {})))
    elif event_type == "message.complained":
        asyncio.create_task(_safe_handle(handle_complaint, event_id, payload.get("complaint", {})))
    else:
        log.info("webhook_ignored_event_type", event_type=event_type)

    return {"status": "ok", "event_id": event_id}


async def _safe_handle(handler, event_id: str, data: dict) -> None:
    """Wrap background handler so exceptions don't crash the event loop."""
    try:
        await handler(event_id, data)
    except Exception:
        log.exception("background_handler_failed", event_id=event_id)
