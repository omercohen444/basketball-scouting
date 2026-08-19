"""Shared builders for the product-layer tests (settings, service, app, client).

Same rationale as ``agents_factories``: several modules need an app wired to an
in-memory repository and a synthetic pack store, and duplicating that wiring
would guarantee drift. Pack-building lives one layer down in ``pack_factories``.

Everything here is offline. No credentials, no network, no provider.
"""

from __future__ import annotations

from pathlib import Path

from pack_factories import PRODUCTION_PACKS_DIR, synthetic_pack, write_synthetic_packs

from basketball_scout.agents.pack_store import PackStore
from basketball_scout.agents.pipeline import StubBackend
from basketball_scout.config import Settings
from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.reports.service import ReportService
from basketball_scout.web.app import create_app

__all__ = [
    "ADMIN_TOKEN",
    "NO_BACKEND",
    "PRODUCTION_PACKS_DIR",
    "admin_headers",
    "make_app",
    "make_service",
    "make_settings",
    "synthetic_pack",
    "write_synthetic_packs",
]

ADMIN_TOKEN = "test-admin-token"

#: Distinguishes "use the default stub backend" from "deliberately no backend".
#: ``None`` cannot do that job — it is the value under test in one of the cases.
NO_BACKEND = object()


def make_settings(**overrides) -> Settings:
    base = {
        "report_admin_token": ADMIN_TOKEN,
        # 0 disables the limiter, so ordinary tests are not throttled by
        # accident; the rate-limit tests set their own values.
        "api_rate_limit_per_minute": 0,
        "admin_rate_limit_per_hour": 0,
        "agent_model": "test-model",
    }
    base.update(overrides)
    return Settings(**base)


def make_service(
    packs_dir: Path,
    *,
    repository: InMemoryReportRepository | None = None,
    backend_factory=NO_BACKEND,
    clock=None,
) -> ReportService:
    kwargs = {
        "pack_store": PackStore(packs_dir),
        "repository": repository or InMemoryReportRepository(),
        "backend_factory": (lambda: StubBackend()) if backend_factory is NO_BACKEND else backend_factory,
        "model_name": "test-model",
        "sleep": lambda _seconds: None,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return ReportService(**kwargs)


def make_app(
    packs_dir: Path,
    *,
    repository: InMemoryReportRepository | None = None,
    settings: Settings | None = None,
    analytics_dir: Path | None = None,
    backend_factory=NO_BACKEND,
):
    """A test app.

    ``analytics_dir`` defaults to a path that does not exist, so a test gets the
    honest "analytics unavailable" state unless it deliberately points at
    artifacts. That keeps the analytics store from silently loading the real
    committed league into tests that are about something else.
    """
    return create_app(
        settings=settings or make_settings(),
        repository=repository or InMemoryReportRepository(),
        packs_dir=packs_dir,
        analytics_dir=analytics_dir if analytics_dir is not None else packs_dir / "_no_analytics",
        backend_factory=(lambda: StubBackend()) if backend_factory is NO_BACKEND else backend_factory,
    )


def admin_headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"X-Admin-Token": token}


# Every route a member of the public can GET, in one place. Three separate
# guarantees read this list — no route reaches the agent backend, no route
# reaches the provider, no route needs a credential — so a new surface that is
# added to the app but forgotten here shows up as a failing OpenAPI test rather
# than as an untested billing path.
PUBLIC_HTML_ROUTES: tuple[str, ...] = (
    "/",
    "/teams/segev:4",
    "/teams/segev_4",
    "/teams/segev:4/splits",
    "/teams/segev:4/quarters",
    "/teams/segev:4/situations",
    "/teams/segev:4/games",
    "/explore",
    "/explore?segment=q4&outcome=losses&family=four_factors",
    "/scouting/segev:4",
)

PUBLIC_API_ROUTES: tuple[str, ...] = (
    "/health",
    "/api/teams",
    "/api/reports/latest/segev:4",
    "/api/openapi.json",
)


def public_routes(report_id: str) -> tuple[str, ...]:
    """Every public GET, including the ones that need a generated report."""
    return PUBLIC_HTML_ROUTES + PUBLIC_API_ROUTES + (
        f"/reports/{report_id}",
        f"/api/reports/{report_id}",
        f"/api/reports/{report_id}/pdf",
    )
