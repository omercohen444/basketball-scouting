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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..analytics.schema import OUTCOMES, SEGMENTS
from ..analytics.views import (
    BASELINE_TOOLTIP,
    GAMES_COLUMNS,
    GAMES_DYNAMICS,
    METRIC_FAMILIES,
    OUTCOME_LABELS,
    QUARTER_SEGMENTS,
    SEGMENT_DEFINITIONS,
    SEGMENT_LABELS,
    SITUATION_SEGMENTS,
    SORTABLE,
    TEAM_TABS,
    baseline_label,
    consistency_view,
    dumbbell_bounds,
    explorer_columns,
    explorer_rows,
    factor_leaders,
    game_log,
    headline_metrics,
    largest_differences,
    league_game_rows,
    league_leaders,
    league_rows,
    normalise_games_filters,
    profile_ranks,
    quarter_bars,
    runs_view,
    scatter_mean_position,
    scatter_points,
    scoring_sources_view,
    segment_rows,
    shot_profile_view,
    split_rows,
    team_four_factors,
    transition_view,
    turnover_view,
)
from ..persistence.repository import RepositoryError
from ..reports.contracts import PublicReport
from ..reports.service import UnknownTeamError
from .context import AppContext
from .errors import bad_request, not_found, unavailable
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

def _template_raise(message: str):
    """Let a macro refuse to render something it must not draw.

    Used by the part-to-whole bar, which may only be handed a genuine
    partition. Overlapping scoring sources stacked into one bar would assert
    that they sum to the scoring, which is false — and a silently wrong chart
    is worse than a failed render, because nobody goes looking for it.
    """
    raise ValueError(message)


# Registered as globals rather than passed per-render: the crest lookup is
# needed in the nav, the league table, team headers and the compare selectors,
# and threading it through every context dict would guarantee it is forgotten
# somewhere and silently render nothing.
templates.env.globals["logo_url"] = logo_url
templates.env.globals["initials"] = initials
templates.env.globals["NAV"] = NAV
templates.env.globals["raise"] = _template_raise

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


GAME_LOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("offensive_rating", "ORtg"), ("defensive_rating", "DRtg"), ("net_rating", "Net"),
    ("pace", "Pace"), ("efg_pct", "eFG%"), ("tov_pct", "TOV%"),
    ("orb_pct", "ORB%"), ("ft_rate", "FTr"),
)

VALID_TABS = {key for key, _label in TEAM_TABS}


def _resolve(ctx: AppContext, team_id: str) -> str:
    try:
        return ctx.service.resolve_team_id(team_id)
    except UnknownTeamError:
        raise bad_request("Unknown team", code="unknown_team") from None


@router.get("/teams/{team_id}", response_class=HTMLResponse, summary="Team analytics")
def team_overview(
    request: Request, team_id: str, view: str = "", ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    # The report used to live at this URL. Anything still pointing here with the
    # old intent is redirected rather than silently shown something else.
    if view == "report":
        return RedirectResponse(f"/scouting/{team_id}", status_code=302)
    return _render_team(request, ctx, team_id, "overview")


@router.get("/teams/{team_id}/{tab}", response_class=HTMLResponse, summary="Team analytics tab")
def team_tab(
    request: Request, team_id: str, tab: str, ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    if tab not in VALID_TABS:
        raise not_found("No such view for this team")
    return _render_team(request, ctx, team_id, tab)


def _render_team(request: Request, ctx: AppContext, team_id: str, tab: str) -> HTMLResponse:
    resolved = _resolve(ctx, team_id)

    if not ctx.analytics.available or not ctx.analytics.has_team(resolved):
        raise unavailable(
            "Analytics artifacts are not present in this deployment.",
            code="analytics_unavailable",
        )

    team = ctx.analytics.team(resolved)
    context = {
        **_base(request, ctx, active="teams"),
        "team": team,
        "tab": tab,
        "tabs": TEAM_TABS,
        "headline": headline_metrics(team),
    }

    if tab == "overview":
        rows = split_rows(team)
        context.update(
            factors=team_four_factors(team),
            quarters=quarter_bars(team),
            top_differences=largest_differences(rows),
            bounds=dumbbell_bounds(rows),
            splits_unavailable=_splits_note(team),
            transition=transition_view(team, _profile_ranks(ctx, resolved)),
            consistency=consistency_view(team),
        )
    elif tab == "profile":
        ranks = _profile_ranks(ctx, resolved)
        context.update(
            shots=shot_profile_view(team),
            sources=scoring_sources_view(team, ranks),
            turnovers=turnover_view(team),
            runs=runs_view(team, ranks),
            transition=transition_view(team, ranks),
        )
    elif tab == "splits":
        rows = split_rows(team)
        wins = team.cell("full", "wins")
        losses = team.cell("full", "losses")
        context.update(
            rows=rows,
            bounds=dumbbell_bounds(rows),
            wins_n=team.wins,
            losses_n=team.losses,
            wins_usable=bool(wins and wins.sample_state != "insufficient"),
            losses_usable=bool(losses and losses.sample_state != "insufficient"),
            split_warning=_splits_note(team),
        )
    elif tab == "quarters":
        context.update(
            quarters=quarter_bars(team),
            rows=segment_rows(team, QUARTER_SEGMENTS),
        )
    elif tab == "situations":
        context.update(rows=segment_rows(team, SITUATION_SEGMENTS))
    elif tab == "games":
        context.update(games=game_log(team), game_columns=GAME_LOG_COLUMNS)

    return templates.TemplateResponse(request, "team.html", context)


def _profile_ranks(ctx: AppContext, team_id: str) -> dict:
    """One team's season profile ranks, which need the whole league in scope.

    Fourteen small cached objects and one pass over them, so this costs
    nothing worth caching separately — and computing it here rather than
    stamping it at build time means a rank can never disagree with the value
    printed beside it.
    """
    return profile_ranks(ctx.analytics.load_all()).get(team_id, {})


def _splits_note(team) -> str:
    """Why a wins-against-losses comparison is thin, in a sentence. Empty when
    both sides clear the bar and there is nothing to warn about."""
    for outcome, singular, plural in (("losses", "loss", "losses"), ("wins", "win", "wins")):
        cell = team.cell("full", outcome)
        if cell is None or cell.sample_state == "sufficient":
            continue
        counted = f"{cell.games} {singular if cell.games == 1 else plural}"
        qualifier = "Insufficient" if cell.sample_state == "insufficient" else "Limited"
        return f"{qualifier} sample — {counted}."
    return ""


@router.get("/explore", response_class=HTMLResponse, summary="Analytics explorer")
def explore(
    request: Request,
    segment: str = "full",
    outcome: str = "all",
    family: str = "efficiency",
    ctx: AppContext = Depends(get_context),
) -> HTMLResponse:
    if not ctx.analytics.available:
        raise unavailable(
            "Analytics artifacts are not present in this deployment.",
            code="analytics_unavailable",
        )

    # Every filter arrives from a URL a user can edit, so an unknown value falls
    # back to the default rather than raising.
    segment = segment if segment in SEGMENTS else "full"
    outcome = outcome if outcome in OUTCOMES else "all"
    family = family if family in METRIC_FAMILIES else "efficiency"

    teams = ctx.analytics.load_all()
    return templates.TemplateResponse(
        request,
        "explore.html",
        {
            **_base(request, ctx, active="explore"),
            "rows": explorer_rows(teams, segment=segment, outcome=outcome, family=family),
            "columns": explorer_columns(family),
            "segment": segment, "outcome": outcome, "family": family,
            "segment_label": SEGMENT_LABELS.get(segment, segment),
            "segment_definition": SEGMENT_DEFINITIONS.get(segment, ""),
            "outcome_label": OUTCOME_LABELS.get(outcome, outcome),
            "segments": [(k, SEGMENT_LABELS[k]) for k in SEGMENTS],
            "outcomes": [(k, OUTCOME_LABELS[k]) for k in OUTCOMES],
            "families": [(k, v[0]) for k, v in METRIC_FAMILIES.items()],
            # The baseline is always the same-outcome full-game value, so the
            # header names which outcome that is rather than always saying
            # "season" while the Losses filter is on.
            "baseline_label": baseline_label(outcome),
            "baseline_tooltip": BASELINE_TOOLTIP,
        },
    )


@router.get("/games", response_class=HTMLResponse, summary="League game log")
def games(
    request: Request,
    sort: str = "date",
    team: str = "",
    venue: str = "",
    result: str = "",
    ctx: AppContext = Depends(get_context),
) -> HTMLResponse:
    if not ctx.analytics.available:
        raise unavailable(
            "Analytics artifacts are not present in this deployment.",
            code="analytics_unavailable",
        )

    teams = ctx.analytics.load_all()
    filters = normalise_games_filters(teams, sort, team, venue, result)
    rows = league_game_rows(
        teams, sort=filters.sort, team=filters.team,
        venue=filters.venue, result=filters.result,
    )
    return templates.TemplateResponse(
        request,
        "games.html",
        {
            **_base(request, ctx, active="games"),
            "rows": rows,
            "total": sum(len(t.games) for t in teams.values()),
            "filters": filters,
            "columns": GAMES_COLUMNS,
            "dynamics_columns": GAMES_DYNAMICS,
            "team_options": sorted(
                ((tid, t.team_name) for tid, t in teams.items()), key=lambda kv: kv[1]
            ),
        },
    )


@router.get("/scouting/{team_id}", response_class=HTMLResponse, summary="AI scouting report")
def scouting_report(
    request: Request, team_id: str, ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    resolved = _resolve(ctx, team_id)
    teams = ctx.service.list_teams()
    selected = next((t for t in teams if t.team_id == resolved), None)

    report: PublicReport | None = None
    storage_error = False
    try:
        report = ctx.service.get_latest(resolved)
    except RepositoryError as exc:
        # An unreachable database should not blank the page; show an honest
        # banner instead.
        log.error("storage failure rendering %s: %s", resolved, exc)
        storage_error = True

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            **_base(request, ctx, active="scouting"),
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
