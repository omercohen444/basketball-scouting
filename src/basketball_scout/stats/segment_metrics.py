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


def _sum_components(components_list: list[TeamGameComponents]) -> TeamGameComponents:
    return TeamGameComponents(
        fgm=sum(c.fgm for c in components_list), fga=sum(c.fga for c in components_list),
        fg3m=sum(c.fg3m for c in components_list), fg3a=sum(c.fg3a for c in components_list),
        ftm=sum(c.ftm for c in components_list), fta=sum(c.fta for c in components_list),
        orb=sum(c.orb for c in components_list), drb=sum(c.drb for c in components_list),
        ast=sum(c.ast for c in components_list), tov=sum(c.tov for c in components_list),
        pf=sum(c.pf for c in components_list), points=sum(c.points for c in components_list),
    )


def build_canonical_aggregate_metrics(games: list) -> DerivedMetrics:
    """The canonical, volume-weighted season/window aggregate for the ten
    core metrics — reused for :mod:`evidence`'s ``EvidenceObject.value``
    (2026-08-15 management audit).

    ``games`` is a list of ``TeamGameStats`` (already-accepted per-game
    records — this operates on ``components_for``/``components_against``/
    ``game_minutes``, not possessions). Sums the raw components across
    every game FIRST, then calls the identical ``formulas.py`` functions
    ONCE on the totals — no new formula, no duplicated arithmetic, exactly
    the same pattern ``compute_segment_metrics`` already uses for a
    possession subset, just with pre-aggregated per-game components as the
    input instead of raw possessions.

    This is standard basketball-statistics convention (matching how
    ``profile.py``'s own ``_season_shot_mix`` already treats 2PA/3PA share
    — volume-weighted, not an average of nightly percentages — season
    eFG%/TOV%/ORtg/etc. are conventionally computed the same way, e.g.
    season eFG% = sum(FGM + 0.5*3PM) / sum(FGA), never the mean of each
    game's own eFG%). Deliberately DIFFERENT from
    ``stability.mean``/``profile.py``'s ``basic.metrics``, which both
    remain the unweighted per-game mean — that is a legitimate, separate,
    distribution-focused statistic, not replaced by this function.
    """
    if not games:
        return DerivedMetrics(
            offensive_rating=None, defensive_rating=None, net_rating=None, pace=None,
            efg_pct=None, tov_pct=None, orb_pct=None, ft_rate=None, fg3a_rate=None, ast_to_ratio=None,
        )

    total_for = _sum_components([g.components_for for g in games])
    total_against = _sum_components([g.components_against for g in games])
    total_minutes = sum(g.game_minutes for g in games)

    team_poss = formulas.estimate_possessions(total_for, total_against)
    opp_poss = formulas.estimate_possessions(total_against, total_for)

    off_rtg = formulas.offensive_rating(total_for.points, team_poss)
    def_rtg = formulas.defensive_rating(total_against.points, opp_poss)

    return DerivedMetrics(
        offensive_rating=off_rtg,
        defensive_rating=def_rtg,
        net_rating=formulas.net_rating(off_rtg, def_rtg),
        pace=formulas.pace(team_poss, opp_poss, total_minutes) if total_minutes > 0 else None,
        efg_pct=formulas.effective_fg_pct(total_for.fgm, total_for.fg3m, total_for.fga),
        tov_pct=formulas.turnover_pct(total_for.tov, total_for.fga, total_for.fta),
        orb_pct=formulas.off_reb_pct(total_for.orb, total_against.drb),
        ft_rate=formulas.free_throw_rate(total_for.fta, total_for.fga),
        fg3a_rate=formulas.three_point_rate(total_for.fg3a, total_for.fga),
        ast_to_ratio=formulas.ast_to_ratio(total_for.ast, total_for.tov),
    )
