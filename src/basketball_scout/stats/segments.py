"""Deterministic possession-level segment classification.

The single reusable classification layer behind every game-flow/clutch/
score-state/recent-form/home-away split. A segment is always assigned from
facts observable at the **start** of a possession — never split across a
possession's lifetime, and never re-derived from `userTime` (see
`possession.py`'s module docstring for why).

Segment taxonomy (`segment_type` / `segment_value`), matching the
enrichment brief §2's conceptual observation shape:

* ``quarter``   / ``Q1``..``Q4``, ``OT`` (all OT periods pooled — see below)
* ``half``      / ``1H``, ``2H`` (regulation only; OT is never folded in)
* ``clutch``    / ``clutch`` (Q4 or OT, clock <=5:00, |margin|<=5 at start)
* ``score_state``/ ``ahead_6_plus``, ``ahead_1_5``, ``tied``, ``behind_1_5``,
  ``behind_6_plus`` (mutually exclusive, offense-team perspective)
* ``close_score``/ ``close_score`` (|margin|<=5 at start, any period)
* ``late_close`` / ``late_close`` (Q4/OT AND |margin|<=5 at start)
* ``venue``     / ``home``, ``away``
* ``result``    / ``win``, ``loss``
* ``recent``    / ``full_season``, ``last_10``, ``last_5``

Nesting invariant (enforced by construction, checked in tests):
``clutch => late_close => close_score``. Clutch adds the <=5:00 clock
condition on top of ``late_close``; ``late_close`` adds the Q4/OT period
condition on top of ``close_score``.
"""

from __future__ import annotations

from .possession import Possession

CLUTCH_CLOCK_SECONDS = 300.0  # <=5:00 remaining
CLUTCH_MARGIN = 5
CLOSE_SCORE_MARGIN = 5
BIG_LEAD_MARGIN = 6


def quarter_segment(possession: Possession, regulation_periods: int) -> str:
    """``"Q1"``..``"Q4"`` for regulation, ``"OT"`` for any overtime period (pooled)."""
    if possession.quarter <= regulation_periods:
        return f"Q{possession.quarter}"
    return "OT"


def half_segment(possession: Possession, regulation_periods: int) -> str | None:
    """``"1H"``/``"2H"`` for regulation; ``None`` for OT (never folded into 2H)."""
    if possession.quarter > regulation_periods:
        return None
    half_size = regulation_periods // 2 or 1
    return "1H" if possession.quarter <= half_size else "2H"


def _offense_margin_at_start(possession: Possession) -> int | None:
    """Offense-team's score minus defense-team's score, at possession start.

    ``None`` if the start clock is unknown (can't be classified for
    clutch/close-score purposes without it, for clutch specifically; the
    score-state bins below don't need the clock and remain classifiable).
    """
    offense_before = (
        possession.score_before_home if possession.offense_team == "home" else possession.score_before_away
    )
    defense_before = (
        possession.score_before_away if possession.offense_team == "home" else possession.score_before_home
    )
    return offense_before - defense_before


def is_clutch(possession: Possession, regulation_periods: int) -> bool:
    """Q4/OT, clock <=5:00, |margin|<=5 — all at possession START."""
    if possession.quarter < regulation_periods:
        return False
    if possession.start_clock_s is None or possession.start_clock_s > CLUTCH_CLOCK_SECONDS:
        return False
    margin = _offense_margin_at_start(possession)
    return margin is not None and abs(margin) <= CLUTCH_MARGIN


def is_late_close(possession: Possession, regulation_periods: int) -> bool:
    """Q4/OT AND |margin|<=5 at possession start (no clock condition)."""
    if possession.quarter < regulation_periods:
        return False
    margin = _offense_margin_at_start(possession)
    return margin is not None and abs(margin) <= CLOSE_SCORE_MARGIN


def is_close_score(possession: Possession) -> bool:
    """|margin|<=5 at possession start, any period."""
    margin = _offense_margin_at_start(possession)
    return margin is not None and abs(margin) <= CLOSE_SCORE_MARGIN


def score_state_bin(possession: Possession) -> str | None:
    """One of the five mutually-exclusive score-state bins, offense perspective.

    ``None`` only if the margin itself is unknown (never happens in
    practice — score is always tracked — kept for defensive completeness).
    """
    margin = _offense_margin_at_start(possession)
    if margin is None:
        return None
    if margin >= BIG_LEAD_MARGIN:
        return "ahead_6_plus"
    if margin >= 1:
        return "ahead_1_5"
    if margin == 0:
        return "tied"
    if margin >= -CLOSE_SCORE_MARGIN + 1:  # -1..-5
        return "behind_1_5"
    return "behind_6_plus"


SCORE_STATE_BINS = ("ahead_6_plus", "ahead_1_5", "tied", "behind_1_5", "behind_6_plus")
CLOSE_SCORE_STATE_BINS = ("tied", "ahead_1_5", "behind_1_5")


def is_close_score_bin(bin_name: str) -> bool:
    """``close_score`` as a derived combination of three of the five bins (§7)."""
    return bin_name in CLOSE_SCORE_STATE_BINS
