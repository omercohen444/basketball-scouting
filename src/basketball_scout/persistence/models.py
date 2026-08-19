"""Storage-facing records.

Frozen dataclasses rather than Pydantic models: these are the *storage* shape,
not a request/response contract, and keeping them plain makes the mapping to and
from a database row obvious in one place (``to_row``/``from_row``).

``report_json`` holds the already-public-safe report (see
``reports.contracts.PublicReport``). ``evidence_json`` holds the deterministic
pack artifact it was generated from, so a saved report can be audited — or
re-rendered — without the PBP cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REPORT_STATUS_PUBLISHED = "published"
REPORT_STATUS_ARCHIVED = "archived"

RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_REJECTED = "rejected"
RUN_STATUS_ERROR = "error"
RUN_STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class TeamRecord:
    """One selectable opponent."""

    team_id: str
    team_name: str
    season: str
    games_n: int = 0
    wins: int = 0
    losses: int = 0
    active: bool = True

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}"

    def to_row(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "season": self.season,
            "games_n": self.games_n,
            "wins": self.wins,
            "losses": self.losses,
            "active": self.active,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TeamRecord":
        return cls(
            team_id=row["team_id"],
            team_name=row.get("team_name") or row["team_id"],
            season=row.get("season") or "unknown",
            games_n=int(row.get("games_n") or 0),
            wins=int(row.get("wins") or 0),
            losses=int(row.get("losses") or 0),
            active=bool(row.get("active", True)),
        )


@dataclass(frozen=True)
class StoredReport:
    """A saved scouting report, exactly as it round-trips through the database."""

    report_id: str
    team_id: str
    team_name: str
    season: str
    generated_at: str
    report_version: str
    evidence_version: str
    definition_version: str
    backend: str
    pack_hash: str
    report_json: dict[str, Any]
    validation_summary: dict[str, Any] = field(default_factory=dict)
    evidence_json: dict[str, Any] | None = None
    model_name: str | None = None
    status: str = REPORT_STATUS_PUBLISHED
    created_at: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.report_id,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "season": self.season,
            "generated_at": self.generated_at,
            "report_version": self.report_version,
            "evidence_version": self.evidence_version,
            "definition_version": self.definition_version,
            "backend": self.backend,
            "model_name": self.model_name,
            "pack_hash": self.pack_hash,
            "status": self.status,
            "report_json": self.report_json,
            "evidence_json": self.evidence_json,
            "validation_summary": self.validation_summary,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "StoredReport":
        return cls(
            report_id=str(row["id"]),
            team_id=row["team_id"],
            team_name=row.get("team_name") or row["team_id"],
            season=row.get("season") or "unknown",
            generated_at=row["generated_at"],
            report_version=row.get("report_version") or "unknown",
            evidence_version=row.get("evidence_version") or "unknown",
            definition_version=row.get("definition_version") or "unknown",
            backend=row.get("backend") or "unknown",
            model_name=row.get("model_name"),
            pack_hash=row.get("pack_hash") or "",
            status=row.get("status") or REPORT_STATUS_PUBLISHED,
            report_json=row.get("report_json") or {},
            evidence_json=row.get("evidence_json"),
            validation_summary=row.get("validation_summary") or {},
            created_at=row.get("created_at"),
        )


@dataclass(frozen=True)
class ReportRef:
    """Just enough to answer "does this team have a report, and which one".

    Exists so the team list costs one query instead of one per team — with 14
    teams that was ~14 sequential round-trips on every home-page load.
    """

    report_id: str
    team_id: str
    generated_at: str


@dataclass(frozen=True)
class GenerationRun:
    """One generation attempt — including the ones that produced no report.

    Kept separate from ``StoredReport`` precisely because a rejected run is never
    saved as a report; without this table a failed generation would leave no
    trace at all.
    """

    run_id: str
    team_id: str
    started_at: str
    finished_at: str
    duration_ms: int
    status: str
    backend: str
    provider_calls: int = 0
    stage_attempts: dict[str, int] = field(default_factory=dict)
    rejects_n: int = 0
    warnings_n: int = 0
    model_name: str | None = None
    report_id: str | None = None
    error_message: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.run_id,
            "team_id": self.team_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "backend": self.backend,
            "model_name": self.model_name,
            "provider_calls": self.provider_calls,
            "stage_attempts": self.stage_attempts,
            "rejects_n": self.rejects_n,
            "warnings_n": self.warnings_n,
            "report_id": self.report_id,
            "error_message": self.error_message,
        }
