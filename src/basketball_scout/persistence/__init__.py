"""Persistence for saved scouting reports.

Deliberately small. The product needs five operations — list teams, read the
latest report for a team, read a report by id, save a report, record a
generation attempt — and nothing else. That is why this package defines a
``Protocol`` and two hand-written adapters rather than adopting an ORM: an ORM
would buy schema migration and query building we do not use, at the cost of a
second source of truth about the table shape.

Two implementations:

* :class:`~basketball_scout.persistence.memory.InMemoryReportRepository` — the
  default when Supabase is not configured, and what every offline test runs
  against.
* :class:`~basketball_scout.persistence.supabase.SupabaseReportRepository` —
  PostgREST over HTTPS with the **server** secret key, used only from the
  backend. The public frontend never talks to Supabase directly.
"""

from .models import GenerationRun, StoredReport, TeamRecord
from .repository import RepositoryError, ReportRepository

__all__ = [
    "GenerationRun",
    "RepositoryError",
    "ReportRepository",
    "StoredReport",
    "TeamRecord",
]
