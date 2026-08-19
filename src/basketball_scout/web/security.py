"""Admin authentication and request throttling.

The threat this guards against is specific: an anonymous visitor triggering paid
Gemini generation, or writing to the report table. Everything public is a read of
something already generated, so exactly one endpoint needs a credential.

Design notes:

* The token is compared with :func:`secrets.compare_digest`, so a wrong guess
  costs the same time as a nearly-right one.
* An unset ``REPORT_ADMIN_TOKEN`` disables generation (503) rather than allowing
  it. A missing secret must never be a permissive default.
* The token never appears in a log line, an error message or a template.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import Request

from .context import AppContext
from .errors import too_many_requests, unauthorized, unavailable
from .ratelimit import client_key

log = logging.getLogger(__name__)

ADMIN_TOKEN_HEADER = "X-Admin-Token"


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


def require_admin(request: Request) -> AppContext:
    """FastAPI dependency: proves the caller holds the admin token."""
    ctx: AppContext = request.app.state.ctx
    expected = ctx.settings.report_admin_token

    if not expected:
        raise unavailable(
            "Report generation is disabled on this instance because no admin "
            "token is configured.",
            code="generation_disabled",
        )

    supplied = request.headers.get(ADMIN_TOKEN_HEADER) or ""
    if not supplied or not secrets.compare_digest(supplied, expected):
        log.warning(
            "rejected admin request from %s (%s)",
            client_key(request),
            "missing token" if not supplied else "bad token",
        )
        raise unauthorized()
    return ctx


def enforce_api_rate_limit(request: Request) -> None:
    ctx: AppContext = request.app.state.ctx
    decision = ctx.api_limiter.check(f"api:{client_key(request)}")
    if not decision.allowed:
        raise too_many_requests(decision.retry_after_seconds)


def enforce_admin_rate_limit(request: Request) -> None:
    ctx: AppContext = request.app.state.ctx
    decision = ctx.admin_limiter.check(f"admin:{client_key(request)}")
    if not decision.allowed:
        raise too_many_requests(decision.retry_after_seconds)
