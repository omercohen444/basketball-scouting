"""Shared test setup.

Tests must not depend on the developer's `.env` or exported credentials — a
suite that passes only on the machine that has a key is worse than no suite.
"""

from __future__ import annotations

import pytest

_PROJECT_ENV_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_VIDEO_MODEL",
    "GEMINI_VIDEO_FPS",
    "GEMINI_MEDIA_RESOLUTION",
    "SEGEVSPORT_PROBE_URL",
    "HTTP_TIMEOUT_SECONDS",
    "DATA_DIR",
    "ARTIFACTS_DIR",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test against a clean, credential-free environment."""
    for name in _PROJECT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
