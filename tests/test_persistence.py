"""Repository behaviour — in-memory semantics and the Supabase wire mapping.

The Supabase tests use an ``httpx.MockTransport``: real request construction and
real response parsing, zero network. That is the part worth testing — the query
string, the headers, and the row mapping are exactly what breaks silently.
"""

from __future__ import annotations

import json

import httpx
import pytest

from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.persistence.models import GenerationRun, StoredReport, TeamRecord
from basketball_scout.persistence.repository import (
    ReportRepository,
    RepositoryError,
    SchemaMissingError,
)
from basketball_scout.persistence.supabase import SupabaseReportRepository, rest_base_url

SECRET = "sb_secret_do_not_log_me"
URL = "https://example.supabase.co"


def make_stored(report_id: str = "r1", team_id: str = "segev:4", generated_at: str = "2026-08-19T00:00:00Z"):
    return StoredReport(
        report_id=report_id,
        team_id=team_id,
        team_name="HAPOEL JERUSALEM",
        season="2025-26",
        generated_at=generated_at,
        report_version="report-v1",
        evidence_version="packs-v1",
        definition_version="agents-v1",
        backend="stub",
        pack_hash="sha256:abc",
        report_json={"report_id": report_id},
        validation_summary={"ok": True, "warnings_n": 0},
    )


# ---- protocol ---------------------------------------------------------------


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(InMemoryReportRepository(), ReportRepository)
    assert isinstance(SupabaseReportRepository(URL, SECRET), ReportRepository)


# ---- in-memory --------------------------------------------------------------


def test_memory_save_read_and_latest():
    repo = InMemoryReportRepository()
    assert repo.get_latest_report("segev:4") is None

    older = make_stored("r1", generated_at="2026-08-18T00:00:00Z")
    newer = make_stored("r2", generated_at="2026-08-19T00:00:00Z")
    repo.save_report(older)
    repo.save_report(newer)

    assert repo.get_report("r1") == older
    assert repo.get_latest_report("segev:4").report_id == "r2"
    assert repo.get_report("missing") is None


def test_memory_latest_is_scoped_per_team():
    repo = InMemoryReportRepository()
    repo.save_report(make_stored("r1", team_id="segev:4"))
    repo.save_report(make_stored("r2", team_id="segev:2"))
    assert repo.get_latest_report("segev:4").report_id == "r1"
    assert repo.get_latest_report("segev:2").report_id == "r2"


def test_memory_ignores_archived_reports():
    repo = InMemoryReportRepository()
    from dataclasses import replace

    repo.save_report(replace(make_stored("r1"), status="archived"))
    assert repo.get_latest_report("segev:4") is None
    assert repo.get_report("r1") is not None


def test_memory_lists_only_active_teams_sorted_by_name():
    repo = InMemoryReportRepository(
        [
            TeamRecord("segev:2", "MACCABI TEL AVIV", "2025-26"),
            TeamRecord("segev:4", "HAPOEL JERUSALEM", "2025-26"),
            TeamRecord("segev:9", "RETIRED", "2025-26", active=False),
        ]
    )
    assert [t.team_name for t in repo.list_teams()] == ["HAPOEL JERUSALEM", "MACCABI TEL AVIV"]


def test_memory_latest_refs_is_one_call_for_the_whole_league():
    repo = InMemoryReportRepository()
    repo.save_report(make_stored("r1", team_id="segev:4", generated_at="2026-08-18T00:00:00Z"))
    repo.save_report(make_stored("r2", team_id="segev:4", generated_at="2026-08-19T00:00:00Z"))
    repo.save_report(make_stored("r3", team_id="segev:2", generated_at="2026-08-19T00:00:00Z"))

    refs = repo.latest_report_refs()
    assert set(refs) == {"segev:4", "segev:2"}
    assert refs["segev:4"].report_id == "r2"
    assert refs["segev:4"].generated_at == "2026-08-19T00:00:00Z"


def test_memory_latest_refs_agrees_with_get_latest_report():
    repo = InMemoryReportRepository()
    for i in range(5):
        repo.save_report(make_stored(f"r{i}", generated_at="2026-08-19T00:00:00Z"))
    assert repo.latest_report_refs()["segev:4"].report_id == repo.get_latest_report("segev:4").report_id


def test_memory_records_runs():
    repo = InMemoryReportRepository()
    repo.record_generation_run(
        GenerationRun(
            run_id="run1", team_id="segev:4", started_at="a", finished_at="b",
            duration_ms=5, status="succeeded", backend="stub",
        )
    )
    assert [r.status for r in repo.runs()] == ["succeeded"]


# ---- supabase mapping (no network) ------------------------------------------


def _repo(handler) -> SupabaseReportRepository:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SupabaseReportRepository(URL, SECRET, client=client)


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("https://x.supabase.co", "https://x.supabase.co/rest/v1"),
        ("https://x.supabase.co/", "https://x.supabase.co/rest/v1"),
        ("https://x.supabase.co/rest/v1", "https://x.supabase.co/rest/v1"),
        ("https://x.supabase.co/rest/v1/", "https://x.supabase.co/rest/v1"),
    ],
)
def test_rest_base_url_accepts_both_configured_forms(configured, expected):
    """Both spellings appear in the Supabase dashboard, and doubling the path
    produces a PGRST125 that looks exactly like a missing table."""
    assert rest_base_url(configured) == expected


def test_list_teams_sends_the_expected_query_and_maps_rows():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["apikey"] = request.headers.get("apikey")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[
            {"team_id": "segev:4", "team_name": "HAPOEL JERUSALEM", "season": "2025-26",
             "games_n": 26, "wins": 18, "losses": 8, "active": True}
        ])

    teams = _repo(handler).list_teams()
    assert seen["url"].startswith("https://example.supabase.co/rest/v1/teams")
    assert "active=is.true" in seen["url"]
    assert "order=team_name.asc" in seen["url"]
    assert seen["apikey"] == SECRET
    assert seen["auth"] == f"Bearer {SECRET}"
    assert teams == [TeamRecord("segev:4", "HAPOEL JERUSALEM", "2025-26", 26, 18, 8, True)]


def test_get_latest_report_filters_published_and_orders_desc():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[
            {"id": "abc", "team_id": "segev:4", "team_name": "X", "season": "2025-26",
             "generated_at": "2026-08-19T00:00:00Z", "report_version": "report-v1",
             "evidence_version": "packs-v1", "definition_version": "agents-v1",
             "backend": "crewai", "pack_hash": "sha256:h", "report_json": {"a": 1},
             "validation_summary": {"ok": True}}
        ])

    report = _repo(handler).get_latest_report("segev:4")
    assert "team_id=eq.segev%3A4" in seen["url"] or "team_id=eq.segev:4" in seen["url"]
    assert "status=eq.published" in seen["url"]
    assert "order=generated_at.desc" in seen["url"]
    assert "limit=1" in seen["url"]
    assert report.report_id == "abc"
    assert report.report_json == {"a": 1}


def test_missing_report_maps_to_none():
    assert _repo(lambda r: httpx.Response(200, json=[])).get_report("abc") is None


def test_save_report_posts_the_full_row():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        seen["prefer"] = request.headers.get("prefer")
        return httpx.Response(201, json=[seen["body"]])

    saved = _repo(handler).save_report(make_stored("uuid-1"))
    assert seen["method"] == "POST"
    assert seen["body"]["id"] == "uuid-1"
    assert seen["body"]["status"] == "published"
    assert seen["prefer"] == "return=representation"
    assert saved.report_id == "uuid-1"


def test_missing_table_raises_the_actionable_schema_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={
            "code": "PGRST205",
            "message": "Could not find the table 'public.teams' in the schema cache",
        })

    with pytest.raises(SchemaMissingError, match="0001_init.sql"):
        _repo(handler).list_teams()


def test_permission_denied_is_a_credentials_error_without_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "42501", "message": "permission denied for table teams"})

    with pytest.raises(RepositoryError) as exc:
        _repo(handler).list_teams()
    assert SECRET not in str(exc.value)


def test_the_secret_never_appears_in_an_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        # A hostile/echoing backend that reflects the credential back at us.
        return httpx.Response(500, json={"message": f"boom while using {SECRET}"})

    with pytest.raises(RepositoryError) as exc:
        _repo(handler).list_teams()
    assert SECRET not in str(exc.value)
    assert "<redacted>" in str(exc.value)


def test_transport_failure_is_wrapped_not_leaked():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(RepositoryError, match="Supabase request failed"):
        _repo(handler).list_teams()


def test_health_distinguishes_reachable_from_schema_ready():
    ready = _repo(lambda r: httpx.Response(200, json=[])).health()
    assert ready == {"reachable": True, "schema_ready": True, "teams_n": 0}

    missing = _repo(
        lambda r: httpx.Response(404, json={"code": "PGRST205", "message": "schema cache"})
    ).health()
    assert missing["reachable"] is True and missing["schema_ready"] is False

    down = _repo(lambda r: httpx.Response(500, json={"message": "gateway"})).health()
    assert down["reachable"] is False


def test_supabase_latest_refs_uses_a_single_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[
            {"id": "new", "team_id": "segev:4", "generated_at": "2026-08-19T00:00:00Z"},
            {"id": "old", "team_id": "segev:4", "generated_at": "2026-08-18T00:00:00Z"},
            {"id": "other", "team_id": "segev:2", "generated_at": "2026-08-17T00:00:00Z"},
        ])

    refs = _repo(handler).latest_report_refs()
    assert len(calls) == 1, "the team list must not fan out to one query per team"
    assert "select=id%2Cteam_id%2Cgenerated_at" in calls[0] or "select=id,team_id,generated_at" in calls[0]
    assert "status=eq.published" in calls[0]
    assert refs["segev:4"].report_id == "new"   # newest-first, first row per team wins
    assert refs["segev:2"].report_id == "other"


def test_supabase_latest_refs_is_empty_without_rows():
    assert _repo(lambda r: httpx.Response(200, json=[])).latest_report_refs() == {}


def test_upsert_teams_uses_merge_duplicates():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["prefer"] = request.headers.get("prefer")
        seen["rows"] = json.loads(request.content)
        return httpx.Response(201, json=[])

    n = _repo(handler).upsert_teams([TeamRecord("segev:4", "X", "2025-26")])
    assert n == 1
    assert "resolution=merge-duplicates" in seen["prefer"]
    assert seen["rows"][0]["team_id"] == "segev:4"
