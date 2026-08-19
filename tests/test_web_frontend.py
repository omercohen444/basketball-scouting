"""Server-rendered frontend smoke tests.

Not a design review — these check that each state the UI must express actually
renders: the selector, a loaded report, the empty state, the storage-error
banner, and the error page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pack_factories import write_synthetic_packs
from product_factories import admin_headers, make_app

from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.persistence.repository import RepositoryError


@pytest.fixture
def repo() -> InMemoryReportRepository:
    return InMemoryReportRepository()


@pytest.fixture
def client(tmp_path, repo) -> TestClient:
    write_synthetic_packs(tmp_path)
    return TestClient(make_app(tmp_path, repository=repo))


def generate(client: TestClient, team_id: str = "segev:4"):
    return client.post(
        "/api/admin/reports/generate", json={"team_id": team_id}, headers=admin_headers()
    )


def test_home_renders_the_selector_and_every_team(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert 'id="team-select"' in body
    assert "HAPOEL JERUSALEM" in body
    assert "Choose an opponent" in body


def test_home_marks_which_teams_have_a_report(client):
    assert "No report yet" in client.get("/").text
    generate(client)
    body = client.get("/").text
    assert "Report ready" in body


def test_team_page_shows_the_empty_state_before_generation(client):
    body = client.get("/teams/segev:4").text
    assert "No scouting report has been generated" in body
    # The empty state must say generation is not something a visitor triggers.
    assert "never triggers" in body


def test_team_page_renders_a_generated_report(client):
    report_id = generate(client).json()["report_id"]
    body = client.get("/teams/segev:4").text

    assert "Executive summary" in body
    assert "Game-plan priorities" in body
    assert "Key deterministic evidence" in body
    assert "Automated validation" in body
    assert "Provenance" in body
    assert "Not available in this data" in body
    assert f"/api/reports/{report_id}/pdf" in body
    assert "Download PDF" in body


def test_report_permalink_renders(client):
    report_id = generate(client).json()["report_id"]
    response = client.get(f"/reports/{report_id}")
    assert response.status_code == 200
    assert "Executive summary" in response.text


def test_team_page_slug_form_works(client):
    generate(client)
    assert client.get("/teams/segev_4").status_code == 200


def test_unknown_team_renders_an_html_error_page_not_json(client):
    response = client.get("/teams/segev:999")
    assert response.status_code == 400
    assert "text/html" in response.headers["content-type"]
    assert "Back to the opponent list" in response.text


def test_missing_page_renders_the_html_error_page(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Nothing here" in response.text


def test_storage_failure_shows_a_banner_rather_than_a_blank_page(tmp_path):
    class Down(InMemoryReportRepository):
        def get_latest_report(self, team_id):
            raise RepositoryError("connection refused")

    write_synthetic_packs(tmp_path)
    client = TestClient(make_app(tmp_path, repository=Down()))
    response = client.get("/teams/segev:4")
    assert response.status_code == 200
    assert "Report storage is unreachable" in response.text
    assert "connection refused" not in response.text


def test_static_assets_are_served(client):
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert client.get("/static/app.js").status_code == 200


def test_no_secret_or_token_reaches_the_browser(client):
    """The pages carry no credential of any kind — that is why a public visitor
    cannot trigger generation even though the endpoint exists."""
    generate(client)
    for path in ("/", "/teams/segev:4", "/static/app.js", "/static/app.css"):
        body = client.get(path).text.lower()
        for forbidden in ("test-admin-token", "x-admin-token", "sb_secret", "apikey", "gemini_api_key"):
            assert forbidden not in body, f"{forbidden} leaked into {path}"


def test_model_prose_is_escaped_in_html(tmp_path, repo):
    """Claim text comes from a model; Jinja autoescaping must hold."""
    from basketball_scout.agents.pipeline import StubBackend
    from basketball_scout.agents.schemas import ScoutingReport

    payload = '<script>alert("xss")</script>'

    class Injecting(StubBackend):
        name = "injecting"

        def run_head_scout(self, pack, triage, tactical, feedback=None):
            report: ScoutingReport = super().run_head_scout(pack, triage, tactical, feedback)
            return report.model_copy(update={"executive_summary": payload})

    write_synthetic_packs(tmp_path)
    client = TestClient(make_app(tmp_path, repository=repo, backend_factory=lambda: Injecting()))
    generate(client)

    body = client.get("/teams/segev:4").text
    assert payload not in body
    assert "&lt;script&gt;" in body


def test_frontend_never_constructs_an_agent_backend(tmp_path, repo):
    def forbidden():
        raise AssertionError("a page render tried to construct an agent backend")

    write_synthetic_packs(tmp_path)
    client = TestClient(make_app(tmp_path, repository=repo, backend_factory=forbidden))
    assert client.get("/").status_code == 200
    assert client.get("/teams/segev:4").status_code == 200
