"""Engine assembly: raw Segev result -> (home, away) TeamGameStats.

Synthetic minimal games here to test assembly rules in isolation (team/
opponent inversion, tie rejection, overtime period detection). The real
game_id=136 fixture is exercised end-to-end in
test_stats_integration_game136.py.
"""

from __future__ import annotations

import pytest

from basketball_scout.stats.engine import EngineError, build_team_game_stats


def game_info(number_of_quarters=4, home_id="2", away_id="4"):
    return {
        "gameId": "999",
        "id": 999,
        "time": "2026-01-11T21:05:00",
        "numberOfQuarters": number_of_quarters,
        "homeTeam": {"id": home_id, "name": "HOME TEAM"},
        "awayTeam": {"id": away_id, "name": "AWAY TEAM"},
    }


def shot(team, quarter, made="made", points=2):
    return {
        "type": "shot", "quarter": quarter,
        "parameters": {"team": team, "made": made, "points": points, "type": "jump-shot"},
    }


def test_home_scores_more_and_wins():
    actions = [shot(1, 1, points=3), shot(1, 1, points=3), shot(2, 1, points=2)]
    result = {"gameInfo": game_info(), "actions": actions}
    home, away = build_team_game_stats(result, season="2025-26")
    assert home.final_score_for == 6
    assert away.final_score_for == 2
    assert home.win is True
    assert away.win is False
    assert home.final_score_against == away.final_score_for
    assert away.final_score_against == home.final_score_for


def test_team_and_opponent_ids_are_provider_qualified_and_inverted():
    actions = [shot(1, 1, points=3), shot(2, 1, points=2)]
    result = {"gameInfo": game_info(), "actions": actions}
    home, away = build_team_game_stats(result, season="2025-26")
    assert home.team_id == "segev:2"
    assert home.opponent_id == "segev:4"
    assert away.team_id == "segev:4"
    assert away.opponent_id == "segev:2"
    assert home.internal_game_id == away.internal_game_id == "segev:999"


def test_tied_score_raises_engine_error():
    actions = [shot(1, 1, points=2), shot(2, 1, points=2)]
    result = {"gameInfo": game_info(), "actions": actions}
    with pytest.raises(EngineError, match="tied"):
        build_team_game_stats(result, season="2025-26")


def test_missing_actions_key_raises_engine_error():
    result = {"gameInfo": game_info()}
    with pytest.raises(EngineError, match="gameInfo.*actions|actions"):
        build_team_game_stats(result, season="2025-26")


def test_no_overtime_when_max_quarter_is_regulation():
    actions = [shot(1, 4, points=3), shot(2, 4, points=2)]
    result = {"gameInfo": game_info(), "actions": actions}
    home, _ = build_team_game_stats(result, season="2025-26")
    assert home.ot_periods == 0
    assert home.game_minutes == 40.0


def test_one_overtime_period_detected_and_adds_five_minutes():
    actions = [shot(1, 4, points=2), shot(2, 5, points=3)]
    result = {"gameInfo": game_info(), "actions": actions}
    home, _ = build_team_game_stats(result, season="2025-26")
    assert home.ot_periods == 1
    assert home.game_minutes == 45.0


def test_net_rating_equals_offensive_minus_defensive():
    actions = [
        shot(1, 1, points=2), shot(1, 1, points=2), shot(1, 1, points=3),
        shot(2, 1, points=2), shot(2, 1, points=3),
    ]
    result = {"gameInfo": game_info(), "actions": actions}
    home, away = build_team_game_stats(result, season="2025-26")
    assert home.metrics.net_rating == pytest.approx(
        home.metrics.offensive_rating - home.metrics.defensive_rating
    )
    assert away.metrics.net_rating == pytest.approx(
        away.metrics.offensive_rating - away.metrics.defensive_rating
    )
