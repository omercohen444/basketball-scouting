"""Spike harness safety: importable offline, no side effects, correct request shape."""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

from basketball_scout.config import REPO_ROOT, Settings
from basketball_scout.video.events import ShotEvent, window_around
from basketball_scout.video.metrics import DEFAULT_METRICS, select_metrics
from basketball_scout.video.prompts import build_classification_prompt, expected_field_names

SPIKES = REPO_ROOT / "scripts" / "spikes"
SPIKE_FILES = ["probe_segevsport.py", "gemini_video_event.py"]
URL = "https://www.youtube.com/watch?v=EXAMPLE"


def import_spike(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Make any attempt to open a socket an immediate, obvious failure."""

    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted at import time")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize("filename", SPIKE_FILES)
def test_spike_imports_without_touching_the_network(filename, no_network):
    """Importing a spike must not fetch, authenticate or construct a client."""
    module = import_spike(SPIKES / filename, f"spike_{filename[:-3]}")
    assert hasattr(module, "main")
    assert hasattr(module, "build_parser")


@pytest.mark.parametrize("filename", SPIKE_FILES)
def test_spike_help_works_without_any_credentials(filename, capsys):
    module = import_spike(SPIKES / filename, f"help_{filename[:-3]}")
    with pytest.raises(SystemExit) as exit_info:
        module.build_parser().parse_args(["--help"])
    assert exit_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_probe_helpers_are_pure():
    probe = import_spike(SPIKES / "probe_segevsport.py", "probe_pure")
    assert probe.slugify("https://basket.co.il/pbp/game.asp?id=1") == "basket.co.il_pbp_game.asp"
    assert probe.human_size(0) == "0 B"
    assert probe.is_interesting("/pbp/js/games.js")
    assert not probe.is_interesting("/images/logo.png")


def test_probe_extracts_endpoint_clues_from_html():
    probe = import_spike(SPIKES / "probe_segevsport.py", "probe_clues")
    html = """
    <html><head><title>Game</title></head><body>
      <a href="/pbp/game.asp?id=42">Play by play</a>
      <a href="/images/logo.png">logo</a>
      <script src="/pbp/js/games.js?v=1"></script>
      <script>var feed = "/api/pbp/events.json?game=42";</script>
      <table></table>
    </body></html>
    """
    clues = probe.extract_clues(html, "https://example.com/")
    assert clues["title"] == "Game"
    hrefs = [link["href"] for link in clues["interesting_links"]]
    assert "https://example.com/pbp/game.asp?id=42" in hrefs
    assert not any("logo.png" in h for h in hrefs)
    assert any("games.js" in s for s in clues["interesting_script_srcs"])
    assert any("events.json" in s for s in clues["urlish_strings"])
    assert clues["tables"] == 1


# --------------------------------------------------------------------------
# Prompt + request shape (offline; no key, no call)
# --------------------------------------------------------------------------


def sample_event() -> ShotEvent:
    return ShotEvent(
        event_id="G001-E017",
        window=window_around(URL, "1:12:30"),
        game_id="G001",
        team="Team A",
        period=3,
        game_clock="04:12",
        description="3PT shot made",
    )


def test_prompt_names_every_field_and_offers_uncertain():
    prompt = build_classification_prompt(sample_event(), DEFAULT_METRICS)
    for field in expected_field_names(DEFAULT_METRICS):
        assert field in prompt
    for metric in DEFAULT_METRICS:
        for label in metric.all_labels:
            assert f'"{label}"' in prompt
    assert "uncertain" in prompt


def test_prompt_shrinks_with_the_metric_selection():
    prompt = build_classification_prompt(sample_event(), select_metrics(["shot_contest"]))
    assert "shot_contest" in prompt
    assert "possession_type" not in prompt


def test_gemini_request_uses_the_verified_sdk_shape():
    """Locks the wire format: fileData.fileUri + videoMetadata start/end offsets."""
    pytest.importorskip("google.genai")
    from basketball_scout.video.gemini_client import build_request

    settings = Settings(gemini_video_model="gemini-test", gemini_video_fps=1.0)
    request = build_request(sample_event(), settings, DEFAULT_METRICS)
    payload = request.debug_dict()

    assert payload["model"] == "gemini-test"
    video_part, text_part = payload["contents"]["parts"]
    assert video_part["fileData"]["fileUri"] == URL
    assert video_part["videoMetadata"]["startOffset"] == "4330s"
    assert video_part["videoMetadata"]["endOffset"] == "4358s"
    assert video_part["videoMetadata"]["fps"] == 1.0
    assert "shot_contest" in text_part["text"]

    config = payload["config"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0.0
    assert config["systemInstruction"]


def test_request_build_needs_no_api_key():
    """--dry-run has to work on a machine that has never seen a credential."""
    pytest.importorskip("google.genai")
    from basketball_scout.video.gemini_client import build_request

    assert build_request(sample_event(), Settings(gemini_api_key=None)) is not None


def test_debug_dict_is_json_safe_and_leaks_no_key():
    """The response schema is a pydantic class; it must not break serialization."""
    pytest.importorskip("google.genai")
    import json

    from basketball_scout.video.gemini_client import build_request

    settings = Settings(gemini_api_key="super-secret-key-1234")
    dumped = json.dumps(build_request(sample_event(), settings).debug_dict())
    assert "super-secret-key-1234" not in dumped
    assert "VideoEventClassification" in dumped


def test_classifier_records_a_provider_failure_instead_of_raising():
    """One bad event must never abort a labelling session."""
    pytest.importorskip("google.genai")
    from basketball_scout.video.gemini_client import GeminiVideoClassifier

    class ExplodingModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("429 quota exceeded")

    class FakeClient:
        models = ExplodingModels()

    classifier = GeminiVideoClassifier(Settings(), client=FakeClient())
    result = classifier.classify(sample_event())

    assert not result.ok
    assert "429 quota exceeded" in (result.error or "")
    assert result.event_id == "G001-E017"
    assert result.latency_seconds is not None


def test_classifier_parses_a_well_formed_provider_response():
    pytest.importorskip("google.genai")
    from basketball_scout.video.gemini_client import GeminiVideoClassifier

    payload = {}
    for metric in DEFAULT_METRICS:
        payload[metric.key] = metric.labels[0]
        payload[f"{metric.key}_confidence"] = 0.75
        payload[f"{metric.key}_evidence"] = "Visible in the clip."

    class FakeResponse:
        parsed = payload
        text = "{}"

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    result = GeminiVideoClassifier(Settings(), client=FakeClient()).classify(sample_event())

    assert result.ok
    assert result.label("shot_contest") == "open"
    assert result.label("possession_type") == "transition"
    assert result.provider == "gemini"


def test_classifier_records_a_schema_violation_as_an_error():
    """An invalid label is a recorded failure, not a silently accepted result."""
    pytest.importorskip("google.genai")
    from basketball_scout.video.gemini_client import GeminiVideoClassifier

    class FakeResponse:
        parsed = None
        text = '{"shot_contest": "sort_of_open"}'

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    result = GeminiVideoClassifier(Settings(), client=FakeClient()).classify(sample_event())
    assert not result.ok
    assert result.error
    assert result.raw_text == '{"shot_contest": "sort_of_open"}'


def test_no_api_key_is_present_in_the_committed_env_example():
    """.env.example must stay a template, never a leaked credential."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY"):
            assert line == "GEMINI_API_KEY=", "placeholder must stay empty"


def test_repo_has_no_committed_dotenv():
    assert not (REPO_ROOT / ".env").exists() or ".env" in (
        REPO_ROOT / ".gitignore"
    ).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _cleanup_spike_modules():
    yield
    for name in list(sys.modules):
        if name.startswith(("spike_", "help_", "probe_")):
            del sys.modules[name]
