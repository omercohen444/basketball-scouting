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
