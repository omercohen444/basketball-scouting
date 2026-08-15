"""Chronological scoring-play timeline — shared foundation for runs,
droughts, and score-dynamics (ties/lead-changes/comebacks).

Built directly from raw actions (not from :class:`Possession` records):
runs/droughts/dynamics need the exact clock moment of every individual
scoring play (a possession can contain more than one, e.g. a non-final made
free throw followed later by a made field goal), which possession-level
aggregation intentionally does not preserve. Ordering is strictly
``(quarter, action id)`` — never ``userTime`` — matching every other module
in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .possession import parse_clock_mmss

_TEAM_SIDE = {1: "home", 2: "away"}


@dataclass(frozen=True)
class ScoringPlay:
    quarter: int
    clock_s: float | None  # seconds remaining in the quarter at this play
    team: str  # "home" / "away"
    points: int
    is_field_goal: bool  # True: made shot. False: made free throw.
    home_score_after: int
    away_score_after: int
    action_id: int


def build_scoring_timeline(actions: list[dict[str, Any]]) -> list[ScoringPlay]:
    """Every made shot / made free throw, in canonical chronological order,
    with the running score immediately after each."""
    ordered = sorted(
        (a for a in actions if isinstance(a.get("quarter"), int) and isinstance(a.get("id"), int)),
        key=lambda a: (a["quarter"], a["id"]),
    )

    plays: list[ScoringPlay] = []
    score = {"home": 0, "away": 0}

    for action in ordered:
        params = action.get("parameters") or {}
        side = _TEAM_SIDE.get(params.get("team"))
        if side is None:
            continue
        action_type = action.get("type")

        if action_type == "shot" and params.get("made") == "made":
            pts = int(params.get("points") or 0)
            score[side] += pts
            plays.append(
                ScoringPlay(
                    quarter=action["quarter"], clock_s=parse_clock_mmss(action.get("quarterTime")),
                    team=side, points=pts, is_field_goal=True,
                    home_score_after=score["home"], away_score_after=score["away"],
                    action_id=action["id"],
                )
            )
        elif action_type == "freeThrow" and params.get("made") == "made":
            score[side] += 1
            plays.append(
                ScoringPlay(
                    quarter=action["quarter"], clock_s=parse_clock_mmss(action.get("quarterTime")),
                    team=side, points=1, is_field_goal=False,
                    home_score_after=score["home"], away_score_after=score["away"],
                    action_id=action["id"],
                )
            )

    return plays
