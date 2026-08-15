"""Run-response and after-own-timeout: crossing detection, no double-count,
truncation exclusion, and turnover taxonomy pass-through."""

from __future__ import annotations

from basketball_scout.stats.possession import Possession
from basketball_scout.stats.possession_context import (
    build_after_own_timeout_profile,
    build_run_response_profile,
    find_run_crossings,
)
from basketball_scout.stats.scoring_timeline import ScoringPlay
from basketball_scout.stats.turnover_taxonomy import build_turnover_taxonomy


def play(team, points, quarter=1, clock_s=300.0, action_id=0):
    return ScoringPlay(quarter=quarter, clock_s=clock_s, team=team, points=points, is_field_goal=True,
                        home_score_after=0, away_score_after=0, action_id=action_id)


def poss(offense_team, quarter=1, start_clock_s=280.0, points=0, turnover=False, fga=0, fgm=0, possession_index=0):
    return Possession(
        possession_index=possession_index, quarter=quarter, offense_team=offense_team,
        defense_team="away" if offense_team == "home" else "home",
        start_clock_s=start_clock_s, end_clock_s=start_clock_s - 10, ended_by="made_fg",
        points=points, turnover=turnover, fga=fga, fgm=fgm,
    )


# ---- Run crossings ----------------------------------------------------

def test_run_crossing_detected_once_at_exactly_8():
    plays = [play("away", 4, action_id=1), play("away", 4, action_id=2), play("home", 2, action_id=3)]
    crossings = find_run_crossings(plays)
    assert len(crossings) == 1
    assert crossings[0].team == "away"


def test_run_continuing_past_threshold_not_double_counted():
    # 4+4+4+4 = 16-0 run, single continuous sequence -> exactly one crossing
    plays = [play("away", 4, action_id=i) for i in range(1, 5)]
    crossings = find_run_crossings(plays)
    assert len(crossings) == 1


def test_two_separate_qualifying_runs_both_detected():
    plays = [
        play("away", 8, action_id=1),
        play("home", 8, action_id=2),
        play("away", 8, action_id=3),
    ]
    crossings = find_run_crossings(plays)
    assert len(crossings) == 3  # away run, home run, away run again -- three distinct runs


def test_run_below_threshold_not_detected():
    plays = [play("away", 3, action_id=1), play("home", 2, action_id=2)]
    assert find_run_crossings(plays) == []


# ---- Run response -------------------------------------------------------

def test_response_finds_next_possession_after_crossing():
    plays = [play("away", 8, quarter=1, clock_s=400.0, action_id=1)]
    home_poss = [poss("home", quarter=1, start_clock_s=390.0, points=3)]
    profile = build_run_response_profile(home_poss, plays, team_side="home")
    assert profile.opponent_runs_conceded == 1
    assert profile.responses_found == 1
    assert profile.response_points == 3


def test_response_excluded_when_no_possession_follows_truncation():
    plays = [play("away", 8, quarter=4, clock_s=5.0, action_id=1)]  # run ends near game's end
    home_poss: list[Possession] = []  # no subsequent possession recorded at all
    profile = build_run_response_profile(home_poss, plays, team_side="home")
    assert profile.opponent_runs_conceded == 1
    assert profile.responses_found == 0  # excluded, not fabricated
    assert profile.points_per_response_possession is None


def test_response_only_counts_possessions_for_the_run_on_team():
    # home is on the run; away's response should be measured, not home's.
    plays = [play("home", 8, quarter=1, clock_s=400.0, action_id=1)]
    away_poss = [poss("away", quarter=1, start_clock_s=395.0, points=2)]
    profile = build_run_response_profile(away_poss, plays, team_side="away")
    assert profile.responses_found == 1
    assert profile.response_points == 2


# ---- After-own-timeout --------------------------------------------------

def timeout_action(id_, quarter, team, quarter_time):
    return {"id": id_, "quarter": quarter, "type": "timeout", "quarterTime": quarter_time,
            "parameters": {"team": team, "player": None}}


def test_after_timeout_matches_next_possession_for_calling_team():
    actions = [timeout_action(1, 1, 1, "05:00")]
    all_poss = [poss("home", quarter=1, start_clock_s=290.0, points=2, fga=1, fgm=1)]
    profile = build_after_own_timeout_profile(actions, all_poss, team_side="home")
    assert profile.own_timeouts_with_team == 1
    assert profile.after_timeout_possessions_found == 1
    assert profile.points == 2


def test_after_timeout_excludes_timeouts_with_no_team():
    actions = [{"id": 1, "quarter": 1, "type": "timeout", "quarterTime": "05:00", "parameters": {"team": None}}]
    profile = build_after_own_timeout_profile(actions, [], team_side="home")
    assert profile.own_timeouts_with_team == 0


def test_after_timeout_excludes_when_next_possession_belongs_to_other_team():
    # A defensive/reactive timeout: the calling team does NOT get the ball next.
    actions = [timeout_action(1, 1, 1, "05:00")]
    all_poss = [poss("away", quarter=1, start_clock_s=290.0, points=2)]
    profile = build_after_own_timeout_profile(actions, all_poss, team_side="home")
    assert profile.own_timeouts_with_team == 1
    assert profile.after_timeout_possessions_found == 0  # no home possession matched


# ---- Blocker regression: intervening opponent possession must not be
# skipped (2026-08-15 final hardening — a real bug was found and fixed
# here: the earlier implementation searched only the calling team's own
# pre-filtered possession list, so it could never "see" an opponent
# possession that came first, and would wrongly match a later same-team
# possession instead). ------------------------------------------------

def test_adversarial_intervening_opponent_possession_is_not_skipped():
    """Team A (home) calls timeout. The very next possession belongs to
    Team B (away), which completes it. Team A gets the ball again later.
    Team A's LATER possession must NOT be counted as following its own
    timeout — the timeout's matched possession (away's) belongs to the
    opponent, so this timeout contributes nothing to home's profile.
    """
    actions = [timeout_action(1, 1, 1, "05:00")]
    away_first = poss("away", quarter=1, start_clock_s=290.0, points=5, possession_index=0)
    home_later = poss("home", quarter=1, start_clock_s=200.0, points=99, possession_index=1)
    all_poss = [away_first, home_later]  # full merged, chronological list

    home_profile = build_after_own_timeout_profile(actions, all_poss, team_side="home")
    assert home_profile.own_timeouts_with_team == 1
    assert home_profile.after_timeout_possessions_found == 0  # NOT matched to home_later
    assert home_profile.points == 0  # the 99-point marker must not leak in

    # And it correctly does NOT get attributed to away either (away didn't
    # call this timeout).
    away_profile = build_after_own_timeout_profile(actions, all_poss, team_side="away")
    assert away_profile.own_timeouts_with_team == 0


def test_timeout_called_mid_possession_does_not_match_the_already_open_possession():
    """A timeout that occurs strictly WITHIN an already-started possession
    (the possession began before the timeout and continues after it) must
    not be treated as "the possession following the timeout" — only a
    possession that actually STARTS at/after the timeout counts. Not a
    redefinition: this is the existing start_clock_s >= behavior, tested
    explicitly per the audit request.
    """
    # Possession starts at 320s (before the timeout); timeout at 300s is
    # mid-possession; the SAME possession is still open/continuing after.
    actions = [timeout_action(1, 1, 1, "05:00")]
    mid_possession = poss("home", quarter=1, start_clock_s=320.0, points=7, possession_index=0)
    next_possession = poss("home", quarter=1, start_clock_s=250.0, points=3, possession_index=1)
    all_poss = [mid_possession, next_possession]

    profile = build_after_own_timeout_profile(actions, all_poss, team_side="home")
    assert profile.after_timeout_possessions_found == 1
    assert profile.points == 3  # the NEXT possession, not the already-open one (7 pts)


def test_timeout_during_free_throw_trip_is_skipped_automatically():
    """A timeout mid-FT-trip: the trip's own possession started before the
    timeout, so it is not a candidate; whichever possession genuinely
    starts next is matched normally, with no special-case code needed."""
    actions = [timeout_action(1, 1, 2, "03:33")]  # away calls timeout mid-trip
    ft_trip_possession = poss("home", quarter=1, start_clock_s=333.0, points=1, possession_index=0)
    next_possession = poss("away", quarter=1, start_clock_s=200.0, points=2, possession_index=1)
    all_poss = [ft_trip_possession, next_possession]

    profile = build_after_own_timeout_profile(actions, all_poss, team_side="away")
    assert profile.after_timeout_possessions_found == 1
    assert profile.points == 2  # matched to away's real next possession, not the FT trip


def test_consecutive_timeouts_before_play_resumes_share_the_same_next_possession():
    """Two consecutive timeouts (both before any possession starts) both
    correctly match the SAME single next possession — no double counting
    of separate possessions, since there is only one."""
    actions = [timeout_action(1, 1, 1, "05:00"), timeout_action(2, 1, 2, "05:00")]
    only_possession = poss("home", quarter=1, start_clock_s=290.0, points=4, possession_index=0)
    profile = build_after_own_timeout_profile(actions, [only_possession], team_side="home")
    assert profile.own_timeouts_with_team == 1  # only home's own timeout counted for home
    assert profile.after_timeout_possessions_found == 1
    assert profile.points == 4


def test_timeout_immediately_before_quarter_end_finds_no_possession():
    actions = [timeout_action(1, 1, 1, "00:05")]
    profile = build_after_own_timeout_profile(actions, [], team_side="home")
    assert profile.own_timeouts_with_team == 1
    assert profile.after_timeout_possessions_found == 0  # excluded, not fabricated


def test_administrative_timeout_with_no_team_never_counted_for_either_side():
    actions = [{"id": 1, "quarter": 1, "type": "timeout", "quarterTime": "05:00", "parameters": {"team": None}}]
    all_poss = [poss("home", quarter=1, start_clock_s=290.0, points=2)]
    home_profile = build_after_own_timeout_profile(actions, all_poss, team_side="home")
    away_profile = build_after_own_timeout_profile(actions, all_poss, team_side="away")
    assert home_profile.own_timeouts_with_team == 0
    assert away_profile.own_timeouts_with_team == 0


def test_after_timeout_efg_and_tov_pct_computed_from_response_sample():
    actions = [timeout_action(1, 1, 1, "05:00")]
    home_poss = [poss("home", quarter=1, start_clock_s=290.0, points=2, fga=1, fgm=1)]
    profile = build_after_own_timeout_profile(actions, home_poss, team_side="home")
    assert profile.efg_pct == 1.0


# ---- Turnover taxonomy ---------------------------------------------------

def test_turnover_taxonomy_counts_verbatim_provider_types():
    actions = [
        {"type": "turnover", "parameters": {"team": 1, "type": "bad-pass"}},
        {"type": "turnover", "parameters": {"team": 1, "type": "bad-pass"}},
        {"type": "turnover", "parameters": {"team": 2, "type": "travelling"}},
        {"type": "shot", "parameters": {"team": 1, "made": "made"}},  # not a turnover, ignored
    ]
    home, away = build_turnover_taxonomy(actions)
    assert home == {"bad-pass": 2}
    assert away == {"travelling": 1}


def test_turnover_taxonomy_drops_no_team_entries():
    actions = [{"type": "turnover", "parameters": {"team": None, "type": "bad-pass"}}]
    home, away = build_turnover_taxonomy(actions)
    assert home == {} and away == {}


# ---- Timeout deduplication (2026-08-15 final semantic integrity check) ----
# Real bug found and fixed: two consecutive timeouts by the SAME team, with
# no possession between them, both resolved to the identical next
# possession — which was then counted (and its points/FGA/FGM summed)
# TWICE, once per timeout action, instead of once for the one real
# possession that actually occurred.

def test_consecutive_same_team_timeouts_do_not_double_count_the_shared_possession():
    actions = [
        timeout_action(1, 1, 1, "05:00"),
        timeout_action(2, 1, 1, "05:00"),  # same team calls a second timeout immediately
    ]
    only_possession = poss("home", quarter=1, start_clock_s=290.0, points=4, fga=2, fgm=1, possession_index=0)
    profile = build_after_own_timeout_profile(actions, [only_possession], team_side="home")
    assert profile.own_timeouts_with_team == 2  # both timeout actions are real, both counted
    assert profile.after_timeout_possessions_found == 1  # but only ONE real possession
    assert profile.points == 4  # not 8
    assert profile.fga == 2  # not 4
    assert profile.fgm == 1  # not 2


def test_consecutive_timeouts_from_different_teams_still_attribute_to_exactly_one_team():
    """Team A then Team B call consecutive timeouts before the one real
    possession resumes. That possession must be attributed to exactly the
    team whose offense it actually is — never duplicated across both
    teams' profiles, never duplicated within either."""
    actions = [timeout_action(1, 1, 1, "05:00"), timeout_action(2, 1, 2, "05:00")]
    only_possession = poss("home", quarter=1, start_clock_s=290.0, points=4, possession_index=0)
    home_profile = build_after_own_timeout_profile(actions, [only_possession], team_side="home")
    away_profile = build_after_own_timeout_profile(actions, [only_possession], team_side="away")

    assert home_profile.after_timeout_possessions_found == 1
    assert home_profile.points == 4
    assert away_profile.own_timeouts_with_team == 1  # away's own timeout is real
    assert away_profile.after_timeout_possessions_found == 0  # but the possession is home's, not away's
