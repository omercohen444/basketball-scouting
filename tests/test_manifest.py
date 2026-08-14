"""Game manifest: load/save round-trip and lookup.

Minimal on purpose (docs/VIDEO_STAGE_PLAN.md §20 — avoid a test empire), but
the manifest is a tracked, stable-contract artifact (§19), so its on-disk
shape earns a round-trip test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basketball_scout.video.manifest import (
    GameManifestEntry,
    Manifest,
    ManifestError,
    TeamRef,
    VideoRef,
    load_manifest,
    save_manifest,
)
from basketball_scout.video.sync import GameSync, QuarterAnchor


def make_entry(game_id: str = "TEST-G1") -> GameManifestEntry:
    return GameManifestEntry(
        game_id=game_id,
        season="2025-26",
        competition="Winner League",
        date_utc="2026-01-11",
        segev_game_id=136,
        home=TeamRef(team_id="T-HOME", segev_team_id=2, name="MACCABI TEL AVIV"),
        away=TeamRef(team_id="T-AWAY", segev_team_id=4, name="HAPOEL JERUSALEM"),
        video=VideoRef(provider="youtube", url="https://youtu.be/x", verified_full_game=True, duration_s=5222.0),
    )


def test_missing_manifest_file_returns_empty_not_an_error(tmp_path: Path):
    manifest = load_manifest(tmp_path / "does_not_exist.json")
    assert manifest.games == []


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "matchday.json"
    manifest = Manifest()
    manifest.upsert(make_entry())
    save_manifest(path, manifest)

    reloaded = load_manifest(path)
    entry = reloaded.get("TEST-G1")
    assert entry.home.name == "MACCABI TEL AVIV"
    assert entry.video.duration_s == 5222.0
    assert entry.segev_game_id == 136


def test_sync_block_round_trips_inside_the_manifest(tmp_path: Path):
    path = tmp_path / "matchday.json"
    manifest = Manifest()
    entry = make_entry()
    entry.sync = GameSync(
        video_duration_s=5222.0,
        anchors=[QuarterAnchor(quarter=1, source_action_id=1360022, pbp_user_time_s=68908.0, video_time_s=400.0)],
    )
    manifest.upsert(entry)
    save_manifest(path, manifest)

    reloaded = load_manifest(path).get("TEST-G1")
    assert reloaded.sync is not None
    assert reloaded.sync.map_to_video(1, 68958.0) == pytest.approx(450.0)


def test_upsert_replaces_by_game_id_not_appends():
    manifest = Manifest()
    manifest.upsert(make_entry())
    manifest.upsert(make_entry())  # same game_id
    assert len(manifest.games) == 1


def test_get_unknown_game_id_raises_clearly():
    manifest = Manifest()
    with pytest.raises(ManifestError, match="TEST-G1"):
        manifest.get("TEST-G1")


def test_team_for_side_resolves_home_and_away():
    entry = make_entry()
    assert entry.team_for_side("home").name == "MACCABI TEL AVIV"
    assert entry.team_for_side("away").name == "HAPOEL JERUSALEM"
    with pytest.raises(ManifestError):
        entry.team_for_side("neither")


def test_entry_missing_required_field_is_rejected():
    with pytest.raises(ManifestError, match="game_id"):
        GameManifestEntry.from_dict({"season": "2025-26"})
