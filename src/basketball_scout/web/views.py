"""Server-rendered frontend.

Deliberately Jinja + plain CSS + a few lines of vanilla JS. This UI exists so the
product is usable and integration-testable end to end; the final visual design is
a separate, interactive step (see ``artifacts/stitch_handoff/README.md``). Adding
React/Vite/npm for a temporary shell would cost a build pipeline and buy nothing.

Server rendering also gives the security story for free: the pages hold no admin
token, make no cross-origin calls, and cannot trigger generation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..persistence.repository import RepositoryError
from ..reports.contracts import PublicReport
from ..reports.service import UnknownTeamError
from .context import AppContext
from .errors import bad_request, not_found, unavailable
from .security import enforce_api_rate_limit, get_context

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["ui"], dependencies=[Depends(enforce_api_rate_limit)])


def _base(request: Request, ctx: AppContext) -> dict:
    return {
        "request": request,
        "season": ctx.pack_store.index.season if ctx.pack_store.available else "unknown",
        "app_version": ctx.app_version,
    }


@router.get("/", response_class=HTMLResponse, summary="Opponent selector")
def home(request: Request, ctx: AppContext = Depends(get_context)) -> HTMLResponse:
    if not ctx.pack_store.available:
        raise unavailable(
            "Deterministic evidence packs are not present in this deployment.",
            code="evidence_unavailable",
        )
    teams = ctx.service.list_teams()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            **_base(request, ctx),
            "teams": teams,
            "reports_n": sum(1 for t in teams if t.has_report),
        },
    )


@router.get("/teams/{team_id}", response_class=HTMLResponse, summary="Latest report for a team")
def team_report(request: Request, team_id: str, ctx: AppContext = Depends(get_context)) -> HTMLResponse:
    try:
        resolved = ctx.service.resolve_team_id(team_id)
    except UnknownTeamError:
        raise bad_request("Unknown team", code="unknown_team") from None

    teams = ctx.service.list_teams()
    selected = next((t for t in teams if t.team_id == resolved), None)

    report: PublicReport | None = None
    storage_error = False
    try:
        report = ctx.service.get_latest(resolved)
    except RepositoryError as exc:
        # An unreachable database should not blank the page; show the selector
        # and an honest banner instead.
        log.error("storage failure rendering %s: %s", resolved, exc)
        storage_error = True

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            **_base(request, ctx),
            "teams": teams,
            "selected": selected,
            "team_id": resolved,
            "report": report,
            "storage_error": storage_error,
        },
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse, summary="One saved report")
def report_permalink(
    request: Request, report_id: str, ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    from .api import _require_report_id

    _require_report_id(report_id)
    try:
        report = ctx.service.get_report(report_id)
    except RepositoryError as exc:
        log.error("storage failure rendering report %s: %s", report_id, exc)
        raise unavailable() from None
    if report is None:
        raise not_found("Report not found")

    teams = ctx.service.list_teams()
    return templates.TemplateResponse(
        request,
        "report.html",
        {
            **_base(request, ctx),
            "teams": teams,
            "selected": next((t for t in teams if t.team_id == report.team_id), None),
            "team_id": report.team_id,
            "report": report,
            "storage_error": False,
        },
    )
