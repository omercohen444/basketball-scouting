"""Per-game enrichment orchestration: raw actions -> one rich record per team.

The only module that wires together ``possession.py``, ``scoring_timeline.py``,
``segments.py``, ``segment_metrics.py``, ``scoring_sources.py``,
``runs_droughts.py`` and ``dynamics.py`` into a single :class:`GameEnrichment`
per team per game. Everything here is assembly — no new arithmetic; every
number traces back to one of those modules' pure functions.

Precomputes the "high-value set" of segments (enrichment brief §19):
overall, quarters, halves, clutch, the five score-state bins, plus
``close_score``/``late_close``. Recent-form windows, home/away, and W/L
comparisons are cross-game concerns handled by ``profile.py`` — this module
produces one game's full segment set, not a season aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import segments
from .dynamics import GameDynamics, build_game_dynamics
from .possession import Possession, build_possessions
from .runs_droughts import DroughtsProfile, RunsProfile, build_droughts_profile, build_runs_profile
from .scoring_sources import (
    AssistedProfile,
    FastBreakProfile,
    PointsOffTurnoversProfile,
    SecondChanceProfile,
    ShotScoringMix,
    build_assisted_profile,
    build_fast_break_profile,
    build_points_off_turnovers_profile,
    build_second_chance_profile,
    build_shot_scoring_mix,
)
from .scoring_timeline import build_scoring_timeline
from .segment_metrics import compute_segment_metrics
from .models import DerivedMetrics

_OTHER_SIDE = {"home": "away", "away": "home"}

# Segment (type, value, minutes-or-None) tuples precomputed for every game —
# the "high-value set", not a full combinatorial explosion (§19).
_QUARTER_SEGMENTS = [("quarter", f"Q{i}", 10.0) for i in range(1, 5)] + [("quarter", "OT", None)]
_HALF_SEGMENTS = [("half", "1H", 20.0), ("half", "2H", 20.0)]
_SCORE_STATE_SEGMENTS = [("score_state", b, None) for b in segments.SCORE_STATE_BINS]
_SITUATIONAL_SEGMENTS = [("clutch", "clutch", None), ("close_score", "close_score", None),
                          ("late_close", "late_close", None)]


@dataclass
class GameEnrichment:
    internal_game_id: str
    team_id: str
    opponent_id: str
    team_side: str
    win: bool
    game_date: str
    regulation_periods: int
    ot_periods: int

    # (segment_type, segment_value) -> (DerivedMetrics, sample dict)
    segment_metrics: dict[tuple[str, str], DerivedMetrics] = field(default_factory=dict)
    segment_samples: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)

    points_off_turnovers: PointsOffTurnoversProfile | None = None
    second_chance: SecondChanceProfile | None = None
    fast_break: FastBreakProfile | None = None
    assisted: AssistedProfile | None = None
    shot_mix: ShotScoringMix | None = None
    runs: RunsProfile | None = None
    droughts: DroughtsProfile | None = None
    dynamics: GameDynamics | None = None

    warnings: list[str] = field(default_factory=list)
    unresolved_assist_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "internal_game_id": self.internal_game_id,
            "team_id": self.team_id,
            "opponent_id": self.opponent_id,
            "team_side": self.team_side,
            "win": self.win,
            "game_date": self.game_date,
            "regulation_periods": self.regulation_periods,
            "ot_periods": self.ot_periods,
            "segment_metrics": {
                f"{t}:{v}": m.to_dict() for (t, v), m in self.segment_metrics.items()
            },
            "segment_samples": {f"{t}:{v}": s for (t, v), s in self.segment_samples.items()},
            "points_off_turnovers": self.points_off_turnovers.to_dict() if self.points_off_turnovers else None,
            "second_chance": self.second_chance.to_dict() if self.second_chance else None,
            "fast_break": self.fast_break.to_dict() if self.fast_break else None,
            "assisted": self.assisted.to_dict() if self.assisted else None,
            "shot_mix": self.shot_mix.to_dict() if self.shot_mix else None,
            "runs": self.runs.to_dict() if self.runs else None,
            "droughts": self.droughts.to_dict() if self.droughts else None,
            "dynamics": self.dynamics.to_dict() if self.dynamics else None,
            "warnings": list(self.warnings),
            "unresolved_assist_count": self.unresolved_assist_count,
        }


def _sample_dict(team_possessions: list[Possession]) -> dict[str, int]:
    return {
        "possessions_n": len(team_possessions),
        "fga_n": sum(p.fga for p in team_possessions),
        "fta_n": sum(p.fta for p in team_possessions),
        "turnover_n": sum(1 for p in team_possessions if p.turnover),
        "rebound_opportunity_n": sum(p.orb for p in team_possessions),
    }


def build_game_enrichment(
    result: dict[str, Any],
    *,
    internal_game_id: str,
    team_id: str,
    opponent_id: str,
    team_side: str,
    win: bool,
    game_date: str,
    regulation_periods: int,
    ot_periods: int,
) -> tuple[GameEnrichment, GameEnrichment]:
    """Build ``(this_team, opponent)`` enrichment records for one game.

    Returns both sides so a caller building a season dataset can store one
    call's output as the complete pair, mirroring ``engine.build_team_game_stats``.
    """
    actions = result["actions"]
    poss_result = build_possessions(actions, regulation_periods=regulation_periods)
    plays = build_scoring_timeline(actions)

    by_side: dict[str, list[Possession]] = {"home": [], "away": []}
    for p in poss_result.possessions:
        by_side[p.offense_team].append(p)

    def _one_side(side: str, this_team_id: str, this_opponent_id: str, this_win: bool) -> GameEnrichment:
        other = _OTHER_SIDE[side]
        team_poss = by_side[side]
        opp_poss = by_side[other]

        enrichment = GameEnrichment(
            internal_game_id=internal_game_id, team_id=this_team_id, opponent_id=this_opponent_id,
            team_side=side, win=this_win, game_date=game_date,
            regulation_periods=regulation_periods, ot_periods=ot_periods,
            warnings=list(poss_result.warnings), unresolved_assist_count=poss_result.unresolved_assist_count,
        )

        all_segments = list(_QUARTER_SEGMENTS) + list(_HALF_SEGMENTS) + list(_SCORE_STATE_SEGMENTS) + list(_SITUATIONAL_SEGMENTS)
        for seg_type, seg_value, minutes in all_segments:
            team_subset, opp_subset = _filter_for_segment(
                team_poss, opp_poss, seg_type, seg_value, regulation_periods
            )
            seg_minutes = minutes
            if seg_type == "quarter" and seg_value == "OT" and ot_periods > 0:
                seg_minutes = 5.0 * ot_periods
            elif seg_type == "quarter" and seg_value == "OT":
                seg_minutes = None  # no OT played; metrics will just be empty-sample
            key = (seg_type, seg_value)
            enrichment.segment_metrics[key] = compute_segment_metrics(team_subset, opp_subset, segment_minutes=seg_minutes)
            enrichment.segment_samples[key] = _sample_dict(team_subset)

        enrichment.points_off_turnovers = build_points_off_turnovers_profile(team_poss, opp_poss, games_n=1)
        enrichment.second_chance = build_second_chance_profile(team_poss, games_n=1)
        enrichment.fast_break = build_fast_break_profile(team_poss, games_n=1)
        enrichment.assisted = build_assisted_profile(
            team_poss, unresolved_assist_count=poss_result.unresolved_assist_count
        )
        enrichment.shot_mix = build_shot_scoring_mix(team_poss)
        enrichment.runs = build_runs_profile(plays, team_side=side)
        enrichment.droughts = build_droughts_profile(plays, team_side=side, regulation_periods=regulation_periods)
        enrichment.dynamics = build_game_dynamics(plays, team_side=side, team_won=this_win)

        return enrichment

    team_enrichment = _one_side(team_side, team_id, opponent_id, win)
    opponent_enrichment = _one_side(_OTHER_SIDE[team_side], opponent_id, team_id, not win)
    return team_enrichment, opponent_enrichment


def _filter_for_segment(
    team_poss: list[Possession],
    opp_poss: list[Possession],
    seg_type: str,
    seg_value: str,
    regulation_periods: int,
) -> tuple[list[Possession], list[Possession]]:
    """Select the possessions of one segment from BOTH sides' lists.

    Every segment except ``score_state`` is **symmetric**: quarter and half are
    properties of the clock, and clutch/close_score/late_close all test
    ``abs(margin)``, which is the same number from either bench. For those, the
    identical predicate applied to both lists already yields a matched pair —
    "team's Q1 offense" against "opponent's Q1 offense".

    ``score_state`` is the one asymmetric case, because it is *signed*: while
    this team is ahead by four the opponent is, by definition, behind by four.
    Applying the same predicate to both lists therefore paired "our ahead_1_5
    offense" with "their ahead_1_5 offense" — two disjoint stretches of clock,
    from different points in the game. Everything derived from the opponent
    subset was wrong as a result: ``defensive_rating`` and ``net_rating`` (pace
    is already ``None`` on these segments). One real team's ``behind_6_plus``
    net rating computed as -58.6 against a league mean of -1.3.

    The mirror negates the opponent's own margin rather than swapping bin
    names. That distinction matters: the bins are not symmetric (``behind_1_5``
    stops at -4 while ``ahead_1_5`` reaches +5 — see
    ``segments.score_state_bin_for_margin``), so a name-swap map is wrong on
    roughly half the cells.

    This fix moves no shipped evidence-pack value: the only score-state pack
    item is team-side ``efg_pct``, and the two opponent-dependent segment items
    sit on quarter/half, which are symmetric. Verified by rebuilding all 14
    packs — 0 hashes changed.
    """
    if seg_type == "quarter":
        pred = lambda p: segments.quarter_segment(p, regulation_periods) == seg_value
    elif seg_type == "half":
        pred = lambda p: segments.half_segment(p, regulation_periods) == seg_value
    elif seg_type == "score_state":
        team_pred = lambda p: segments.score_state_bin(p) == seg_value
        opp_pred = lambda p: segments.mirrored_score_state_bin(p) == seg_value
        return [p for p in team_poss if team_pred(p)], [p for p in opp_poss if opp_pred(p)]
    elif seg_type == "clutch":
        pred = lambda p: segments.is_clutch(p, regulation_periods)
    elif seg_type == "close_score":
        pred = lambda p: segments.is_close_score(p)
    elif seg_type == "late_close":
        pred = lambda p: segments.is_late_close(p, regulation_periods)
    else:
        raise ValueError(f"unknown segment_type {seg_type!r}")

    return [p for p in team_poss if pred(p)], [p for p in opp_poss if pred(p)]
