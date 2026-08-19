"""Server-rendered frontend smoke tests.

Not a design review — these check that each state the UI must express actually
renders: the selector, a loaded report, the empty state, the storage-error
banner, and the error page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from analytics_factories import write_synthetic_analytics
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
    analytics = tmp_path / "analytics"
    write_synthetic_analytics(analytics)
    return TestClient(make_app(tmp_path, repository=repo, analytics_dir=analytics))


def generate(client: TestClient, team_id: str = "segev:4"):
    return client.post(
        "/api/admin/reports/generate", json={"team_id": team_id}, headers=admin_headers()
    )


def test_the_landing_page_is_the_league_not_a_report_picker(client):
    """The product decision this whole redesign turns on: a visitor arrives at
    league analytics, not at a menu of AI documents."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "League overview" in body
    assert "Israeli Basketball Premier League" in body
    assert "Possession-level play-by-play" in body


def test_the_landing_page_carries_the_global_navigation(client):
    body = client.get("/").text
    for label in ("League", "Teams", "Explorer", "Games", "Compare", "Scouting", "Methodology"):
        assert f">{label}</a>" in body, label


def test_the_landing_page_says_so_when_analytics_are_absent(tmp_path, repo):
    """A deployment without the analytics artifact must degrade honestly rather
    than render an empty table — the scouting reports still work."""
    write_synthetic_packs(tmp_path)
    bare = TestClient(make_app(tmp_path, repository=repo))
    body = bare.get("/").text
    assert "Analytics artifacts are not present" in body
    assert bare.get("/scouting/segev:4").status_code == 200


def test_scouting_page_shows_the_empty_state_before_generation(client):
    body = client.get("/scouting/segev:4").text
    assert "No scouting report has been generated" in body
    # The empty state must say generation is not something a visitor triggers.
    assert "never triggers" in body


def test_scouting_page_renders_a_generated_report(client):
    report_id = generate(client).json()["report_id"]
    body = client.get("/scouting/segev:4").text

    assert "Executive summary" in body
    assert "Keys to Win" in body
    assert "Why it matters" in body
    assert "Deterministic evidence" in body
    assert "Data Limits" in body
    assert f"/api/reports/{report_id}/pdf" in body
    assert "Download PDF" in body
    # The stub backend alternates 0/1 tactics per Key — with the synthetic
    # pack's 12 evidence items it always produces at least one, so this locks
    # in that a present tactic actually renders.
    assert "Tactical option" in body


def test_the_three_information_types_are_distinguishable_in_the_markup(client):
    """The load-bearing property of the design: a coach must be able to tell
    measured evidence from an agent's interpretation from an optional tactical
    suggestion. Each gets its own class hook, so the distinction survives any
    later restyling — it is structural, not a matter of how it happens to look."""
    generate(client)
    body = client.get("/scouting/segev:4").text

    assert 'class="ev"' in body, "measured evidence must render as its own card"
    assert 'class="why"' in body, "interpretation must render as plain prose, not a card"
    assert 'class="tactic"' in body, "a tactical option must render as its own inset block"
    assert 'class="tactic-label">Tactical option<' in body, (
        "a suggestion must be labelled as one, not left to look like a measurement"
    )


def test_win_loss_split_is_surfaced_as_a_comparison_not_buried_in_prose(client):
    """W/L difference is one of the most practically useful things here, so it
    gets an explicit wins-value / losses-value / gap treatment on the card."""
    generate(client)
    body = client.get("/scouting/segev:4").text
    assert 'class="wl-values"' in body
    assert 'class="wl-w"' in body and 'class="wl-l"' in body
    assert "SD</span>" in body, "the gap needs a unit, or the number means nothing"


def test_report_page_has_no_horizontal_overflow_hazards(client):
    """The evidence table is the one element that cannot fit a phone; it must
    live in its own scroll container rather than widening the page."""
    generate(client)
    body = client.get("/scouting/segev:4").text
    assert 'class="table-scroll"' in body
    assert "<table" in body
    assert body.index('class="table-scroll"') < body.index("<table")


def test_scouting_page_hides_validation_and_provenance_detail(client):
    """The report is for a coach, not an instructor auditing the pipeline —
    detailed validation, hashes, model/backend identifiers must not appear."""
    generate(client)
    body = client.get("/scouting/segev:4").text
    assert "Automated validation" not in body
    assert "Hard rejections" not in body
    assert "Pack hash" not in body
    assert "sha256" not in body
    assert "report-v1" not in body
    assert "packs-v1" not in body
    assert "agents-v1" not in body


def test_scouting_page_shows_no_internal_claim_strength_labels(client):
    """(established)/(indicated)/(speculative) are audit vocabulary — they
    stay in the JSON contract but must never render for a coach."""
    generate(client)
    body = client.get("/scouting/segev:4").text
    for label in ("(established)", "(indicated)", "(speculative)"):
        assert label not in body


def test_scouting_page_merges_caveats_and_unavailable_into_one_section(client):
    """Goal 5: one short "Data Limits" section, not two separate headings
    that say overlapping things."""
    generate(client)
    body = client.get("/scouting/segev:4").text
    assert "Caveats</h2>" not in body
    assert "Not available in this data" not in body
    assert body.count("Data Limits") == 1


def test_report_permalink_renders(client):
    report_id = generate(client).json()["report_id"]
    response = client.get(f"/reports/{report_id}")
    assert response.status_code == 200
    assert "Executive summary" in response.text


def test_every_team_tab_renders(client):
    """Six tabs, each with one job. A tab that 404s is a broken nav item."""
    for tab in ("", "/splits", "/quarters", "/situations", "/profile", "/games"):
        response = client.get(f"/teams/segev:4{tab}")
        assert response.status_code == 200, tab


def test_a_tab_that_does_not_exist_is_a_404_not_a_blank_page(client):
    assert client.get("/teams/segev:4/nonsense").status_code == 404


def test_the_shot_profile_announces_that_it_is_experimental(client):
    """Complete data, provisional validation. The caveat belongs on the page,
    not in a tooltip."""
    body = client.get("/teams/segev:4/profile").text
    assert "Experimental" in body
    assert "single arena" in body or "single game" in body
    assert "/methodology#shot-geometry" in body


def test_half_court_is_only_ever_mentioned_to_deny_it(client):
    """A false provider fast-break flag means only that the provider did not
    call the play a fast break — 5.7% of provider-negatives happen inside four
    seconds of a change of possession. So the site carries no half-court
    *figure*, and every occurrence of the phrase is part of saying so."""
    import re

    for path in ("/", "/teams/segev:4", "/teams/segev:4/profile", "/explore"):
        text = re.sub(r"\s+", " ", client.get(path).text.lower())
        for match in re.finditer(r"half[- ]court", text):
            before = text[max(0, match.start() - 60):match.start()]
            assert any(word in before for word in ("never", "not ", "no ")), (
                f"{path}: 'half-court' used without denying it — ...{before[-60:]}"
            )


def test_no_surface_claims_momentum(client):
    """Runs and droughts describe patterns. None of them claims a cause."""
    for path in ("/teams/segev:4", "/teams/segev:4/profile"):
        assert "momentum" not in client.get(path).text.lower(), path


def test_the_scoring_partition_is_the_only_thing_drawn_as_a_whole(client):
    """Points off turnovers, second chance and fast break overlap each other.
    The partition macro refuses anything whose shares do not sum to one, so a
    page that stacked them would fail to render rather than mislead."""
    body = client.get("/teams/segev:4/profile").text
    assert 'class="partition"' in body
    assert body.count('class="partition"') == 1
    assert "overlap" in body


def test_consistency_is_silent_where_the_measure_does_not_apply(client):
    """Net rating sits near zero, so relative variability has no meaning. The
    page shows its range and no label at all."""
    body = client.get("/teams/segev:4").text
    assert "Consistency" in body
    assert "/methodology#stability" in body


def test_the_underscore_slug_form_works_on_both_team_surfaces(client):
    """`segev_4` is the URL-safe spelling of `segev:4`; both routes accept it."""
    generate(client)
    assert client.get("/teams/segev_4").status_code == 200
    assert client.get("/scouting/segev_4").status_code == 200


def test_the_old_report_url_still_reaches_the_report(client):
    """`/teams/{id}` used to serve the report and now serves analytics, so the
    old intent is redirected rather than silently answered with something else."""
    generate(client)
    response = client.get("/teams/segev:4?view=report", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/scouting/segev:4"


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
    response = client.get("/scouting/segev:4")
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
    for path in ("/", "/teams/segev:4", "/scouting/segev:4", "/static/app.js", "/static/app.css"):
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

    body = client.get("/scouting/segev:4").text
    assert payload not in body
    assert "&lt;script&gt;" in body


def test_frontend_never_constructs_an_agent_backend(tmp_path, repo):
    def forbidden():
        raise AssertionError("a page render tried to construct an agent backend")

    write_synthetic_packs(tmp_path)
    analytics = tmp_path / "analytics"
    write_synthetic_analytics(analytics)
    client = TestClient(
        make_app(tmp_path, repository=repo, analytics_dir=analytics, backend_factory=forbidden)
    )
    for path in ("/", "/teams/segev:4", "/teams/segev:4/splits", "/explore", "/scouting/segev:4"):
        assert client.get(path).status_code == 200, path
