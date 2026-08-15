"""Per-shot fast-break context — CP2.4B (2026-08-16).

Deterministic MVP binary over Segev's provider ``fastBreak`` flag, plus
supporting timing/possession-change context for validating it. Works
directly from raw actions (a lightweight, parallel state-machine walk,
matching ``scoring_timeline.py``'s pattern) rather than modifying the
already-accepted ``possession.py`` — this module only needs "what changed
possession right before this shot, and how many seconds ago", which the
existing ``Possession`` aggregate does not expose per-shot.

**Semantics, binding (brief §12/§16):**

* ``fast_break := provider.fastBreak == true``. Provider-POSITIVE only.
* ``non_fast_break`` means only "the provider did not classify this play as
  fast break". It is **not** semantically ``half_court``, ``defense_set``,
  or ``secondary_transition`` — none of those are derivable from a
  provider-negative without inventing information the source doesn't give.
  ``secondary_transition`` is explicitly deferred, not implemented here.
* No video, no model calls, no possession-classification authority beyond
  the provider flag itself — this module only adds *diagnostic* context
  (elapsed time, change type) to validate whether the flag behaves
  consistently with plausible transition timing; it never overrides or
  re-derives the flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .possession import parse_clock_mmss

_TEAM_SIDE = {1: "home", 2: "away"}
_OTHER_SIDE = {"home": "away", "away": "home"}

PossessionChangeType = str  # "defensive_rebound" | "opponent_turnover" | "opponent_score" | "quarter_start" | "other"


@dataclass(frozen=True)
class FastBreakShotEvent:
    """One shot (or final free throw) attempt's fast-break diagnostic context."""

    game_id: str
    action_id: int
    quarter: int
    team: str
    provider_fast_break: bool | None  # None only if the source field itself was missing (not False)
    is_first_attempt_of_possession: bool
    elapsed_since_possession_change_s: float | None  # only meaningful when is_first_attempt_of_possession
    possession_change_type: PossessionChangeType | None
    made: bool | None
    action_type: str  # "shot" | "freeThrow"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_fastbreak_events(game_id: str, actions: list[dict[str, Any]]) -> list[FastBreakShotEvent]:
    """Walk one game's raw actions and emit a :class:`FastBreakShotEvent`
    for every shot attempt and every FINAL free throw of a trip (the only
    attempt types that can plausibly be "fast break").

    ``possession_change_type``/``elapsed_since_possession_change_s`` are
    only populated for the first live-ball attempt following a change of
    possession control (rebound/turnover/opponent score/quarter start); a
    later attempt within the same continuing possession (e.g. after an
    offensive rebound) is marked ``is_first_attempt_of_possession=False``
    and carries no elapsed/change-type value — it is definitionally not a
    fast-break candidate.
    """
    ordered = sorted(
        (a for a in actions if isinstance(a.get("quarter"), int) and isinstance(a.get("id"), int)),
        key=lambda a: (a["quarter"], a["id"]),
    )

    events: list[FastBreakShotEvent] = []
    # boundary: the most recent live-ball-control-changing event not yet
    # "consumed" by a first attempt. None once consumed (or never set, e.g.
    # start of game before any boundary has occurred).
    boundary_type: PossessionChangeType | None = None
    boundary_clock: float | None = None
    boundary_team: str | None = None  # which team now controls the ball

    def set_boundary(change_type: PossessionChangeType, clock: float | None, team: str | None) -> None:
        nonlocal boundary_type, boundary_clock, boundary_team
        boundary_type, boundary_clock, boundary_team = change_type, clock, team

    for action in ordered:
        action_type = action.get("type")
        params = action.get("parameters") or {}
        clock = parse_clock_mmss(action.get("quarterTime"))

        if action_type == "quarter" and params.get("type") == "start-of-quarter":
            set_boundary("quarter_start", clock, None)  # team unknown until first action of the quarter
            continue
        if action_type == "quarter" and params.get("type") == "end-of-quarter":
            set_boundary(None, None, None)  # nothing meaningfully "changes to" across a quarter break
            continue

        side = _TEAM_SIDE.get(params.get("team"))
        if side is None:
            continue

        if action_type == "rebound" and params.get("type") == "defensive":
            set_boundary("defensive_rebound", clock, side)
            continue
        if action_type == "turnover":
            set_boundary("opponent_turnover", clock, _OTHER_SIDE[side])
            continue

        if action_type == "shot":
            is_first = boundary_team is not None and boundary_team == side
            elapsed = None
            change_type = None
            if is_first:
                elapsed = (boundary_clock - clock) if (boundary_clock is not None and clock is not None) else None
                change_type = boundary_type
            events.append(FastBreakShotEvent(
                game_id=game_id, action_id=action["id"], quarter=action["quarter"], team=side,
                provider_fast_break=params.get("fastBreak") if "fastBreak" in params else None,
                is_first_attempt_of_possession=is_first,
                elapsed_since_possession_change_s=elapsed, possession_change_type=change_type,
                made=(params.get("made") == "made") if "made" in params else None,
                action_type="shot",
            ))
            # This team has now taken an attempt in the current possession —
            # any further attempt (post-OREB) is not a fast-break candidate.
            if boundary_team == side:
                boundary_team = None
            made = params.get("made") == "made"
            if made:
                set_boundary("opponent_score", clock, _OTHER_SIDE[side])
            continue

        if action_type == "freeThrow":
            awarded = int(params.get("freeThrowsAwarded") or 0)
            number = int(params.get("freeThrowNumber") or 0)
            is_final = awarded > 0 and number == awarded
            if not is_final:
                continue  # only the final FT of a trip is a plausible fast-break-and-1/2-shot-foul outcome marker
            is_first = boundary_team is not None and boundary_team == side
            elapsed = None
            change_type = None
            if is_first:
                elapsed = (boundary_clock - clock) if (boundary_clock is not None and clock is not None) else None
                change_type = boundary_type
            events.append(FastBreakShotEvent(
                game_id=game_id, action_id=action["id"], quarter=action["quarter"], team=side,
                provider_fast_break=params.get("fastBreak") if "fastBreak" in params else None,
                is_first_attempt_of_possession=is_first,
                elapsed_since_possession_change_s=elapsed, possession_change_type=change_type,
                made=(params.get("made") == "made"), action_type="freeThrow",
            ))
            if boundary_team == side:
                boundary_team = None
            if params.get("made") == "made":
                set_boundary("opponent_score", clock, _OTHER_SIDE[side])
            continue

    return events


@dataclass(frozen=True)
class FastBreakClassification:
    """The MVP binary + supporting context for one attempt — the
    product-facing output. ``fast_break``/``non_fast_break`` are always
    exactly one of the two; there is no third state, per brief §12/§16."""

    is_fast_break: bool  # True only if provider.fastBreak == True
    elapsed_since_possession_change_s: float | None
    possession_change_type: PossessionChangeType | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def classify(event: FastBreakShotEvent) -> FastBreakClassification:
    """provider.fastBreak == True -> fast_break; anything else (False OR
    missing) -> non_fast_break. Missing is never silently treated as a
    confident negative claim about defense being set — see module
    docstring; the caller is expected to look at ``provider_fast_break is
    None`` separately if that distinction matters downstream."""
    return FastBreakClassification(
        is_fast_break=bool(event.provider_fast_break),
        elapsed_since_possession_change_s=event.elapsed_since_possession_change_s,
        possession_change_type=event.possession_change_type,
    )
