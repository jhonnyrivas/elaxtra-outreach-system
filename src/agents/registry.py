"""One-time setup: creates environment, vault, and agents. Persists IDs to .env.

Call `initial_setup()` once via `python -m src.main setup`. It:
1. Creates a Managed Agents environment (cloud, unrestricted networking)
2. Creates a vault for MCP credentials
3. Creates the composer, responder, and scheduler agents
4. Uploads the company profile PDF to the Files API
5. Creates the AgentMail inbox if it doesn't exist
6. Writes all returned IDs back to .env so subsequent runs can load them

`update_agents()` republishes the current agent configs as new versions — use
after editing a system prompt or tool set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anthropic

from src.agents.assistant import build_assistant_agent_config
from src.agents.composer import build_composer_agent_config
from src.agents.responder import build_responder_agent_config
from src.agents.scheduler_agent import build_scheduler_agent_config
from src.config import settings
from src.services.agentmail_client import get_agentmail_client
from src.services.file_upload import upload_company_profile
from src.utils.logging import get_logger

log = get_logger(__name__)

ENV_FILE = Path(".env")
ENVIRONMENT_NAME = "elaxtra-outreach"
VAULT_NAME = "elaxtra-mcp-credentials"


@dataclass
class SetupResult:
    environment_id: str
    vault_id: str
    composer_agent_id: str
    composer_agent_version: str
    responder_agent_id: str
    responder_agent_version: str
    scheduler_agent_id: str
    scheduler_agent_version: str
    assistant_agent_id: str
    assistant_agent_version: str
    company_profile_file_id: str
    agentmail_inbox_id: str


# --------- .env writer (single source of truth for persisted IDs) ---------


def _read_env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    data: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def _write_env_updates(updates: dict[str, str]) -> None:
    """Preserve existing lines (including comments) and update/append keys."""
    existing_lines: list[str] = (
        ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    )
    seen: set[str] = set()
    out: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n")


# --------- Managed Agents bootstrap ---------


async def _ensure_environment(client: anthropic.AsyncAnthropic) -> str:
    """Return existing env ID from settings, or create a new one."""
    if settings.ENVIRONMENT_ID:
        try:
            env = await client.beta.environments.retrieve(
                environment_id=settings.ENVIRONMENT_ID
            )
            log.info("environment_reused", environment_id=env.id)
            return env.id
        except Exception as e:
            log.warning(
                "environment_retrieve_failed_creating_new",
                environment_id=settings.ENVIRONMENT_ID,
                error=str(e),
            )

    env = await client.beta.environments.create(
        name=ENVIRONMENT_NAME,
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    log.info("environment_created", environment_id=env.id)
    return env.id


async def _ensure_vault(client: anthropic.AsyncAnthropic) -> str:
    if settings.VAULT_ID:
        try:
            vault = await client.beta.vaults.retrieve(vault_id=settings.VAULT_ID)
            log.info("vault_reused", vault_id=vault.id)
            return vault.id
        except Exception as e:
            log.warning(
                "vault_retrieve_failed_creating_new",
                vault_id=settings.VAULT_ID,
                error=str(e),
            )

    vault = await client.beta.vaults.create(
        display_name=VAULT_NAME,
        metadata={"purpose": "MCP credentials for Elaxtra outreach agents"},
    )
    log.info("vault_created", vault_id=vault.id)
    return vault.id


async def _create_agent(
    client: anthropic.AsyncAnthropic, config: dict
) -> tuple[str, str]:
    """Create a new agent. Returns (id, version)."""
    agent = await client.beta.agents.create(**config)
    log.info("agent_created", name=config["name"], agent_id=agent.id, version=agent.version)
    return agent.id, str(agent.version)


async def _update_agent(
    client: anthropic.AsyncAnthropic, agent_id: str, config: dict
) -> tuple[str, str]:
    """Update an existing agent (bumps the version).

    The SDK's agents.update requires the current `version` to be passed
    explicitly. We fetch the agent first to learn its current version,
    then send the update.
    """
    current = await client.beta.agents.retrieve(agent_id=agent_id)
    agent = await client.beta.agents.update(
        agent_id=agent_id,
        version=int(current.version),
        **config,
    )
    log.info(
        "agent_updated",
        name=config["name"],
        agent_id=agent.id,
        version=agent.version,
    )
    return agent.id, str(agent.version)


# --------- Public API ---------


async def initial_setup() -> SetupResult:
    """First-time bootstrap. Safe to re-run — reuses existing IDs from .env."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    environment_id = await _ensure_environment(client)
    vault_id = await _ensure_vault(client)

    # Agents — create if missing, otherwise keep existing IDs (use update-agents to bump version)
    composer_id, composer_version = await _ensure_agent(
        client,
        existing_id=settings.COMPOSER_AGENT_ID,
        config=build_composer_agent_config(),
    )
    responder_id, responder_version = await _ensure_agent(
        client,
        existing_id=settings.RESPONDER_AGENT_ID,
        config=build_responder_agent_config(),
    )
    scheduler_id, scheduler_version = await _ensure_agent(
        client,
        existing_id=settings.SCHEDULER_AGENT_ID,
        config=build_scheduler_agent_config(),
    )
    assistant_id, assistant_version = await _ensure_agent(
        client,
        existing_id=settings.ASSISTANT_AGENT_ID,
        config=build_assistant_agent_config(),
    )

    # Upload company profile PDF (skip if already uploaded and file still exists)
    if settings.COMPANY_PROFILE_FILE_ID:
        file_id = settings.COMPANY_PROFILE_FILE_ID
        log.info("company_profile_reused", file_id=file_id)
    else:
        file_id = await upload_company_profile(client, settings.COMPANY_PROFILE_PDF_PATH)

    # AgentMail inbox
    am = get_agentmail_client()
    inbox = await am.get_or_create_inbox(settings.AGENTMAIL_INBOX_ADDRESS)
    inbox_id = inbox["id"]

    # Persist all IDs to .env
    _write_env_updates(
        {
            "ENVIRONMENT_ID": environment_id,
            "VAULT_ID": vault_id,
            "COMPOSER_AGENT_ID": composer_id,
            "COMPOSER_AGENT_VERSION": composer_version,
            "RESPONDER_AGENT_ID": responder_id,
            "RESPONDER_AGENT_VERSION": responder_version,
            "SCHEDULER_AGENT_ID": scheduler_id,
            "SCHEDULER_AGENT_VERSION": scheduler_version,
            "ASSISTANT_AGENT_ID": assistant_id,
            "ASSISTANT_AGENT_VERSION": assistant_version,
            "COMPANY_PROFILE_FILE_ID": file_id,
            "AGENTMAIL_INBOX_ID": inbox_id,
        }
    )
    log.info("setup_complete", environment_id=environment_id)

    return SetupResult(
        environment_id=environment_id,
        vault_id=vault_id,
        composer_agent_id=composer_id,
        composer_agent_version=composer_version,
        responder_agent_id=responder_id,
        responder_agent_version=responder_version,
        scheduler_agent_id=scheduler_id,
        scheduler_agent_version=scheduler_version,
        assistant_agent_id=assistant_id,
        assistant_agent_version=assistant_version,
        company_profile_file_id=file_id,
        agentmail_inbox_id=inbox_id,
    )


async def _ensure_agent(
    client: anthropic.AsyncAnthropic, existing_id: str, config: dict
) -> tuple[str, str]:
    if existing_id:
        try:
            agent = await client.beta.agents.retrieve(agent_id=existing_id)
            log.info("agent_reused", name=config["name"], agent_id=agent.id)
            return agent.id, str(agent.version)
        except Exception as e:
            log.warning(
                "agent_retrieve_failed_creating_new",
                agent_id=existing_id,
                error=str(e),
            )
    return await _create_agent(client, config)


async def update_agents() -> SetupResult:
    """Push current configs as new versions. Requires agents to exist."""
    if not (
        settings.COMPOSER_AGENT_ID
        and settings.RESPONDER_AGENT_ID
        and settings.SCHEDULER_AGENT_ID
    ):
        raise RuntimeError("Agents not yet created — run `setup` first.")

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    composer_id, composer_version = await _update_agent(
        client, settings.COMPOSER_AGENT_ID, build_composer_agent_config()
    )
    responder_id, responder_version = await _update_agent(
        client, settings.RESPONDER_AGENT_ID, build_responder_agent_config()
    )
    scheduler_id, scheduler_version = await _update_agent(
        client, settings.SCHEDULER_AGENT_ID, build_scheduler_agent_config()
    )
    # Assistant is optional historically — only update if it's been created.
    if settings.ASSISTANT_AGENT_ID:
        assistant_id, assistant_version = await _update_agent(
            client, settings.ASSISTANT_AGENT_ID, build_assistant_agent_config()
        )
    else:
        assistant_id, assistant_version = "", ""

    env_updates = {
        "COMPOSER_AGENT_VERSION": composer_version,
        "RESPONDER_AGENT_VERSION": responder_version,
        "SCHEDULER_AGENT_VERSION": scheduler_version,
    }
    if assistant_version:
        env_updates["ASSISTANT_AGENT_VERSION"] = assistant_version
    _write_env_updates(env_updates)

    return SetupResult(
        environment_id=settings.ENVIRONMENT_ID,
        vault_id=settings.VAULT_ID,
        composer_agent_id=composer_id,
        composer_agent_version=composer_version,
        responder_agent_id=responder_id,
        responder_agent_version=responder_version,
        scheduler_agent_id=scheduler_id,
        scheduler_agent_version=scheduler_version,
        assistant_agent_id=assistant_id,
        assistant_agent_version=assistant_version,
        company_profile_file_id=settings.COMPANY_PROFILE_FILE_ID,
        agentmail_inbox_id=settings.AGENTMAIL_INBOX_ID,
    )


async def ensure_assistant_agent() -> tuple[str, str]:
    """Create the Assistant agent (or reuse if already set in .env).

    Used when setup was run before the Assistant existed. Safe to call
    repeatedly — reuses if ASSISTANT_AGENT_ID is already persisted.
    """
    if not settings.ENVIRONMENT_ID:
        raise RuntimeError("Run `setup` first — environment is required.")
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    assistant_id, assistant_version = await _ensure_agent(
        client,
        existing_id=settings.ASSISTANT_AGENT_ID,
        config=build_assistant_agent_config(),
    )
    _write_env_updates(
        {
            "ASSISTANT_AGENT_ID": assistant_id,
            "ASSISTANT_AGENT_VERSION": assistant_version,
        }
    )
    return assistant_id, assistant_version


async def add_mcp_credential(
    *,
    server_name: str,
    mcp_server_url: str,
    access_token: str,
    refresh_token: str | None = None,
    token_endpoint: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Add an MCP OAuth credential to the vault.

    If refresh_token + token_endpoint are provided, Anthropic auto-refreshes
    the access token before it expires. Otherwise the token is used as-is
    until it expires, at which point the agent loses access to that server.

    client_secret is only needed for confidential OAuth clients. Public
    clients (most MCP integrations) pass token_endpoint_auth.type = "none".
    """
    if not settings.VAULT_ID:
        raise RuntimeError("Vault not created — run `setup` first.")

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    auth: dict = {
        "type": "mcp_oauth",
        "mcp_server_url": mcp_server_url,
        "access_token": access_token,
    }
    if refresh_token and token_endpoint:
        refresh: dict = {
            "refresh_token": refresh_token,
            "token_endpoint": token_endpoint,
        }
        if client_id:
            refresh["client_id"] = client_id
        if client_secret:
            refresh["token_endpoint_auth"] = {
                "type": "client_secret_post",
                "client_secret": client_secret,
            }
        else:
            refresh["token_endpoint_auth"] = {"type": "none"}
        auth["refresh"] = refresh

    credential = await client.beta.vaults.credentials.create(
        vault_id=settings.VAULT_ID,
        display_name=f"{server_name} ({mcp_server_url})",
        auth=auth,
    )
    log.info(
        "credential_added",
        vault_id=settings.VAULT_ID,
        credential_id=credential.id,
        server=server_name,
    )
    return credential.id
