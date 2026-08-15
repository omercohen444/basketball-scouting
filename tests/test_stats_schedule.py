"""Multi-game discovery: offline unit tests (no network).

Live range-scan behaviour was verified manually against the real Segev API
this session (see WORKLOG.md and schedule.py's module docstring for the
findings); these tests cover the pure classification/filtering logic with
:func:`fetch_and_cache_actions` monkeypatched out, so the suite stays
network-free per CLAUDE.md §8.
"""

from __future__ import annotations

import pytest

from basketball_scout.config import load_settings
from basketball_scout.pbp.segev import SegevNotFound, SegevUnavailable
from basketball_scout.stats import schedule as schedule_mod
from basketball_scout.stats.schedule import (
    DiscoveredGame,
    discover_games,
    select_double_round_robin_games,
)


def test_is_usable_requires_finished_and_target_competition():
    finished_winner_league = DiscoveredGame(
        game_id=1, competition="Winner League", game_date="t",
        game_finished=True, home_team="A", away_team="B",
    )
    assert finished_winner_league.is_usable is True

    unfinished = DiscoveredGame(
        game_id=2, competition="Winner League", game_date="t",
        game_finished=False, home_team="A", away_team="B",
    )
    assert unfinished.is_usable is False

    wrong_competition = DiscoveredGame(
        game_id=3, competition="Winner Cup", game_date="t",
        game_finished=True, home_team="A", away_team="B",
    )
    assert wrong_competition.is_usable is False


def _fake_result(game_id: int, competition, finished=True):
    return {
        "gameInfo": {
            "gameId": str(game_id), "id": game_id, "time": "2026-01-01T18:00:00",
            "gameFinished": finished, "competition": competition,
            "homeTeam": {"name": "HOME"}, "awayTeam": {"name": "AWAY"},
        },
        "actions": [],
    }


def test_discover_games_classifies_not_found_and_unavailable(monkeypatch):
    def fake_fetch(game_id, settings, *, refresh=False):
        if game_id == 1:
            raise SegevNotFound("nope")
        if game_id == 2:
            raise SegevUnavailable("no pbp yet")
        return _fake_result(game_id, "Winner League")

    monkeypatch.setattr(schedule_mod, "fetch_and_cache_actions", fake_fetch)
    settings = load_settings(use_dotenv=False)
    events = []
    found = discover_games([1, 2, 3], settings, delay_seconds=0.0, on_progress=lambda gid, s: events.append((gid, s)))

    assert len(found) == 1
    assert found[0].game_id == 3
    assert found[0].is_usable is True
    assert events == [(1, "not_found"), (2, "unplayed_no_pbp"), (3, "usable")]


def test_discover_games_records_non_target_competition_as_not_usable(monkeypatch):
    def fake_fetch(game_id, settings, *, refresh=False):
        return _fake_result(game_id, "Women")

    monkeypatch.setattr(schedule_mod, "fetch_and_cache_actions", fake_fetch)
    settings = load_settings(use_dotenv=False)
    found = discover_games([1], settings, delay_seconds=0.0)

    assert len(found) == 1
    assert found[0].competition == "Women"
    assert found[0].is_usable is False


def test_discover_games_handles_dict_and_string_competition(monkeypatch):
    def fake_fetch(game_id, settings, *, refresh=False):
        comp = {"name": "Winner League"} if game_id == 1 else "Winner League"
        return _fake_result(game_id, comp)

    monkeypatch.setattr(schedule_mod, "fetch_and_cache_actions", fake_fetch)
    settings = load_settings(use_dotenv=False)
    found = discover_games([1, 2], settings, delay_seconds=0.0)

    assert all(g.competition == "Winner League" for g in found)
    assert all(g.is_usable for g in found)


# ---- select_double_round_robin_games (2026-08-15 management review) -------

def dg(game_id, home_id, away_id, date, competition="Winner League", finished=True):
    return DiscoveredGame(
        game_id=game_id, competition=competition, game_date=date, game_finished=finished,
        home_team=f"TEAM{home_id}", away_team=f"TEAM{away_id}",
        home_team_id=home_id, away_team_id=away_id,
    )


def test_select_keeps_exactly_first_two_chronological_meetings_per_pair():
    discovered = [
        dg(1, "A", "B", "2025-10-01"),
        dg(2, "B", "A", "2026-03-01"),
        dg(3, "A", "B", "2026-05-15"),  # 3rd meeting -> playoff rematch, must be excluded
    ]
    result = select_double_round_robin_games(discovered)
    assert result.selected_game_ids == [1, 2]
    assert result.excluded_extra_meeting_ids == [3]
    assert result.meetings_per_pair[frozenset({"A", "B"})] == 3
    assert result.pairs_with_unexpected_meeting_count == {frozenset({"A", "B"}): 3}


def test_select_excludes_non_usable_games():
    discovered = [
        dg(1, "A", "B", "2025-10-01"),
        dg(2, "A", "C", "2025-10-02", competition="Winner Cup"),  # wrong competition
        dg(3, "A", "D", "2025-10-03", finished=False),  # not finished
    ]
    result = select_double_round_robin_games(discovered)
    assert result.selected_game_ids == [1]
    assert result.excluded_extra_meeting_ids == []


def test_select_excludes_games_missing_team_ids():
    missing_id = DiscoveredGame(
        game_id=9, competition="Winner League", game_date="2025-10-01",
        game_finished=True, home_team="X", away_team="Y",
        home_team_id=None, away_team_id="B",
    )
    result = select_double_round_robin_games([missing_id])
    assert result.selected_game_ids == []


def test_select_computes_games_per_team_and_pair_counts():
    # 3 teams, full double round-robin: 3 pairs x 2 meetings = 6 games, 4 games/team
    discovered = [
        dg(1, "A", "B", "2025-10-01"), dg(2, "B", "A", "2025-11-01"),
        dg(3, "A", "C", "2025-10-05"), dg(4, "C", "A", "2025-11-05"),
        dg(5, "B", "C", "2025-10-10"), dg(6, "C", "B", "2025-11-10"),
    ]
    result = select_double_round_robin_games(discovered)
    assert len(result.selected_game_ids) == 6
    assert result.pair_count == 3
    assert result.pairs_with_exactly_two_meetings == 3
    assert result.games_per_team == {"A": 4, "B": 4, "C": 4}
    assert result.team_ids == ["A", "B", "C"]


def test_select_handles_single_meeting_pair_without_dropping_it():
    # Only one meeting found for this pair (e.g. discovery range didn't cover
    # the return fixture) -> keep the one real game, flag via meetings_per_pair.
    discovered = [dg(1, "A", "B", "2025-10-01")]
    result = select_double_round_robin_games(discovered)
    assert result.selected_game_ids == [1]
    assert result.meetings_per_pair[frozenset({"A", "B"})] == 1
    assert result.pairs_with_unexpected_meeting_count == {frozenset({"A", "B"}): 1}


# ---- quarters_verified_complete fallback (2026-08-15 targeted recovery) ---
# Real games found this review: gameInfo.gameFinished is stale/False for
# ids 178/209/224 despite every quarter having its own end-of-quarter
# marker and getBoxScore's score reconciling exactly with getActions. The
# fix: is_usable also accepts a self-certified-complete action stream.

def _quarter_marker(quarter, marker_type, user_time="00:00:00"):
    return {"type": "quarter", "quarter": quarter,
            "parameters": {"type": marker_type}, "userTime": user_time}


def _complete_four_quarter_actions(bulk_timestamp=None):
    """Mirrors the real id=178 shape: every quarter closed, but (optionally)
    every action shares one bulk-insert timestamp — irrelevant to our engine,
    which never reads userTime, but distinctive of the stale-flag games."""
    ts = bulk_timestamp or "03:51:52"
    actions = []
    for q in range(1, 5):
        actions.append(_quarter_marker(q, "start-of-quarter", ts))
        actions.append({"type": "shot", "quarter": q, "userTime": ts,
                         "parameters": {"team": 1, "made": "made", "points": 2, "type": "jump-shot"}})
        actions.append(_quarter_marker(q, "end-of-quarter", ts))
    return actions


def test_quarters_verified_complete_true_when_every_quarter_closed():
    actions = _complete_four_quarter_actions()
    assert schedule_mod._quarters_verified_complete(actions) is True


def test_quarters_verified_complete_false_when_final_quarter_never_closed():
    # Real suspended/incomplete-game shape: Q4 started but has no end marker.
    actions = _complete_four_quarter_actions()
    actions = [a for a in actions if not (a["type"] == "quarter" and a["quarter"] == 4
                                           and a["parameters"]["type"] == "end-of-quarter")]
    assert schedule_mod._quarters_verified_complete(actions) is False


def test_quarters_verified_complete_false_for_empty_actions():
    assert schedule_mod._quarters_verified_complete([]) is False


def test_is_usable_accepts_stale_finished_flag_when_quarters_self_certify():
    # id=178's real shape: gameFinished=False but the action stream itself
    # shows every quarter closed.
    game = DiscoveredGame(
        game_id=178, competition="Winner League", game_date="2026-05-27T18:40:00",
        game_finished=False, home_team="HAPOEL JERUSALEM", away_team="BEER SHEVA",
        home_team_id="4", away_team_id="11", quarters_verified_complete=True,
    )
    assert game.is_usable is True


def test_is_usable_still_rejects_genuinely_incomplete_game():
    game = DiscoveredGame(
        game_id=999, competition="Winner League", game_date="2026-01-01T18:00:00",
        game_finished=False, home_team="A", away_team="B",
        home_team_id="1", away_team_id="2", quarters_verified_complete=False,
    )
    assert game.is_usable is False


def test_is_usable_still_rejects_wrong_competition_even_if_quarters_complete():
    game = DiscoveredGame(
        game_id=1, competition="Winner Cup", game_date="2025-09-01T18:00:00",
        game_finished=False, home_team="A", away_team="B",
        home_team_id="1", away_team_id="2", quarters_verified_complete=True,
    )
    assert game.is_usable is False


def test_discover_games_recovers_stale_finished_flag_games(monkeypatch):
    """Regression: a game shaped exactly like the real ids 178/209/224
    (gameFinished False, but every quarter closed in the action stream) must
    be discovered as usable, not silently dropped."""
    def fake_fetch(game_id, settings, *, refresh=False):
        return {
            "gameInfo": {
                "gameId": str(game_id), "id": game_id, "time": "2026-05-10T20:50:00",
                "gameFinished": False, "competition": "Winner League",
                "homeTeam": {"name": "GALIL ELION", "id": "10"},
                "awayTeam": {"name": "HAPOEL HOLON", "id": "5"},
            },
            "actions": _complete_four_quarter_actions(),
        }

    monkeypatch.setattr(schedule_mod, "fetch_and_cache_actions", fake_fetch)
    settings = load_settings(use_dotenv=False)
    found = discover_games([224], settings, delay_seconds=0.0)

    assert len(found) == 1
    assert found[0].game_finished is False
    assert found[0].quarters_verified_complete is True
    assert found[0].is_usable is True
