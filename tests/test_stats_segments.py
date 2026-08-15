"""Segment classification: quarter/half/OT, clutch, score-state, nesting."""

from __future__ import annotations

from basketball_scout.stats.possession import Possession
from basketball_scout.stats.segments import (
    CLOSE_SCORE_STATE_BINS,
    half_segment,
    is_clutch,
    is_close_score,
    is_close_score_bin,
    is_late_close,
    quarter_segment,
    score_state_bin,
)


def make_possession(quarter=4, start_clock_s=200.0, score_before_home=90, score_before_away=88, offense="home"):
    return Possession(
        possession_index=0, quarter=quarter, offense_team=offense,
        defense_team="away" if offense == "home" else "home",
        start_clock_s=start_clock_s, end_clock_s=None, ended_by="made_fg",
        score_before_home=score_before_home, score_before_away=score_before_away,
    )


def test_quarter_segment_labels():
    assert quarter_segment(make_possession(quarter=1), 4) == "Q1"
    assert quarter_segment(make_possession(quarter=4), 4) == "Q4"
    assert quarter_segment(make_possession(quarter=5), 4) == "OT"
    assert quarter_segment(make_possession(quarter=6), 4) == "OT"


def test_half_segment_excludes_ot():
    assert half_segment(make_possession(quarter=1), 4) == "1H"
    assert half_segment(make_possession(quarter=2), 4) == "1H"
    assert half_segment(make_possession(quarter=3), 4) == "2H"
    assert half_segment(make_possession(quarter=4), 4) == "2H"
    assert half_segment(make_possession(quarter=5), 4) is None  # OT never folded into 2H


# ---- Clutch threshold boundaries ------------------------------------------

def test_clutch_at_exactly_5_00_is_clutch():
    p = make_possession(quarter=4, start_clock_s=300.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p, 4) is True


def test_clutch_at_5_01_is_not_clutch():
    p = make_possession(quarter=4, start_clock_s=301.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p, 4) is False


def test_clutch_margin_exactly_5_is_clutch():
    p = make_possession(quarter=4, start_clock_s=100.0, score_before_home=90, score_before_away=85)
    assert is_clutch(p, 4) is True


def test_clutch_margin_6_is_not_clutch():
    p = make_possession(quarter=4, start_clock_s=100.0, score_before_home=91, score_before_away=85)
    assert is_clutch(p, 4) is False


def test_clutch_requires_q4_or_ot():
    p = make_possession(quarter=3, start_clock_s=100.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p, 4) is False
    p_ot = make_possession(quarter=5, start_clock_s=100.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p_ot, 4) is True


# ---- Score-state bins -------------------------------------------------

def test_all_five_score_state_bins():
    assert score_state_bin(make_possession(score_before_home=96, score_before_away=88, offense="home")) == "ahead_6_plus"
    assert score_state_bin(make_possession(score_before_home=91, score_before_away=88, offense="home")) == "ahead_1_5"
    assert score_state_bin(make_possession(score_before_home=88, score_before_away=88, offense="home")) == "tied"
    assert score_state_bin(make_possession(score_before_home=85, score_before_away=88, offense="home")) == "behind_1_5"
    assert score_state_bin(make_possession(score_before_home=80, score_before_away=88, offense="home")) == "behind_6_plus"


def test_score_state_is_offense_perspective():
    # away team trailing by 8 while home leads -> from away's own offense
    # perspective (they are the ones about to possess), it's their deficit.
    p = make_possession(score_before_home=96, score_before_away=88, offense="away")
    assert score_state_bin(p) == "behind_6_plus"


def test_close_score_bin_derivation_matches_the_three_middle_bins():
    assert set(CLOSE_SCORE_STATE_BINS) == {"tied", "ahead_1_5", "behind_1_5"}
    assert is_close_score_bin("tied") is True
    assert is_close_score_bin("ahead_6_plus") is False
    assert is_close_score_bin("behind_6_plus") is False


# ---- Nesting invariant: clutch => late_close => close_score ----------------

def test_clutch_implies_late_close_implies_close_score():
    p = make_possession(quarter=4, start_clock_s=200.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p, 4) is True
    assert is_late_close(p, 4) is True
    assert is_close_score(p) is True


def test_late_close_true_but_clutch_false_outside_clock_window():
    p = make_possession(quarter=4, start_clock_s=400.0, score_before_home=90, score_before_away=88)
    assert is_clutch(p, 4) is False
    assert is_late_close(p, 4) is True
    assert is_close_score(p) is True


def test_close_score_true_but_late_close_false_outside_q4_ot():
    p = make_possession(quarter=2, start_clock_s=100.0, score_before_home=90, score_before_away=88)
    assert is_late_close(p, 4) is False
    assert is_close_score(p) is True
