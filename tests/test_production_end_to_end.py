"""The whole product path over the **real shipped packs**, offline.

Everything else in the suite runs against synthetic packs, which is right for
unit behaviour and wrong for this question: does the committed deterministic
evidence actually survive the agents, the validators, the public contract, the
renderer and ReportLab? These tests answer that for all 14 teams with the
deterministic stub backend — zero provider calls, zero network.

They also guard two properties the documentation claims out loud: the
no-win-loss degradation gate still holds end to end, and a deployment without
the CrewAI tree still serves everything except generation.
"""

from __future__ import annotations

import builtins

import pytest
from fastapi.testclient import TestClient
from pack_factories import PRODUCTION_ANALYTICS_DIR, PRODUCTION_PACKS_DIR
from product_factories import admin_headers, make_app, make_service

from basketball_scout.agents.pack_store import PackStore
from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.reports.pdf import build_report_pdf

pytestmark = pytest.mark.skipif(
    not (PRODUCTION_PACKS_DIR / "index.json").is_file(),
    reason="production evidence packs are not present in this checkout",
)

#: Maccabi Tel Aviv, 24-2 — the only team with no rankable win/loss evidence,
#: and therefore the regression gate for the degradation path.
DEGENERATE_TEAM = "segev:2"


@pytest.fixture(scope="module")
def team_ids() -> list[str]:
    return PackStore(PRODUCTION_PACKS_DIR).team_ids()


def test_every_shipped_pack_survives_the_whole_chain(team_ids):
    """Pack -> 3 agents -> validation -> public contract -> PDF, 14 times."""
    repo = InMemoryReportRepository()
    service = make_service(PRODUCTION_PACKS_DIR, repository=repo)

    for team_id in team_ids:
        outcome = service.generate(team_id)
        assert outcome.status == "succeeded", f"{team_id}: {outcome.error} {outcome.rejects}"
        report = outcome.report
        assert report.validation.rejects_n == 0, f"{team_id} produced hard rejections"
        assert report.executive_summary
        assert report.recommendations
        assert report.key_evidence
        assert report.provenance.pack_hash.startswith("sha256:")

        pdf = build_report_pdf(report)
        assert pdf[:5] == b"%PDF-", f"{team_id} produced no PDF"
        assert len(pdf) > 3000, f"{team_id} produced a suspiciously small PDF"

    assert len(repo.reports()) == len(team_ids) == 14


def test_the_degeneration_gate_survives_serialization_and_rendering():
    """Maccabi Tel Aviv (24-2) has no rankable W/L evidence. The pack says so,
    the public contract must repeat it, and no card may carry a split."""
    service = make_service(PRODUCTION_PACKS_DIR)
    report = service.generate(DEGENERATE_TEAM).report

    assert "no_win_loss_evidence" in report.provenance.pack_states

    cards = list(report.key_evidence)
    for claim in (
        *report.sections.offensive_identity,
        *report.sections.strengths,
        *report.sections.vulnerabilities,
        *report.sections.transition_notes,
        *report.sections.turnover_notes,
    ):
        cards.extend(claim.evidence)
    for rec in report.recommendations:
        cards.extend(rec.evidence)

    assert cards, "the degenerate team produced no evidence at all"
    for card in cards:
        assert card.win_loss.available is False, card.evidence_id
        assert card.win_loss.effect_size is None, card.evidence_id
        assert card.win_loss.in_wins is None, card.evidence_id
        assert card.win_loss.reason == "masked_no_wl_evidence", card.evidence_id


def test_the_degenerate_team_renders_without_a_win_loss_column():
    repo = InMemoryReportRepository()
    client = TestClient(make_app(PRODUCTION_PACKS_DIR, repository=repo, analytics_dir=PRODUCTION_ANALYTICS_DIR))
    client.post(
        "/api/admin/reports/generate",
        json={"team_id": DEGENERATE_TEAM},
        headers=admin_headers(),
    )

    body = client.get(f"/scouting/{DEGENERATE_TEAM}").text
    assert "no_win_loss_evidence" in body, "the data-state banner is missing"
    assert "· W " not in body, "a win/loss split rendered for a team that has none"


def test_the_api_serves_the_real_thing(team_ids):
    repo = InMemoryReportRepository()
    client = TestClient(make_app(PRODUCTION_PACKS_DIR, repository=repo, analytics_dir=PRODUCTION_ANALYTICS_DIR))

    teams = client.get("/api/teams").json()
    assert teams["teams_n"] == 14
    assert {t["team_id"] for t in teams["teams"]} == set(team_ids)

    report_id = client.post(
        "/api/admin/reports/generate", json={"team_id": "segev:4"}, headers=admin_headers()
    ).json()["report_id"]

    served = client.get("/api/reports/latest/segev:4").json()
    assert served["report_id"] == report_id
    assert served["team_name"] == "HAPOEL JERUSALEM"
    assert served["provenance"]["games_n"] == 26
    assert served["provenance"]["record"] == "18-8"

    pdf = client.get(f"/api/reports/{report_id}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"


def test_a_deployment_without_crewai_serves_everything_but_generation(monkeypatch):
    """Backs the lean-deploy note in docs/DEPLOYMENT.md: install
    requirements-ci.txt and the product still works, minus generation."""
    from basketball_scout.config import Settings
    from basketball_scout.reports.service import GenerationUnavailableError, default_backend_factory

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.endswith("crew") or name.startswith("crewai"):
            raise ImportError("No module named 'crewai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    factory = default_backend_factory(Settings(gemini_api_key="x"), "gemini-3.5-flash")
    with pytest.raises(GenerationUnavailableError, match="not installed"):
        factory()

    monkeypatch.undo()

    # The public surface is unaffected: a report that already exists still
    # serves, including its PDF.
    repo = InMemoryReportRepository()
    seeded = TestClient(make_app(PRODUCTION_PACKS_DIR, repository=repo, analytics_dir=PRODUCTION_ANALYTICS_DIR))
    report_id = seeded.post(
        "/api/admin/reports/generate", json={"team_id": "segev:4"}, headers=admin_headers()
    ).json()["report_id"]

    lean = TestClient(
        make_app(
            PRODUCTION_PACKS_DIR,
            repository=repo,
            analytics_dir=PRODUCTION_ANALYTICS_DIR,
            backend_factory=default_backend_factory(Settings(gemini_api_key="x"), "m"),
        )
    )
    assert lean.get("/health").status_code == 200
    assert lean.get("/api/teams").status_code == 200
    assert lean.get("/api/reports/latest/segev:4").status_code == 200
    assert lean.get(f"/api/reports/{report_id}/pdf").status_code == 200
    for path in ("/", "/teams/segev:4", "/teams/segev:4/splits", "/explore", "/scouting/segev:4"):
        assert lean.get(path).status_code == 200, path


def test_the_legacy_trailing_label_never_reaches_a_reader():
    """Amendment: the stored evidence keeps its id and its value, but a reader
    must never be shown the overstated label. This asserts the rendered page,
    not the helper — the helper existed and was wired nowhere for a while."""
    repo = InMemoryReportRepository()
    client = TestClient(
        make_app(PRODUCTION_PACKS_DIR, repository=repo, analytics_dir=PRODUCTION_ANALYTICS_DIR)
    )
    client.post(
        "/api/admin/reports/generate", json={"team_id": "segev:4"}, headers=admin_headers()
    )
    body = client.get("/scouting/segev:4").text

    assert "Trailing 6+" not in body, "the overstated legacy label reached the page"
    if "behind_6_plus" in body or "Trailing 5+" in body:
        assert "Trailing 5+" in body
