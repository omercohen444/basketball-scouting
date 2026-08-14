"""Configuration must load safely with no credentials present."""

from __future__ import annotations

import pytest

from basketball_scout.config import (
    ConfigError,
    Settings,
    load_settings,
    require_gemini_api_key,
)


def test_loads_with_no_environment_at_all():
    settings = load_settings(use_dotenv=False)
    assert settings.gemini_api_key is None
    assert not settings.has_gemini_key
    assert settings.gemini_video_model
    assert settings.http_timeout_seconds > 0


def test_env_values_are_read_and_blanks_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  secret-key-value  ")
    monkeypatch.setenv("GEMINI_VIDEO_MODEL", "gemini-test-model")
    monkeypatch.setenv("GEMINI_VIDEO_FPS", "  ")  # blank means "use the default"
    settings = load_settings(use_dotenv=False)
    assert settings.gemini_api_key == "secret-key-value"
    assert settings.gemini_video_model == "gemini-test-model"
    assert settings.gemini_video_fps is None


def test_missing_key_fails_only_at_the_point_of_use():
    settings = load_settings(use_dotenv=False)  # must not raise
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        require_gemini_api_key(settings)


def test_present_key_is_returned():
    assert require_gemini_api_key(Settings(gemini_api_key="abc")) == "abc"


def test_redacted_snapshot_never_leaks_the_key():
    settings = Settings(gemini_api_key="super-secret-key-1234")
    snapshot = settings.redacted()
    assert "super-secret-key-1234" not in str(snapshot)
    assert snapshot["gemini_api_key"] == "<set:…1234>"
    assert Settings().redacted()["gemini_api_key"] == "<unset>"


def test_short_key_is_redacted_without_revealing_a_suffix():
    assert Settings(gemini_api_key="abc123").redacted()["gemini_api_key"] == "<set>"


@pytest.mark.parametrize("value, expected", [("low", "LOW"), ("HIGH", "HIGH"), ("", None)])
def test_media_resolution_is_normalized(monkeypatch, value, expected):
    monkeypatch.setenv("GEMINI_MEDIA_RESOLUTION", value)
    assert load_settings(use_dotenv=False).gemini_media_resolution == expected


def test_bad_media_resolution_is_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_MEDIA_RESOLUTION", "ULTRA")
    with pytest.raises(ConfigError, match="GEMINI_MEDIA_RESOLUTION"):
        load_settings(use_dotenv=False)


def test_bad_numeric_value_is_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_VIDEO_FPS", "many")
    with pytest.raises(ConfigError, match="GEMINI_VIDEO_FPS"):
        load_settings(use_dotenv=False)


def test_relative_paths_resolve_against_the_repo_root(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "data")
    settings = load_settings(use_dotenv=False)
    assert settings.data_dir.is_absolute()
    assert settings.validation_dir == settings.data_dir / "validation"


def test_cli_overrides_win_but_none_is_ignored():
    """`--model x` should override env; an unset flag must not blank the value."""
    base = Settings(gemini_video_model="from-env", gemini_video_fps=2.0)
    assert base.with_overrides(gemini_video_model="from-cli").gemini_video_model == "from-cli"
    assert base.with_overrides(gemini_video_model=None).gemini_video_model == "from-env"
    assert base.with_overrides(gemini_video_model=None).gemini_video_fps == 2.0
