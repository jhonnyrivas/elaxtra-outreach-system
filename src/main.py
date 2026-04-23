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

    port = int(os.environ.get("PORT", os.environ.get("WEBHOOK_PORT", "8000")))
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")

    # Diagnostics go to stderr so Railway's Deploy Logs (which surface stderr
    # reliably) always captures them, even if stdout is buffered/filtered.
    def _diag(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    _diag(f"[serve] resolved host={host} port={port}")
    _diag("[serve] importing src.app:app ...")
    try:
        from src.app import app as _app  # probe import before uvicorn.run
        _diag(f"[serve] import OK: {_app}")
    except Exception as e:
        _diag(f"[serve] FATAL import error: {e!r}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise

    _diag(f"[serve] launching uvicorn on {host}:{port}")
    uvicorn.run(
        "src.app:app",
        host=host,
        port=port,
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


@cli.command("verify-config")
def verify_config_cmd() -> None:
    """Hit every configured ID against its API to confirm it really exists.

    Useful when something fails with a 404 and you suspect a key/workspace
    mismatch between local and prod. Exits 0 if everything resolves, 1 if
    anything is missing or unreachable.
    """
    import anthropic

    from src.services.agentmail_client import get_agentmail_client

    async def _run() -> bool:
        ok = True
        click.echo("=== Anthropic Managed Agents ===")
        if not settings.ANTHROPIC_API_KEY:
            click.echo("  [skip] ANTHROPIC_API_KEY not set")
            return False

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        async def check(label: str, coro) -> bool:
            try:
                res = await coro
                name = getattr(res, "name", None) or getattr(res, "id", "ok")
                click.echo(f"  ✅ {label}: {name}")
                return True
            except Exception as e:
                click.echo(f"  ❌ {label}: {type(e).__name__} — {e}")
                return False

        checks = [
            (
                "Environment",
                settings.ENVIRONMENT_ID,
                lambda _id: client.beta.environments.retrieve(environment_id=_id),
            ),
            (
                "Vault",
                settings.VAULT_ID,
                lambda _id: client.beta.vaults.retrieve(vault_id=_id),
            ),
            (
                "Composer agent",
                settings.COMPOSER_AGENT_ID,
                lambda _id: client.beta.agents.retrieve(agent_id=_id),
            ),
            (
                "Responder agent",
                settings.RESPONDER_AGENT_ID,
                lambda _id: client.beta.agents.retrieve(agent_id=_id),
            ),
            (
                "Scheduler agent",
                settings.SCHEDULER_AGENT_ID,
                lambda _id: client.beta.agents.retrieve(agent_id=_id),
            ),
            (
                "Assistant agent",
                settings.ASSISTANT_AGENT_ID,
                lambda _id: client.beta.agents.retrieve(agent_id=_id),
            ),
        ]
        for label, value, factory in checks:
            if not value:
                click.echo(f"  ⚠️  {label}: not configured (empty)")
                ok = False
                continue
            ok = (await check(f"{label} [{value}]", factory(value))) and ok

        click.echo("\n=== AgentMail ===")
        try:
            am = get_agentmail_client()
            resp = await am.raw.inboxes.list()
            found = False
            for inbox in getattr(resp, "inboxes", []) or []:
                email = getattr(inbox, "email", None) or getattr(inbox, "address", None)
                if email == settings.AGENTMAIL_INBOX_ADDRESS:
                    click.echo(f"  ✅ Inbox: {email} (id={inbox.inbox_id})")
                    if settings.AGENTMAIL_INBOX_ID != inbox.inbox_id:
                        click.echo(
                            f"  ⚠️  AGENTMAIL_INBOX_ID mismatch — .env has "
                            f"{settings.AGENTMAIL_INBOX_ID!r}, API returned "
                            f"{inbox.inbox_id!r}"
                        )
                        ok = False
                    found = True
                    break
            if not found:
                click.echo(
                    f"  ❌ Inbox {settings.AGENTMAIL_INBOX_ADDRESS} not found "
                    "under this API key"
                )
                ok = False
        except Exception as e:
            click.echo(f"  ❌ AgentMail list failed: {type(e).__name__} — {e}")
            ok = False

        return ok

    ok = asyncio.run(_run())
    click.echo("\nresult:", nl=False)
    if ok:
        click.echo(" ✅ all configured IDs resolve")
        sys.exit(0)
    else:
        click.echo(" ❌ problems found (see above)")
        sys.exit(1)


@cli.command("setup-assistant")
def setup_assistant_cmd() -> None:
    """Create (or reuse) the internal Assistant agent.

    Safe to run after the main `setup`. Writes ASSISTANT_AGENT_ID/VERSION
    to .env so the webhook handler can route authorized inbound queries.
    """
    from src.agents.registry import ensure_assistant_agent

    agent_id, version = asyncio.run(ensure_assistant_agent())
    click.echo("✅ Assistant agent ready.")
    click.echo(f"   ASSISTANT_AGENT_ID={agent_id}")
    click.echo(f"   ASSISTANT_AGENT_VERSION={version}")
    click.echo("\nCopy those to Railway Variables and redeploy.")


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
