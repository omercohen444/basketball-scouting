"""In-memory repository.

Two jobs:

1. the deterministic target for every offline test, so the API suite needs no
   credentials and no network;
2. the fallback when Supabase is not configured, so the app still boots, serves
   ``/health`` and renders an honest empty state instead of crashing.

Thread-safe because FastAPI runs sync endpoints in a threadpool and generation
writes from one of those threads.
"""

from __future__ import annotations

import threading

from .models import GenerationRun, ReportRef, StoredReport, TeamRecord


class InMemoryReportRepository:
    """Process-local storage. Contents are lost on restart, by design."""

    name = "memory"

    def __init__(self, teams: list[TeamRecord] | None = None):
        self._lock = threading.RLock()
        self._teams: list[TeamRecord] = list(teams or [])
        self._reports: dict[str, StoredReport] = {}
        self._runs: list[GenerationRun] = []
        # generated_at has second precision, so two reports saved inside the
        # same second tie. Insertion order is the tiebreaker: "latest" means the
        # most recently saved, never an arbitrary uuid ordering.
        self._sequence: dict[str, int] = {}
        self._next_sequence = 0

    # -- ReportRepository ----------------------------------------------------

    def list_teams(self) -> list[TeamRecord]:
        with self._lock:
            return sorted((t for t in self._teams if t.active), key=lambda t: t.team_name)

    def get_latest_report(self, team_id: str) -> StoredReport | None:
        with self._lock:
            candidates = [
                r
                for r in self._reports.values()
                if r.team_id == team_id and r.status == "published"
            ]
        if not candidates:
            return None
        # generated_at is an ISO-8601 UTC string, so lexical order is chronological.
        return max(candidates, key=lambda r: (r.generated_at, self._sequence.get(r.report_id, 0)))

    def latest_report_refs(self) -> dict[str, ReportRef]:
        with self._lock:
            published = [r for r in self._reports.values() if r.status == "published"]
            ordered = sorted(
                published,
                key=lambda r: (r.generated_at, self._sequence.get(r.report_id, 0)),
            )
        # Ascending order means the last write per team wins.
        return {
            r.team_id: ReportRef(r.report_id, r.team_id, r.generated_at) for r in ordered
        }

    def get_report(self, report_id: str) -> StoredReport | None:
        with self._lock:
            return self._reports.get(report_id)

    def save_report(self, report: StoredReport) -> StoredReport:
        with self._lock:
            self._reports[report.report_id] = report
            self._sequence[report.report_id] = self._next_sequence
            self._next_sequence += 1
        return report

    def record_generation_run(self, run: GenerationRun) -> None:
        with self._lock:
            self._runs.append(run)

    # -- test/inspection helpers ---------------------------------------------

    @property
    def has_teams(self) -> bool:
        """Bootstrap check that reads state directly.

        Deliberately not ``list_teams()``: app construction must not depend on
        an overridable read succeeding, or a repository that fails on read would
        stop the process from starting at all.
        """
        with self._lock:
            return bool(self._teams)

    def set_teams(self, teams: list[TeamRecord]) -> None:
        with self._lock:
            self._teams = list(teams)

    def runs(self) -> list[GenerationRun]:
        with self._lock:
            return list(self._runs)

    def reports(self) -> list[StoredReport]:
        with self._lock:
            return list(self._reports.values())
