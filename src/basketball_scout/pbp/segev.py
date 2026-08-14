"""SegevSport JSON-RPC PBP client — the only module that knows this wire format.

Verified live against the real API this session (see docs/VIDEO_STAGE_PLAN.md
§5.1). Facts encoded here, not assumed:

* endpoint: ``https://stats.segevstats.com/realtimestat_heb/api/`` with
  ``?method=getActions&game_id=<id>`` or ``?method=getBoxScore&game_id=<id>``;
* public, unauthenticated, but a browser-like ``Referer`` avoids being refused;
* JSON-RPC 2.0 envelope, but ``Content-Type: text/html`` despite a JSON body;
* a "game not found" error is returned with **HTTP 200**, not 404 — a naive
  client that only checks the status code will treat failure as success;
* an unplayed fixture has ``result.gameInfo`` but **no** ``result.actions``
  key at all (not an empty list);
* the response is Hebrew UTF-8 with no charset header, so it must be sniffed
  (same problem the bootstrap probe already solved for basket.co.il).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SegevError(RuntimeError):
    """Base class for all Segev API failures."""


class SegevNotFound(SegevError):
    """The API responded (HTTP 200) with error code -32000 "game not found"."""


class SegevUnavailable(SegevError):
    """``result.gameInfo`` exists but ``result.actions`` is entirely absent.

    This means the fixture has not been played, or has no recorded PBP — not
    that PBP exists and happens to be empty. The two are deliberately not
    conflated: an empty list would silently look like "0 shots", which is a
    very different, much worse failure to debug later.
    """


class SegevRequestError(SegevError):
    """The HTTP request itself failed (network, timeout, non-200, bad JSON)."""


def _session_headers(settings: Settings) -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Referer": settings.segev_referer}


def _get_json(url: str, settings: Settings) -> dict[str, Any]:
    import requests

    try:
        response = requests.get(
            url, headers=_session_headers(settings), timeout=settings.http_timeout_seconds
        )
    except requests.RequestException as exc:
        raise SegevRequestError(f"request to {url} failed: {exc}") from exc

    if response.status_code != 200:
        raise SegevRequestError(f"{url} returned HTTP {response.status_code}")

    # No charset header on this server; sniff to avoid mojibaking Hebrew names.
    if "charset=" not in response.headers.get("Content-Type", "").lower():
        response.encoding = response.apparent_encoding or "utf-8"

    try:
        return response.json()
    except ValueError as exc:
        raise SegevRequestError(f"{url} did not return valid JSON: {exc}") from exc


def _check_envelope(payload: dict[str, Any], game_id: int) -> dict[str, Any]:
    """Validate the JSON-RPC envelope and return ``result``.

    The "not found" case returns HTTP 200 with a populated ``error`` field —
    it must be checked explicitly, not inferred from the HTTP status.
    """
    error = payload.get("error")
    if error:
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        if "not found" in message.lower():
            raise SegevNotFound(f"game_id={game_id}: {message}")
        raise SegevError(f"game_id={game_id}: API error: {message}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise SegevError(f"game_id={game_id}: malformed response, no 'result' object")
    return result


def fetch_actions(game_id: int, settings: Settings) -> dict[str, Any]:
    """Fetch the raw ``getActions`` result for one game.

    Returns ``result`` (containing ``gameInfo`` and, if the game has been
    played, ``actions``). Raises :class:`SegevNotFound` if the id does not
    exist, :class:`SegevUnavailable` if the fixture exists but has no PBP yet.
    """
    url = f"{settings.segev_api_url}?method=getActions&game_id={game_id}"
    payload = _get_json(url, settings)
    result = _check_envelope(payload, game_id)

    if "gameInfo" not in result:
        raise SegevError(f"game_id={game_id}: response has no gameInfo")
    if "actions" not in result:
        raise SegevUnavailable(
            f"game_id={game_id}: gameInfo present but no actions — "
            "fixture likely not yet played"
        )
    return result


def fetch_boxscore(game_id: int, settings: Settings) -> dict[str, Any]:
    """Fetch the raw ``getBoxScore`` result for one game (fallback/cross-check use)."""
    url = f"{settings.segev_api_url}?method=getBoxScore&game_id={game_id}"
    payload = _get_json(url, settings)
    return _check_envelope(payload, game_id)


def raw_cache_path(game_id: int, settings: Settings) -> Path:
    return settings.raw_pbp_dir / f"segev_{game_id}.json"


def fetch_and_cache_actions(
    game_id: int, settings: Settings, *, refresh: bool = False
) -> dict[str, Any]:
    """Fetch ``getActions`` for a game, caching the raw result to disk.

    On a cache hit (and ``refresh=False``) this makes no network call at all —
    the design goal from CP0: once fetched, the source is out of the risk
    chain for the rest of the stage.
    """
    path = raw_cache_path(game_id, settings)
    if path.is_file() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    result = fetch_actions(game_id, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """Small printable summary — used by the fetch CLI and the CP1 report."""
    gi = result.get("gameInfo", {})
    actions = result.get("actions", [])
    quarters = sorted({a.get("quarter") for a in actions if a.get("quarter") is not None})
    competition = gi.get("competition")
    if isinstance(competition, dict):
        competition = competition.get("name") or competition.get("nameLocal")
    type_counts: dict[str, int] = {}
    for a in actions:
        t = a.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    return {
        "game_id": gi.get("gameId"),
        "time": gi.get("time"),
        "game_finished": gi.get("gameFinished"),
        "home_team": gi.get("homeTeam", {}).get("name"),
        "away_team": gi.get("awayTeam", {}).get("name"),
        "competition": competition,
        "quarters": quarters,
        "action_count": len(actions),
        "type_counts": type_counts,
        "shot_count": type_counts.get("shot", 0),
    }
