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
from ..analytics.views import (
    SORTABLE,
    factor_leaders,
    league_leaders,
    league_rows,
    scatter_mean_position,
    scatter_points,
)
from .logos import initials, logo_url
from .security import enforce_api_rate_limit, get_context

log = logging.getLogger(__name__)

# The global surfaces, in the order they appear in the top bar. Held here so
# the nav, the tests and the no-provider route list all read one definition.
NAV: tuple[tuple[str, str], ...] = (
    ("league", "League"),
    ("teams", "Teams"),
    ("explore", "Explorer"),
    ("games", "Games"),
    ("compare", "Compare"),
    ("scouting", "Scouting"),
    ("methodology", "Methodology"),
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Registered as globals rather than passed per-render: the crest lookup is
# needed in the nav, the league table, team headers and the compare selectors,
# and threading it through every context dict would guarantee it is forgotten
# somewhere and silently render nothing.
templates.env.globals["logo_url"] = logo_url
templates.env.globals["initials"] = initials
templates.env.globals["NAV"] = NAV

router = APIRouter(tags=["ui"], dependencies=[Depends(enforce_api_rate_limit)])


def _base(request: Request, ctx: AppContext, active: str = "") -> dict:
    return {
        "request": request,
        "season": ctx.pack_store.index.season if ctx.pack_store.available else "unknown",
        "app_version": ctx.app_version,
        "active": active,
        "analytics_available": ctx.analytics.available,
    }


LEAGUE_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("offensive_rating", "ORtg"), ("defensive_rating", "DRtg"), ("net_rating", "Net"),
    ("pace", "Pace"), ("efg_pct", "eFG%"), ("tov_pct", "TOV%"), ("orb_pct", "ORB%"),
    ("ft_rate", "FTr"), ("opp_efg_pct", "oeFG%"), ("opp_tov_pct", "oTOV%"), ("drb_pct", "DRB%"),
)


@router.get("/", response_class=HTMLResponse, summary="League overview")
def league(
    request: Request, sort: str = "net_rating", ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    if not ctx.pack_store.available:
        raise unavailable(
            "Deterministic evidence packs are not present in this deployment.",
            code="evidence_unavailable",
        )

    context = {**_base(request, ctx, active="league"), "rows": [], "leaders": [],
               "points": [], "mean": {}, "factor_leaders": [], "games_n": 0,
               "columns": LEAGUE_TABLE_COLUMNS, "sort": sort}

    if ctx.analytics.available:
        teams = ctx.analytics.load_all()
        # An unknown sort key falls back rather than 500ing — the sort arrives
        # from a URL a user can edit.
        sort_key = sort if sort in SORTABLE else "net_rating"
        rows = league_rows(teams, sort=sort_key)
        points = scatter_points(rows)
        context.update(
            rows=rows,
            leaders=league_leaders(rows),
            points=points,
            mean=scatter_mean_position(rows, points),
            factor_leaders=factor_leaders(rows),
            games_n=sum(t.games_n for t in teams.values()),
            sort=sort_key,
        )

    return templates.TemplateResponse(request, "league.html", context)


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
