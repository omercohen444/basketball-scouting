"""Deterministic win-vs-loss statistical comparison and signal ranking.

Given one team's full :class:`TeamGameStats` history, computes for each of
the ten core metrics: average in wins, average in losses, the raw
difference, sample counts, and a standardized effect size — then ranks
metrics by the size of that standardized effect, restricted by default to
the metrics a scout can actually act on. This is the structured,
code-computed input the (out-of-scope, later-stage) Data Analysis Agent is
designed to read; see PROJECT_SPEC.md's agent table. No LLM chooses or
calculates anything here.

Why a standardized effect, not raw difference (2026-08-15 management review)
------------------------------------------------------------------------------

The ten metrics live on incompatible scales — Net Rating swings by tens of
points, eFG% by hundredths. Ranking by raw ``win_mean - loss_mean`` always
puts Net Rating at the top: it is close to *definitionally* different
between wins and losses (a team that outscored its opponent per-possession
won), which is a tautological finding, not a scouting insight. Comparing
metrics on a common scale needs a standardized effect:

    effect = (win_mean - loss_mean) / pooled_standard_deviation

using the classic pooled sample standard deviation (Cohen's-d style,
ddof=1):

    pooled_sd = sqrt(((n_wins-1)*var_wins + (n_losses-1)*var_losses)
                      / (n_wins + n_losses - 2))

This is an **effect-size heuristic for ranking, not a significance test** —
no p-value, no hypothesis test, nothing claiming statistical significance is
computed anywhere in this module. ``effect_size`` is ``None`` (never a
fabricated number) when it cannot be honestly computed:

* either group has fewer than 2 sampled values (sample variance is undefined
  with n<2) — ``effect_note="insufficient_sample_for_variance"``;
* the pooled variance is exactly zero (every win and every loss produced the
  identical value for that metric — a real possibility for a metric with
  little game-to-game variation) — ``effect_note="zero_pooled_variance"``,
  because dividing by zero would fabricate an infinite effect, not report a
  real one.

Sample sufficiency for reporting purposes remains a separate, simpler,
documented threshold (``MIN_SUFFICIENT_SAMPLE``) — not a statistical test
either; see below.

Metric categories
------------------

* **Outcome/context** (``offensive_rating``, ``defensive_rating``,
  ``net_rating``, ``pace``) describe *what happened*, not what a team can
  change going into the next game. Their win/loss comparison is still
  computed (useful for an overview), but they are excluded from the default
  scouting ranking precisely because they are near-tautological ("the team
  that scored more per possession tended to win").
* **Actionable factors** (``efg_pct``, ``tov_pct``, ``orb_pct``, ``ft_rate``,
  ``fg3a_rate``, ``ast_to_ratio``) are the things a game plan can target.
  :func:`rank_actionable_signals` is the default entry point for the
  scouting signal ranking and only ever ranks this set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .models import DerivedMetrics, TeamGameStats

# Below this many sampled wins *and* losses, `sample_sufficient` is False.
# Deliberately simple and documented rather than a statistical test — see
# module docstring. Separate from the (stricter, n>=2) minimum needed to
# compute a variance for the standardized effect at all.
MIN_SUFFICIENT_SAMPLE = 5

OUTCOME_CONTEXT = "outcome_context"
ACTIONABLE = "actionable"

_METRIC_GETTERS: dict[str, Callable[[DerivedMetrics], float | None]] = {
    "offensive_rating": lambda m: m.offensive_rating,
    "defensive_rating": lambda m: m.defensive_rating,
    "net_rating": lambda m: m.net_rating,
    "pace": lambda m: m.pace,
    "efg_pct": lambda m: m.efg_pct,
    "tov_pct": lambda m: m.tov_pct,
    "orb_pct": lambda m: m.orb_pct,
    "ft_rate": lambda m: m.ft_rate,
    "fg3a_rate": lambda m: m.fg3a_rate,
    "ast_to_ratio": lambda m: m.ast_to_ratio,
}

def category_for(metric: str) -> str:
    """``OUTCOME_CONTEXT`` for the four near-tautological metrics, else ``ACTIONABLE``.

    Accepts a bare metric name (``"net_rating"``) or a segment-qualified signal
    name (``"clutch:clutch:efg_pct"``), so callers that build ids as
    ``f"{segment_type}:{segment_value}:{metric}"`` can pass them straight in.

    Anything unrecognised is treated as actionable: an unknown metric is far
    more likely to be a genuine descriptive factor than one of the four fixed
    outcome measures, and over-reporting a metric as actionable is the
    pre-existing behaviour rather than a new failure mode.
    """
    return _CATEGORY.get(metric.rsplit(":", 1)[-1], ACTIONABLE)


_CATEGORY: dict[str, str] = {
    "offensive_rating": OUTCOME_CONTEXT,
    "defensive_rating": OUTCOME_CONTEXT,
    "net_rating": OUTCOME_CONTEXT,
    "pace": OUTCOME_CONTEXT,
    "efg_pct": ACTIONABLE,
    "tov_pct": ACTIONABLE,
    "orb_pct": ACTIONABLE,
    "ft_rate": ACTIONABLE,
    "fg3a_rate": ACTIONABLE,
    "ast_to_ratio": ACTIONABLE,
}

# Metrics where a *lower* value is the favorable direction for the team
# (fewer points allowed per 100 possessions, fewer turnovers per play).
# Used only to label "favorable_in_wins" — never changes the arithmetic.
_LOWER_IS_BETTER = {"defensive_rating", "tov_pct"}


@dataclass(frozen=True)
class MetricSignal:
    """One metric's win-vs-loss comparison for one team."""

    metric: str
    category: str  # OUTCOME_CONTEXT or ACTIONABLE
    win_average: float | None
    loss_average: float | None
    difference: float | None  # win_average - loss_average (raw, original units)
    sample_wins: int
    sample_losses: int
    sample_sufficient: bool
    # True if the win-side average sits in the direction that is normally
    # "better" for that metric; None if either average is undefined.
    favorable_in_wins: bool | None
    pooled_std: float | None
    effect_size: float | None  # (win_avg - loss_avg) / pooled_std; signed
    effect_note: str | None  # why effect_size is None, when it is

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "category": self.category,
            "win_average": self.win_average,
            "loss_average": self.loss_average,
            "difference": self.difference,
            "sample_wins": self.sample_wins,
            "sample_losses": self.sample_losses,
            "sample_sufficient": self.sample_sufficient,
            "favorable_in_wins": self.favorable_in_wins,
            "pooled_std": self.pooled_std,
            "effect_size": self.effect_size,
            "effect_note": self.effect_note,
        }


def games_for_team(games: list[TeamGameStats], team_id: str) -> list[TeamGameStats]:
    """Filter a mixed game list down to one team's own-perspective rows."""
    return [g for g in games if g.team_id == team_id]


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sample_variance(values: list[float]) -> float | None:
    """Sample variance (ddof=1). ``None`` if fewer than 2 values — variance
    is mathematically undefined for a single point, not zero."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / (n - 1)


def _pooled_std(win_vals: list[float], loss_vals: list[float]) -> tuple[float | None, str | None]:
    """Pooled sample standard deviation across the two groups.

    Returns ``(pooled_std, note)``. ``note`` is ``None`` on success, else a
    short machine-readable reason ``pooled_std`` is ``None``.
    """
    n1, n2 = len(win_vals), len(loss_vals)
    if n1 < 2 or n2 < 2:
        return None, "insufficient_sample_for_variance"
    var1 = _sample_variance(win_vals)
    var2 = _sample_variance(loss_vals)
    df = n1 + n2 - 2
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / df
    if pooled_var <= 0:
        return 0.0, "zero_pooled_variance"
    return math.sqrt(pooled_var), None


def build_metric_signal(
    metric: str,
    category: str,
    win_vals: list[float],
    loss_vals: list[float],
    *,
    lower_is_better: bool = False,
    min_sufficient_sample: int = MIN_SUFFICIENT_SAMPLE,
) -> MetricSignal:
    """Build one :class:`MetricSignal` from raw win/loss value lists.

    This is the single shared implementation behind both
    :func:`compute_signals` (the ten season-level core metrics) and any
    segmented/enriched metric (§18 of the 2026-08-15 enrichment brief —
    "extend the W/L comparison machinery so it can operate on segmented/
    enriched observations"). Every enrichment-layer W/L comparison
    (quarter eFG%, clutch TOV%, points off turnovers, ...) calls this same
    function so the effect-size/sample-handling contract never drifts
    between the original ten metrics and the new segmented ones.
    """
    win_avg = _average(win_vals)
    loss_avg = _average(loss_vals)
    diff = (win_avg - loss_avg) if (win_avg is not None and loss_avg is not None) else None

    favorable: bool | None = None
    if diff is not None:
        favorable = (diff < 0) if lower_is_better else (diff > 0)

    pooled_std: float | None = None
    effect_size: float | None = None
    effect_note: str | None = None
    if diff is None:
        effect_note = "insufficient_sample_for_variance" if not win_vals or not loss_vals else None
    else:
        pooled_std, effect_note = _pooled_std(win_vals, loss_vals)
        if pooled_std is not None and pooled_std > 0:
            effect_size = diff / pooled_std

    return MetricSignal(
        metric=metric,
        category=category,
        win_average=win_avg,
        loss_average=loss_avg,
        difference=diff,
        sample_wins=len(win_vals),
        sample_losses=len(loss_vals),
        sample_sufficient=(
            len(win_vals) >= min_sufficient_sample and len(loss_vals) >= min_sufficient_sample
        ),
        favorable_in_wins=favorable,
        pooled_std=pooled_std,
        effect_size=effect_size,
        effect_note=effect_note,
    )


def compute_signal_from_pairs(
    metric: str,
    category: str,
    pairs: list[tuple[float | None, bool]],
    *,
    lower_is_better: bool = False,
    min_sufficient_sample: int = MIN_SUFFICIENT_SAMPLE,
) -> MetricSignal:
    """Generic entry point for a segmented/enriched W/L signal.

    ``pairs`` is ``[(value_or_None, was_a_win), ...]`` — one entry per game
    (or per game-segment observation). ``None`` values (the segment simply
    had no sample in that game, e.g. no clutch possessions) are dropped
    before averaging, never treated as 0.
    """
    win_vals = [v for v, win in pairs if win and v is not None]
    loss_vals = [v for v, win in pairs if not win and v is not None]
    return build_metric_signal(
        metric, category, win_vals, loss_vals,
        lower_is_better=lower_is_better, min_sufficient_sample=min_sufficient_sample,
    )


# Threshold for an *agent-facing ranked* signal (enrichment brief §18) —
# stricter than and separate from MIN_SUFFICIENT_SAMPLE / sample_sufficient
# (which is a reporting-only "trust this number" flag). A signal failing
# this check is still computed and returned by compute_signals/rank_signals
# — it is simply excluded from the top-ranked agent view, with its own
# effect_note explaining why if applicable.
AGENT_RANKABLE_MIN_WINS = 3
AGENT_RANKABLE_MIN_LOSSES = 3


def is_agent_rankable(signal: MetricSignal) -> bool:
    return (
        signal.sample_wins >= AGENT_RANKABLE_MIN_WINS
        and signal.sample_losses >= AGENT_RANKABLE_MIN_LOSSES
        and signal.effect_size is not None
    )


def compute_signals(
    games: list[TeamGameStats],
    *,
    min_sufficient_sample: int = MIN_SUFFICIENT_SAMPLE,
) -> list[MetricSignal]:
    """One :class:`MetricSignal` per core metric, for all ten metrics.

    Returned in metric-registry order (outcome/context first, then
    actionable) — **not** ranked by effect size. This is the "compute
    everything, for overview and interpretation" entry point; use
    :func:`rank_actionable_signals` for the default scouting ranking.

    ``games`` must already be filtered to a single team (e.g. via
    :func:`games_for_team`) — mixing teams would silently average across
    unrelated opponents and produce a meaningless signal. This is checked:
    passing a mixed list raises ``ValueError``.
    """
    team_ids = {g.team_id for g in games}
    if len(team_ids) > 1:
        raise ValueError(
            f"compute_signals requires games for a single team_id, got {sorted(team_ids)}; "
            "use games_for_team() first"
        )

    wins = [g for g in games if g.win]
    losses = [g for g in games if not g.win]

    signals: list[MetricSignal] = []
    for name, getter in _METRIC_GETTERS.items():
        win_vals = [v for g in wins if (v := getter(g.metrics)) is not None]
        loss_vals = [v for g in losses if (v := getter(g.metrics)) is not None]
        signals.append(
            build_metric_signal(
                name, _CATEGORY[name], win_vals, loss_vals,
                lower_is_better=name in _LOWER_IS_BETTER, min_sufficient_sample=min_sufficient_sample,
            )
        )

    return signals


def _rank_key(signal: MetricSignal) -> float:
    # Undefined effects sort last, not first — a missing/undefined effect
    # must never outrank a real, computed one.
    return abs(signal.effect_size) if signal.effect_size is not None else -1.0


def rank_signals(signals: list[MetricSignal]) -> list[MetricSignal]:
    """Sort already-computed signals by |effect_size| descending.

    Sign is preserved on each ``MetricSignal`` (not discarded by the
    ranking) — only the ordering uses the absolute value.
    """
    return sorted(signals, key=_rank_key, reverse=True)


def rank_actionable_signals(
    games: list[TeamGameStats],
    *,
    min_sufficient_sample: int = MIN_SUFFICIENT_SAMPLE,
) -> list[MetricSignal]:
    """The default scouting signal ranking: ACTIONABLE metrics only, by |effect_size|.

    Outcome/context metrics (ORtg, DRtg, Net Rating, Pace) are deliberately
    excluded here — see the module docstring for why ranking them produces
    tautological top findings. Use :func:`compute_signals` directly for the
    full ten-metric overview.
    """
    signals = compute_signals(games, min_sufficient_sample=min_sufficient_sample)
    actionable = [s for s in signals if s.category == ACTIONABLE]
    return rank_signals(actionable)
