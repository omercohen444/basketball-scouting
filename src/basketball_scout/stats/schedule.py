"""Multi-game discovery adapter for the Segev numeric ``game_id`` space.

Findings from bounded live investigation this session (see WORKLOG.md for the
full account — summarized here because this module's design depends on it):

* There is **no** clean schedule/game-list JSON-RPC method on the Segev API.
  Ten plausible method names (``getGames``, ``getSchedule``,
  ``getCompetitions``, ...) were tried against
  ``https://stats.segevstats.com/realtimestat_heb/api/`` — every one returned
  JSON-RPC error ``-33000 method not found``.
* ``https://basket.co.il/pbp/json/games_all.json`` **is** a real, public,
  unauthenticated endpoint (found via the site's own ``games.js``), but it is
  a "next games" widget feed — 12 upcoming fixtures for the *next* season at
  the time of writing, not a historical archive. It cannot enumerate a
  completed season.
* The Segev numeric ``game_id`` space itself, however, is dense and
  self-describing: probing ids in the 30-270 range (bounded, ~25 requests,
  0.25s spacing) found no gaps, and **every** ``gameInfo`` response carries
  its own ``competition.name`` — "Winner League", "Winner Cup", "Women",
  "Leumit", "School" were all observed interleaved in the same id range.
  Filtering on the game's own declared ``competition.name`` (a field we
  already read from the authoritative source, not an inferred mapping) is a
  clean, reliable, non-brittle discovery strategy. This is deliberately
  **not** the basket.co.il-widget-``id`` <-> Segev-``game_id`` mapping the
  build brief explicitly forbids inferring — those are two different id
  spaces entirely (confirmed: widget ids run ~26000+, Segev ids ~30-270 for
  the whole 2025-26 season) and no such mapping is used here.

2026-08-15 management review — target changed to the full double round-robin
(182 games) and a round/phase field was explicitly re-checked before falling
back to a heuristic:

* Neither ``getActions``' ``gameInfo`` nor ``getBoxScore`` (checked fresh
  this review — its ``result.boxscore.gameInfo`` has ``homeScore``,
  ``awayScore``, timeouts, ``currentQuarter``, ``gameFinished``,
  ``gameId``/``homeTeamId``/``awayTeamId`` — no round, phase, or matchday
  field of any kind) exposes an authoritative round/phase identifier. One
  bounded request each; not pursued further per instruction.
* **Selection rule used instead** — :func:`select_double_round_robin_games`:
  group every discovered "Winner League" + finished game by its *unordered
  team-id pair* (``frozenset({home_team_id, away_team_id})``), sort each
  pair's meetings chronologically by ``gameInfo.time``, and keep only the
  **first two**. This is deterministic, uses only data already in hand, and
  needs no knowledge of what "playoffs" are called: a genuine regular-season
  double round-robin gives every pair exactly two meetings; any 3rd+ meeting
  between a pair (playoff rematches, play-in, etc.) is excluded purely by
  chronological position, not by guessing a competition-stage label. The
  resulting counts (14 teams, 91 pairs, 182 games, 26 games/team) are the
  verification, not an assumption baked into the rule.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..config import Settings
from ..pbp.segev import SegevError, SegevNotFound, SegevUnavailable, fetch_and_cache_actions

DEFAULT_COMPETITION = "Winner League"
DEFAULT_REQUEST_DELAY_SECONDS = 0.2


@dataclass(frozen=True)
class DiscoveredGame:
    """One game found during a range scan, with enough gameInfo to filter/sort.

    2026-08-15 management review (targeted recovery of ids 148/178/209/224):
    ``gameInfo.gameFinished`` was found to be a stale/wrong flag for a real
    subset of games whose action stream is otherwise complete (all quarters
    present with their own ``end-of-quarter`` marker, final score reconciling
    exactly between ``getActions`` and ``getBoxScore``). Trusting that flag
    alone silently dropped real regular-season games. ``is_usable`` now also
    accepts a game whose own action stream self-certifies completeness via
    :attr:`quarters_verified_complete` — a fact read directly from the source
    data, not inferred or fabricated.
    """

    game_id: int
    competition: str | None
    game_date: str | None
    game_finished: bool
    home_team: str
    away_team: str
    # Source-numeric team ids (e.g. "2"), not names — names can vary in
    # casing/formatting; ids are the reliable key for grouping a team's
    # games across the season. Optional only so a partially-populated
    # DiscoveredGame remains constructible in tests that don't need pairing.
    home_team_id: str | None = None
    away_team_id: str | None = None
    # True iff every quarter number that appears anywhere in the action
    # stream (1..max observed) has its own "end-of-quarter" marker action —
    # i.e. the action stream itself, independent of the (sometimes stale)
    # gameFinished flag, shows no quarter was left open. None means "not
    # computed" (e.g. a DiscoveredGame built without the action list, as in
    # tests that only need the gameInfo-level fields).
    quarters_verified_complete: bool | None = None

    @property
    def is_usable(self) -> bool:
        """PBP-bearing game in the target competition, confirmed complete.

        Completeness is ``gameFinished`` (the common, reliable case) OR the
        action stream's own quarter markers self-certifying completeness
        (the fallback for the stale-flag case found in this review).
        """
        if self.competition != DEFAULT_COMPETITION:
            return False
        return bool(self.game_finished or self.quarters_verified_complete)

    @property
    def team_pair(self) -> frozenset[str] | None:
        """Unordered ``{home_team_id, away_team_id}`` key, or ``None`` if either id is missing."""
        if not self.home_team_id or not self.away_team_id:
            return None
        return frozenset({self.home_team_id, self.away_team_id})


def _competition_name(game_info: dict[str, Any]) -> str | None:
    competition = game_info.get("competition")
    if isinstance(competition, dict):
        return competition.get("name") or competition.get("nameLocal")
    return competition


def _quarters_verified_complete(actions: list[dict[str, Any]]) -> bool:
    """True iff every quarter number seen in the action stream (1..max) has
    its own ``end-of-quarter`` marker action.

    Purely source-observable: reads only ``type``/``quarter``/``parameters``
    already present on real action rows, never infers or fabricates a value.
    An empty action list is not complete (``False``), matching
    ``SegevUnavailable``'s existing "no actions key = not usable" contract.
    """
    quarters_seen = {a.get("quarter") for a in actions if isinstance(a.get("quarter"), int)}
    if not quarters_seen:
        return False
    closed_quarters = {
        a.get("quarter")
        for a in actions
        if a.get("type") == "quarter" and (a.get("parameters") or {}).get("type") == "end-of-quarter"
    }
    return set(range(1, max(quarters_seen) + 1)) <= closed_quarters


def discover_games(
    game_ids: Iterable[int],
    settings: Settings,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[DiscoveredGame]:
    """Range-scan Segev ``game_id`` values, caching each result to disk.

    Every id is fetched through :func:`fetch_and_cache_actions` (so a
    re-run of discovery is free for already-cached ids) and classified:
    not found, unplayed/no PBP, or a real result with its own declared
    competition. Nothing is skipped or filtered here except request
    failures — filtering to "Winner League and finished" is the caller's
    job via :attr:`DiscoveredGame.is_usable`, so this function stays a
    faithful, unopinionated record of what the range scan actually found.

    ``on_progress(game_id, status)`` is called after every id if given —
    useful for a CLI to print progress during a scan of dozens of ids.
    """
    found: list[DiscoveredGame] = []
    for game_id in game_ids:
        try:
            result = fetch_and_cache_actions(game_id, settings)
        except SegevNotFound:
            if on_progress:
                on_progress(game_id, "not_found")
            time.sleep(delay_seconds)
            continue
        except SegevUnavailable:
            if on_progress:
                on_progress(game_id, "unplayed_no_pbp")
            time.sleep(delay_seconds)
            continue
        except SegevError as exc:
            if on_progress:
                on_progress(game_id, f"error:{exc}")
            time.sleep(delay_seconds)
            continue

        game_info = result.get("gameInfo") or {}
        home_team_info = game_info.get("homeTeam") or {}
        away_team_info = game_info.get("awayTeam") or {}
        actions = result.get("actions") or []
        discovered = DiscoveredGame(
            game_id=game_id,
            competition=_competition_name(game_info),
            game_date=game_info.get("time"),
            game_finished=bool(game_info.get("gameFinished")),
            home_team=str(home_team_info.get("name") or ""),
            away_team=str(away_team_info.get("name") or ""),
            home_team_id=str(home_team_info.get("id")) if home_team_info.get("id") is not None else None,
            away_team_id=str(away_team_info.get("id")) if away_team_info.get("id") is not None else None,
            quarters_verified_complete=_quarters_verified_complete(actions),
        )
        found.append(discovered)
        if on_progress:
            status = "usable" if discovered.is_usable else f"skip:{discovered.competition}"
            on_progress(game_id, status)
        time.sleep(delay_seconds)

    return found


@dataclass(frozen=True)
class RegularSeasonSelection:
    """Result of :func:`select_double_round_robin_games` — the selection and its evidence.

    ``meetings_per_pair`` and ``games_per_team`` are the verification data,
    not an input assumption: a genuine double round-robin makes them come
    out to 91 pairs x2 and 26 games/team on their own; nothing here forces
    that shape.
    """

    selected_game_ids: list[int]
    excluded_extra_meeting_ids: list[int]
    team_ids: list[str]
    meetings_per_pair: dict[frozenset[str], int]
    games_per_team: dict[str, int] = field(default_factory=dict)

    @property
    def pair_count(self) -> int:
        return len(self.meetings_per_pair)

    @property
    def pairs_with_exactly_two_meetings(self) -> int:
        return sum(1 for n in self.meetings_per_pair.values() if n == 2)

    @property
    def pairs_with_unexpected_meeting_count(self) -> dict[frozenset[str], int]:
        """Pairs that met a number of times other than exactly 2 — worth a human look."""
        return {pair: n for pair, n in self.meetings_per_pair.items() if n != 2}


def select_double_round_robin_games(
    discovered: list[DiscoveredGame],
) -> RegularSeasonSelection:
    """Deterministically select the double round-robin regular season.

    Input must already be filtered to usable games (``discovered game.is_usable``
    — finished, target competition) with both team ids populated; games
    missing either id or failing ``is_usable`` are silently excluded (they
    were never real candidates). See the module docstring for the exact
    selection rule and why it needs no round/phase field.
    """
    usable = [g for g in discovered if g.is_usable and g.team_pair is not None]

    by_pair: dict[frozenset[str], list[DiscoveredGame]] = defaultdict(list)
    for g in usable:
        by_pair[g.team_pair].append(g)

    selected_ids: list[int] = []
    excluded_ids: list[int] = []
    meetings_per_pair: dict[frozenset[str], int] = {}
    games_per_team: dict[str, int] = defaultdict(int)

    for pair, meetings in by_pair.items():
        meetings.sort(key=lambda g: g.game_date or "")
        keep, drop = meetings[:2], meetings[2:]
        selected_ids.extend(g.game_id for g in keep)
        excluded_ids.extend(g.game_id for g in drop)
        meetings_per_pair[pair] = len(meetings)
        for g in keep:
            games_per_team[g.home_team_id] += 1
            games_per_team[g.away_team_id] += 1

    team_ids = sorted({t for pair in by_pair for t in pair})
    return RegularSeasonSelection(
        selected_game_ids=sorted(selected_ids),
        excluded_extra_meeting_ids=sorted(excluded_ids),
        team_ids=team_ids,
        meetings_per_pair=meetings_per_pair,
        games_per_team=dict(games_per_team),
    )
