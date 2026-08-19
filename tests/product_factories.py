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
    backend_factory=NO_BACKEND,
):
    return create_app(
        settings=settings or make_settings(),
        repository=repository or InMemoryReportRepository(),
        packs_dir=packs_dir,
        backend_factory=(lambda: StubBackend()) if backend_factory is NO_BACKEND else backend_factory,
    )


def admin_headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"X-Admin-Token": token}
