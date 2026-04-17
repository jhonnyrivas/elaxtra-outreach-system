"""FastAPI app factory. Mounts webhook routes and wires startup/shutdown.

The HTTP server MUST always boot so that platform health checks (e.g. Railway
at /health) succeed. Features that depend on external state (agent IDs from
setup, database connectivity, company profile PDF, contacts Excel) are
evaluated lazily and degrade gracefully when unavailable.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from src.config import settings
from src.db.engine import dispose_engine
from src.utils.logging import configure_logging, get_logger
from src.webhooks.router import router as webhooks_router

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _batch_job_safe() -> None:
    try:
        from src.agents.orchestrator import run_outreach_batch

        await run_outreach_batch()
    except Exception:
        log.exception("scheduled_batch_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    log.info(
        "app_lifespan_starting",
        port=os.environ.get("PORT", "not set"),
        database_configured=bool(settings.DATABASE_URL),
        setup_complete=settings.setup_complete,
    )

    if not settings.setup_complete:
        log.warning(
            "setup_not_complete",
            hint="Run `python -m src.main setup` to create agents, vault, and environment. "
                 "The HTTP server will still serve /health; the scheduler is disabled until setup runs.",
        )
    else:
        global _scheduler
        try:
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
                "scheduler_started",
                cron_hours=settings.OUTREACH_CRON_HOUR,
                cron_days=settings.OUTREACH_CRON_DAY_OF_WEEK,
            )
        except Exception:
            log.exception("scheduler_start_failed")

    log.info(
        "system_started",
        setup_complete=settings.setup_complete,
        dry_run=settings.DRY_RUN,
    )

    try:
        yield
    finally:
        if _scheduler:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                log.exception("scheduler_shutdown_failed")
        try:
            await dispose_engine()
        except Exception:
            log.exception("engine_dispose_failed")
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
