"""Application configuration. Single source of truth for all env-driven settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Anthropic — empty default so the app can still boot on first deploy.
    # Features that need Anthropic will fail at call-time, not at import-time.
    ANTHROPIC_API_KEY: str = ""

    # AgentMail — same rationale as ANTHROPIC_API_KEY.
    AGENTMAIL_API_KEY: str = ""
    AGENTMAIL_INBOX_ADDRESS: str = "elaxtra@agentmail.to"
    AGENTMAIL_WEBHOOK_SECRET: str = ""

    # MCP server URLs (declared on agents; auth goes in vaults)
    MCP_HUBSPOT_URL: str = "https://mcp.hubspot.com/anthropic"
    MCP_APOLLO_URL: str = "https://mcp.apollo.io/mcp"
    MCP_APIFY_URL: str = "https://mcp.apify.com"
    MCP_MS365_URL: str = "https://microsoft365.mcp.claude.com/mcp"
    MCP_AGENTMAIL_URL: str = "https://mcp.agentmail.to"

    HUBSPOT_PORTAL_ID: str = "45774781"

    # Email policy
    BCC_EMAIL: str = "andrew.burgert@elaxtra.com"
    SIGNER_NAME: str = "Andrew Burgert"
    SIGNER_TITLE: str = "Partner, Elaxtra Advisors"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://localhost/outreach"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        """Railway provides postgresql:// but asyncpg needs postgresql+asyncpg://.

        Also tolerates the legacy `postgres://` scheme (Heroku-style).
        Empty strings fall back to the default (so the app can still boot).
        """
        if not v:
            return "postgresql+asyncpg://localhost/outreach"
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    # Rate limiting
    MAX_EMAILS_PER_DAY: int = 25
    MAX_EMAILS_PER_HOUR: int = 5
    MIN_DELAY_BETWEEN_EMAILS_SECONDS: int = 120
    OUTREACH_BATCH_SIZE: int = 10

    # Concurrency
    MAX_CONCURRENT_AGENT_SESSIONS: int = 5

    # Scheduling
    OUTREACH_CRON_HOUR: str = "9,13,17"
    OUTREACH_CRON_DAY_OF_WEEK: str = "mon-fri"
    OUTREACH_TIMEZONE: str = "America/New_York"

    # Models
    COMPOSER_MODEL: str = "claude-sonnet-4-6"
    RESPONDER_MODEL: str = "claude-sonnet-4-6"
    SCHEDULER_MODEL: str = "claude-sonnet-4-6"

    # File paths
    CONTACTS_EXCEL_PATH: Path = Path("./data/contacts.xlsx")
    COMPANY_PROFILE_PDF_PATH: Path = Path("./docs/elaxtra_company_profile.pdf")

    # Persisted Managed Agents IDs (populated by setup CLI)
    COMPOSER_AGENT_ID: str = ""
    COMPOSER_AGENT_VERSION: str = ""
    RESPONDER_AGENT_ID: str = ""
    RESPONDER_AGENT_VERSION: str = ""
    SCHEDULER_AGENT_ID: str = ""
    SCHEDULER_AGENT_VERSION: str = ""
    ENVIRONMENT_ID: str = ""
    VAULT_ID: str = ""
    COMPANY_PROFILE_FILE_ID: str = ""
    AGENTMAIL_INBOX_ID: str = ""

    # Server
    WEBHOOK_HOST: str = "0.0.0.0"
    WEBHOOK_PORT: int = 8000
    PUBLIC_WEBHOOK_URL: str = ""

    # Mode
    DRY_RUN: bool = False
    LOG_LEVEL: str = "INFO"

    @property
    def setup_complete(self) -> bool:
        """True when the one-time setup has populated all required IDs."""
        return bool(
            self.COMPOSER_AGENT_ID
            and self.RESPONDER_AGENT_ID
            and self.SCHEDULER_AGENT_ID
            and self.ENVIRONMENT_ID
            and self.AGENTMAIL_INBOX_ID
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
