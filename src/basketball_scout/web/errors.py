"""Error shaping.

Two rules:

1. **Nothing internal reaches a public caller.** No tracebacks, no file paths, no
   SQL, no provider payloads, no credential fragments. Unexpected exceptions are
   logged server-side with full detail and answered with a generic 500.
2. **Errors keep one shape.** Every ``/api`` failure is
   ``{"error": {"code": ..., "message": ...}}`` so a frontend has one branch to
   write; HTML routes get a rendered page instead.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

GENERIC_500 = "The server could not complete this request."
GENERIC_503 = "A required backing service is unavailable. Try again shortly."


class ApiError(HTTPException):
    """An HTTPException that carries a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", message)


def bad_request(message: str, code: str = "bad_request") -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, code, message)


def unauthorized(message: str = "Missing or invalid admin token") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", message)


def unavailable(message: str = GENERIC_503, code: str = "unavailable") -> ApiError:
    return ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, code, message)


def too_many_requests(retry_after: int) -> ApiError:
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "rate_limited",
        "Too many requests. Please wait before trying again.",
        headers={"Retry-After": str(retry_after)},
    )


def _payload(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api") or request.url.path == "/health"


def install_error_handlers(app, templates=None) -> None:
    """Attach the handlers. ``templates`` enables HTML error pages for the UI."""

    def _html_or_json(request: Request, status_code: int, code: str, message: str, headers=None):
        if templates is not None and not _is_api(request):
            response = templates.TemplateResponse(
                request,
                "error.html",
                {"status_code": status_code, "code": code, "message": message},
                status_code=status_code,
            )
            if headers:
                response.headers.update(headers)
            return response
        return JSONResponse(_payload(code, message), status_code=status_code, headers=headers)

    # Registered against Starlette's base class, not FastAPI's subclass: the
    # router's own "no route matched" 404 raises the base one, and handler
    # lookup walks the raised class's MRO — a handler on the subclass would
    # never see it, leaving FastAPI's default {"detail": ...} body on 404s.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        code = getattr(exc, "code", None) or _default_code(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else _default_message(exc.status_code)
        return _html_or_json(request, exc.status_code, code, message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # Field locations are safe and genuinely useful; the raw input is not
        # echoed back, so a hostile value never round-trips to the client.
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) for err in exc.errors()})
        message = "Request body failed validation"
        if fields:
            message += f" ({', '.join(f for f in fields if f)})"
        # Literal 422 rather than status.HTTP_422_*: the constant's name is
        # mid-rename in Starlette and importing either spelling emits a warning.
        return _html_or_json(request, 422, "invalid_request", message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error serving %s %s", request.method, request.url.path)
        return _html_or_json(request, status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", GENERIC_500)


def _default_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        422: "invalid_request",
        429: "rate_limited",
        503: "unavailable",
    }.get(status_code, "error")


def _default_message(status_code: int) -> str:
    return {
        404: "Not found",
        405: "Method not allowed",
        503: GENERIC_503,
    }.get(status_code, GENERIC_500)
