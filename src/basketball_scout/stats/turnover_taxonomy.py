"""Provider-defined turnover taxonomy (bounded audit, 2026-08-15 v2 §10).

Audit finding across all 182 cached games (5,205 turnovers): the source
``turnover.parameters.type`` field is clean and stable — 10 distinct
values, **0% null**, no inconsistent/artifact-looking entries:

    bad-pass 44.9%, ball-handling 28.5%, other 11.5%, travelling 8.7%,
    24-seconds-violation 2.6%, out-of-bounds 2.3%, 8-seconds-violation 0.5%,
    5-seconds-violation 0.4%, backcourt-violation 0.4%, 3-seconds-violation 0.3%

Because the data quality is sufficient, this module preserves the provider's
own categories verbatim rather than inventing or normalizing a scouting
taxonomy on top of them — the exact ten string values above are exposed
as-is, nothing coarser is imposed on them.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

_TEAM_SIDE = {1: "home", 2: "away"}


def build_turnover_taxonomy(actions: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    """Raw ``turnover.parameters.type`` counts, per team side, verbatim.

    Returns ``(home_counts, away_counts)``, each ``{provider_type: count}``.
    A turnover with no team attribution is dropped (not guessed) — the
    same convention ``boxscore.py`` already uses for every action type.
    """
    home: Counter[str] = Counter()
    away: Counter[str] = Counter()
    for action in actions:
        if action.get("type") != "turnover":
            continue
        params = action.get("parameters") or {}
        side = _TEAM_SIDE.get(params.get("team"))
        if side is None:
            continue
        turnover_type = params.get("type") or "unknown"
        (home if side == "home" else away)[turnover_type] += 1
    return dict(home), dict(away)
