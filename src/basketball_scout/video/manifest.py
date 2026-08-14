"""Game manifest: the internal record linking game identity, PBP source, video
and calibration (docs/VIDEO_STAGE_PLAN.md §5.2, §7.4).

One manifest file holds a list of game entries. For CP1 this holds exactly one
game; CP4 scales it to seven without changing the shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sync import GameSync


class ManifestError(ValueError):
    """Raised for a malformed manifest entry."""


@dataclass
class TeamRef:
    team_id: str
    segev_team_id: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "segev_team_id": self.segev_team_id, "name": self.name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamRef:
        return cls(team_id=data["team_id"], segev_team_id=data["segev_team_id"], name=data["name"])


@dataclass
class VideoRef:
    provider: str
    url: str
    verified_full_game: bool = False
    duration_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "url": self.url,
            "verified_full_game": self.verified_full_game,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoRef:
        return cls(
            provider=data["provider"],
            url=data["url"],
            verified_full_game=data.get("verified_full_game", False),
            duration_s=data.get("duration_s"),
        )


@dataclass
class GameManifestEntry:
    game_id: str
    season: str
    competition: str
    date_utc: str
    segev_game_id: int
    home: TeamRef
    away: TeamRef
    video: VideoRef
    sync: GameSync | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "season": self.season,
            "competition": self.competition,
            "date_utc": self.date_utc,
            "source": {"segev_game_id": self.segev_game_id},
            "home": self.home.to_dict(),
            "away": self.away.to_dict(),
            "video": self.video.to_dict(),
            "sync": self.sync.to_dict() if self.sync else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameManifestEntry:
        try:
            return cls(
                game_id=data["game_id"],
                season=data["season"],
                competition=data["competition"],
                date_utc=data["date_utc"],
                segev_game_id=data["source"]["segev_game_id"],
                home=TeamRef.from_dict(data["home"]),
                away=TeamRef.from_dict(data["away"]),
                video=VideoRef.from_dict(data["video"]),
                sync=GameSync.from_dict(data["sync"]) if data.get("sync") else None,
            )
        except KeyError as exc:
            raise ManifestError(f"manifest entry missing required field: {exc}") from exc

    def team_for_side(self, side: str) -> TeamRef:
        if side == "home":
            return self.home
        if side == "away":
            return self.away
        raise ManifestError(f"unknown team_side {side!r}")


@dataclass
class Manifest:
    games: list[GameManifestEntry] = field(default_factory=list)

    def get(self, game_id: str) -> GameManifestEntry:
        for game in self.games:
            if game.game_id == game_id:
                return game
        raise ManifestError(f"game_id {game_id!r} not found in manifest")

    def upsert(self, entry: GameManifestEntry) -> None:
        for i, game in enumerate(self.games):
            if game.game_id == entry.game_id:
                self.games[i] = entry
                return
        self.games.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {"games": [g.to_dict() for g in self.games]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        return cls(games=[GameManifestEntry.from_dict(g) for g in data.get("games", [])])


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        return Manifest()
    return Manifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_manifest(path: Path, manifest: Manifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path
