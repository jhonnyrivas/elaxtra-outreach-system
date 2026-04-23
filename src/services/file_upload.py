"""Upload the company profile PDF to Anthropic Files API.

Uploaded ONCE during `setup`. The returned file_id is persisted in .env and
reused across every agent session — the agent references it in each user
message so it's cached server-side.
"""
from __future__ import annotations

from pathlib import Path

import anthropic

from src.config import settings
from src.utils.logging import get_logger

log = get_logger(__name__)


async def upload_company_profile(
    client: anthropic.AsyncAnthropic, path: Path | str
) -> str:
    """Upload the PDF via Files API. Returns file_id."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Company profile PDF not found: {path}")

    with path.open("rb") as f:
        file = await client.beta.files.upload(
            file=(path.name, f, "application/pdf"),
            betas=["files-api-2025-04-14"],
        )
    log.info("company_profile_uploaded", file_id=file.id, path=str(path))
    return file.id


def company_profile_content_block(file_id: str) -> dict:
    """Build the document content block that every agent session gets.

    Placed at the start of the user message (before any per-request data)
    so every agent sees the PDF consistently. The Sessions API validates
    content block fields strictly — no `cache_control` here (it's rejected
    with "Extra inputs are not permitted"); caching is handled server-side
    by Anthropic and is opaque to us.
    """
    return {
        "type": "document",
        "source": {"type": "file", "file_id": file_id},
        "title": "Elaxtra Advisors — Company Profile",
    }
