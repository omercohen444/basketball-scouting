"""Flat-file JSON persistence for TeamGameStats — no database yet.

PROJECT_SPEC.md: "Do not introduce a database yet unless absolutely
required." One JSON file per game holds both teams' records, so re-running
ingestion for an already-processed game is a plain overwrite (idempotent),
and a single game is easy to inspect by hand without a query tool.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import TeamGameStats


def game_file_path(stats_dir: Path, source_provider: str, source_game_id: str) -> Path:
    return stats_dir / f"{source_provider}_{source_game_id}.json"


def save_game(stats_dir: Path, home: TeamGameStats, away: TeamGameStats) -> Path:
    """Write both teams' records for one game to a single JSON file."""
    path = game_file_path(stats_dir, home.source_provider, home.source_game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"home": home.to_dict(), "away": away.to_dict()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_game(path: Path) -> tuple[TeamGameStats, TeamGameStats]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TeamGameStats.from_dict(data["home"]), TeamGameStats.from_dict(data["away"])


def load_all_games(stats_dir: Path) -> list[TeamGameStats]:
    """Flat list of every team-game record (2 rows per game file) under ``stats_dir``.

    Returns an empty list (not an error) if ``stats_dir`` does not exist yet
    — no games processed is a valid, unremarkable starting state.
    """
    games: list[TeamGameStats] = []
    if not stats_dir.is_dir():
        return games
    for path in sorted(stats_dir.glob("*.json")):
        home, away = load_game(path)
        games.append(home)
        games.append(away)
    return games
