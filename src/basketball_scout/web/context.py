"""Application wiring.

One place decides which repository the process uses, where the shipped evidence
packs live, and whether generation is possible at all. Tests build a context
explicitly with an in-memory repository; production builds one from the
environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.pack_store import PackStore
from ..analytics.store import AnalyticsStore
from ..config import Settings, load_settings
from ..persistence.memory import InMemoryReportRepository
from ..persistence.repository import ReportRepository
from ..reports.service import ReportService, default_backend_factory, utc_now_iso
from .ratelimit import FixedWindowRateLimiter

log = logging.getLogger(__name__)

APP_VERSION = "0.1.0"


@dataclass
class AppContext:
    """Everything the HTTP layer needs, resolved once at startup."""

    settings: Settings
    pack_store: PackStore
    analytics: AnalyticsStore
    repository: ReportRepository
    service: ReportService
    api_limiter: FixedWindowRateLimiter
    admin_limiter: FixedWindowRateLimiter
    started_at: str = field(default_factory=utc_now_iso)
    app_version: str = APP_VERSION

    @property
    def storage_backend(self) -> str:
        return getattr(self.repository, "name", "unknown")

    @property
    def generation_configured(self) -> bool:
        """True when an admin *could* generate — token set and a key present."""
        return bool(self.settings.has_admin_token and self.settings.has_gemini_key)

    def health(self) -> dict[str, object]:
        packs_available = self.pack_store.available
        return {
            "status": "ok",
            "app_version": self.app_version,
            "started_at": self.started_at,
            "storage": self.storage_backend,
            "evidence_packs": {
                "available": packs_available,
                "teams_n": len(self.pack_store.team_ids()) if packs_available else 0,
                "season": self.pack_store.index.season if packs_available else None,
                "artifact_version": self.pack_store.index.artifact_version
                if packs_available
                else None,
            },
            "analytics": self.analytics.health(),
            "generation_configured": self.generation_configured,
        }


def build_repository(settings: Settings) -> ReportRepository:
    """Supabase when configured, otherwise in-memory with a loud warning.

    Falling back rather than refusing to boot is deliberate: ``/health``, the
    team list and the frontend's empty state all still work, which is what you
    want while a deployment's environment variables are being filled in. The
    warning exists so this is never mistaken for working persistence.
    """
    if settings.has_supabase:
        from ..persistence.supabase import SupabaseReportRepository

        return SupabaseReportRepository(
            settings.supabase_url or "",
            settings.supabase_secret_key or "",
            timeout=settings.http_timeout_seconds,
        )

    log.warning(
        "SUPABASE_URL/SUPABASE_SECRET_KEY are not set — using in-memory storage. "
        "Saved reports will be lost on restart."
    )
    return InMemoryReportRepository()


def build_context(
    *,
    settings: Settings | None = None,
    repository: ReportRepository | None = None,
    packs_dir: Path | None = None,
    analytics_dir: Path | None = None,
    backend_factory=None,
    seed_teams: bool = True,
) -> AppContext:
    settings = settings or load_settings()
    pack_store = PackStore(packs_dir or settings.evidence_packs_dir)
    # A second, independent artifact. Missing analytics degrades those pages
    # rather than the whole site, exactly as a missing pack does.
    analytics = AnalyticsStore(analytics_dir or settings.analytics_dir)
    repository = repository if repository is not None else build_repository(settings)

    service = ReportService(
        pack_store=pack_store,
        repository=repository,
        backend_factory=backend_factory
        if backend_factory is not None
        else default_backend_factory(settings, settings.agent_model),
        model_name=settings.agent_model,
    )

    # The in-memory repository has no seeded team table; take it from the shipped
    # pack index so /api/teams behaves identically with either backend.
    if seed_teams and isinstance(repository, InMemoryReportRepository) and not repository.has_teams:
        repository.set_teams(service.team_records())

    return AppContext(
        settings=settings,
        pack_store=pack_store,
        analytics=analytics,
        repository=repository,
        service=service,
        api_limiter=FixedWindowRateLimiter(settings.api_rate_limit_per_minute, 60.0),
        admin_limiter=FixedWindowRateLimiter(settings.admin_rate_limit_per_hour, 3600.0),
    )
