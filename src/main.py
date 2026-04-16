"""CLI entry point."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
import uvicorn

from src.config import settings
from src.utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


@click.group()
def cli() -> None:
    """Elaxtra Outreach System CLI."""


@cli.command()
def serve() -> None:
    """Start the webhook server and the outreach scheduler.

    Port resolution order (for Railway/Render/Fly compatibility):
      1. $PORT (injected by Railway/Heroku/etc.)
      2. $WEBHOOK_PORT (our own env var)
      3. 8000 default
    """
    import os

    port = int(os.environ.get("PORT") or settings.WEBHOOK_PORT or 8000)
    uvicorn.run(
        "src.app:app",
        host=settings.WEBHOOK_HOST,
        port=port,
        log_config=None,  # we configure our own
    )


@cli.command()
def setup() -> None:
    """One-time: create environment, vault, agents, upload PDF, create inbox.

    Writes all returned IDs to .env. Safe to re-run — reuses existing IDs.
    """
    from src.agents.registry import initial_setup

    result = asyncio.run(initial_setup())
    click.echo("✅ Setup complete.")
    click.echo(f"   Environment:  {result.environment_id}")
    click.echo(f"   Vault:        {result.vault_id}")
    click.echo(f"   Composer:     {result.composer_agent_id} (v{result.composer_agent_version})")
    click.echo(f"   Responder:    {result.responder_agent_id} (v{result.responder_agent_version})")
    click.echo(f"   Scheduler:    {result.scheduler_agent_id} (v{result.scheduler_agent_version})")
    click.echo(f"   Profile PDF:  {result.company_profile_file_id}")
    click.echo(f"   Inbox:        {result.agentmail_inbox_id}")
    click.echo("\nIDs written to .env — restart `serve` to pick them up.")


@cli.command("update-agents")
def update_agents_cmd() -> None:
    """Push current agent configs as new versions (bumps agent version numbers)."""
    from src.agents.registry import update_agents

    result = asyncio.run(update_agents())
    click.echo("✅ Agents updated.")
    click.echo(f"   Composer:  v{result.composer_agent_version}")
    click.echo(f"   Responder: v{result.responder_agent_version}")
    click.echo(f"   Scheduler: v{result.scheduler_agent_version}")


@cli.command()
def batch() -> None:
    """Run one outreach batch immediately (manual trigger)."""
    from src.agents.orchestrator import run_outreach_batch

    result = asyncio.run(run_outreach_batch())
    click.echo(f"Batch complete: {result}")


@cli.command("import-contacts")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=lambda: settings.CONTACTS_EXCEL_PATH,
)
def import_contacts_cmd(file_path: Path) -> None:
    """Import contacts from Excel → Postgres."""
    from src.db import queries as q
    from src.db.engine import session_scope
    from src.services.excel import read_contacts

    async def _run() -> int:
        rows = read_contacts(file_path)
        eligible = [r for r in rows if r.is_eligible()]
        imported = 0
        async with session_scope() as session:
            for row in eligible:
                await q.upsert_contact(session, row.to_contact_dict())
                imported += 1
        return imported

    count = asyncio.run(_run())
    click.echo(f"✅ Imported {count} eligible contacts from {file_path}")


@cli.command()
def status() -> None:
    """Show rate-limit budget, pending contacts, and active sessions."""
    from src.db import queries as q
    from src.db.engine import session_scope
    from src.services.rate_limiter import RateLimiter

    async def _run() -> dict:
        rl = RateLimiter()
        async with session_scope() as session:
            by_status = await q.count_contacts_by_status(session)
        return {
            "remaining_today": await rl.remaining_today(),
            "remaining_this_hour": await rl.remaining_this_hour(),
            "contacts_by_status": by_status,
            "setup_complete": settings.setup_complete,
        }

    data = asyncio.run(_run())
    click.echo("System status:")
    for k, v in data.items():
        click.echo(f"  {k}: {v}")


@cli.command("add-credential")
@click.option("--server", "server_name", required=True, help="MCP server name (e.g. hubspot, apollo, ms365)")
@click.option("--url", "mcp_server_url", required=True, help="MCP server URL")
@click.option("--token", "access_token", required=True, help="OAuth access token")
@click.option("--refresh-token", "refresh_token", default=None)
@click.option("--token-endpoint", "token_endpoint", default=None)
@click.option("--client-id", "client_id", default=None)
@click.option("--client-secret", "client_secret", default=None)
def add_credential_cmd(
    server_name: str,
    mcp_server_url: str,
    access_token: str,
    refresh_token: str | None,
    token_endpoint: str | None,
    client_id: str | None,
    client_secret: str | None,
) -> None:
    """Store an MCP OAuth credential in the vault."""
    from src.agents.registry import add_mcp_credential

    credential_id = asyncio.run(
        add_mcp_credential(
            server_name=server_name,
            mcp_server_url=mcp_server_url,
            access_token=access_token,
            refresh_token=refresh_token,
            token_endpoint=token_endpoint,
            client_id=client_id,
            client_secret=client_secret,
        )
    )
    click.echo(f"✅ Credential {credential_id} added to vault.")


@cli.command("setup-webhook")
@click.option("--url", "webhook_url", required=True, help="Public URL of your /webhooks/agentmail endpoint")
def setup_webhook_cmd(webhook_url: str) -> None:
    """Register the AgentMail webhook."""
    from src.services.agentmail_client import get_agentmail_client

    async def _run() -> dict:
        am = get_agentmail_client()
        return await am.register_webhook(webhook_url, settings.AGENTMAIL_INBOX_ID)

    result = asyncio.run(_run())
    click.echo(f"✅ Webhook registered: {result}")


if __name__ == "__main__":
    # Allow `python -m src.main <command>`
    cli()
