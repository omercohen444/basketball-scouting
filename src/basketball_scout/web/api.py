"""JSON API.

Route inventory (the whole public surface):

===============================================  ======  ==============================
``GET  /api/teams``                              public  the 14 supported opponents
``GET  /api/reports/latest/{team_id}``           public  latest saved report for a team
``GET  /api/reports/{report_id}``                public  one saved report
``GET  /api/reports/{report_id}/pdf``            public  PDF of a saved report
``POST /api/admin/reports/generate``             admin   generate + validate + save
===============================================  ======  ==============================

Two invariants worth stating explicitly, because they are the product's cost and
safety model:

* **No public route calls Gemini.** Reads hit storage; the PDF is rendered from a
  stored report. Only the admin route can spend a provider call.
* **There is no free-text endpoint.** The only caller-supplied values are a
  ``team_id`` from a fixed 14-entry allowlist and a boolean. Nothing a visitor
  types can become part of a prompt.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..persistence.repository import RepositoryError, SchemaMissingError
from ..reports.contracts import PublicReport, TeamSummary
from ..reports.pdf import build_report_pdf, pdf_filename
from ..reports.service import GenerationUnavailableError, UnknownTeamError
from .context import AppContext
from .errors import bad_request, not_found, unavailable
from .security import (
    enforce_admin_rate_limit,
    enforce_api_rate_limit,
    get_context,
    require_admin,
)

log = logging.getLogger(__name__)

# Report ids are UUIDs we mint. A shape gate here means a malformed id never
# reaches storage as a query parameter.
REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")

router = APIRouter(prefix="/api", tags=["api"])


# ---- response contracts -----------------------------------------------------


class TeamsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str
    teams_n: int
    teams: list[TeamSummary]


class GenerateRequest(BaseModel):
    """The complete admin generation input.

    ``extra="forbid"`` is load-bearing: it is what makes "no arbitrary free-text
    reaches a prompt" checkable rather than merely intended.
    """

    model_config = ConfigDict(extra="forbid")

    team_id: str = Field(min_length=1, max_length=40)
    force_regenerate: bool = False


class GenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    team_id: str
    report_id: str | None = None
    persisted: bool = False
    backend: str = "unknown"
    model_name: str | None = None
    provider_calls: int = 0
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    transient_retries: int = 0
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    rejects: list[str] = Field(default_factory=list)
    detail: str | None = None
    report: PublicReport | None = None


# ---- helpers ----------------------------------------------------------------


def _storage_error(exc: RepositoryError) -> Exception:
    """Map a storage failure to a public 503 while logging the real cause."""
    if isinstance(exc, SchemaMissingError):
        log.error("supabase schema is not applied: %s", exc)
        return unavailable(
            "Report storage is not initialised on this deployment yet.",
            code="storage_not_initialised",
        )
    log.error("storage failure: %s", exc)
    return unavailable()


def _require_report_id(report_id: str) -> str:
    if not REPORT_ID_PATTERN.match(report_id or ""):
        # 404 rather than 400: a malformed id and an unknown id are the same
        # answer to a caller, and that reveals less.
        raise not_found("Report not found")
    return report_id


# ---- public routes ----------------------------------------------------------


@router.get(
    "/teams",
    response_model=TeamsResponse,
    summary="List the supported opponents",
    dependencies=[Depends(enforce_api_rate_limit)],
)
def list_teams(ctx: AppContext = Depends(get_context)) -> TeamsResponse:
    if not ctx.pack_store.available:
        raise unavailable(
            "Deterministic evidence packs are not present in this deployment.",
            code="evidence_unavailable",
        )
    teams = ctx.service.list_teams()
    return TeamsResponse(
        season=ctx.pack_store.index.season,
        teams_n=len(teams),
        teams=teams,
    )


@router.get(
    "/reports/latest/{team_id}",
    response_model=PublicReport,
    summary="Latest saved report for one team",
    dependencies=[Depends(enforce_api_rate_limit)],
)
def latest_report(team_id: str, ctx: AppContext = Depends(get_context)) -> PublicReport:
    try:
        report = ctx.service.get_latest(team_id)
    except UnknownTeamError:
        raise bad_request("Unknown team id", code="unknown_team") from None
    except RepositoryError as exc:
        raise _storage_error(exc) from None
    if report is None:
        raise not_found("No saved report exists for this team yet")
    return report


@router.get(
    "/reports/{report_id}",
    response_model=PublicReport,
    summary="One saved report by id",
    dependencies=[Depends(enforce_api_rate_limit)],
)
def get_report(report_id: str, ctx: AppContext = Depends(get_context)) -> PublicReport:
    _require_report_id(report_id)
    try:
        report = ctx.service.get_report(report_id)
    except RepositoryError as exc:
        raise _storage_error(exc) from None
    if report is None:
        raise not_found("Report not found")
    return report


@router.get(
    "/reports/{report_id}/pdf",
    summary="PDF of a saved report",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "PDF document"}},
    dependencies=[Depends(enforce_api_rate_limit)],
)
def get_report_pdf(report_id: str, ctx: AppContext = Depends(get_context)) -> Response:
    """Rendered from the stored report. Never calls the provider."""
    _require_report_id(report_id)
    try:
        report = ctx.service.get_report(report_id)
    except RepositoryError as exc:
        raise _storage_error(exc) from None
    if report is None:
        raise not_found("Report not found")

    pdf_bytes = build_report_pdf(report)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename(report)}"',
            "Cache-Control": "public, max-age=300",
        },
    )


# ---- admin route ------------------------------------------------------------


@router.post(
    "/admin/reports/generate",
    response_model=GenerateResponse,
    summary="Generate, validate and save a report (admin only)",
    dependencies=[Depends(enforce_admin_rate_limit)],
)
def generate_report(
    payload: GenerateRequest,
    response: Response,
    ctx: AppContext = Depends(require_admin),
) -> GenerateResponse:
    """The only endpoint that can spend a provider call.

    Declared ``def`` (not ``async def``) on purpose: FastAPI runs it in the
    threadpool, so a generation that takes a minute does not block the event loop
    for everybody reading saved reports.
    """
    try:
        team_id = ctx.service.resolve_team_id(payload.team_id)
    except UnknownTeamError:
        raise bad_request("Unknown team id", code="unknown_team") from None

    if not ctx.pack_store.available:
        raise unavailable(
            "Deterministic evidence packs are not present in this deployment.",
            code="evidence_unavailable",
        )

    try:
        outcome = ctx.service.generate(team_id, force_regenerate=payload.force_regenerate)
    except GenerationUnavailableError as exc:
        log.error("generation backend unavailable: %s", exc)
        raise unavailable(
            "The report generation backend is not available on this instance.",
            code="generation_unavailable",
        ) from None
    except RepositoryError as exc:
        raise _storage_error(exc) from None

    body = GenerateResponse(
        status=outcome.status,
        team_id=outcome.team_id,
        report_id=outcome.stored_report_id,
        persisted=outcome.persisted,
        backend=outcome.backend,
        model_name=outcome.model_name,
        provider_calls=outcome.provider_calls,
        stage_attempts=outcome.stage_attempts,
        transient_retries=outcome.transient_retries,
        duration_ms=outcome.duration_ms,
        warnings=outcome.warnings,
        rejects=outcome.rejects,
        detail=outcome.error,
        report=outcome.report,
    )

    if outcome.status == "rejected":
        # The pipeline produced something that failed deterministic validation.
        # Nothing was saved. 422 says "the request was fine, the result was not".
        response.status_code = 422  # UNPROCESSABLE_CONTENT
    elif outcome.status == "error":
        response.status_code = status.HTTP_502_BAD_GATEWAY

    return body
