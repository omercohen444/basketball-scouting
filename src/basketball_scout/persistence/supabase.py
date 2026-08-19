"""Supabase repository — PostgREST over HTTPS with the server secret key.

Why plain ``httpx`` instead of ``supabase-py``
----------------------------------------------
The repository contract is five methods over two tables. The official client
would wrap the same PostgREST calls and pull in auth/realtime/storage/functions
packages this product never uses — and this environment already had one
dependency install silently downgrade ``pydantic`` under an existing verified
pin. ``httpx`` is already present (CrewAI depends on it), the wire format is
stable and documented, and every request is visible in one file.

Security posture
----------------
* The secret key is read from the environment, never hard-coded, never logged,
  and never included in an error message — :func:`_sanitize` strips anything
  that looks like it before an exception leaves this module.
* Only the backend holds this key. The public frontend talks to FastAPI, never
  to Supabase.
* The tables are expected to have RLS enabled with no anon policies (see
  ``supabase/migrations/0001_init.sql``), so even a leaked *anon* key reads
  nothing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..net import enable_system_trust_store
from .models import GenerationRun, ReportRef, StoredReport, TeamRecord
from .repository import RepositoryError, SchemaMissingError

TEAMS_TABLE = "teams"
REPORTS_TABLE = "scouting_reports"
RUNS_TABLE = "generation_runs"

DEFAULT_TIMEOUT_SECONDS = 20.0

# Upper bound on the rows scanned when resolving "latest report per team".
# 14 teams regenerated daily for a season stays far below this.
MAX_REPORT_REFS = 500

# PostgREST's "relation does not exist / not in schema cache" family.
_SCHEMA_MISSING_CODES = {"PGRST205", "PGRST202", "42P01"}


def rest_base_url(url: str) -> str:
    """Normalise whatever form of project URL was configured.

    Supabase shows the project URL two ways — the bare origin
    (``https://<ref>.supabase.co``) and the REST base
    (``https://<ref>.supabase.co/rest/v1``) — and this project's ``.env`` was set
    up with the second. Appending ``/rest/v1`` unconditionally produced
    ``/rest/v1/rest/v1/...`` and a PGRST125 "Invalid path" that looks exactly
    like a missing table. Accept both rather than depend on which one was pasted.
    """
    base = (url or "").strip().rstrip("/")
    if base.endswith("/rest/v1"):
        return base
    return f"{base}/rest/v1"


def _sanitize(text: str, secret: str | None) -> str:
    """Remove the key from any string that might reach a log or an exception."""
    if secret and secret in text:
        text = text.replace(secret, "<redacted>")
    return text


class SupabaseReportRepository:
    """Reads and writes saved reports through Supabase's REST interface."""

    name = "supabase"

    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
        schema: str = "public",
    ):
        if not url or not secret_key:
            raise RepositoryError("SUPABASE_URL and SUPABASE_SECRET_KEY are both required")

        # This machine verifies TLS against the OS trust store (CLAUDE.md §10).
        # truststore patches ssl globally, so it covers httpx too.
        enable_system_trust_store()

        self._secret = secret_key
        self.base_url = rest_base_url(url)
        self.schema = schema
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    # -- plumbing ------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._secret,
            "Authorization": f"Bearer {self._secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
    ) -> list[dict[str, Any]]:
        extra = {"Prefer": prefer} if prefer else None
        try:
            response = self._client.request(
                method,
                f"{self.base_url}/{table}",
                params=params,
                json=json_body,
                headers=self._headers(extra),
            )
        except httpx.HTTPError as exc:
            raise RepositoryError(
                f"Supabase request failed ({method} {table}): "
                f"{_sanitize(str(exc), self._secret)}"
            ) from None

        if response.status_code >= 400:
            self._raise_for_response(method, table, response)

        if not response.content:
            return []
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise RepositoryError(
                f"Supabase returned a non-JSON body for {method} {table}"
            ) from None
        if isinstance(payload, dict):
            return [payload]
        return list(payload)

    def _raise_for_response(self, method: str, table: str, response: httpx.Response) -> None:
        detail: dict[str, Any] = {}
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body
        except (json.JSONDecodeError, ValueError):
            pass

        code = str(detail.get("code") or "")
        message = _sanitize(str(detail.get("message") or response.text[:300]), self._secret)

        if code in _SCHEMA_MISSING_CODES or (
            response.status_code == 404 and "schema cache" in message.lower()
        ):
            raise SchemaMissingError(
                f"table {self.schema}.{table!r} does not exist in this Supabase project. "
                f"Apply supabase/migrations/0001_init.sql in the SQL editor, then retry. "
                f"(PostgREST said: {message})"
            )
        if response.status_code in (401, 403):
            raise RepositoryError(
                f"Supabase rejected the credentials for {method} {table} "
                f"(HTTP {response.status_code}). Check SUPABASE_SECRET_KEY."
            )
        raise RepositoryError(
            f"Supabase {method} {table} failed with HTTP {response.status_code}: {message}"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- ReportRepository ----------------------------------------------------

    def list_teams(self) -> list[TeamRecord]:
        rows = self._request(
            "GET",
            TEAMS_TABLE,
            params={"select": "*", "active": "is.true", "order": "team_name.asc"},
        )
        return [TeamRecord.from_row(row) for row in rows]

    def get_latest_report(self, team_id: str) -> StoredReport | None:
        rows = self._request(
            "GET",
            REPORTS_TABLE,
            params={
                "select": "*",
                "team_id": f"eq.{team_id}",
                "status": "eq.published",
                # created_at (now(), microsecond precision) breaks the tie when
                # two reports share a generated_at second.
                "order": "generated_at.desc,created_at.desc",
                "limit": "1",
            },
        )
        return StoredReport.from_row(rows[0]) if rows else None

    def latest_report_refs(self) -> dict[str, ReportRef]:
        """One request for the whole league.

        PostgREST has no ``DISTINCT ON``, so this fetches the id/team/timestamp
        columns for published reports newest-first and keeps the first row per
        team. Only three small columns travel, and MAX_REPORT_REFS bounds the
        response even after months of regeneration.
        """
        rows = self._request(
            "GET",
            REPORTS_TABLE,
            params={
                "select": "id,team_id,generated_at",
                "status": "eq.published",
                "order": "generated_at.desc,created_at.desc",
                "limit": str(MAX_REPORT_REFS),
            },
        )
        latest: dict[str, ReportRef] = {}
        for row in rows:
            team_id = row["team_id"]
            if team_id not in latest:
                latest[team_id] = ReportRef(str(row["id"]), team_id, row["generated_at"])
        return latest

    def get_report(self, report_id: str) -> StoredReport | None:
        rows = self._request(
            "GET",
            REPORTS_TABLE,
            params={"select": "*", "id": f"eq.{report_id}", "limit": "1"},
        )
        return StoredReport.from_row(rows[0]) if rows else None

    def save_report(self, report: StoredReport) -> StoredReport:
        rows = self._request(
            "POST",
            REPORTS_TABLE,
            json_body=report.to_row(),
            prefer="return=representation",
        )
        return StoredReport.from_row(rows[0]) if rows else report

    def record_generation_run(self, run: GenerationRun) -> None:
        self._request("POST", RUNS_TABLE, json_body=run.to_row(), prefer="return=minimal")

    # -- operational ---------------------------------------------------------

    def upsert_teams(self, teams: list[TeamRecord]) -> int:
        """Seed/refresh the team allowlist from the shipped pack index.

        Used by the ops CLI, never by a request handler.
        """
        if not teams:
            return 0
        self._request(
            "POST",
            TEAMS_TABLE,
            json_body=[t.to_row() for t in teams],
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return len(teams)

    def health(self) -> dict[str, Any]:
        """Cheap connectivity/schema probe for the ops CLI and /health detail."""
        try:
            teams = self.list_teams()
        except SchemaMissingError as exc:
            return {"reachable": True, "schema_ready": False, "detail": str(exc)}
        except RepositoryError as exc:
            return {"reachable": False, "schema_ready": False, "detail": str(exc)}
        return {"reachable": True, "schema_ready": True, "teams_n": len(teams)}
