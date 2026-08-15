"""End-to-end statistics layer sanity checks against real game_id=136 data.

Fixture (data/validation/segev_game136_full.json) is a verbatim, complete
copy of the real Segev getActions response for game_id=136 (MACCABI TEL AVIV
95 - HAPOEL JERUSALEM 84, 2026-01-11 Winner League) — the same game already
used as the real development fixture for the video pipeline
(segev_game136_trimmed.json), here kept whole because the statistics layer
needs every action type (rebounds, turnovers, free throws), not just shots.

These are the "real data" sanity checks BUILD_PLAN.md/CLAUDE.md ask for:
both teams represented, score reconciles, ORtg/DRtg internally coherent,
NetRating = ORtg - DRtg exactly, percentages stay in valid ranges, no
player-level aggregation, overtime handled (this game has none).
"""

from __future__ import annotations

import json

import pytest

from basketball_scout.config import REPO_ROOT
from basketball_scout.stats.engine import build_team_game_stats

FIXTURE = REPO_ROOT / "data" / "validation" / "segev_game136_full.json"


@pytest.fixture
def raw_result() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def game(raw_result):
    return build_team_game_stats(raw_result, season="2025-26")


def test_fixture_is_the_real_full_game(raw_result):
    assert len(raw_result["actions"]) == 867
    assert raw_result["gameInfo"]["homeTeam"]["name"] == "MACCABI TEL AVIV"
    assert raw_result["gameInfo"]["awayTeam"]["name"] == "HAPOEL JERUSALEM"


def test_both_teams_represented_with_distinct_ids(game):
    home, away = game
    assert home.team_id != away.team_id
    assert home.team_id == "segev:2"
    assert away.team_id == "segev:4"
    assert home.opponent_id == away.team_id
    assert away.opponent_id == home.team_id


def test_final_score_matches_known_real_result(game):
    # Independently known result for this game (WORKLOG.md 2026-08-15):
    # Maccabi Tel Aviv 95 - Hapoel Jerusalem 84.
    home, away = game
    assert home.final_score_for == 95
    assert away.final_score_for == 84
    assert home.win is True
    assert away.win is False


def test_score_reconciles_between_home_and_away_views(game):
    home, away = game
    assert home.final_score_for == away.final_score_against
    assert away.final_score_for == home.final_score_against


def test_no_overtime_in_this_game(game):
    home, away = game
    assert home.ot_periods == 0
    assert away.ot_periods == 0
    assert home.game_minutes == 40.0


def test_net_rating_equals_offensive_minus_defensive_rating(game):
    for team in game:
        assert team.metrics.net_rating == pytest.approx(
            team.metrics.offensive_rating - team.metrics.defensive_rating
        )


def test_home_and_away_ratings_are_internally_coherent(game):
    home, away = game
    # Winning team (Maccabi, home) should have a positive net rating and the
    # losing team a negative one on real data — not a mathematical identity,
    # a sanity check on real values.
    assert home.metrics.net_rating > 0
    assert away.metrics.net_rating < 0
    # A team's defensive rating is, by construction, 100 * opponent_points /
    # opponent_possessions — exactly the opponent's own offensive rating
    # formula. This is a structural invariant of the engine (not a
    # coincidence of this game's box score), so it holds exactly, not
    # approximately.
    assert home.metrics.offensive_rating == pytest.approx(away.metrics.defensive_rating)
    assert away.metrics.offensive_rating == pytest.approx(home.metrics.defensive_rating)


def test_percentages_and_rates_stay_in_valid_ranges(game):
    for team in game:
        m = team.metrics
        assert 0.0 <= m.efg_pct <= 1.0
        assert 0.0 <= m.tov_pct <= 1.0
        assert 0.0 <= m.orb_pct <= 1.0
        assert m.ft_rate >= 0.0
        assert 0.0 <= m.fg3a_rate <= 1.0
        assert m.ast_to_ratio > 0.0


def test_no_accidental_player_level_aggregation(game):
    for team in game:
        # TeamGameStats carries exactly one components_for and one
        # components_against — no per-player breakdown leaks into this layer.
        assert hasattr(team, "components_for")
        assert not hasattr(team, "players")
        assert isinstance(team.components_for.points, int)


def test_action_counts_cover_every_action_in_the_source(game, raw_result):
    home, _ = game
    total_counted = sum(v for k, v in home.action_counts.items() if k != "dropped_no_team")
    assert total_counted == len(raw_result["actions"])


def test_points_reconcile_from_shots_and_free_throws(game):
    for team in game:
        c = team.components_for
        expected_points = 2 * (c.fgm - c.fg3m) + 3 * c.fg3m + c.ftm
        assert c.points == expected_points
