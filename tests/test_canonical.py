"""Canonical shot-event extraction, against a trimmed REAL Segev response.

The fixture (data/validation/segev_game136_trimmed.json) is a real,
verbatim slice of game_id=136 (MACCABI TEL AVIV vs HAPOEL JERUSALEM,
2025-26 Winner League), fetched live this session and trimmed to ~19 actions.
It deliberately includes the exact anchor lesson learned this session: the
59-minute gap between ``start-of-game`` (17:52:13) and the real Q1
``start-of-quarter`` (18:51:12) — see docs/VIDEO_STAGE_PLAN.md §7.1, A9.
"""

from __future__ import annotations

import json

import pytest

from basketball_scout.config import REPO_ROOT
from basketball_scout.pbp.canonical import extract_shot_events

FIXTURE = REPO_ROOT / "data" / "validation" / "segev_game136_trimmed.json"


@pytest.fixture
def real_actions() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["actions"]


def test_fixture_exists_and_is_real_trimmed_data(real_actions):
    assert len(real_actions) == 19
    types = {a["type"] for a in real_actions}
    assert {"game", "quarter", "shot", "substitution", "foul", "freeThrow", "assist"} <= types


def test_extracts_only_shot_type_actions(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    assert result.total_shot_actions == 8  # 8 shots staged in the fixture
    assert all(e.event_type == "shot" for e in result.events)
    # freeThrow, foul, substitution, quarter/game markers must never appear
    assert len(result.events) == result.total_shot_actions


def test_event_id_is_stable_and_traceable(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    ids = {e.event_id for e in result.events}
    assert "TEST-G136:1360022" in ids
    for event in result.events:
        assert event.event_id == f"TEST-G136:{event.source_action_id}"


def test_team_side_is_derived_from_parameters_team(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    sides = {e.team_side for e in result.events}
    assert sides <= {"home", "away"}
    assert len(sides) == 2  # this slice has shots from both teams


def test_assisted_flag_is_linked_via_parent_action_id(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    # action 1360037 (dunk) has a linked assist (1360038) in the real fixture.
    dunk = next(e for e in result.events if e.source_action_id == 1360037)
    assert dunk.assisted is True
    # a shot with no assist action pointing at it must be False, not missing.
    other = next(e for e in result.events if e.source_action_id == 1360022)
    assert other.assisted is False


def test_shot_fields_match_the_real_source_record(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    event = next(e for e in result.events if e.source_action_id == 1360022)
    assert event.quarter == 1
    assert event.shot_type == "jump-shot"
    assert event.user_time_s == 19 * 3600 + 7 * 60 + 59  # userTime 19:07:59
    assert event.coord_x is not None and event.coord_y is not None


def test_events_are_sorted_by_quarter_then_user_time(real_actions):
    result = extract_shot_events("TEST-G136", real_actions)
    times = [(e.quarter, e.user_time_s) for e in result.events]
    assert times == sorted(times)


def test_raw_source_record_is_preserved(real_actions):
    """The full raw action must survive — the later PBP stage depends on it."""
    result = extract_shot_events("TEST-G136", real_actions)
    event = next(e for e in result.events if e.source_action_id == 1360022)
    assert event.raw["id"] == 1360022
    assert event.raw["userTime"] == "19:07:59"
    assert "parameters" in event.raw


def test_missing_team_is_dropped_and_counted():
    action = {
        "id": 1, "quarter": 1, "type": "shot", "userTime": "19:00:00",
        "teamId": 8, "playerId": 100,
        "parameters": {"team": None, "player": "5", "type": "jump-shot", "points": 2},
    }
    result = extract_shot_events("G", [action])
    assert result.events == []
    assert result.dropped_no_team == 1


def test_missing_or_unparseable_user_time_is_dropped_and_counted():
    action = {
        "id": 1, "quarter": 1, "type": "shot", "userTime": "not-a-time",
        "teamId": 8, "playerId": 100,
        "parameters": {"team": 1, "player": "5", "type": "jump-shot", "points": 2},
    }
    result = extract_shot_events("G", [action])
    assert result.events == []
    assert result.dropped_no_time == 1


def test_missing_action_id_is_dropped_as_malformed():
    action = {
        "quarter": 1, "type": "shot", "userTime": "19:00:00",
        "teamId": 8, "playerId": 100,
        "parameters": {"team": 1, "player": "5", "type": "jump-shot", "points": 2},
    }
    result = extract_shot_events("G", [action])
    assert result.dropped_malformed == 1


def test_free_throws_and_fouls_are_never_extracted_as_shots(real_actions):
    """type=='freeThrow' is a DIFFERENT action type — must never leak in as a shot."""
    result = extract_shot_events("TEST-G136", real_actions)
    free_throw_ids = {a["id"] for a in real_actions if a["type"] == "freeThrow"}
    extracted_ids = {e.source_action_id for e in result.events}
    assert not (free_throw_ids & extracted_ids)


def test_non_monotonic_source_order_is_corrected_and_counted():
    """userTime is only mostly monotonic in the real source — must not trust order."""
    out_of_order = [
        {"id": 2, "quarter": 1, "type": "shot", "userTime": "19:00:10",
         "teamId": 8, "playerId": 1,
         "parameters": {"team": 1, "player": "1", "type": "jump-shot", "points": 2}},
        {"id": 1, "quarter": 1, "type": "shot", "userTime": "19:00:05",
         "teamId": 8, "playerId": 2,
         "parameters": {"team": 1, "player": "2", "type": "jump-shot", "points": 2}},
    ]
    result = extract_shot_events("G", out_of_order)
    assert [e.source_action_id for e in result.events] == [1, 2]
    # A 2-element swap changes 2 positions (both entries moved), not 1 —
    # inversions_corrected counts positions that differ pre/post sort.
    assert result.inversions_corrected == 2


def test_the_famous_59_minute_start_of_game_gap_is_visible_in_the_fixture(real_actions):
    """Pins the exact lesson from docs/VIDEO_STAGE_PLAN.md A9: never anchor on
    'start-of-game' — it can be nowhere near the real tip-off."""
    game_start = next(a for a in real_actions if a["type"] == "game")
    quarter_start = next(a for a in real_actions if a["type"] == "quarter")
    assert game_start["userTime"] == "17:52:13"
    assert quarter_start["userTime"] == "18:51:12"
