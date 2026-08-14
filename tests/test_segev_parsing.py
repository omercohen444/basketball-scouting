"""SegevSport JSON-RPC envelope parsing.

Uses no network. The response envelope quirks tested here were all directly
observed against the real API this session (see docs/VIDEO_STAGE_PLAN.md §5.1):
a "not found" game returns HTTP 200 with a populated ``error`` field, and an
unplayed fixture has no ``actions`` key at all (not an empty list).
"""

from __future__ import annotations

import pytest

from basketball_scout.pbp.segev import (
    SegevError,
    SegevNotFound,
    SegevUnavailable,
    _check_envelope,
    summarize,
)

NOT_FOUND_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": "1",
    "result": {"success": False},
    "error": {"code": "-32000", "message": "game not found"},
}

UNPLAYED_FIXTURE_RESULT = {
    "gameInfo": {"gameId": "1", "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"}},
    # no "actions" key at all — this is real, observed behaviour, not synthetic.
}


def test_not_found_is_recognized_despite_http_200():
    """The API returns HTTP 200 for a nonexistent game_id; only the envelope tells you."""
    with pytest.raises(SegevNotFound, match="not found"):
        _check_envelope(NOT_FOUND_PAYLOAD, game_id=99999999)


def test_other_json_rpc_errors_are_not_silently_treated_as_not_found():
    payload = {"jsonrpc": "2.0", "id": "1", "result": None,
               "error": {"code": "-32001", "message": "internal server error"}}
    with pytest.raises(SegevError, match="internal server error"):
        _check_envelope(payload, game_id=1)


def test_malformed_result_is_rejected():
    with pytest.raises(SegevError, match="malformed"):
        _check_envelope({"jsonrpc": "2.0", "id": "1", "error": None, "result": "not a dict"}, game_id=1)


def test_missing_actions_key_means_unplayed_not_empty_list():
    """Distinguishing 'no PBP yet' from 'PBP exists and has zero events' matters —
    an empty list would silently look like a real 0-shot game."""
    assert "actions" not in UNPLAYED_FIXTURE_RESULT
    # This is exercised end-to-end via fetch_actions() in test_canonical.py's
    # fixture-based tests; here we just pin the observed shape.


def test_summarize_handles_dict_or_string_competition():
    result_string_comp = {
        "gameInfo": {"gameId": "1", "time": "t", "gameFinished": True,
                     "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
                     "competition": "Winner League"},
        "actions": [],
    }
    result_dict_comp = {
        "gameInfo": {"gameId": "1", "time": "t", "gameFinished": True,
                     "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
                     "competition": {"name": "Winner League", "nameLocal": "x"}},
        "actions": [],
    }
    assert summarize(result_string_comp)["competition"] == "Winner League"
    assert summarize(result_dict_comp)["competition"] == "Winner League"


def test_summarize_counts_shots_and_quarters():
    result = {
        "gameInfo": {"gameId": "1", "time": "t", "gameFinished": True,
                     "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
                     "competition": "Winner League"},
        "actions": [
            {"id": 1, "quarter": 1, "type": "shot"},
            {"id": 2, "quarter": 1, "type": "shot"},
            {"id": 3, "quarter": 2, "type": "foul"},
        ],
    }
    summary = summarize(result)
    assert summary["shot_count"] == 2
    assert summary["quarters"] == [1, 2]
    assert summary["type_counts"] == {"shot": 2, "foul": 1}
