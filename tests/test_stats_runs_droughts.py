"""Scoring runs and scoring/FG droughts."""

from __future__ import annotations

from basketball_scout.stats.runs_droughts import (
    DROUGHT_THRESHOLD_SECONDS,
    build_droughts_profile,
    build_runs_profile,
)
from basketball_scout.stats.scoring_timeline import ScoringPlay


def play(team, points, quarter=1, clock_s=300.0, is_fg=True, home_after=0, away_after=0, action_id=0):
    return ScoringPlay(
        quarter=quarter, clock_s=clock_s, team=team, points=points, is_field_goal=is_fg,
        home_score_after=home_after, away_score_after=away_after, action_id=action_id,
    )


# ---- Runs -------------------------------------------------------------

def test_consecutive_points_form_one_run_not_split():
    # home scores 12 straight (a 5-3-4 sequence of makes), then away scores.
    plays = [
        play("home", 5, home_after=5, action_id=1),
        play("home", 3, home_after=8, action_id=2),
        play("home", 4, home_after=12, action_id=3),
        play("away", 2, away_after=2, action_id=4),
    ]
    profile = build_runs_profile(plays, team_side="home")
    assert profile.largest_scoring_run_for == 12  # one run, not 5+3+4 counted separately


def test_runs_8_plus_counts_only_qualifying_runs():
    plays = [
        play("home", 8, home_after=8, action_id=1),
        play("away", 2, away_after=2, action_id=2),
        play("home", 5, home_after=13, action_id=3),  # below threshold
        play("away", 2, away_after=4, action_id=4),
    ]
    profile = build_runs_profile(plays, team_side="home")
    assert profile.runs_8_plus_for == 1


def test_runs_for_and_against_are_symmetric_from_opposite_perspectives():
    plays = [
        play("home", 8, action_id=1), play("away", 3, action_id=2), play("home", 4, action_id=3),
    ]
    home_profile = build_runs_profile(plays, team_side="home")
    away_profile = build_runs_profile(plays, team_side="away")
    assert home_profile.largest_scoring_run_for == away_profile.largest_scoring_run_against
    assert home_profile.largest_scoring_run_against == away_profile.largest_scoring_run_for


def test_period_boundary_does_not_end_a_run():
    plays = [
        play("home", 5, quarter=1, clock_s=10.0, action_id=1),
        play("home", 5, quarter=2, clock_s=590.0, action_id=2),  # crosses Q1->Q2, still home scoring
        play("away", 2, quarter=2, clock_s=580.0, action_id=3),
    ]
    profile = build_runs_profile(plays, team_side="home")
    assert profile.largest_scoring_run_for == 10  # one continuous run across the boundary


# ---- Droughts -----------------------------------------------------------

def test_drought_exactly_3_00_counts():
    # A single play at clock=420 in a 600s quarter produces two gaps: the
    # LEADING gap (period start 600 -> first score 420 = exactly 180s) and
    # the TRAILING gap (420 -> period end = 420s). Both are real droughts;
    # asserting count==2 specifically proves the boundary value 180 is
    # included (a stray `>` instead of `>=` would drop it to count==1).
    plays = [play("home", 2, quarter=1, clock_s=420.0, action_id=1)]
    profile = build_droughts_profile(plays, team_side="home", regulation_periods=4)
    assert profile.drought_count_3m_plus == 2
    assert DROUGHT_THRESHOLD_SECONDS == 180.0


def test_drought_just_under_3_00_does_not_count():
    # Leading gap = 600-421 = 179s (must NOT count); trailing gap = 421s
    # (must count). count==1 proves the 179s gap is correctly excluded.
    plays = [play("home", 2, quarter=1, clock_s=421.0, action_id=1)]
    profile = build_droughts_profile(plays, team_side="home", regulation_periods=4)
    assert profile.drought_count_3m_plus == 1
    assert profile.longest_scoring_drought_seconds == 421.0


def test_free_throw_ends_scoring_drought_but_not_fg_drought():
    plays = [
        play("home", 2, quarter=1, clock_s=595.0, is_fg=True, action_id=1),
        play("home", 1, quarter=1, clock_s=550.0, is_fg=False, action_id=2),  # FT: scoring gap 45s
        play("home", 2, quarter=1, clock_s=200.0, is_fg=True, action_id=3),
        play("home", 2, quarter=1, clock_s=5.0, is_fg=True, action_id=4),
    ]
    profile = build_droughts_profile(plays, team_side="home", regulation_periods=4)
    # Scoring gaps (FT counts): [5, 45, 350, 195, 5] -> longest 350.
    # FG-only gaps (FT excluded, so FG@595 -> FG@200 is one 395s gap that
    # the scoring sequence never sees because the FT at 550 splits it into
    # 45+350): [5, 395, 195, 5] -> longest 395.
    assert profile.longest_scoring_drought_seconds == 350.0
    assert profile.longest_fg_drought_seconds == 395.0
    assert profile.longest_fg_drought_seconds > profile.longest_scoring_drought_seconds


def test_drought_does_not_bridge_quarter_boundary():
    # Last score of Q1 at clock=155 (trailing gap 155s); first score of Q2
    # at clock=515 (leading gap 85s). If bridged, these would merge into one
    # 240s gap (>=180). Checked via the internal gap list (white-box) since
    # the public API only exposes count/longest, which alone can't
    # distinguish "two separate sub-threshold gaps" from "one merged one".
    from basketball_scout.stats.runs_droughts import _drought_gaps

    plays = [
        play("home", 2, quarter=1, clock_s=155.0, action_id=1),
        play("home", 2, quarter=2, clock_s=515.0, action_id=2),
    ]
    gaps = _drought_gaps(plays, regulation_periods=4, field_goals_only=False)
    assert 155.0 in gaps
    assert 85.0 in gaps
    assert 240.0 not in gaps


def test_ot_period_uses_5_minute_length_for_drought_gaps():
    # OT (quarter=5) is 5:00 long. No score until 1:00 remains -> gap = 240s.
    plays = [play("home", 2, quarter=5, clock_s=60.0, action_id=1)]
    profile = build_droughts_profile(plays, team_side="home", regulation_periods=4)
    assert profile.drought_count_3m_plus == 1  # 300-60 = 240s >= 180
