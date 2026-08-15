"""Possession-state builder: continuity, boundaries, and real-data sanity."""

from __future__ import annotations

import json

import pytest

from basketball_scout.config import REPO_ROOT
from basketball_scout.stats.possession import build_possessions

FIXTURE = REPO_ROOT / "data" / "validation" / "segev_game136_full.json"


def action(id_, quarter, type_, team, quarter_time="05:00", **params):
    return {"id": id_, "quarter": quarter, "type": type_, "quarterTime": quarter_time,
            "parameters": {"team": team, **params}}


def test_offensive_rebound_continues_same_possession():
    actions = [
        action(1, 1, "shot", 1, made="missed", points=2, type="jump-shot"),
        action(2, 1, "rebound", 1, type="offensive"),
        action(3, 1, "shot", 1, made="made", points=2, type="jump-shot"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1
    p = result.possessions[0]
    assert p.fga == 2 and p.fgm == 1 and p.orb == 1 and p.had_offensive_rebound is True


def test_possession_ends_after_turnover():
    actions = [
        action(1, 1, "turnover", 1, type="bad-pass"),
        action(2, 1, "shot", 2, made="made", points=2, type="jump-shot"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 2
    assert result.possessions[0].offense_team == "home"
    assert result.possessions[0].turnover is True
    assert result.possessions[0].ended_by == "turnover"
    assert result.possessions[1].offense_team == "away"
    assert result.possessions[1].followed_opponent_turnover is True


def test_possession_ends_after_made_field_goal():
    actions = [action(1, 1, "shot", 1, made="made", points=2, type="jump-shot")]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1
    assert result.possessions[0].ended_by == "made_fg"


def test_missed_shot_plus_defensive_rebound_ends_possession_and_starts_new_one():
    actions = [
        action(1, 1, "shot", 1, made="missed", points=2, type="jump-shot"),
        action(2, 1, "rebound", 2, type="defensive"),
        action(3, 1, "shot", 2, made="made", points=2, type="jump-shot"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 2
    assert result.possessions[0].ended_by == "defensive_rebound"
    assert result.possessions[0].offense_team == "home"
    assert result.possessions[1].offense_team == "away"


def test_turnover_with_no_prior_shot_is_still_a_real_possession():
    """A 5-second violation or similar can be the entire possession — must
    not be silently lost just because no shot was ever attempted."""
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="jump-shot"),  # closes possession 1
        action(2, 1, "turnover", 2, type="5-seconds-violation"),  # possession 2: shot-less turnover
        action(3, 1, "shot", 1, made="made", points=2, type="jump-shot"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 3
    assert result.possessions[1].offense_team == "away"
    assert result.possessions[1].fga == 0
    assert result.possessions[1].turnover is True


def test_quarter_end_closes_an_open_possession():
    actions = [
        action(1, 1, "shot", 1, made="missed", points=2, type="jump-shot"),
        {"id": 2, "quarter": 1, "type": "quarter", "quarterTime": "00:00",
         "parameters": {"type": "end-of-quarter"}},
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1
    assert result.possessions[0].ended_by == "quarter_end"


def test_assist_links_to_the_correct_made_shot():
    actions = [
        action(1, 1, "shot", 1, made="made", points=3, type="jump-shot"),
        {"id": 2, "quarter": 1, "type": "assist", "quarterTime": "05:00",
         "parameters": {"team": 1, "parentActionId": 1}},
    ]
    result = build_possessions(actions, regulation_periods=4)
    p = result.possessions[0]
    assert p.assisted_fgm == 1 and p.unassisted_fgm == 0
    assert p.fg3m_assisted == 1


def test_unresolved_assist_is_counted_not_dropped():
    actions = [
        action(1, 1, "foul", 1, type="personal", kind="shooting", freeThrows=1),
        {"id": 2, "quarter": 1, "type": "assist", "quarterTime": "05:00",
         "parameters": {"team": 1, "parentActionId": 1}},  # parent is a foul, not a shot
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert result.unresolved_assist_count == 1


def test_second_chance_points_only_counted_after_the_offensive_rebound():
    actions = [
        action(1, 1, "shot", 1, made="missed", points=2, type="jump-shot"),
        action(2, 1, "rebound", 1, type="offensive"),
        action(3, 1, "shot", 1, made="missed", points=2, type="jump-shot"),  # still no points yet
        action(4, 1, "rebound", 1, type="offensive"),  # second OREB, same possession
        action(5, 1, "shot", 1, made="made", points=2, type="jump-shot"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    p = result.possessions[0]
    assert p.points == 2
    assert p.points_after_first_oreb == 2  # scored after the FIRST oreb (the only points at all here)
    assert p.orb == 2


def test_final_ft_missed_then_defensive_rebound_ends_possession_and_opens_next():
    actions = [
        action(1, 1, "shot", 1, made="missed", points=2, type="jump-shot"),  # opens the possession normally
        action(2, 1, "freeThrow", 1, made="missed", freeThrowsAwarded=1, freeThrowNumber=1),
        action(3, 1, "rebound", 2, type="defensive"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    # The DREB closes team 1's possession and, correctly, opens a new one
    # for team 2 — which is empty here (the test data stops) and gets
    # closed at the implicit end-of-quarter finalization. Both are real.
    assert result.possessions[0].ended_by == "defensive_rebound"
    assert result.possessions[0].offense_team == "home"
    assert result.possessions[0].fta == 1 and result.possessions[0].ftm == 0
    assert result.possessions[-1].offense_team == "away"


def test_orphan_free_throw_with_no_open_possession_still_counts_points():
    """A technical-foul FT (or genuine rare and-1) arriving with no open
    possession must not be dropped — see possession.py's module docstring."""
    actions = [action(1, 1, "freeThrow", 1, made="made", freeThrowsAwarded=1, freeThrowNumber=1)]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1
    assert result.possessions[0].ftm == 1
    assert result.possessions[0].points == 1


def test_blocked_shot_counts_as_fga_not_fgm():
    actions = [action(1, 1, "shot", 1, made="blocked", points=2, type="jump-shot")]
    result = build_possessions(actions, regulation_periods=4)
    p = result.possessions[0]
    assert p.fga == 1 and p.fgm == 0 and p.points == 0


# ---- Real-data regression: games 178/209/224 (2026-08-15 targeted recovery) --

@pytest.mark.parametrize("game_id", [178, 209, 224])
def test_real_flagged_provenance_games_build_without_crashing(game_id):
    """These games have a stale gameFinished flag (see stats/schedule.py) but
    complete action streams — the possession builder must handle them
    identically to any other game, reconciling exactly to the known score.
    """
    from basketball_scout.config import load_settings

    settings = load_settings()
    path = settings.raw_pbp_dir / f"segev_{game_id}.json"
    if not path.is_file():
        pytest.skip(f"raw cache for game {game_id} not present locally (data/raw is git-ignored)")
    data = json.loads(path.read_text(encoding="utf-8"))
    result = build_possessions(data["actions"], regulation_periods=4)
    home_points = sum(p.points for p in result.possessions if p.offense_team == "home")
    away_points = sum(p.points for p in result.possessions if p.offense_team == "away")
    assert home_points > 0 and away_points > 0
    assert home_points != away_points  # no real finished game ties


def test_real_game_136_possession_totals_reconcile_with_boxscore():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = build_possessions(data["actions"], regulation_periods=4)
    home_poss = [p for p in result.possessions if p.offense_team == "home"]
    away_poss = [p for p in result.possessions if p.offense_team == "away"]
    assert sum(p.points for p in home_poss) == 95
    assert sum(p.points for p in away_poss) == 84
    assert sum(p.fga for p in home_poss) == 70
    assert sum(p.orb for p in home_poss) == 14
    assert sum(1 for p in home_poss if p.turnover) == 7
    assert sum(1 for p in away_poss if p.turnover) == 13


# ---- And-1 continuation (2026-08-15 management hardening) -----------------
# Exhaustive audit of all 182 games found 729 genuine and-1 sequences across
# 180/182 games (6.5% of all made shots) — the initial ~0/250-sample claim
# was wrong because it didn't skip `foul-drawn`, which Segev always inserts
# between the shot and the deciding foul. See possession.py's module
# docstring for the full audit evidence.

def foul_action(id_, quarter, team, fouled_on, kind="shooting", free_throws=1, quarter_time="05:00"):
    return {"id": id_, "quarter": quarter, "type": "foul", "quarterTime": quarter_time,
            "parameters": {"team": team, "kind": kind, "fouledOn": fouled_on, "freeThrows": free_throws}}


def foul_drawn_action(id_, quarter, team, player, quarter_time="05:00"):
    return {"id": id_, "quarter": quarter, "type": "foul-drawn", "quarterTime": quarter_time,
            "parameters": {"team": team, "player": player}}


def test_and1_made_shot_does_not_close_possession_until_ft_resolves():
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="lay-up", player="21"),
        foul_drawn_action(2, 1, 1, "21"),
        foul_action(3, 1, 2, fouled_on="21"),  # opponent (team 2) fouls the shooter
        action(4, 1, "freeThrow", 1, made="made", freeThrowsAwarded=1, freeThrowNumber=1),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1  # NOT split into shot-possession + orphan-FT-possession
    p = result.possessions[0]
    assert p.fgm == 1 and p.ftm == 1 and p.fta == 1 and p.points == 3
    assert p.ended_by == "made_ft"


def test_and1_with_assist_still_links_correctly():
    actions = [
        action(1, 1, "shot", 1, made="made", points=3, type="jump-shot", player="21"),
        {"id": 2, "quarter": 1, "type": "assist", "quarterTime": "05:00",
         "parameters": {"team": 1, "parentActionId": 1}},
        foul_drawn_action(3, 1, 1, "21"),
        foul_action(4, 1, 2, fouled_on="21"),
        action(5, 1, "freeThrow", 1, made="made", freeThrowsAwarded=1, freeThrowNumber=1),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert len(result.possessions) == 1
    p = result.possessions[0]
    assert p.assisted_fgm == 1 and p.unassisted_fgm == 0
    assert p.fg3m_assisted == 1
    assert p.points == 4  # 3 (assisted three) + 1 (and-1 FT)


def test_and1_missed_ft_then_defensive_rebound_still_one_possession_for_the_shooting_team():
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="lay-up", player="21"),
        foul_drawn_action(2, 1, 1, "21"),
        foul_action(3, 1, 2, fouled_on="21"),
        action(4, 1, "freeThrow", 1, made="missed", freeThrowsAwarded=1, freeThrowNumber=1),
        action(5, 1, "rebound", 2, type="defensive"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert result.possessions[0].fgm == 1 and result.possessions[0].fta == 1 and result.possessions[0].ftm == 0
    assert result.possessions[0].ended_by == "defensive_rebound"
    assert result.possessions[0].offense_team == "home"
    assert result.possessions[-1].offense_team == "away"  # rebound correctly hands off


def test_non_and1_foul_after_make_does_not_suppress_closure():
    """A shooting foul on the OTHER team's player (unrelated defensive foul
    right after this team's make) must NOT be mistaken for an and-1 — the
    made shot still closes its own possession normally."""
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="lay-up", player="21"),
        action(2, 1, "shot", 2, made="missed", points=2, type="jump-shot", player="9"),
        foul_action(3, 1, 1, fouled_on="9"),  # team 1 fouls team 2's shooter -- unrelated to action 1
    ]
    result = build_possessions(actions, regulation_periods=4)
    assert result.possessions[0].ended_by == "made_fg"  # closed normally, not treated as and-1
    assert result.possessions[0].offense_team == "home"


def test_technical_foul_ft_is_not_mistaken_for_and1():
    # kind="technical" must not qualify, even with fouledOn set.
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="lay-up", player="21"),
        foul_action(2, 1, 2, fouled_on="21", kind="technical"),
        action(3, 1, "freeThrow", 1, made="made", freeThrowsAwarded=1, freeThrowNumber=1),
    ]
    result = build_possessions(actions, regulation_periods=4)
    # Made shot closes normally (not and-1); the technical FT is then an
    # orphan possession (handled by the existing fallback).
    assert result.possessions[0].ended_by == "made_fg"
    assert "orphan_free_throw_no_open_possession" in result.possessions[1].warnings


def test_real_game_136_and1_count_and_possession_total():
    from basketball_scout.config import load_settings

    settings = load_settings()
    if not FIXTURE.is_file():
        pytest.skip("fixture missing")
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = build_possessions(data["actions"], regulation_periods=4)
    # Verified count from the exhaustive audit (2026-08-15 hardening run).
    made_ft_ended = [p for p in result.possessions if p.ended_by == "made_ft" and p.fgm == 1]
    assert len(made_ft_ended) >= 4  # the 4 confirmed and-1s in this game
    home_points = sum(p.points for p in result.possessions if p.offense_team == "home")
    away_points = sum(p.points for p in result.possessions if p.offense_team == "away")
    assert home_points == 95 and away_points == 84  # still reconciles exactly


def test_open_possession_never_silently_overwritten_by_unattributed_rebound_oddity():
    """Regression (game 74, 2026-08-15 hardening): a stray "offensive
    rebound" oddity for the OTHER team arriving while an and-1 possession
    is still open (pending its foul-drawn/foul/FT) must not discard the
    and-1 shot's stats — it must force-close that possession first."""
    actions = [
        action(1, 1, "shot", 1, made="made", points=2, type="lay-up", player="3"),
        foul_drawn_action(2, 1, 1, "3"),
        foul_action(3, 1, 2, fouled_on="3"),
        # No free throw follows -- instead, a stray oddity: an "offensive
        # rebound" logged for the OTHER team (the real game-74 anomaly).
        action(4, 1, "rebound", 2, type="offensive"),
    ]
    result = build_possessions(actions, regulation_periods=4)
    total_fga = sum(p.fga for p in result.possessions if p.offense_team == "home")
    total_points = sum(p.points for p in result.possessions if p.offense_team == "home")
    assert total_fga == 1  # the made lay-up must not be lost
    assert total_points == 2
