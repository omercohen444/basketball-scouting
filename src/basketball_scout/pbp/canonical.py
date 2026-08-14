"""Canonical PBP shot events — the minimum representation Video Analytics needs.

Deliberately not the full future PBP platform (see docs/VIDEO_STAGE_PLAN.md
§5.3): only shot attempts, only the fields localization/attribution/aggregation
need, plus the complete raw source row so nothing is lost for later reuse.

Parsing rules encoded here, verified against real data this session:

* ``userTime`` is only *mostly* monotonic (48-60 inversions per ~1000 actions
  were observed) — never assume the source order is already sorted;
* ``team`` in ``parameters`` is ``1`` (home) or ``2`` (away), not a team id;
* ``assisted`` is derived by linking ``assist`` actions to their shot via
  ``parentActionId`` — it is never present directly on the shot record;
* free throws are a **different** action type (``freeThrow``) and are excluded
  here at the source — see docs/VIDEO_STAGE_PLAN.md §8.1 for why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_TEAM_SIDE = {1: "home", 2: "away"}


class CanonicalError(ValueError):
    """Raised for malformed source data that cannot be turned into an event."""


@dataclass(frozen=True)
class PbpEvent:
    """One shot attempt, ready for temporal localization and team attribution."""

    event_id: str
    game_id: str
    source_action_id: int
    quarter: int
    quarter_time: str
    user_time_s: float
    event_type: str
    team_side: str
    source_team_id: int
    player_jersey: str
    source_player_id: int
    shot_type: str
    points: int
    outcome: str
    fast_break: bool
    assisted: bool
    coord_x: float | None
    coord_y: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "game_id": self.game_id,
            "source_action_id": self.source_action_id,
            "quarter": self.quarter,
            "quarter_time": self.quarter_time,
            "user_time_s": self.user_time_s,
            "event_type": self.event_type,
            "team_side": self.team_side,
            "source_team_id": self.source_team_id,
            "player_jersey": self.player_jersey,
            "source_player_id": self.source_player_id,
            "shot_type": self.shot_type,
            "points": self.points,
            "outcome": self.outcome,
            "fast_break": self.fast_break,
            "assisted": self.assisted,
            "coord_x": self.coord_x,
            "coord_y": self.coord_y,
        }


@dataclass
class CanonicalResult:
    """Extraction output: the usable events plus a full accounting of drops.

    Every excluded action is counted and reasoned, per docs/VIDEO_STAGE_PLAN.md
    §8.1 — coverage reporting depends on these numbers being real, not
    estimated.
    """

    events: list[PbpEvent]
    inversions_corrected: int
    dropped_no_team: int = 0
    dropped_no_time: int = 0
    dropped_malformed: int = 0
    total_shot_actions: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "total_shot_actions": self.total_shot_actions,
            "events_extracted": len(self.events),
            "dropped_no_team": self.dropped_no_team,
            "dropped_no_time": self.dropped_no_time,
            "dropped_malformed": self.dropped_malformed,
            "inversions_corrected": self.inversions_corrected,
        }


def _parse_user_time(value: str | None) -> float | None:
    """``HH:MM:SS`` -> seconds since midnight. None if unparseable."""
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        return None
    return h * 3600.0 + m * 60.0 + s


def _assisted_shot_ids(actions: list[dict[str, Any]]) -> set[int]:
    """Shot action ids that have a linked ``assist`` action (parentActionId)."""
    ids: set[int] = set()
    for action in actions:
        if action.get("type") == "assist":
            parent = action.get("parentActionId")
            if isinstance(parent, int) and parent:
                ids.add(parent)
    return ids


def extract_shot_events(game_id: str, actions: list[dict[str, Any]]) -> CanonicalResult:
    """Extract canonical shot events from raw Segev ``actions``.

    Free throws, substitutions, fouls etc. are not shot attempts and are not
    considered here at all — this function only ever looks at ``type=="shot"``
    records (see docs/VIDEO_STAGE_PLAN.md §8.1 for the full inclusion table;
    the remaining exclusion rules there — sync-related — apply at the
    localization stage, not here).
    """
    assisted_ids = _assisted_shot_ids(actions)
    shot_actions = [a for a in actions if a.get("type") == "shot"]

    result = CanonicalResult(events=[], inversions_corrected=0)
    result.total_shot_actions = len(shot_actions)

    staged: list[PbpEvent] = []
    for action in shot_actions:
        params = action.get("parameters") or {}
        action_id = action.get("id")
        if not isinstance(action_id, int):
            result.dropped_malformed += 1
            continue

        team_num = params.get("team")
        team_side = _TEAM_SIDE.get(team_num)
        if team_side is None:
            result.dropped_no_team += 1
            continue

        user_time_s = _parse_user_time(action.get("userTime"))
        if user_time_s is None:
            result.dropped_no_time += 1
            continue

        quarter = action.get("quarter")
        if not isinstance(quarter, int):
            result.dropped_malformed += 1
            continue

        staged.append(
            PbpEvent(
                event_id=f"{game_id}:{action_id}",
                game_id=game_id,
                source_action_id=action_id,
                quarter=quarter,
                quarter_time=str(action.get("quarterTime") or ""),
                user_time_s=user_time_s,
                event_type="shot",
                team_side=team_side,
                source_team_id=int(action.get("teamId") or 0),
                player_jersey=str(params.get("player") or ""),
                source_player_id=int(action.get("playerId") or 0),
                shot_type=str(params.get("type") or ""),
                points=int(params.get("points") or 0),
                outcome=str(params.get("made") or ""),
                fast_break=bool(params.get("fastBreak")),
                assisted=action_id in assisted_ids,
                coord_x=_as_float(params.get("coordX")),
                coord_y=_as_float(params.get("coordY")),
                raw=action,
            )
        )

    # Sort by (quarter, user_time_s); userTime is only mostly monotonic in the
    # source, so this is required, not defensive paranoia.
    ordered = sorted(staged, key=lambda e: (e.quarter, e.user_time_s))
    original_order = [(e.quarter, e.source_action_id) for e in staged]
    sorted_order = [(e.quarter, e.source_action_id) for e in ordered]
    result.inversions_corrected = sum(
        1 for a, b in zip(original_order, sorted_order) if a != b
    )
    result.events = ordered
    return result


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
