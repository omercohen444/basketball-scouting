"""Segev PBP action stream -> raw team-game components.

Statistics-side adapter. Deliberately **not** built on top of
``pbp/canonical.py`` — that module is the video pipeline's contract (shot
events only, provider-agnostic, out of bounds for this track per CLAUDE.md).
Building a second, fuller canonical layer to unify both was judged not worth
the redesign risk inside the one-week window; if a second PBP provider is
ever added, promoting this into a provider-agnostic layer is the natural next
step — see PROJECT_SPEC.md §6 on adapter seams.

Field mapping verified against the real game_id=136 action stream this
session (``data/validation/segev_game136_full.json``):

* ``parameters.team`` is ``1`` (home) or ``2`` (away) — consistent across
  every action type observed (shot, freeThrow, rebound, assist, turnover,
  foul, steal, block, deflection, timeout, substitution). This is the same
  convention ``pbp/canonical.py`` already uses for shot events.
* A missed shot with ``parameters.made == "blocked"`` is still a field goal
  attempt (FGA increments; FGM does not) — basketball scoring convention.
* Each free throw attempt is its own ``freeThrow`` action (not one action per
  trip), so counting them directly gives FTA/FTM with no extra bookkeeping.
* An offensive foul is followed by a *separate* ``turnover`` action
  (``parameters.type == "other"``) for the same team/player — confirmed by
  inspection, not assumed. Turnovers are therefore counted **only** from
  ``type == "turnover"`` actions; offensive fouls are not double-counted as
  a second turnover.
* ``rebound.parameters.type`` is ``"offensive"`` or ``"defensive"``. Any other
  or missing value is counted in ``action_counts`` provenance but
  deliberately not attributed to either ORB or DRB, rather than guessed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .models import TeamGameComponents

_TEAM_SIDE = {1: "home", 2: "away"}


class BoxScoreError(ValueError):
    """Raised when raw actions cannot be turned into team-game components."""


def _side(params: dict[str, Any]) -> str | None:
    return _TEAM_SIDE.get(params.get("team"))


def build_components(
    actions: list[dict[str, Any]],
) -> tuple[TeamGameComponents, TeamGameComponents, dict[str, int]]:
    """Aggregate raw Segev actions into ``(home, away, action_counts)``.

    ``action_counts`` is a provenance dict of every action ``type`` seen
    (including types not used arithmetically, e.g. ``substitution``,
    ``clock``), plus a ``dropped_no_team`` count for actions of an otherwise
    countable type whose ``parameters.team`` was neither 1 nor 2.
    """
    totals: dict[str, Counter] = {"home": Counter(), "away": Counter()}
    action_counts: Counter[str] = Counter()
    dropped_no_team = 0

    for action in actions:
        action_type = action.get("type")
        action_counts[action_type or "?"] += 1
        params = action.get("parameters") or {}

        if action_type == "shot":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            is_three = int(params.get("points") or 0) == 3
            made = params.get("made") == "made"
            totals[side]["fga"] += 1
            if is_three:
                totals[side]["fg3a"] += 1
            if made:
                totals[side]["fgm"] += 1
                totals[side]["points"] += int(params.get("points") or 0)
                if is_three:
                    totals[side]["fg3m"] += 1

        elif action_type == "freeThrow":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            totals[side]["fta"] += 1
            if params.get("made") == "made":
                totals[side]["ftm"] += 1
                totals[side]["points"] += 1

        elif action_type == "rebound":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            reb_type = params.get("type")
            if reb_type == "offensive":
                totals[side]["orb"] += 1
            elif reb_type == "defensive":
                totals[side]["drb"] += 1

        elif action_type == "assist":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            totals[side]["ast"] += 1

        elif action_type == "turnover":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            totals[side]["tov"] += 1

        elif action_type == "foul":
            side = _side(params)
            if side is None:
                dropped_no_team += 1
                continue
            totals[side]["pf"] += 1

    action_counts["dropped_no_team"] = dropped_no_team

    def _build(side: str) -> TeamGameComponents:
        t = totals[side]
        return TeamGameComponents(
            fgm=t["fgm"], fga=t["fga"], fg3m=t["fg3m"], fg3a=t["fg3a"],
            ftm=t["ftm"], fta=t["fta"], orb=t["orb"], drb=t["drb"],
            ast=t["ast"], tov=t["tov"], pf=t["pf"], points=t["points"],
        )

    return _build("home"), _build("away"), dict(action_counts)
