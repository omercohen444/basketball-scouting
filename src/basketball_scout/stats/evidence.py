"""Agent evidence object — one deterministic bundle of facts per
(team, metric, segment, perspective): league-relative context, stability,
recent-window deltas, and win/loss evidence, all over the same value.

This module performs no new metric arithmetic. It only extracts an
already-computed per-game value (via a caller-supplied ``extractor``) and
runs it through ``league_context.py``, ``stability.py`` and
``winloss.py``'s existing ``compute_signal_from_pairs`` — the exact same
functions already accepted for the season-level ten metrics. The output is
plain facts (numbers, sample counts, provenance strings) — never scout
prose, per the 2026-08-15 v2 brief §5.

``signals.py``-style candidate flags live at the bottom of this module
(kept together rather than a separate file — the flags are computed
directly from one already-built :class:`EvidenceObject` with no additional
data access, so splitting them out would only add an import for no
architectural benefit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .league_context import Direction, LeagueContext, build_league_context
from .models import TeamGameStats
from .profile import TeamGamePair, select_window
from .segment_metrics import build_canonical_aggregate_metrics
from .stability import StabilityProfile, build_stability_profile
from .winloss import ACTIONABLE, MetricSignal, compute_signal_from_pairs, is_agent_rankable

VALUE_DEFINITION_CANONICAL = "canonical_aggregate_ratio"
VALUE_DEFINITION_UNWEIGHTED_MEAN = "unweighted_mean_of_per_game_values"

Extractor = Callable[[TeamGameStats, Any], float | None]  # Any = GameEnrichment, avoids import cycle


@dataclass(frozen=True)
class RecentDelta:
    season_value: float | None
    last_10_value: float | None
    last_5_value: float | None
    last5_minus_season: float | None
    last10_minus_season: float | None
    last5_minus_last10: float | None
    sample_season: int
    sample_last_10: int
    sample_last_5: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_recent_delta(
    pairs: list[TeamGamePair], extractor: Extractor
) -> RecentDelta:
    season_pairs = select_window(pairs, "full_season")
    last10_pairs = select_window(pairs, "last_10")
    last5_pairs = select_window(pairs, "last_5")

    season_vals = [v for s, e in season_pairs if (v := extractor(s, e)) is not None]
    last10_vals = [v for s, e in last10_pairs if (v := extractor(s, e)) is not None]
    last5_vals = [v for s, e in last5_pairs if (v := extractor(s, e)) is not None]

    season_value = _mean(season_vals)
    last10_value = _mean(last10_vals)
    last5_value = _mean(last5_vals)

    return RecentDelta(
        season_value=season_value, last_10_value=last10_value, last_5_value=last5_value,
        last5_minus_season=(last5_value - season_value) if (last5_value is not None and season_value is not None) else None,
        last10_minus_season=(last10_value - season_value) if (last10_value is not None and season_value is not None) else None,
        last5_minus_last10=(last5_value - last10_value) if (last5_value is not None and last10_value is not None) else None,
        sample_season=len(season_vals), sample_last_10=len(last10_vals), sample_last_5=len(last5_vals),
    )


@dataclass(frozen=True)
class EvidenceObject:
    """``value`` and ``stability.mean`` answer two DIFFERENT questions and
    must never be assumed equal (2026-08-15 management audit):

    * ``value`` — "what is the team's season/segment statistic", the
      number a report would headline. For the ten core metrics this is now
      the CANONICAL, volume-weighted aggregate (sum of raw components
      across every eligible game, run through the same ``formulas.py``
      function once — see ``segment_metrics.build_canonical_aggregate_metrics``)
      when a raw-component path is available (currently: season-level
      evidence only, ``use_canonical_aggregate=True``). This matches
      standard basketball-statistics convention and v1's own precedent for
      volume-weighted ratios (``profile.py``'s ``_season_shot_mix``).
    * ``stability.mean`` — the unweighted arithmetic mean of each eligible
      game's own already-computed ratio, i.e. the distributional statistic
      "on a typical night, what does this look like" — deliberately
      per-game-equal-weighted, precisely because that is what stability/
      consistency analysis needs (a 10-shot game and a 90-shot game SHOULD
      count equally when asking "how consistent is this team", even though
      they should not count equally in the season total).

    These are numerically identical only when every game contributes the
    same volume (never true in real data) or when ``use_canonical_aggregate``
    is left ``False`` (segment-level evidence today — no raw-component
    aggregation path exists yet for possession-derived segments; ``value``
    there still equals ``stability.mean``, honestly labeled as such via
    ``value_definition``). ``value_definition``/``stability_definition`` in
    ``provenance`` state which convention is in effect for both fields
    explicitly — a consumer must never infer one from the other.
    """

    team_id: str
    metric: str
    segment_type: str
    segment_value: str
    perspective: str
    value: float | None
    sample_games: int
    league_context: LeagueContext | None
    stability: StabilityProfile
    recent: RecentDelta
    win_loss: MetricSignal
    source: str = "segev"
    definition_version: str = "enrichment-v2"
    value_definition: str = VALUE_DEFINITION_UNWEIGHTED_MEAN
    stability_definition: str = "unweighted_mean_of_per_game_values"

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "metric": self.metric,
            "segment_type": self.segment_type,
            "segment_value": self.segment_value,
            "perspective": self.perspective,
            "value": self.value,
            "sample_games": self.sample_games,
            "league_context": self.league_context.to_dict() if self.league_context else None,
            "stability": self.stability.to_dict(),
            "recent": self.recent.to_dict(),
            "win_loss": self.win_loss.to_dict(),
            "provenance": {
                "source": self.source,
                "definition_version": self.definition_version,
                "value_definition": self.value_definition,
                "stability_definition": self.stability_definition,
            },
        }


def _canonical_season_value(pairs: list[TeamGamePair], metric: str) -> float | None:
    """Volume-weighted season aggregate for one of the ten core metric
    field names, via ``build_canonical_aggregate_metrics``. ``metric`` must
    be a real ``DerivedMetrics`` attribute name — this is only ever called
    when ``use_canonical_aggregate=True``, which callers should only pass
    for season-level ten-core-metric evidence (see ``build_evidence``'s
    docstring)."""
    stats_only = [s for s, _ in select_window(pairs, "full_season")]
    canonical = build_canonical_aggregate_metrics(stats_only)
    return getattr(canonical, metric, None)


def build_evidence(
    team_id: str,
    metric: str,
    segment_type: str,
    segment_value: str,
    perspective: str,
    extractor: Extractor,
    league_pairs: dict[str, list[TeamGamePair]],
    *,
    direction: Direction = "neutral",
    use_canonical_aggregate: bool = False,
) -> EvidenceObject:
    """Build the full evidence bundle for one team/metric/segment.

    ``league_pairs`` must map every eligible team's own-perspective
    ``(TeamGameStats, GameEnrichment)`` pairs — used both for this team's
    own per-game values and (via each team's own season aggregate) for the
    league-context ranking.

    ``use_canonical_aggregate`` (2026-08-15 management audit): when
    ``True``, ``value`` (and every team's value feeding ``league_context``)
    is the volume-weighted canonical aggregate — sum raw components across
    every eligible game, then the same ``formulas.py`` function once — via
    ``segment_metrics.build_canonical_aggregate_metrics``. Only meaningful
    for season-level evidence over one of the ten core metric field names
    (that function only knows how to aggregate ``TeamGameStats.components_for``/
    ``components_against``/``game_minutes``, not possession-derived segment
    metrics). When ``False`` (the default, and the only option for
    segment-level evidence today — clutch, quarter, score-state, ... —
    since no raw-component aggregation path exists yet for those),
    ``value`` is the unweighted mean of per-game values, identical to
    ``stability.mean``. Either way, ``EvidenceObject.value_definition``
    states explicitly which convention was used — see the class docstring.
    """
    team_pairs = league_pairs[team_id]
    season_vals = [v for s, e in select_window(team_pairs, "full_season") if (v := extractor(s, e)) is not None]

    stability = build_stability_profile(season_vals)
    recent = build_recent_delta(team_pairs, extractor)

    win_loss_pairs = [(extractor(s, e), s.win) for s, e in team_pairs]
    win_loss = compute_signal_from_pairs(
        f"{segment_type}:{segment_value}:{metric}", ACTIONABLE, win_loss_pairs,
        lower_is_better=(direction == "lower_is_better"),
    )

    if use_canonical_aggregate:
        value = _canonical_season_value(team_pairs, metric)
        value_definition = VALUE_DEFINITION_CANONICAL
        league_values = {tid: _canonical_season_value(pairs, metric) for tid, pairs in league_pairs.items()}
    else:
        value = stability.mean
        value_definition = VALUE_DEFINITION_UNWEIGHTED_MEAN
        league_values = {
            tid: _mean([v for s, e in select_window(pairs, "full_season") if (v := extractor(s, e)) is not None])
            for tid, pairs in league_pairs.items()
        }
    league_context = build_league_context(team_id, league_values, direction=direction)

    return EvidenceObject(
        team_id=team_id, metric=metric, segment_type=segment_type, segment_value=segment_value,
        perspective=perspective, value=value, sample_games=stability.games,
        league_context=league_context, stability=stability, recent=recent, win_loss=win_loss,
        value_definition=value_definition,
    )


# ---- Candidate signal discovery (transparent flags, no composite score) ---
#
# 2026-08-15 management decision, binding: these four constants are approved
# ONLY as transparent, screening-stage candidate-discovery heuristics. They
# are NOT calibrated for, and must never be read as, any of:
#   - statistical truth or significance (no p-value is computed anywhere
#     in this package — see winloss.py's module docstring);
#   - a report-prominence decision;
#   - a scout-importance judgment.
# A `True` flag means only "cheap enough to compute, worth a second look" —
# it is a filter to shrink a search space, not a conclusion. A future
# agent/policy layer consuming these flags MUST additionally weigh the
# underlying facts the flag was derived from (actual values, sample size,
# league context, W/L evidence, stability) before treating anything as
# reportable; the flag alone is deliberately insufficient for that, by
# design, and each SignalFlags instance is only ever emitted alongside the
# full EvidenceObject it was computed from so that data is always at hand.

LEAGUE_EXTREME_PERCENTILE = 10.0  # <=10th or >=90th percentile
LARGE_EFFECT_SIZE = 0.8  # conventional Cohen's-d "large effect" threshold
RECENT_SHIFT_STD_MULTIPLE = 0.5  # last-5 delta at least half a std dev from season
STABLE_CV_THRESHOLD = 0.15  # low relative variability

HEURISTIC_DISCLAIMER = (
    "Screening heuristic only. Not a significance test, not a report-"
    "prominence decision, not a scout-importance judgment. Interpret only "
    "alongside the full EvidenceObject (value, sample, league_context, "
    "stability, win_loss) this flag was computed from."
)


@dataclass(frozen=True)
class SignalFlags:
    """Transparent candidate-discovery flags — see the module-level
    disclaimer above ``LEAGUE_EXTREME_PERCENTILE`` for the binding
    constraint on how these may and may not be used downstream.
    """

    league_extreme: bool | None  # None if no league context available
    win_loss_signal: bool | None  # None if not agent-rankable (undefined, not "false")
    recent_shift: bool | None  # None if std/delta unavailable
    stable_pattern: bool | None  # None if CV not computable
    disclaimer: str = HEURISTIC_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def compute_signal_flags(evidence: EvidenceObject) -> SignalFlags:
    """Transparent boolean candidate flags — deliberately not a composite
    score. Every threshold is a module-level constant, documented, and
    reported alongside the flags so a caller can recompute/adjust rather
    than trust an opaque number. See ``HEURISTIC_DISCLAIMER``: these are
    screening heuristics, not truth/significance/prominence/importance
    judgments — never sufficient on their own for report inclusion.
    """
    league_extreme = None
    if evidence.league_context is not None and evidence.league_context.percentile is not None:
        p = evidence.league_context.percentile
        league_extreme = p <= LEAGUE_EXTREME_PERCENTILE or p >= (100 - LEAGUE_EXTREME_PERCENTILE)

    win_loss_signal = None
    if is_agent_rankable(evidence.win_loss):
        win_loss_signal = abs(evidence.win_loss.effect_size) >= LARGE_EFFECT_SIZE

    recent_shift = None
    if evidence.recent.last5_minus_season is not None and evidence.stability.std is not None and evidence.stability.std > 0:
        recent_shift = abs(evidence.recent.last5_minus_season) >= RECENT_SHIFT_STD_MULTIPLE * evidence.stability.std

    stable_pattern = None
    if evidence.stability.coefficient_of_variation is not None:
        stable_pattern = abs(evidence.stability.coefficient_of_variation) <= STABLE_CV_THRESHOLD

    return SignalFlags(
        league_extreme=league_extreme, win_loss_signal=win_loss_signal,
        recent_shift=recent_shift, stable_pattern=stable_pattern,
    )
