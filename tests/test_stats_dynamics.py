"""Score dynamics: ties, lead changes, largest lead/deficit, comebacks."""

from __future__ import annotations

from basketball_scout.stats.dynamics import build_comeback_profile, build_game_dynamics
from basketball_scout.stats.scoring_timeline import ScoringPlay


def play(team, home_after, away_after, action_id=0):
    return ScoringPlay(quarter=1, clock_s=300.0, team=team, points=1, is_field_goal=False,
                        home_score_after=home_after, away_score_after=away_after, action_id=action_id)


def test_times_tied_excludes_initial_state_and_counts_real_ties():
    # 2-0, 2-2 (tie #1), 4-2, 4-4 (tie #2), 6-4
    plays = [
        play("home", 2, 0, 1), play("away", 2, 2, 2), play("home", 4, 2, 3),
        play("away", 4, 4, 4), play("home", 6, 4, 5),
    ]
    dyn = build_game_dynamics(plays, team_side="home", team_won=True)
    assert dyn.times_tied == 2


def test_lead_change_through_a_tie_still_counts_as_one_change():
    # home leads (2-0) -> tied (2-2) -> away leads (2-3): one real lead change.
    plays = [play("home", 2, 0, 1), play("away", 2, 2, 2), play("away", 2, 3, 3)]
    dyn = build_game_dynamics(plays, team_side="home", team_won=False)
    assert dyn.lead_changes == 1
    assert dyn.times_tied == 1


def test_no_lead_change_when_same_team_extends_lead():
    plays = [play("home", 2, 0, 1), play("home", 4, 0, 2), play("home", 6, 0, 3)]
    dyn = build_game_dynamics(plays, team_side="home", team_won=True)
    assert dyn.lead_changes == 0


def test_largest_lead_and_deficit():
    plays = [play("home", 10, 0, 1), play("away", 10, 15, 2)]
    dyn = build_game_dynamics(plays, team_side="home", team_won=False)
    assert dyn.largest_lead == 10
    assert dyn.largest_deficit == 5


def test_trailed_and_led_by_10_plus_flags():
    plays = [play("away", 0, 12, 1), play("home", 25, 12, 2)]
    dyn = build_game_dynamics(plays, team_side="home", team_won=True)
    assert dyn.trailed_by_10_plus is True
    assert dyn.led_by_10_plus is True


def test_dynamics_symmetric_between_the_two_teams():
    plays = [play("home", 10, 0, 1), play("away", 10, 18, 2)]
    home_dyn = build_game_dynamics(plays, team_side="home", team_won=False)
    away_dyn = build_game_dynamics(plays, team_side="away", team_won=True)
    assert home_dyn.largest_lead == away_dyn.largest_deficit
    assert home_dyn.largest_deficit == away_dyn.largest_lead
    assert home_dyn.lead_changes == away_dyn.lead_changes
    assert home_dyn.times_tied == away_dyn.times_tied


# ---- Comeback / blown-lead profile -----------------------------------

def test_comeback_conversion_uses_correct_denominator():
    # 3 games trailed by 10+, 2 of them won (comebacks); 1 game never
    # trailed by 10+ at all -> must not appear in the denominator.
    from basketball_scout.stats.dynamics import GameDynamics

    games = [
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=True, led_by_10_plus=False, team_won=True),
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=True, led_by_10_plus=False, team_won=True),
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=True, led_by_10_plus=False, team_won=False),
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=False, led_by_10_plus=False, team_won=True),
    ]
    profile = build_comeback_profile(games)
    assert profile.games_trailing_10_plus == 3
    assert profile.comeback_wins_from_10_plus == 2
    assert profile.comeback_conversion_rate == 2 / 3


def test_blown_lead_rate_uses_correct_denominator():
    from basketball_scout.stats.dynamics import GameDynamics

    games = [
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=False, led_by_10_plus=True, team_won=False),
        GameDynamics(0, 0, 0, 0, trailed_by_10_plus=False, led_by_10_plus=True, team_won=True),
    ]
    profile = build_comeback_profile(games)
    assert profile.games_leading_10_plus == 2
    assert profile.losses_after_leading_10_plus == 1
    assert profile.blown_10_plus_lead_rate == 0.5


def test_comeback_rate_is_none_when_opportunity_never_occurred():
    from basketball_scout.stats.dynamics import GameDynamics

    games = [GameDynamics(0, 0, 0, 0, trailed_by_10_plus=False, led_by_10_plus=False, team_won=True)]
    profile = build_comeback_profile(games)
    assert profile.games_trailing_10_plus == 0
    assert profile.comeback_conversion_rate is None  # not fabricated as 0.0
