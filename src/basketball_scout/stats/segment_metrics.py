"""Generic possession-subset -> (components, metrics) — reused by every segment.

The one function that turns "some possessions" into the same
:class:`TeamGameComponents` / :class:`DerivedMetrics` shapes the season-level
engine already produces, by calling the identical pure functions in
``formulas.py``. No segment (quarter, clutch, score-state, ...) has its own
metric arithmetic — they only differ in *which possessions* get passed in.
This is what "preserve the existing ten core metrics" (enrichment brief §4)
means structurally: one formula implementation, many possession subsets.

Two derivations worth spelling out because they are not directly stored on
a :class:`Possession`:

* **assists** (``TeamGameComponents.ast``) = sum of ``raw_assist_count``
  across the team's own possessions in the subset — every provider assist
  action, whether or not this model could link it to a specific made shot
  (2026-08-15 management decision: AST/TO is an event-count statistic, so a
  shot-linkage failure must never remove a real assist from AST/TO). This
  is the same raw-action convention the season-level ``boxscore.py`` engine
  already uses, so segment-level and season-level AST counts reconcile
  exactly over the same scope — verified in
  ``tests/test_stats_segment_metrics.py``. Shot-level assisted/unassisted
  attribution (a stricter, separate concept — see
  ``scoring_sources.AssistedProfile`` and ``Possession.assisted_fgm``) is
  not affected by this and keeps its own explicit
  ``unresolved_assist_count``/rate.
* **defensive rebounds** (``TeamGameComponents.drb``) are never possessions.
  a team's DRB count for period doesn't live on any offensive possession —
  it's derived as *how many of the opponent's offensive possessions in this
  same subset ended with* ``ended_by == "defensive_rebound"``, which is
  exactly what a defensive rebound means. This requires *both* teams'
  possession subsets for the segment, which is why every function here
  takes both.

Personal fouls (``TeamGameComponents.pf``) are not tracked at the
possession level (out of scope for the ten core metrics) and are always 0
here — never used by any of the ten formulas, so this has no effect on any
computed metric; documented so it's never mistaken for a real foul count.
"""

from __future__ import annotations

from . import formulas
from .models import DerivedMetrics, TeamGameComponents
from .possession import Possession


def _defensive_rebounds_forced(opponent_offense_possessions: list[Possession]) -> int:
    return sum(1 for p in opponent_offense_possessions if p.ended_by == "defensive_rebound")


def build_segment_components(
    team_possessions: list[Possession],
    opponent_possessions: list[Possession],
) -> tuple[TeamGameComponents, TeamGameComponents]:
    """``(components_for, components_against)`` for one team over one possession subset.

    ``team_possessions``/``opponent_possessions`` must already be filtered
    to exactly the possessions of interest (a segment, a game, a window —
    this function doesn't know or care) and to each side's own offense.
    """
    team_drb = _defensive_rebounds_forced(opponent_possessions)
    opp_drb = _defensive_rebounds_forced(team_possessions)

    def _build(possessions: list[Possession], drb: int) -> TeamGameComponents:
        return TeamGameComponents(
            fgm=sum(p.fgm for p in possessions),
            fga=sum(p.fga for p in possessions),
            fg3m=sum(p.fg3m for p in possessions),
            fg3a=sum(p.fg3a for p in possessions),
            ftm=sum(p.ftm for p in possessions),
            fta=sum(p.fta for p in possessions),
            orb=sum(p.orb for p in possessions),
            drb=drb,
            ast=sum(p.raw_assist_count for p in possessions),
            tov=sum(1 for p in possessions if p.turnover),
            pf=0,
            points=sum(p.points for p in possessions),
        )

    return _build(team_possessions, team_drb), _build(opponent_possessions, opp_drb)


def compute_segment_metrics(
    team_possessions: list[Possession],
    opponent_possessions: list[Possession],
    *,
    segment_minutes: float | None,
) -> DerivedMetrics:
    """The ten core metrics for one possession subset, via the shared formulas.

    ``segment_minutes`` drives Pace only (``None`` -> omit Pace, per the
    enrichment brief's explicit guidance for clutch/score-state segments
    where a rigorous elapsed-time denominator isn't available). Every other
    metric is computed the same way regardless of segment.
    """
    components_for, components_against = build_segment_components(team_possessions, opponent_possessions)

    team_poss = formulas.estimate_possessions(components_for, components_against)
    opp_poss = formulas.estimate_possessions(components_against, components_for)

    off_rtg = formulas.offensive_rating(components_for.points, team_poss)
    def_rtg = formulas.defensive_rating(components_against.points, opp_poss)
    pace = None
    if segment_minutes is not None:
        pace = formulas.pace(team_poss, opp_poss, segment_minutes)

    return DerivedMetrics(
        offensive_rating=off_rtg,
        defensive_rating=def_rtg,
        net_rating=formulas.net_rating(off_rtg, def_rtg),
        pace=pace,
        efg_pct=formulas.effective_fg_pct(components_for.fgm, components_for.fg3m, components_for.fga),
        tov_pct=formulas.turnover_pct(components_for.tov, components_for.fga, components_for.fta),
        orb_pct=formulas.off_reb_pct(components_for.orb, components_against.drb),
        ft_rate=formulas.free_throw_rate(components_for.fta, components_for.fga),
        fg3a_rate=formulas.three_point_rate(components_for.fg3a, components_for.fga),
        ast_to_ratio=formulas.ast_to_ratio(components_for.ast, components_for.tov),
    )
