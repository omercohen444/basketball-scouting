"""FastAPI application factory.

Start command (Railway or local)::

    uvicorn basketball_scout.web.app:app --host 0.0.0.0 --port $PORT

``app`` is created at import time from the environment. ``create_app()`` is the
seam every test uses to inject an in-memory repository and a stub agent backend,
so the whole HTTP surface is exercisable with no credentials and no network.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..net import enable_system_trust_store
from ..persistence.repository import ReportRepository
from . import api, views
from .context import APP_VERSION, AppContext, build_context
from .errors import install_error_handlers

log = logging.getLogger(__name__)

DESCRIPTION = """
Opponent scouting for the Israeli Basketball Premier League.

Deterministic play-by-play analytics compute every number; a constrained
three-agent pipeline selects, interprets and prioritizes that evidence and writes
no numbers of its own. Public routes only ever read reports that were already
generated, validated and saved.
""".strip()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # httpx logs one INFO line per outbound request, including the full Supabase
    # query string. Useful when debugging, noise (and needless URL exposure in
    # logs) in normal operation.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_app(
    *,
    context: AppContext | None = None,
    settings: Settings | None = None,
    repository: ReportRepository | None = None,
    packs_dir: Path | None = None,
    analytics_dir: Path | None = None,
    backend_factory=None,
) -> FastAPI:
    ctx = context or build_context(
        settings=settings,
        repository=repository,
        packs_dir=packs_dir,
        analytics_dir=analytics_dir,
        backend_factory=backend_factory,
    )

    app = FastAPI(
        title="Basketball Analytics and AI Scouting System",
        description=DESCRIPTION,
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.ctx = ctx

    @app.get("/health", tags=["ops"], summary="Liveness and configuration snapshot")
    def health() -> JSONResponse:
        """Never touches the database or the provider — it must stay cheap enough
        for a platform health check to hit it constantly."""
        return JSONResponse(ctx.health())

    app.include_router(api.router)
    app.include_router(views.router)

    if views.STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(views.STATIC_DIR)), name="static")

    install_error_handlers(app, templates=views.templates)
    return app


def _build_default_app() -> FastAPI:
    # HTTPS on the development machine needs the OS trust store (CLAUDE.md §10);
    # harmless everywhere else, and it must run before any outbound client.
    enable_system_trust_store()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    return create_app()


app = _build_default_app()
