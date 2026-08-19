"""Environment-based configuration.

Rules this module enforces:

* secrets come from the environment (or a git-ignored ``.env``), never from code;
* importing this module never raises and never performs I/O beyond reading
  ``.env`` when explicitly asked;
* missing credentials are only an error at the point of use, so tests and
  ``--help`` work on a machine with no API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_TRUTHY = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing or unusable."""


def _clean(value: str | None) -> str | None:
    """Return a stripped value, or None when unset/blank."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _as_float(value: str | None, name: str) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _as_int(value: str | None, name: str, default: int) -> int:
    value = _clean(value)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def _as_path(value: str | None, default: str) -> Path:
    raw = _clean(value) or default
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    gemini_api_key: str | None = None
    gemini_video_model: str = "gemini-2.5-flash"
    gemini_video_fps: float | None = None
    gemini_media_resolution: str | None = None
    segevsport_probe_url: str | None = None
    http_timeout_seconds: float = 20.0
    data_dir: Path = REPO_ROOT / "data"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    # Verified live this session (CP0 investigation): public, unauthenticated
    # JSON-RPC 2.0 endpoint. See docs/VIDEO_STAGE_PLAN.md §5.1.
    segev_api_url: str = "https://stats.segevstats.com/realtimestat_heb/api/"
    segev_referer: str = "https://basket.co.il/pbp/"

    # ---- product stage (FastAPI / Supabase / agents) ----
    # Verified working pin, mirrors agents.crew.DEFAULT_MODEL. Duplicated as a
    # literal rather than imported: config must never depend on the agent layer.
    agent_model: str = "gemini-3.5-flash"
    supabase_url: str | None = None
    supabase_secret_key: str | None = None
    report_admin_token: str | None = None
    api_rate_limit_per_minute: int = 120
    admin_rate_limit_per_hour: int = 12
    log_level: str = "INFO"

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def validation_dir(self) -> Path:
        return self.data_dir / "validation"

    @property
    def raw_pbp_dir(self) -> Path:
        return self.data_dir / "raw" / "pbp"

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifest"

    @property
    def processed_video_dir(self) -> Path:
        return self.data_dir / "processed" / "video"

    @property
    def evidence_packs_dir(self) -> Path:
        """Shipped production EvidencePack artifacts. Tracked in Git, unlike
        ``data/raw`` and ``data/processed`` — a deployment has only these."""
        return self.data_dir / "evidence_packs"

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_secret_key)

    @property
    def has_admin_token(self) -> bool:
        return bool(self.report_admin_token)

    def redacted(self) -> dict[str, object]:
        """Config snapshot safe to print or write to a debug artifact."""
        return {
            "gemini_api_key": _redact(self.gemini_api_key),
            "gemini_video_model": self.gemini_video_model,
            "gemini_video_fps": self.gemini_video_fps,
            "gemini_media_resolution": self.gemini_media_resolution,
            "segevsport_probe_url": self.segevsport_probe_url,
            "http_timeout_seconds": self.http_timeout_seconds,
            "data_dir": str(self.data_dir),
            "artifacts_dir": str(self.artifacts_dir),
            "segev_api_url": self.segev_api_url,
            "agent_model": self.agent_model,
            "supabase_url": self.supabase_url,
            "supabase_secret_key": _redact(self.supabase_secret_key),
            "report_admin_token": _redact(self.report_admin_token),
            "api_rate_limit_per_minute": self.api_rate_limit_per_minute,
            "admin_rate_limit_per_hour": self.admin_rate_limit_per_hour,
            "log_level": self.log_level,
        }

    def with_overrides(self, **kwargs: object) -> Settings:
        """Return a copy with non-None overrides applied (CLI flags win over env)."""
        supplied = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **supplied) if supplied else self


def _redact(secret: str | None) -> str:
    if not secret:
        return "<unset>"
    if len(secret) <= 8:
        return "<set>"
    return f"<set:…{secret[-4:]}>"


def load_dotenv_if_present(env_file: Path | None = None) -> Path | None:
    """Load ``.env`` into ``os.environ`` if python-dotenv and the file are available.

    Existing environment variables win, so an explicitly exported value is never
    silently overridden by a stale file. Returns the file that was loaded.
    """
    path = env_file or (REPO_ROOT / ".env")
    if not path.is_file():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is convenience only, not a hard requirement
        return None
    load_dotenv(path, override=False)
    return path


def load_settings(*, use_dotenv: bool = True, env_file: Path | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    Never raises for a missing secret — only for a malformed value.
    """
    if use_dotenv:
        load_dotenv_if_present(env_file)

    env = os.environ
    return Settings(
        gemini_api_key=_clean(env.get("GEMINI_API_KEY")),
        gemini_video_model=_clean(env.get("GEMINI_VIDEO_MODEL")) or "gemini-2.5-flash",
        gemini_video_fps=_as_float(env.get("GEMINI_VIDEO_FPS"), "GEMINI_VIDEO_FPS"),
        gemini_media_resolution=_normalize_resolution(env.get("GEMINI_MEDIA_RESOLUTION")),
        segevsport_probe_url=_clean(env.get("SEGEVSPORT_PROBE_URL")),
        http_timeout_seconds=_as_float(env.get("HTTP_TIMEOUT_SECONDS"), "HTTP_TIMEOUT_SECONDS")
        or 20.0,
        data_dir=_as_path(env.get("DATA_DIR"), "data"),
        artifacts_dir=_as_path(env.get("ARTIFACTS_DIR"), "artifacts"),
        agent_model=_clean(env.get("AGENT_MODEL")) or "gemini-3.5-flash",
        supabase_url=_clean(env.get("SUPABASE_URL")),
        # SUPABASE_KEY is accepted as a legacy alias for the same server secret.
        supabase_secret_key=_clean(env.get("SUPABASE_SECRET_KEY"))
        or _clean(env.get("SUPABASE_KEY")),
        report_admin_token=_clean(env.get("REPORT_ADMIN_TOKEN")),
        api_rate_limit_per_minute=_as_int(
            env.get("API_RATE_LIMIT_PER_MINUTE"), "API_RATE_LIMIT_PER_MINUTE", 120
        ),
        admin_rate_limit_per_hour=_as_int(
            env.get("ADMIN_RATE_LIMIT_PER_HOUR"), "ADMIN_RATE_LIMIT_PER_HOUR", 12
        ),
        log_level=(_clean(env.get("LOG_LEVEL")) or "INFO").upper(),
    )


_ALLOWED_RESOLUTIONS = {"LOW", "MEDIUM", "HIGH"}


def _normalize_resolution(value: str | None) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    upper = value.upper().removeprefix("MEDIA_RESOLUTION_")
    if upper not in _ALLOWED_RESOLUTIONS:
        raise ConfigError(
            f"GEMINI_MEDIA_RESOLUTION must be one of {sorted(_ALLOWED_RESOLUTIONS)}, got {value!r}"
        )
    return upper


def require_gemini_api_key(settings: Settings) -> str:
    """Return the Gemini API key or fail with an actionable message."""
    if not settings.gemini_api_key:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in, "
            "or export GEMINI_API_KEY in your shell. Never hard-code the key."
        )
    return settings.gemini_api_key


def require_admin_token(settings: Settings) -> str:
    """Return the admin token or fail with an actionable message.

    Never falls back to a default. An unset ``REPORT_ADMIN_TOKEN`` must make the
    generation endpoint unusable, not open.
    """
    if not settings.report_admin_token:
        raise ConfigError(
            "REPORT_ADMIN_TOKEN is not set, so report generation is disabled. "
            "Set it in .env (local) or in the deployment environment."
        )
    return settings.report_admin_token


def require_supabase(settings: Settings) -> tuple[str, str]:
    """Return ``(url, secret_key)`` or fail with an actionable message."""
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ConfigError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must both be set to use the "
            "Supabase repository. Without them the app falls back to in-memory "
            "storage, which loses reports on restart."
        )
    return settings.supabase_url, settings.supabase_secret_key
