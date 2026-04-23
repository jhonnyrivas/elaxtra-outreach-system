"""Dashboard routes (HTML + JSON) for the Elaxtra outreach system."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.web.auth import require_dashboard_auth
from src.web.data import (
    get_activity_feed,
    get_agents_overview,
    get_mcp_servers_overview,
    get_overview,
    list_contacts,
)

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()


def _base_ctx(active: str, extra: dict | None = None) -> dict:
    """Variables required by base.html for every page.

    `request` is passed separately to TemplateResponse (Starlette 1.0+ API),
    not in the context dict.
    """
    from src.config import settings

    ctx = {
        "active": active,
        "setup_complete": settings.setup_complete,
        "inbox_address": settings.AGENTMAIL_INBOX_ADDRESS,
        "now_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    if extra:
        ctx.update(extra)
    return ctx


# --- Root redirect --- #


@router.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=307)


# --- Dashboard pages --- #


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_overview(
    request: Request, _: str = Depends(require_dashboard_auth)
) -> HTMLResponse:
    overview = await get_overview()
    return templates.TemplateResponse(
        request,
        "overview.html",
        _base_ctx("overview", {"overview": overview}),
    )


@router.get("/dashboard/agents", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_agents(
    request: Request, _: str = Depends(require_dashboard_auth)
) -> HTMLResponse:
    agents = await get_agents_overview()
    return templates.TemplateResponse(
        request,
        "agents.html",
        _base_ctx("agents", {"agents": agents}),
    )


@router.get("/dashboard/mcp", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_mcp(
    request: Request, _: str = Depends(require_dashboard_auth)
) -> HTMLResponse:
    servers = await get_mcp_servers_overview()
    return templates.TemplateResponse(
        request,
        "mcp.html",
        _base_ctx("mcp", {"servers": servers}),
    )


@router.get(
    "/dashboard/activity", response_class=HTMLResponse, include_in_schema=False
)
async def dashboard_activity(
    request: Request, _: str = Depends(require_dashboard_auth)
) -> HTMLResponse:
    activity = await get_activity_feed(limit=50)
    return templates.TemplateResponse(
        request,
        "activity.html",
        _base_ctx("activity", {"activity": activity}),
    )


@router.get(
    "/dashboard/activity/partial",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def dashboard_activity_partial(
    request: Request, _: str = Depends(require_dashboard_auth)
) -> HTMLResponse:
    activity = await get_activity_feed(limit=50)
    return templates.TemplateResponse(
        request, "_activity_list.html", {"activity": activity}
    )


@router.get(
    "/dashboard/contacts", response_class=HTMLResponse, include_in_schema=False
)
async def dashboard_contacts(
    request: Request,
    search: str | None = Query(default=None),
    _: str = Depends(require_dashboard_auth),
) -> HTMLResponse:
    contacts = await list_contacts(limit=50, offset=0, search=search)
    return templates.TemplateResponse(
        request,
        "contacts.html",
        _base_ctx("contacts", {"contacts": contacts, "search": search}),
    )


@router.get(
    "/dashboard/contacts/partial",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def dashboard_contacts_partial(
    request: Request,
    search: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    append: int = Query(default=0),
    _: str = Depends(require_dashboard_auth),
) -> HTMLResponse:
    contacts = await list_contacts(limit=50, offset=offset, search=search)
    # `append=1` is sent by the "Load more" button — we only want the inner
    # table rows appended, not the full table scaffold. Easiest path: render
    # the same partial; HTMX with hx-swap="beforeend" will append the whole
    # _contacts_table block. That's fine for the demo; we can split later.
    return templates.TemplateResponse(
        request, "_contacts_table.html", {"contacts": contacts, "search": search}
    )


# --- JSON API (same data, programmatic consumers) --- #

api_router = APIRouter(prefix="/api")


@api_router.get("/stats")
async def api_stats(_: str = Depends(require_dashboard_auth)) -> dict:
    from src.services.system_stats import build_system_snapshot

    return await build_system_snapshot()


@api_router.get("/agents")
async def api_agents(_: str = Depends(require_dashboard_auth)) -> list[dict]:
    return await get_agents_overview()


@api_router.get("/mcp-servers")
async def api_mcp_servers(_: str = Depends(require_dashboard_auth)) -> list[dict]:
    return await get_mcp_servers_overview()


@api_router.get("/activity")
async def api_activity(
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(require_dashboard_auth),
) -> list[dict]:
    return await get_activity_feed(limit=limit)


@api_router.get("/contacts")
async def api_contacts(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(require_dashboard_auth),
) -> dict:
    return await list_contacts(limit=limit, offset=offset, search=search)
