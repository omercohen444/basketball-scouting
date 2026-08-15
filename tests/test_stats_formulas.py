"""Unit tests for the ten core deterministic team-game formulas.

Zero-denominator handling gets its own explicit test per metric — the
contract (PROJECT_SPEC.md / CLAUDE.md "never fabricate") is that a
zero-denominator ratio returns ``None``, never ``0.0`` or ``inf``.
"""

from __future__ import annotations

import pytest

from basketball_scout.stats.formulas import (
    ast_to_ratio,
    defensive_rating,
    effective_fg_pct,
    estimate_possessions,
    free_throw_rate,
    game_minutes,
    net_rating,
    off_reb_pct,
    offensive_rating,
    pace,
    three_point_rate,
    turnover_pct,
)
from basketball_scout.stats.models import TeamGameComponents


def make_components(**overrides) -> TeamGameComponents:
    base = dict(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0, ast=0, tov=0, pf=0, points=0)
    base.update(overrides)
    return TeamGameComponents(**base)


# ---- game_minutes -----------------------------------------------------

def test_game_minutes_regulation_only():
    assert game_minutes(4, 0) == 40.0


def test_game_minutes_with_overtime():
    assert game_minutes(4, 1) == 45.0
    assert game_minutes(4, 2) == 50.0


# ---- estimate_possessions ----------------------------------------------

def test_estimate_possessions_basic():
    team = make_components(fga=80, fgm=35, orb=10, tov=12, fta=20)
    opp = make_components(drb=40)
    poss = estimate_possessions(team, opp)
    # Poss = 80 - 1.07*(10/50)*(80-35) + 12 + 0.4*20 = 80 - 9.63 + 12 + 8 = 90.37
    assert round(poss, 2) == 90.37


def test_estimate_possessions_zero_rebound_denominator_does_not_raise():
    team = make_components(fga=10, fgm=5, orb=0, tov=1, fta=2)
    opp = make_components(drb=0)
    poss = estimate_possessions(team, opp)
    # ORB-weighting term treated as 0 when orb+opp_drb == 0
    assert poss == 10 - 0 + 1 + 0.4 * 2


# ---- offensive/defensive/net rating -------------------------------------

def test_offensive_rating_basic():
    assert offensive_rating(90, 90.0) == 100.0


def test_offensive_rating_zero_possessions_is_none():
    assert offensive_rating(0, 0.0) is None


def test_defensive_rating_zero_possessions_is_none():
    assert defensive_rating(80, 0.0) is None


def test_net_rating_combines_off_and_def():
    assert net_rating(110.0, 95.0) == 15.0


def test_net_rating_none_if_either_side_none():
    assert net_rating(None, 95.0) is None
    assert net_rating(110.0, None) is None


# ---- pace -----------------------------------------------------------------

def test_pace_basic():
    # both teams at 90 possessions, 40 minute game -> 40 * 90 / 40 = 90
    assert pace(90.0, 90.0, 40.0) == 90.0


def test_pace_zero_minutes_is_none():
    assert pace(90.0, 90.0, 0.0) is None


def test_pace_scales_with_overtime_minutes():
    # same total possession output, but game ran long (45 min incl. OT) ->
    # pace per-40 should be lower than the 40-minute case above.
    assert pace(95.0, 95.0, 45.0) < pace(90.0, 90.0, 40.0)


def test_pace_is_symmetric_regardless_of_call_order():
    # Management review 2026-08-15: pace is a game-level tempo estimate, not
    # per-team — the two possession args are summed before dividing, so
    # swapping team/opponent (as engine.py does for the away-side call) must
    # not change the result.
    assert pace(88.0, 92.0, 45.0) == pace(92.0, 88.0, 45.0)


def test_pace_uses_actual_ot_minutes_not_fixed_regulation():
    single_ot = pace(90.0, 90.0, game_minutes(4, 1))   # 45 real minutes
    double_ot = pace(90.0, 90.0, game_minutes(4, 2))   # 50 real minutes
    regulation = pace(90.0, 90.0, game_minutes(4, 0))  # 40 real minutes
    assert regulation == pytest.approx(90.0)
    assert single_ot == pytest.approx(40.0 * 90.0 / 45.0)
    assert double_ot == pytest.approx(40.0 * 90.0 / 50.0)
    assert regulation > single_ot > double_ot


# ---- eFG% -------------------------------------------------------------

def test_effective_fg_pct_weights_threes():
    # 10 FGA, 5 FGM all threes -> (5 + 0.5*5)/10 = 0.75
    assert effective_fg_pct(fgm=5, fg3m=5, fga=10) == 0.75


def test_effective_fg_pct_zero_fga_is_none():
    assert effective_fg_pct(fgm=0, fg3m=0, fga=0) is None


# ---- TOV% -------------------------------------------------------------

def test_turnover_pct_basic():
    # tov=10, fga=70, fta=20 -> plays = 70 + 8.8 + 10 = 88.8 -> 10/88.8
    result = turnover_pct(tov=10, fga=70, fta=20)
    assert round(result, 4) == round(10 / 88.8, 4)


def test_turnover_pct_zero_plays_is_none():
    assert turnover_pct(tov=0, fga=0, fta=0) is None


# ---- ORB% -------------------------------------------------------------

def test_off_reb_pct_basic():
    assert off_reb_pct(team_orb=10, opponent_drb=30) == 0.25


def test_off_reb_pct_zero_denominator_is_none():
    assert off_reb_pct(team_orb=0, opponent_drb=0) is None


# ---- FT Rate / 3PA Rate ------------------------------------------------

def test_free_throw_rate_basic():
    assert free_throw_rate(fta=20, fga=80) == 0.25


def test_free_throw_rate_zero_fga_is_none():
    assert free_throw_rate(fta=5, fga=0) is None


def test_three_point_rate_basic():
    assert three_point_rate(fg3a=30, fga=80) == 0.375


def test_three_point_rate_zero_fga_is_none():
    assert three_point_rate(fg3a=0, fga=0) is None


# ---- AST/TO -------------------------------------------------------------

def test_ast_to_ratio_basic():
    assert ast_to_ratio(ast=20, tov=10) == 2.0


def test_ast_to_ratio_zero_tov_is_none_not_infinity():
    assert ast_to_ratio(ast=15, tov=0) is None
