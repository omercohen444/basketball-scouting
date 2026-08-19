"""Runtime access to the committed analytics artifacts.

Deliberately the same shape as ``agents/pack_store.py``: a lazy index, an
``available`` property that swallows the load error, and a cached per-team
getter. The whole web layer already degrades gracefully around that pattern, so
a missing or corrupt analytics artifact behaves like a missing evidence pack —
the affected pages say so, and everything else keeps working.

Read-only. Never touches the play-by-play cache, never computes a metric, never
calls a provider.
"""

from __future__ import annotations

import json
from pathlib import Path

from .build import content_hash
from .schema import INDEX_FILENAME, AnalyticsArtifact, AnalyticsIndex, TeamAnalytics

DEFAULT_ANALYTICS_DIRNAME = "analytics"


class AnalyticsArtifactError(RuntimeError):
    """An artifact is missing, unreadable, the wrong version, or tampered with."""


def default_analytics_dir(data_dir: Path) -> Path:
    return data_dir / DEFAULT_ANALYTICS_DIRNAME


def load_index(analytics_dir: Path) -> AnalyticsIndex:
    path = analytics_dir / INDEX_FILENAME
    if not path.is_file():
        raise AnalyticsArtifactError(f"no analytics index at {path}")
    try:
        return AnalyticsIndex.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - any malformed index is the same failure
        raise AnalyticsArtifactError(f"unreadable analytics index at {path}: {exc}") from exc


def load_artifact(path: Path, *, verify: bool = True) -> AnalyticsArtifact:
    if not path.is_file():
        raise AnalyticsArtifactError(f"no analytics artifact at {path}")
    try:
        artifact = AnalyticsArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        raise AnalyticsArtifactError(f"unreadable analytics artifact at {path}: {exc}") from exc

    if verify:
        actual = content_hash(artifact.team)
        if actual != artifact.content_hash:
            raise AnalyticsArtifactError(
                f"analytics artifact at {path} does not match its own hash "
                f"(declared {artifact.content_hash[:23]}…, computed {actual[:23]}…)"
            )
    return artifact


class AnalyticsStore:
    """Loads once, caches, verifies. Constructed at app start alongside PackStore."""

    def __init__(self, analytics_dir: Path, *, verify: bool = True) -> None:
        self.analytics_dir = Path(analytics_dir)
        self._verify = verify
        self._index: AnalyticsIndex | None = None
        self._cache: dict[str, AnalyticsArtifact] = {}

    @property
    def index(self) -> AnalyticsIndex:
        if self._index is None:
            self._index = load_index(self.analytics_dir)
        return self._index

    @property
    def available(self) -> bool:
        """True when the artifacts are present and readable.

        Swallowing the error here is what lets the site serve scouting reports
        from a deployment that has no analytics artifact, rather than 500ing.
        """
        try:
            return bool(self.index.teams)
        except AnalyticsArtifactError:
            return False

    def team_ids(self) -> list[str]:
        return [e.team_id for e in self.index.teams]

    def entries(self):
        return list(self.index.teams)

    def has_team(self, team_id: str) -> bool:
        return any(e.team_id == team_id for e in self.index.teams)

    def get(self, team_id: str) -> AnalyticsArtifact:
        if team_id in self._cache:
            return self._cache[team_id]
        entry = next((e for e in self.index.teams if e.team_id == team_id), None)
        if entry is None:
            raise AnalyticsArtifactError(f"unknown team {team_id!r} in the analytics index")
        artifact = load_artifact(self.analytics_dir / entry.file, verify=self._verify)
        if artifact.content_hash != entry.content_hash:
            raise AnalyticsArtifactError(
                f"analytics artifact for {team_id} disagrees with the index hash"
            )
        self._cache[team_id] = artifact
        return artifact

    def team(self, team_id: str) -> TeamAnalytics:
        return self.get(team_id).team

    def load_all(self) -> dict[str, TeamAnalytics]:
        return {e.team_id: self.team(e.team_id) for e in self.index.teams}

    def health(self) -> dict[str, object]:
        if not self.available:
            return {"available": False, "teams_n": 0, "season": None, "artifact_version": None}
        index = self.index
        return {
            "available": True,
            "teams_n": len(index.teams),
            "season": index.season,
            "artifact_version": index.artifact_version,
        }
