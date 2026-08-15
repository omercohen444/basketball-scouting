"""Canonical PBP possession-state builder — the foundation of the enrichment layer.

Turns a raw Segev action stream into an ordered list of :class:`Possession`
records, using the FIBA statistical possession concept (2026-08-15
enrichment brief §3):

* a possession starts when a team gains control of a live ball;
* it ends when the opponent gains control following a turnover, a made
  field goal, or a final made free throw, or a missed field goal / final
  missed free throw followed by a **defensive** rebound;
* an **offensive** rebound continues the same possession — it never starts
  a new one.

**Never uses `userTime`** for ordering or clock state — some real games
(178, 209, 224; see WORKLOG.md 2026-08-15 Run 5) contain bulk/backfilled
`userTime` values with no live-clock meaning at all. The authoritative
timeline here is ``(quarter, source action id)`` for ordering — Segev action
ids are strictly increasing per game in true recording order (verified
against real data) — and ``quarterTime`` (``MM:SS`` countdown) for in-game
clock state.

And-1 continuation (2026-08-15, corrected after management review): a made
field goal does **not** close the possession when it is immediately followed
by a qualifying "and-1" foul — an opponent `foul` action with
``kind == "shooting"``, ``fouledOn`` equal to the shooter, and
``freeThrows > 0`` — found after skipping only the non-boundary,
same-play "attached" action types (``assist``, ``block``, ``deflection``,
``foul-drawn``; the last of these being the mirror record Segev always logs
alongside a `foul`).

This was **not** the original design. The first pass of this module
(pre-review) checked adjacency skipping only ``assist``/``block``/
``deflection`` and found ~0 and-1 sequences in a small sample, concluding
the pattern was negligible. That was wrong: it missed ``foul-drawn``, which
Segev *always* inserts between the shot and the deciding foul action,
so the "next real action" check never reached the foul. An exhaustive
audit of all 182 games with the corrected skip-list found **729 and-1
sequences across 180 of 182 games** (6.5% of all 11,268 made shots) — a
real, frequent pattern, not an edge case. See :func:`_find_and1_shot_ids`
for the exact detection query and WORKLOG.md 2026-08-15 (management
hardening run) for the full audit evidence.

Mechanically: :func:`_find_and1_shot_ids` pre-scans the action list once to
identify every made-shot action id that qualifies; the main loop then
leaves that possession open (instead of closing on the make) so the
resulting free throw(s) attach to the *same* possession, closing normally
per the free-throw rules below (final make -> close; final miss -> await
the rebound). A free throw that still arrives with no possession open at
all (a technical foul, unrelated to any shot) remains handled by the
orphan-FT fallback — its points/FTA/FTM still count toward game totals
rather than being dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_TEAM_SIDE = {1: "home", 2: "away"}
_OTHER_SIDE = {"home": "away", "away": "home"}


def parse_clock_mmss(value: str | None) -> float | None:
    """``"MM:SS"`` (Segev ``quarterTime``, a countdown) -> seconds remaining.

    Returns ``None`` for anything unparseable rather than guessing.
    """
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        m, s = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return float(m * 60 + s)


def quarter_length_seconds(quarter: int, regulation_periods: int) -> float:
    """FIBA: regulation quarters are 10:00, every OT period is 5:00."""
    return 600.0 if quarter <= regulation_periods else 300.0


# Non-boundary "attached" action types that can sit between a made shot and
# its and-1 foul without breaking adjacency — see module docstring.
_AND1_SKIP_TYPES = {"assist", "block", "deflection", "foul-drawn"}


def _find_and1_shot_ids(ordered_actions: list[dict[str, Any]]) -> set[int]:
    """Pre-scan pass: made-shot action ids immediately followed (after
    skipping only ``_AND1_SKIP_TYPES``) by a qualifying and-1 foul —
    opponent team, ``kind == "shooting"``, ``fouledOn`` equal to the
    shooter, ``freeThrows > 0``. This is the exact query validated against
    all 182 real games (729/11268 made shots, 180/182 games) — see module
    docstring.
    """
    and1_ids: set[int] = set()
    for i, action in enumerate(ordered_actions):
        if action.get("type") != "shot":
            continue
        params = action.get("parameters") or {}
        if params.get("made") != "made":
            continue
        j = i + 1
        while j < len(ordered_actions) and ordered_actions[j].get("type") in _AND1_SKIP_TYPES:
            j += 1
        nxt = ordered_actions[j] if j < len(ordered_actions) else None
        if nxt is None or nxt.get("type") != "foul":
            continue
        nxt_params = nxt.get("parameters") or {}
        if (
            nxt_params.get("kind") == "shooting"
            and nxt_params.get("team") != params.get("team")
            and str(nxt_params.get("fouledOn")) == str(params.get("player"))
            and int(nxt_params.get("freeThrows") or 0) > 0
        ):
            and1_ids.add(action.get("id"))
    return and1_ids


@dataclass
class Possession:
    """One completed offensive possession, source-observable facts only."""

    possession_index: int
    quarter: int
    offense_team: str  # "home" / "away"
    defense_team: str
    start_clock_s: float | None
    end_clock_s: float | None
    ended_by: str  # made_fg | made_ft | turnover | defensive_rebound | quarter_end | orphan_ft
    points: int = 0
    fgm: int = 0
    fga: int = 0
    fg3m: int = 0
    fg3a: int = 0
    ftm: int = 0
    fta: int = 0
    orb: int = 0  # offensive rebounds that continued THIS possession
    turnover: bool = False
    followed_opponent_turnover: bool = False
    had_offensive_rebound: bool = False
    points_after_first_oreb: int = 0
    fast_break_points: int = 0
    assisted_fgm: int = 0
    unassisted_fgm: int = 0
    fg2m_assisted: int = 0
    fg2m_unassisted: int = 0
    fg3m_assisted: int = 0
    fg3m_unassisted: int = 0
    made_shot_is_three: bool | None = None  # None = no made FG this possession
    # Raw authoritative assist-ACTION count for this possession — the AST/TO
    # numerator convention (2026-08-15 management decision): every assist
    # action belonging to this possession, whether or not it could be linked
    # to a specific made shot. Deliberately separate from assisted_fgm/
    # unassisted_fgm (shot-attribution; see scoring_sources.AssistedProfile).
    # A resolution failure must never remove a real assist from AST/TO.
    raw_assist_count: int = 0
    score_before_home: int = 0
    score_before_away: int = 0
    score_after_home: int = 0
    score_after_away: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["warnings"] = list(self.warnings)
        return d


@dataclass
class PossessionBuildResult:
    possessions: list[Possession]
    warnings: list[str] = field(default_factory=list)
    # Assist actions whose `parentActionId` does not point to a made shot —
    # observed in real data (e.g. game 136) as pointing to a `foul` action
    # instead, apparently Segev's representation of an and-1-adjacent assist.
    # Not attributed to any specific shot (that would require guessing);
    # surfaced here as an explicit count so downstream assisted/unassisted
    # totals are never silently short by this amount. See possession.py
    # module docstring and WORKLOG.md 2026-08-15 enrichment run.
    unresolved_assist_count: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "possession_count": len(self.possessions),
            "warning_count": len(self.warnings),
            "orphan_ft_possessions": sum(1 for p in self.possessions if p.ended_by == "orphan_ft"),
            "unresolved_assist_count": self.unresolved_assist_count,
        }


class _Open:
    """Mutable in-progress possession while we walk the action stream."""

    def __init__(self, index: int, quarter: int, offense: str, start_clock: float | None,
                 score_home: int, score_away: int, followed_turnover: bool = False):
        self.index = index
        self.quarter = quarter
        self.offense = offense
        self.start_clock = start_clock
        self.score_before_home = score_home
        self.score_before_away = score_away
        self.points = 0
        self.fgm = self.fga = self.fg3m = self.fg3a = 0
        self.ftm = self.fta = 0
        self.orb = 0
        self.turnover = False
        self.followed_turnover = followed_turnover
        self.had_oreb = False
        self.oreb_seen = False
        self.points_after_first_oreb = 0
        self.fast_break_points = 0
        self.assisted_fgm = 0
        self.unassisted_fgm = 0
        self.fg2m_assisted = 0
        self.fg2m_unassisted = 0
        self.fg3m_assisted = 0
        self.fg3m_unassisted = 0
        self.raw_assist_count = 0
        self.last_made_shot_action_id: int | None = None
        self.last_made_shot_is_three: bool = False
        self.warnings: list[str] = []


def build_possessions(actions: list[dict[str, Any]], *, regulation_periods: int = 4) -> PossessionBuildResult:
    """Build the ordered possession list for one game's raw actions.

    ``actions`` need not be pre-sorted; this function sorts by
    ``(quarter, id)`` itself (never ``userTime``). Quarters are processed in
    order; a possession never spans a quarter boundary — one still open when
    a quarter ends is closed with ``ended_by="quarter_end"``.
    """
    ordered = sorted(
        (a for a in actions if isinstance(a.get("quarter"), int) and isinstance(a.get("id"), int)),
        key=lambda a: (a["quarter"], a["id"]),
    )
    and1_shot_ids = _find_and1_shot_ids(ordered)

    possessions: list[Possession] = []
    global_warnings: list[str] = []
    score = {"home": 0, "away": 0}
    open_poss: _Open | None = None
    # Track the possession index of the last CLOSED possession whose final
    # action was a made shot, keyed by source action id, so a trailing
    # `assist` action (which arrives after the shot closes the possession)
    # can still be linked back to it.
    pending_assist_targets: dict[int, tuple[Possession, str]] = {}
    unresolved_assist_count = 0
    action_by_id = {a.get("id"): a for a in ordered}
    # Most recently closed possession per team — the raw_assist_count target
    # for an assist action whose team currently has no open possession
    # (e.g. its parent is a foul, not a shot). See raw_assist_count's
    # docstring on Possession.
    last_closed_by_team: dict[str, Possession] = {}

    def close(reason: str) -> None:
        nonlocal open_poss
        assert open_poss is not None
        p = Possession(
            possession_index=open_poss.index,
            quarter=open_poss.quarter,
            offense_team=open_poss.offense,
            defense_team=_OTHER_SIDE[open_poss.offense],
            start_clock_s=open_poss.start_clock,
            end_clock_s=current_clock[0],
            ended_by=reason,
            points=open_poss.points,
            fgm=open_poss.fgm, fga=open_poss.fga, fg3m=open_poss.fg3m, fg3a=open_poss.fg3a,
            ftm=open_poss.ftm, fta=open_poss.fta, orb=open_poss.orb,
            turnover=open_poss.turnover,
            followed_opponent_turnover=open_poss.followed_turnover,
            had_offensive_rebound=open_poss.had_oreb,
            points_after_first_oreb=open_poss.points_after_first_oreb,
            fast_break_points=open_poss.fast_break_points,
            assisted_fgm=open_poss.assisted_fgm, unassisted_fgm=open_poss.unassisted_fgm,
            fg2m_assisted=open_poss.fg2m_assisted, fg2m_unassisted=open_poss.fg2m_unassisted,
            fg3m_assisted=open_poss.fg3m_assisted, fg3m_unassisted=open_poss.fg3m_unassisted,
            raw_assist_count=open_poss.raw_assist_count,
            made_shot_is_three=(open_poss.last_made_shot_is_three if open_poss.last_made_shot_action_id else None),
            score_before_home=open_poss.score_before_home, score_before_away=open_poss.score_before_away,
            score_after_home=score["home"], score_after_away=score["away"],
            warnings=tuple(open_poss.warnings),
        )
        possessions.append(p)
        if open_poss.last_made_shot_action_id is not None:
            pending_assist_targets[open_poss.last_made_shot_action_id] = (p, "fg")
        last_closed_by_team[p.offense_team] = p
        open_poss = None

    def open_new(quarter: int, offense: str, clock: float | None, followed_turnover: bool = False) -> None:
        nonlocal open_poss
        if open_poss is not None:
            # Structural safety net, not just a call-site convention: an
            # in-progress possession (e.g. an and-1 shot awaiting its free
            # throw) must never be silently overwritten and lost. A real
            # case found in game 74: a stray "offensive rebound" oddity for
            # the OTHER team arrived while an and-1 possession was still
            # open pending its foul-drawn/foul/free-throw resolution; the
            # old code path called open_new() directly here, discarding the
            # and-1 shot's FGA/FGM/points entirely (3 FGA silently lost
            # game-wide). Closing here first is a hard guarantee, not
            # dependent on every caller remembering to close first.
            open_poss.warnings.append("open_possession_force_closed_before_new_one_opened")
            close("forced_new_possession")
        open_poss = _Open(len(possessions), quarter, offense, clock, score["home"], score["away"], followed_turnover)

    current_clock: list[float | None] = [None]

    for action in ordered:
        quarter = action["quarter"]
        params = action.get("parameters") or {}
        current_clock[0] = parse_clock_mmss(action.get("quarterTime"))
        action_type = action.get("type")

        if action_type == "quarter" and params.get("type") == "end-of-quarter":
            if open_poss is not None:
                close("quarter_end")
            continue

        team_num = params.get("team")
        side = _TEAM_SIDE.get(team_num)

        if action_type == "shot":
            if side is None:
                global_warnings.append(f"id={action.get('id')}: shot with no team, dropped")
                continue
            if open_poss is None or open_poss.offense != side:
                if open_poss is not None:
                    open_poss.warnings.append("shot_from_non_offense_side_forced_new_possession")
                    close("forced_new_possession")
                open_new(quarter, side, current_clock[0])
            is_three = int(params.get("points") or 0) == 3
            made = params.get("made") == "made"
            open_poss.fga += 1
            if is_three:
                open_poss.fg3a += 1
            if made:
                pts = int(params.get("points") or 0)
                open_poss.fgm += 1
                open_poss.points += pts
                if open_poss.oreb_seen:
                    open_poss.points_after_first_oreb += pts
                if is_three:
                    open_poss.fg3m += 1
                if params.get("fastBreak"):
                    open_poss.fast_break_points += pts
                open_poss.last_made_shot_action_id = action.get("id")
                open_poss.last_made_shot_is_three = is_three
                score[side] += pts
                # Assist linkage resolved lazily below via pending markers;
                # tentatively mark unassisted, an `assist` action (if any)
                # arriving right after will flip it.
                if is_three:
                    open_poss.fg3m_unassisted += 1
                else:
                    open_poss.fg2m_unassisted += 1
                open_poss.unassisted_fgm += 1
                # And-1: an immediately following qualifying foul means the
                # possession is not actually over yet — the resulting free
                # throw(s) belong to this same possession, which closes
                # normally via the free-throw rules below, not here.
                if action.get("id") not in and1_shot_ids:
                    close("made_fg")
            continue

        if action_type == "freeThrow":
            if side is None:
                global_warnings.append(f"id={action.get('id')}: freeThrow with no team, dropped")
                continue
            if open_poss is None:
                open_new(quarter, side, current_clock[0])
                open_poss.warnings.append("orphan_free_throw_no_open_possession")
            elif open_poss.offense != side:
                open_poss.warnings.append("free_throw_team_mismatch_forced_new_possession")
                close("forced_new_possession")
                open_new(quarter, side, current_clock[0])
                open_poss.warnings.append("orphan_free_throw_no_open_possession")

            awarded = int(params.get("freeThrowsAwarded") or 0)
            number = int(params.get("freeThrowNumber") or 0)
            is_final = awarded > 0 and number == awarded
            made = params.get("made") == "made"
            open_poss.fta += 1
            if made:
                open_poss.ftm += 1
                open_poss.points += 1
                if open_poss.oreb_seen:
                    open_poss.points_after_first_oreb += 1
                if params.get("fastBreak"):
                    open_poss.fast_break_points += 1
                score[side] += 1
            if is_final:
                if made:
                    close("made_ft")
                # else: leave open, awaiting the rebound to resolve it (or,
                # if this was itself the orphan-possession fallback, we
                # accept an unresolved outcome and close defensively at the
                # next quarter boundary / next real event forces a new one).
            continue

        if action_type == "rebound":
            if side is None:
                global_warnings.append(f"id={action.get('id')}: rebound with no team, dropped")
                continue
            reb_type = params.get("type")
            if reb_type == "offensive":
                if open_poss is not None and open_poss.offense == side:
                    open_poss.orb += 1
                    open_poss.had_oreb = True
                    open_poss.oreb_seen = True
                else:
                    global_warnings.append(
                        f"id={action.get('id')}: offensive rebound with no matching open possession"
                    )
                    open_new(quarter, side, current_clock[0])
                    open_poss.warnings.append("oreb_opened_new_possession_unexpectedly")
                    open_poss.orb += 1
                    open_poss.had_oreb = True
                    open_poss.oreb_seen = True
            elif reb_type == "defensive":
                if open_poss is not None:
                    close("defensive_rebound")
                open_new(quarter, side, current_clock[0])
            else:
                # Unattributed rebound type (e.g. a "team" rebound) — per
                # boxscore.py's existing convention, not counted as ORB/DRB.
                # Best-effort possession handling: if the recovering side
                # differs from the current offense, treat it like a
                # defensive rebound (possession changes hands); otherwise
                # leave state untouched. Always flagged.
                global_warnings.append(f"id={action.get('id')}: unattributed rebound type {reb_type!r}")
                if open_poss is not None and open_poss.offense != side:
                    close("unattributed_rebound_change")
                    open_new(quarter, side, current_clock[0])
            continue

        if action_type == "turnover":
            if side is None:
                global_warnings.append(f"id={action.get('id')}: turnover with no team, dropped")
                continue
            if open_poss is None or open_poss.offense != side:
                # No shot/FT was ever attempted this possession (e.g. a
                # 5-second violation, or any turnover immediately following
                # the previous possession's made basket) — it is still a
                # real, zero-FGA possession and must not be silently lost.
                if open_poss is not None:
                    open_poss.warnings.append("turnover_team_mismatch_forced_new_possession")
                    close("forced_new_possession")
                open_new(quarter, side, current_clock[0])
            open_poss.turnover = True
            close("turnover")
            open_new(quarter, _OTHER_SIDE[side], current_clock[0], followed_turnover=True)
            continue

        if action_type == "assist":
            parent_id = params.get("parentActionId") or action.get("parentActionId")

            # AST/TO numerator (2026-08-15 management decision): every
            # assist action counts here, independent of whether shot
            # attribution below resolves — a resolution failure must never
            # remove a real, provider-recorded assist from AST/TO. Target
            # is whichever possession is "current" for the assisting team:
            # the open one if it belongs to them, else their most recently
            # closed one (an assist always refers to something that just
            # happened for that team).
            if side is not None:
                if open_poss is not None and open_poss.offense == side:
                    open_poss.raw_assist_count += 1
                elif side in last_closed_by_team:
                    last_closed_by_team[side].raw_assist_count += 1
                else:
                    global_warnings.append(
                        f"id={action.get('id')}: assist with no possession context "
                        f"(team={side!r}) to attribute raw_assist_count to"
                    )

            # Shot attribution (assisted/unassisted FGM split) — a separate,
            # stricter concept; see scoring_sources.AssistedProfile.
            target = pending_assist_targets.get(parent_id)
            if target is not None:
                p, _ = target
                if p.unassisted_fgm > 0:
                    p.unassisted_fgm -= 1
                    p.assisted_fgm += 1
                    if p.made_shot_is_three:
                        p.fg3m_unassisted -= 1
                        p.fg3m_assisted += 1
                    else:
                        p.fg2m_unassisted -= 1
                        p.fg2m_assisted += 1
            elif open_poss is not None and open_poss.last_made_shot_action_id == parent_id:
                # And-1 case: the shot's possession is still open (awaiting
                # its continuation free throw), so the target is the
                # in-progress _Open, not yet a closed Possession.
                if open_poss.unassisted_fgm > 0:
                    open_poss.unassisted_fgm -= 1
                    open_poss.assisted_fgm += 1
                    if open_poss.last_made_shot_is_three:
                        open_poss.fg3m_unassisted -= 1
                        open_poss.fg3m_assisted += 1
                    else:
                        open_poss.fg2m_unassisted -= 1
                        open_poss.fg2m_assisted += 1
            else:
                # Real observed case (game 136): parentActionId points to a
                # `foul` action, not a made shot — apparently Segev's
                # representation for an and-1-adjacent assist. Deliberately
                # not guessed at; counted, not silently dropped or force-
                # attributed to an unassisted shot's flip.
                parent_action = action_by_id.get(parent_id)
                parent_type = parent_action.get("type") if parent_action else "missing"
                global_warnings.append(
                    f"id={action.get('id')}: assist parentActionId={parent_id} "
                    f"is not a linked made shot (parent type={parent_type!r})"
                )
                unresolved_assist_count += 1
            continue

        # foul, foul-drawn, steal, deflection, timeout, substitution, clock,
        # block, game, other quarter markers: no possession-boundary effect.

    if open_poss is not None:
        close("quarter_end")

    return PossessionBuildResult(
        possessions=possessions, warnings=global_warnings, unresolved_assist_count=unresolved_assist_count
    )
