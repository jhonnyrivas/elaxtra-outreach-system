"""FastAPI app factory. Mounts webhook routes and wires startup/shutdown."""
from __future__ import annotations

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from src.agents.orchestrator import run_outreach_batch
from src.config import settings
from src.db.engine import dispose_engine
from src.utils.logging import configure_logging, get_logger
from src.webhooks.router import router as webhooks_router

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _batch_job_safe() -> None:
    try:
        await run_outreach_batch()
    except Exception:
        log.exception("scheduled_batch_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if not settings.setup_complete:
        log.warning(
            "setup_not_complete",
            hint="Run `python -m src.main setup` to create agents, vault, and environment.",
        )

    # Start scheduler
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=settings.OUTREACH_TIMEZONE)
    _scheduler.add_job(
        _batch_job_safe,
        trigger=CronTrigger(
            hour=settings.OUTREACH_CRON_HOUR,
            day_of_week=settings.OUTREACH_CRON_DAY_OF_WEEK,
            timezone=settings.OUTREACH_TIMEZONE,
        ),
        id="outreach_batch",
        name="Outreach batch",
        replace_existing=True,
    )
    _scheduler.start()
    log.info(
        "system_started",
        webhook_port=settings.WEBHOOK_PORT,
        cron_hours=settings.OUTREACH_CRON_HOUR,
        cron_days=settings.OUTREACH_CRON_DAY_OF_WEEK,
        dry_run=settings.DRY_RUN,
    )

    try:
        yield
    finally:
        if _scheduler:
            _scheduler.shutdown(wait=False)
        await dispose_engine()
        log.info("system_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Elaxtra Outreach System",
        version="0.1.0",
        description="Autonomous B2B outreach for Elaxtra Advisors",
        lifespan=lifespan,
    )
    app.include_router(webhooks_router)
    return app


app = create_app()
