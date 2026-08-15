"""Two bounded-audit deterministic views: response after an opponent
scoring run, and the offensive possession immediately following a team's
own timeout (2026-08-15 v2 §7-8).

Both reduce to the same primitive: given a game-clock moment (quarter,
seconds remaining), find the first possession for a specific team that
starts at or after it. Possessions are ordered by ``(quarter,
start_clock_s descending)`` — clock counts down, so "later in game time"
means a smaller remaining-clock value within the same quarter, or any
later quarter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .possession import Possession, parse_clock_mmss
from .scoring_timeline import ScoringPlay

_OTHER_SIDE = {"home": "away", "away": "home"}

RUN_THRESHOLD_POINTS = 8


def _first_possession_at_or_after(
    possessions: list[Possession], quarter: int, clock_s: float
) -> Possession | None:
    """The single earliest possession in ``possessions`` starting at or
    after ``(quarter, clock_s)``.

    Generic over whatever list is passed — it does not itself know or care
    whose possessions they are. This matters: :func:`build_run_response_profile`
    deliberately passes one team's own possessions only (it wants "when
    does this specific team next have the ball", which is well-defined on
    its own), while :func:`build_after_own_timeout_profile` must pass the
    full both-teams-merged list and check the result's ``offense_team``
    afterward — passing a pre-filtered single-team list there would
    silently skip over an intervening opponent possession and misattribute
    a later same-team possession as if it directly followed the timeout
    (see that function's docstring for the real bug this was, 2026-08-15).
    """
    candidates = [
        p for p in possessions
        if p.start_clock_s is not None
        and (p.quarter > quarter or (p.quarter == quarter and p.start_clock_s <= clock_s))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.quarter, -p.start_clock_s, p.possession_index))
    return candidates[0]


# ---- §7: response after an opponent 8+ run ---------------------------------

def find_run_crossings(plays: list[ScoringPlay], *, threshold: int = RUN_THRESHOLD_POINTS) -> list[ScoringPlay]:
    """The scoring play that FIRST pushes a continuing run to >= threshold.

    Exactly one crossing per qualifying run — a run continuing past the
    threshold (e.g. 8-0 becoming 16-0) is never retriggered, because
    ``triggered`` only resets when the run itself ends (the other team
    scores), not when the running total merely keeps growing.
    """
    crossings: list[ScoringPlay] = []
    run_team: str | None = None
    run_total = 0
    triggered = False
    for play in plays:
        if play.team == run_team:
            run_total += play.points
        else:
            run_team = play.team
            run_total = play.points
            triggered = False
        if run_total >= threshold and not triggered:
            crossings.append(play)
            triggered = True
    return crossings


@dataclass(frozen=True)
class RunResponseProfile:
    """Team's own next possession immediately after conceding a qualifying
    opponent run. ``responses_found`` may be less than the number of
    qualifying opponent runs — a run at the very end of a quarter/game
    with no subsequent possession is excluded, not fabricated.
    """

    opponent_runs_conceded: int
    responses_found: int
    response_points: int
    response_scoring_rate: float | None  # share of response possessions with >=1 point
    response_turnover_rate: float | None
    points_per_response_possession: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_run_response_profile(
    team_possessions: list[Possession], plays: list[ScoringPlay], *, team_side: str
) -> RunResponseProfile:
    opponent_side = _OTHER_SIDE[team_side]
    crossings = [p for p in find_run_crossings(plays) if p.team == opponent_side]

    responses: list[Possession] = []
    for crossing in crossings:
        resp = _first_possession_at_or_after(team_possessions, crossing.quarter, crossing.clock_s)
        if resp is not None:
            responses.append(resp)

    n = len(responses)
    points = sum(p.points for p in responses)
    scoring = sum(1 for p in responses if p.points > 0)
    turnovers = sum(1 for p in responses if p.turnover)

    return RunResponseProfile(
        opponent_runs_conceded=len(crossings),
        responses_found=n,
        response_points=points,
        response_scoring_rate=(scoring / n) if n > 0 else None,
        response_turnover_rate=(turnovers / n) if n > 0 else None,
        points_per_response_possession=(points / n) if n > 0 else None,
    )


# ---- §8: possession immediately following the team's own timeout ----------

@dataclass(frozen=True)
class AfterOwnTimeoutProfile:
    """Offensive possessions immediately following this team's OWN called
    timeout.

    Definition (fixed 2026-08-15 after a real bug was found — see below):
    for each of this team's own timeouts (``team`` attribution present —
    ~3.9% of all timeouts have none and are excluded, never guessed), find
    the single earliest possession — of *either* team — starting at or
    after the timeout, over the FULL game's possession list. Only that one
    possession is ever eligible; it counts toward this profile if and only
    if its own offense equals the calling team. If the immediately-next
    possession belongs to the opponent (a defensive/reactive timeout), or
    the game/quarter ends first, this timeout contributes nothing to the
    sample — it is never satisfied by some *later* possession of the
    calling team.

    This also correctly and automatically excludes mid-free-throw-trip
    timeouts (~3.3% of all timeouts): the trip's own already-in-progress
    possession does not "start" after the timeout (it started earlier), so
    it is not a candidate; whichever possession genuinely starts next is
    evaluated normally.

    The bug this replaced: an earlier version searched only the calling
    team's own pre-filtered possession list, which cannot see an
    intervening opponent possession at all — it would silently match a
    later same-team possession as if it directly followed the timeout,
    even when the opponent possessed the ball first. Fixed by searching
    the merged, both-teams possession list and checking team match only
    on the single nearest result.

    Deduplication (second bug found and fixed the same day): consecutive
    timeouts before play resumes — by this team alone, or interleaved with
    the opponent's — all resolve to the same one real possession. That
    possession contributes to ``after_timeout_possessions_found`` and the
    points/FGA/FGM/turnover sums **at most once**, never once per timeout
    action. ``own_timeouts_with_team`` is unaffected by this — it counts
    every individual timeout action, a separate, legitimate fact (a large
    gap between it and ``after_timeout_possessions_found`` is itself a
    signal that timeouts were often called in bunches).
    """

    own_timeouts_with_team: int
    after_timeout_possessions_found: int
    points: int
    fga: int
    fgm: int
    turnovers: int
    points_per_possession: float | None
    efg_pct: float | None
    tov_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


_TEAM_SIDE = {1: "home", 2: "away"}


def build_after_own_timeout_profile(
    actions: list[dict[str, Any]], all_possessions: list[Possession], *, team_side: str
) -> AfterOwnTimeoutProfile:
    """``all_possessions`` must be the FULL, both-teams-merged possession
    list for the game (``build_possessions(actions, ...).possessions``) —
    never a single team's pre-filtered subset. See
    :class:`AfterOwnTimeoutProfile`'s docstring for exactly why.
    """
    ordered = sorted(
        (a for a in actions if isinstance(a.get("quarter"), int) and a.get("type") == "timeout"),
        key=lambda a: (a["quarter"], a["id"]),
    )

    own_timeouts = 0
    responses: list[Possession] = []
    counted_possession_indices: set[int] = set()  # dedup: see docstring below
    for action in ordered:
        params = action.get("parameters") or {}
        side = _TEAM_SIDE.get(params.get("team"))
        if side != team_side:
            continue
        own_timeouts += 1
        clock = parse_clock_mmss(action.get("quarterTime"))
        if clock is None:
            continue
        resp = _first_possession_at_or_after(all_possessions, action["quarter"], clock)
        if resp is None or resp.offense_team != team_side:
            continue
        # Consecutive timeouts (by this team, or interleaved with the
        # opponent's) before play resumes all resolve to the SAME single
        # real possession — it must contribute to the sample at most once,
        # never once per timeout action. Real bug found here 2026-08-15:
        # two of this team's own timeouts in a row, with no possession
        # between them, doubled points/FGA/FGM by counting that one
        # possession twice. `own_timeouts_with_team` still counts every
        # timeout action (a legitimate, separate fact); only the possession
        # sample is deduplicated.
        if resp.possession_index in counted_possession_indices:
            continue
        counted_possession_indices.add(resp.possession_index)
        responses.append(resp)

    n = len(responses)
    points = sum(p.points for p in responses)
    fga = sum(p.fga for p in responses)
    fgm = sum(p.fgm for p in responses)
    fg3m = sum(p.fg3m for p in responses)
    fta = sum(p.fta for p in responses)
    tov = sum(1 for p in responses if p.turnover)

    return AfterOwnTimeoutProfile(
        own_timeouts_with_team=own_timeouts,
        after_timeout_possessions_found=n,
        points=points, fga=fga, fgm=fgm, turnovers=tov,
        points_per_possession=(points / n) if n > 0 else None,
        efg_pct=((fgm + 0.5 * fg3m) / fga) if fga > 0 else None,
        tov_pct=(tov / (fga + 0.44 * fta + tov)) if (fga + 0.44 * fta + tov) > 0 else None,
    )
