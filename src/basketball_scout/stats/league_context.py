"""Deterministic league-relative context — rank/percentile of one team's
value against the rest of the league for the same metric/segment/scope.

No new arithmetic on the metric itself: this module only ever consumes
already-computed values (one per team) and ranks them. Ties are handled
by "competition ranking" (tied teams share the best rank of the tie
group; the next distinct value's rank skips ahead accordingly) —
deterministic and the same convention sports standings use.

Direction (``higher_is_better`` / ``lower_is_better`` / ``neutral``) never
changes the rank/percentile arithmetic — it only changes which end of the
ranking is "rank 1". A ``neutral`` metric (e.g. Pace — faster is not
inherently better or worse) is still ranked (descending by value, by
convention) so a percentile number exists, but callers must not read
"high percentile = good" into it. This module never invents a direction
for a metric; the caller supplies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["higher_is_better", "lower_is_better", "neutral"]

# Metrics where a larger raw value is favorable. Everything not listed here
# defaults to "neutral" by the caller unless stated otherwise — this dict is
# a convenience default, not an authority; evidence.py passes direction
# explicitly per metric.
DEFAULT_DIRECTIONS: dict[str, Direction] = {
    "offensive_rating": "higher_is_better",
    "defensive_rating": "lower_is_better",
    "net_rating": "higher_is_better",
    "pace": "neutral",
    "efg_pct": "higher_is_better",
    "tov_pct": "lower_is_better",
    "orb_pct": "higher_is_better",
    "ft_rate": "neutral",
    "fg3a_rate": "neutral",
    "ast_to_ratio": "higher_is_better",
}


@dataclass(frozen=True)
class LeagueContext:
    team_value: float
    league_mean: float | None
    league_median: float | None
    rank: int | None  # 1 = best per direction; None if team not eligible
    eligible_teams: int
    percentile: float | None  # 0-100, 100 = best per direction
    direction: Direction

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def build_league_context(
    team_id: str,
    values_by_team: dict[str, float | None],
    *,
    direction: Direction = "neutral",
) -> LeagueContext | None:
    """Rank ``team_id`` among every team with a non-``None`` value.

    Returns ``None`` (not a fabricated context) if ``team_id`` itself has
    no value, or if it is the only eligible team (a percentile against a
    league of one is meaningless).
    """
    eligible = {tid: v for tid, v in values_by_team.items() if v is not None}
    if team_id not in eligible or len(eligible) < 2:
        return None

    team_value = eligible[team_id]
    all_values = list(eligible.values())
    mean = sum(all_values) / len(all_values)
    median = _median(all_values)
    n = len(eligible)

    if direction == "lower_is_better":
        better_count = sum(1 for v in all_values if v < team_value)
    else:
        # "higher_is_better" and "neutral" both rank descending by value —
        # neutral metrics still get a rank/percentile number, just no
        # "good/bad" meaning attached to which end is higher.
        better_count = sum(1 for v in all_values if v > team_value)

    rank = better_count + 1  # competition ranking: ties share the best rank
    percentile = 100.0 * (n - rank) / (n - 1)

    return LeagueContext(
        team_value=team_value, league_mean=mean, league_median=median,
        rank=rank, eligible_teams=n, percentile=percentile, direction=direction,
    )
