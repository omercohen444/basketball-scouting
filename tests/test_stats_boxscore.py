"""Segev action stream -> raw team-game components.

Synthetic actions here, deliberately: each test isolates one aggregation
rule (team-side inversion, blocked-shot FGA counting, unattributed rebound
types, offensive-foul-not-double-counted) rather than relying on the real
game fixture to exercise every edge case incidentally. The real fixture is
covered end-to-end in test_stats_integration_game136.py.
"""

from __future__ import annotations

from basketball_scout.stats.boxscore import build_components


def shot(team, made, points=2, action_type="jump-shot"):
    return {
        "type": "shot",
        "parameters": {"team": team, "made": made, "points": points, "type": action_type},
    }


def free_throw(team, made):
    return {"type": "freeThrow", "parameters": {"team": team, "made": "made" if made else "missed"}}


def rebound(team, reb_type):
    return {"type": "rebound", "parameters": {"team": team, "type": reb_type}}


def turnover(team):
    return {"type": "turnover", "parameters": {"team": team, "type": "ball-handling"}}


def assist(team):
    return {"type": "assist", "parameters": {"team": team}}


def foul(team):
    return {"type": "foul", "parameters": {"team": team, "type": "personal", "kind": "shooting"}}


def test_team_side_inversion_1_is_home_2_is_away():
    actions = [shot(1, "made"), shot(2, "made")]
    home, away, _ = build_components(actions)
    assert home.fgm == 1 and home.fga == 1
    assert away.fgm == 1 and away.fga == 1


def test_made_three_counts_both_fgm_and_fg3m_and_points():
    actions = [shot(1, "made", points=3)]
    home, _, _ = build_components(actions)
    assert home.fga == 1
    assert home.fgm == 1
    assert home.fg3a == 1
    assert home.fg3m == 1
    assert home.points == 3


def test_missed_three_counts_fga_and_fg3a_only():
    actions = [shot(1, "missed", points=3)]
    home, _, _ = build_components(actions)
    assert home.fga == 1
    assert home.fg3a == 1
    assert home.fgm == 0
    assert home.fg3m == 0
    assert home.points == 0


def test_blocked_shot_counts_as_fga_not_fgm():
    actions = [shot(1, "blocked", points=2)]
    home, _, _ = build_components(actions)
    assert home.fga == 1
    assert home.fgm == 0
    assert home.points == 0


def test_free_throws_count_attempts_and_makes_separately():
    actions = [free_throw(1, made=True), free_throw(1, made=True), free_throw(1, made=False)]
    home, _, _ = build_components(actions)
    assert home.fta == 3
    assert home.ftm == 2
    assert home.points == 2


def test_rebound_offensive_and_defensive_attributed_correctly():
    actions = [rebound(1, "offensive"), rebound(1, "defensive"), rebound(2, "defensive")]
    home, away, _ = build_components(actions)
    assert home.orb == 1
    assert home.drb == 1
    assert away.drb == 1
    assert away.orb == 0


def test_unknown_rebound_type_not_attributed_to_either_bucket():
    actions = [rebound(1, "team")]  # a real Segev value meaning "team rebound", not player ORB/DRB
    home, _, counts = build_components(actions)
    assert home.orb == 0
    assert home.drb == 0
    assert counts["rebound"] == 1  # still counted in provenance


def test_turnover_and_offensive_foul_are_not_double_counted():
    # Real Segev data: an offensive foul is followed by a *separate*
    # turnover action for the same team. Only the turnover action should
    # increment tov; the foul action must not add a second one.
    actions = [foul(1), turnover(1)]
    home, _, _ = build_components(actions)
    assert home.tov == 1
    assert home.pf == 1


def test_assist_counted_per_team():
    actions = [assist(1), assist(1), assist(2)]
    home, away, _ = build_components(actions)
    assert home.ast == 2
    assert away.ast == 1


def test_action_with_no_team_is_dropped_and_counted():
    actions = [{"type": "shot", "parameters": {"team": None, "made": "made", "points": 2}}]
    home, away, counts = build_components(actions)
    assert home.fga == 0 and away.fga == 0
    assert counts["dropped_no_team"] == 1


def test_action_counts_includes_types_not_used_arithmetically():
    actions = [shot(1, "made"), {"type": "substitution", "parameters": {"team": 1}}]
    _, _, counts = build_components(actions)
    assert counts["shot"] == 1
    assert counts["substitution"] == 1


def test_empty_actions_yields_zeroed_components():
    home, away, counts = build_components([])
    assert home.fga == 0 and home.points == 0
    assert away.fga == 0 and away.points == 0
    assert counts["dropped_no_team"] == 0
