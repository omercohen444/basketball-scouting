"""Segment possession filtering — and the one segment that is not symmetric.

Every segment except ``score_state`` is symmetric: quarter and half are clock
facts, and clutch/close/late-close test ``abs(margin)``, which reads the same
from either bench. ``score_state`` is signed, so a team's "ahead by four" has to
be paired against the opponent's "behind by four" — not against the opponent's
own "ahead by four", which is a different part of the game entirely.

These tests exist because that pairing was wrong, and because the values it
corrupts (``defensive_rating``, ``net_rating``) are exactly the ones a website
would put in a table.
"""

from __future__ import annotations

import pytest

from basketball_scout.stats.enrichment import _filter_for_segment
from basketball_scout.stats.possession import Possession
from basketball_scout.stats.segments import (
    offense_margin_at_start,
    score_state_bin,
    score_state_bin_for_margin,
)


def poss(*, quarter=4, start_clock_s=200.0, home=90, away=88, offense="home"):
    return Possession(
        possession_index=0, quarter=quarter, offense_team=offense,
        defense_team="away" if offense == "home" else "home",
        start_clock_s=start_clock_s, end_clock_s=None, ended_by="made_fg",
        score_before_home=home, score_before_away=away,
    )


def at_margin(margin: int, *, offense="home", **kw):
    """A possession whose offense leads by `margin` (negative = trailing)."""
    return poss(home=100 + margin, away=100, offense=offense, **kw) if offense == "home" \
        else poss(home=100, away=100 + margin, offense=offense, **kw)


# ---- the refactor must not have moved a boundary ----------------------------


@pytest.mark.parametrize("margin", range(-20, 21))
def test_score_state_bin_for_margin_matches_the_possession_form(margin):
    """The margin-only helper and the possession-taking wrapper must agree
    everywhere. This is what pins the refactor to zero behaviour change."""
    p = at_margin(margin)
    assert offense_margin_at_start(p) == margin
    assert score_state_bin_for_margin(margin) == score_state_bin(p)


def test_the_bins_are_known_to_be_asymmetric():
    """Documented, deliberately preserved: `behind_1_5` stops at -4 while
    `ahead_1_5` reaches +5, so `behind_6_plus` actually starts at -5.

    Correcting it would move a shipped pack value by up to 8pp and invalidate
    every stored report, so the bin stays and the display label is corrected
    instead. This test exists so the asymmetry can never be 'tidied up' by
    accident — it is load-bearing for the mirroring logic below.
    """
    assert score_state_bin_for_margin(5) == "ahead_1_5"
    assert score_state_bin_for_margin(-5) == "behind_6_plus"   # not behind_1_5
    assert score_state_bin_for_margin(-4) == "behind_1_5"


# ---- the fix ----------------------------------------------------------------


def test_score_state_pairs_a_team_with_the_opponents_mirrored_state():
    """The bug: while we were ahead 1-5, the opponent's possessions in that same
    stretch are `behind_1_5` from their own perspective. Filtering their list by
    `ahead_1_5` selected a different part of the game — or nothing at all."""
    team = [at_margin(3, offense="home")]
    # Same moment, opponent has the ball: they are behind by 3.
    opponent = [at_margin(-3, offense="away")]

    team_subset, opp_subset = _filter_for_segment(team, opponent, "score_state", "ahead_1_5", 4)

    assert team_subset == team
    assert opp_subset == opponent, (
        "the opponent's possessions from the mirrored state must be selected; "
        "before the fix this was empty"
    )


def test_score_state_does_not_select_the_opponents_own_same_named_state():
    """The inverse guard: an opponent possession where *they* were ahead 1-5 is
    a different moment and must not be paired with our ahead_1_5."""
    team = [at_margin(3, offense="home")]
    opponent_also_ahead = [at_margin(3, offense="away")]

    _, opp_subset = _filter_for_segment(team, opponent_also_ahead, "score_state", "ahead_1_5", 4)

    assert opp_subset == []


def test_mirroring_negates_the_margin_rather_than_swapping_bin_names():
    """A name-swap map (`ahead_1_5` <-> `behind_1_5`) is wrong precisely because
    the bins are asymmetric. At margin -5 the opponent is +5: `ahead_1_5`, not
    `ahead_6_plus`. A naive swap would put this possession in the wrong cell."""
    opponent_trailing_by_5 = [at_margin(-5, offense="away")]

    _, into_ahead_1_5 = _filter_for_segment([], opponent_trailing_by_5, "score_state", "ahead_1_5", 4)
    _, into_ahead_6_plus = _filter_for_segment([], opponent_trailing_by_5, "score_state", "ahead_6_plus", 4)

    # Their -5 mirrors to +5, which bins as ahead_1_5.
    assert into_ahead_1_5 == opponent_trailing_by_5
    assert into_ahead_6_plus == []


@pytest.mark.parametrize(
    "seg_type,seg_value",
    [("quarter", "Q4"), ("half", "2H"), ("clutch", "clutch"),
     ("close_score", "close_score"), ("late_close", "late_close")],
)
def test_every_other_segment_stays_symmetric(seg_type, seg_value):
    """Pins the invariant that score_state is the *only* asymmetric segment.

    If a future segment becomes signed, this test will not catch it — but it
    will stop anyone 'fixing' the symmetric ones by analogy.
    """
    team = [at_margin(2, offense="home", quarter=4, start_clock_s=100.0)]
    opponent = [at_margin(-2, offense="away", quarter=4, start_clock_s=100.0)]

    _, opp_subset = _filter_for_segment(team, opponent, seg_type, seg_value, 4)

    # abs(margin) and the clock read the same from both benches, so the
    # opponent's possession qualifies under the identical predicate.
    assert opp_subset == opponent


def test_unknown_segment_type_still_raises():
    with pytest.raises(ValueError, match="unknown segment_type"):
        _filter_for_segment([], [], "not_a_segment", "x", 4)
