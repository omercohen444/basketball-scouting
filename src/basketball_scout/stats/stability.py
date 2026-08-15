"""Per-game distribution/stability profile for a metric — lets a caller tell
"consistently high" apart from "high because of two extreme games".

Pure descriptive statistics over an already-extracted list of per-game
values (one metric, one team, one segment/window). No new metric
arithmetic — this module never touches raw box-score components.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StabilityProfile:
    games: int
    mean: float | None
    median: float | None
    std: float | None  # sample std (ddof=1); None if games < 2
    iqr: float | None  # Q3 - Q1; None if games < 4 (need a meaningful quartile split)
    min: float | None
    max: float | None
    coefficient_of_variation: float | None  # std / |mean|; only when mean != 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_stability_profile(values: list[float]) -> StabilityProfile:
    """``values`` must already exclude ``None`` (no-sample) games — the
    caller decides what "no sample in this segment for this game" means;
    this function only ever sees real numbers.
    """
    n = len(values)
    if n == 0:
        return StabilityProfile(games=0, mean=None, median=None, std=None,
                                 iqr=None, min=None, max=None, coefficient_of_variation=None)

    mean = statistics.mean(values)
    median = statistics.median(values)
    std = statistics.stdev(values) if n >= 2 else None
    iqr = None
    if n >= 4:
        quartiles = statistics.quantiles(values, n=4, method="exclusive")
        iqr = quartiles[2] - quartiles[0]
    cv = (std / abs(mean)) if (std is not None and mean != 0) else None

    return StabilityProfile(
        games=n, mean=mean, median=median, std=std, iqr=iqr,
        min=min(values), max=max(values), coefficient_of_variation=cv,
    )
