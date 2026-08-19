"""The repository contract.

Five methods. Any storage backend that satisfies this protocol can serve the
product, which is what makes the offline test suite possible: every API test
runs against the in-memory implementation with no network and no credentials.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import GenerationRun, ReportRef, StoredReport, TeamRecord


class RepositoryError(RuntimeError):
    """Storage failed for an external reason (network, auth, schema).

    Carries a message safe to log. It is never returned to a public caller
    verbatim — ``web/errors.py`` maps it to a generic 503.
    """


class SchemaMissingError(RepositoryError):
    """The target tables do not exist yet.

    Distinguished from a generic failure because the remedy is a specific
    one-step action (apply ``supabase/migrations/0001_init.sql``), and because a
    deployment that has not been migrated should say so rather than look broken.
    """


@runtime_checkable
class ReportRepository(Protocol):
    """Storage operations the product needs. Deliberately five, not fifty."""

    name: str

    def list_teams(self) -> list[TeamRecord]:
        """Every selectable opponent, ordered by display name."""
        ...

    def get_latest_report(self, team_id: str) -> StoredReport | None:
        """Most recent published report for a team, or ``None``."""
        ...

    def latest_report_refs(self) -> dict[str, ReportRef]:
        """The newest published report per team, as ``{team_id: ReportRef}``.

        One call, not one per team: the opponent list needs this for every team
        on every page load.
        """
        ...

    def get_report(self, report_id: str) -> StoredReport | None:
        """One report by its id, or ``None``."""
        ...

    def save_report(self, report: StoredReport) -> StoredReport:
        """Persist a report. Callers must only pass validated reports."""
        ...

    def record_generation_run(self, run: GenerationRun) -> None:
        """Append an audit row for a generation attempt (success or failure).

        Best-effort by contract: a failure here must never lose an otherwise
        successful report.
        """
        ...
