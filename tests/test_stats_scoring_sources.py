"""Points off turnovers, second chance, fast break, assisted/unassisted, shot mix."""

from __future__ import annotations

import pytest

from basketball_scout.stats.possession import Possession
from basketball_scout.stats.scoring_sources import (
    build_assisted_profile,
    build_fast_break_profile,
    build_points_off_turnovers_profile,
    build_second_chance_profile,
    build_shot_scoring_mix,
)


def poss(**overrides):
    base = dict(
        possession_index=0, quarter=1, offense_team="home", defense_team="away",
        start_clock_s=300.0, end_clock_s=280.0, ended_by="made_fg", points=0,
        fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, turnover=False,
        followed_opponent_turnover=False, had_offensive_rebound=False,
        points_after_first_oreb=0, fast_break_points=0, assisted_fgm=0, unassisted_fgm=0,
        fg2m_assisted=0, fg2m_unassisted=0, fg3m_assisted=0, fg3m_unassisted=0,
    )
    base.update(overrides)
    return Possession(**base)


def test_points_off_turnovers_only_from_the_immediately_following_possession():
    possessions = [
        poss(points=3, followed_opponent_turnover=True),
        poss(points=2, followed_opponent_turnover=False),  # a later, unrelated possession
    ]
    profile = build_points_off_turnovers_profile(possessions, [], games_n=1)
    assert profile.points_off_turnovers == 3


def test_points_per_opponent_turnover_uses_correct_denominator():
    team = [poss(points=6, followed_opponent_turnover=True)]
    opponent = [poss(turnover=True), poss(turnover=True), poss(turnover=False)]
    profile = build_points_off_turnovers_profile(team, opponent, games_n=1)
    assert profile.opponent_turnovers == 2
    assert profile.points_per_opponent_turnover == 3.0


def test_second_chance_points_only_after_oreb_and_conversion_rate():
    possessions = [
        poss(had_offensive_rebound=True, points_after_first_oreb=2),  # scored after OREB
        poss(had_offensive_rebound=True, points_after_first_oreb=0),  # OREB but no score after
        poss(had_offensive_rebound=False, points_after_first_oreb=0),  # no OREB at all, irrelevant
    ]
    profile = build_second_chance_profile(possessions, games_n=1)
    assert profile.offensive_rebound_possessions == 2
    assert profile.second_chance_points == 2
    assert profile.second_chance_scoring_conversion == 0.5  # 1 of 2 OREB possessions scored


def test_fast_break_is_provider_defined_and_provenance_tagged():
    possessions = [poss(fast_break_points=2), poss(fast_break_points=0)]
    profile = build_fast_break_profile(possessions, games_n=1)
    assert profile.provider_fast_break_points == 2
    assert profile.source == "segev_provider_flag"


def test_assisted_profile_splits_by_2pt_and_3pt():
    possessions = [
        poss(assisted_fgm=1, unassisted_fgm=0, fg2m_assisted=1, fg2m_unassisted=0),
        poss(assisted_fgm=0, unassisted_fgm=1, fg3m_assisted=0, fg3m_unassisted=1),
    ]
    profile = build_assisted_profile(possessions, unresolved_assist_count=2)
    assert profile.assisted_fgm == 1 and profile.unassisted_fgm == 1
    assert profile.assisted_fgm_pct == 0.5
    assert profile.assisted_2pm == 1 and profile.unassisted_2pm == 0
    assert profile.assisted_3pm == 0 and profile.unassisted_3pm == 1
    assert profile.unresolved_assist_count == 2


def test_assisted_pct_is_none_for_zero_fgm():
    profile = build_assisted_profile([])
    assert profile.assisted_fgm_pct is None
    assert profile.unassisted_fgm_pct is None


def test_assisted_profile_exposes_provider_assist_provenance():
    """2026-08-15 management decision: shot-attribution and the raw AST/TO
    convention are separate concepts, but coverage must be explicit."""
    possessions = [
        poss(assisted_fgm=1, unassisted_fgm=0),
        poss(assisted_fgm=1, unassisted_fgm=0),
        poss(assisted_fgm=0, unassisted_fgm=1),
    ]
    profile = build_assisted_profile(possessions, unresolved_assist_count=3)
    assert profile.resolved_shot_attributed_assists == 2  # == assisted_fgm
    assert profile.total_provider_assists == 5  # 2 resolved + 3 unresolved
    assert profile.unresolved_assist_count == 3
    assert profile.unresolved_assist_rate == pytest.approx(3 / 5)


def test_unresolved_assist_rate_is_none_when_no_provider_assists_at_all():
    profile = build_assisted_profile([], unresolved_assist_count=0)
    assert profile.total_provider_assists == 0
    assert profile.unresolved_assist_rate is None


def test_shot_scoring_mix_reconciles_to_one():
    possessions = [poss(fga=10, fg3a=4, fgm=6, fg3m=2, ftm=3, points=17)]
    # points = 2*(6-2) + 3*2 + 3 = 8+6+3 = 17, consistent
    mix = build_shot_scoring_mix(possessions)
    assert mix.fg2a_share == 0.6
    assert mix.fg3a_share == 0.4
    assert mix.scoring_share_reconciles is True


def test_shot_scoring_mix_none_for_zero_denominators():
    mix = build_shot_scoring_mix([])
    assert mix.fg2a_share is None
    assert mix.fg3a_share is None
    assert mix.scoring_share_reconciles is None
