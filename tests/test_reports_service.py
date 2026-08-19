"""The generation service — the one path that produces and stores a report.

The rules under test are the product's safety rules: only allowlisted teams,
never save an invalid report, never regenerate silently, retry provider blips but
not provider refusals, and always leave an audit row.
"""

from __future__ import annotations

import pytest
from pack_factories import write_synthetic_packs
from product_factories import make_service

from basketball_scout.agents.pipeline import PipelineError, StubBackend
from basketball_scout.agents.schemas import TriageOutput
from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.persistence.repository import RepositoryError
from basketball_scout.reports.service import (
    GenerationUnavailableError,
    ReportService,
    UnknownTeamError,
    is_transient,
)


@pytest.fixture
def packs(tmp_path):
    return write_synthetic_packs(tmp_path)


# ---- allowlist --------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("segev:4", "segev:4"), ("segev_4", "segev:4")])
def test_canonical_and_slug_team_ids_both_resolve(packs, raw, expected):
    assert make_service(packs).resolve_team_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "segev:999",
        "",
        "../../etc/passwd",
        "segev:4; drop table teams",
        "<script>alert(1)</script>",
        "a" * 60,
        "segev:4\nX-Injected: 1",
    ],
)
def test_hostile_or_unknown_team_ids_are_refused(packs, raw):
    with pytest.raises(UnknownTeamError):
        make_service(packs).resolve_team_id(raw)


def test_a_rejected_id_that_failed_the_shape_gate_is_not_echoed_back(packs):
    with pytest.raises(UnknownTeamError) as exc:
        make_service(packs).resolve_team_id("<script>alert(1)</script>")
    assert "script" not in str(exc.value)


def test_allowlist_is_empty_when_no_packs_are_shipped(tmp_path):
    service = make_service(tmp_path / "absent")
    assert service.allowed_team_ids() == []
    with pytest.raises(UnknownTeamError):
        service.resolve_team_id("segev:4")


# ---- reads ------------------------------------------------------------------


def test_list_teams_comes_from_the_packs_not_from_storage(packs):
    """Storage only decorates the list; an empty database must not empty it."""
    teams = make_service(packs).list_teams()
    assert {t.team_id for t in teams} == {"segev:4", "segev:11"}
    assert all(t.has_report is False for t in teams)
    assert all(t.record for t in teams)


def test_list_teams_survives_unreachable_storage(packs):
    class Broken(InMemoryReportRepository):
        def get_latest_report(self, team_id):
            raise RepositoryError("database down")

    teams = make_service(packs, repository=Broken()).list_teams()
    assert len(teams) == 2
    assert all(t.has_report is False for t in teams)


def test_get_latest_returns_none_before_anything_is_generated(packs):
    assert make_service(packs).get_latest("segev:4") is None


# ---- generation -------------------------------------------------------------


def test_generate_runs_the_whole_chain_and_persists(packs):
    repo = InMemoryReportRepository()
    outcome = make_service(packs, repository=repo).generate("segev:4")

    assert outcome.status == "succeeded"
    assert outcome.persisted is True
    assert outcome.report is not None
    assert outcome.report.team_id == "segev:4"
    assert outcome.stage_attempts == {"triage": 1, "tactical": 1, "head_scout": 1}
    assert outcome.rejects == []

    stored = repo.get_latest_report("segev:4")
    assert stored.report_id == outcome.stored_report_id
    assert stored.report_json["team_id"] == "segev:4"
    assert stored.evidence_json["pack"]["team_id"] == "segev:4"
    assert stored.pack_hash.startswith("sha256:")


def test_generate_is_idempotent_unless_forced(packs):
    repo = InMemoryReportRepository()
    service = make_service(packs, repository=repo)

    first = service.generate("segev:4")
    second = service.generate("segev:4")
    assert second.status == "skipped"
    assert second.stored_report_id == first.stored_report_id
    assert len(repo.reports()) == 1

    third = service.generate("segev:4", force_regenerate=True)
    assert third.status == "succeeded"
    assert third.stored_report_id != first.stored_report_id
    assert len(repo.reports()) == 2
    assert repo.get_latest_report("segev:4").report_id == third.stored_report_id


def test_generate_refuses_an_unknown_team(packs):
    with pytest.raises(UnknownTeamError):
        make_service(packs).generate("segev:999")


def test_an_invalid_report_is_never_saved(packs):
    """A stage that cannot satisfy validation must leave storage untouched."""

    class TooFewSignals(StubBackend):
        name = "broken"

        def run_triage(self, pack, feedback=None):
            return TriageOutput(signals=super().run_triage(pack).signals[:2])

    repo = InMemoryReportRepository()
    outcome = make_service(packs, repository=repo, backend_factory=lambda: TooFewSignals()).generate(
        "segev:4"
    )

    assert outcome.status == "rejected"
    assert outcome.persisted is False
    assert repo.reports() == []
    assert repo.get_latest_report("segev:4") is None
    assert outcome.rejects


def test_a_pipeline_error_is_recorded_as_a_failed_run(packs):
    class Exploding(StubBackend):
        name = "exploding"

        def run_triage(self, pack, feedback=None):
            raise PipelineError("stage 'triage' still invalid")

    repo = InMemoryReportRepository()
    outcome = make_service(packs, repository=repo, backend_factory=lambda: Exploding()).generate(
        "segev:4"
    )
    assert outcome.status == "rejected"
    assert [r.status for r in repo.runs()] == ["rejected"]
    assert repo.reports() == []


def test_a_skipped_run_is_also_audited(packs):
    repo = InMemoryReportRepository()
    service = make_service(packs, repository=repo)
    service.generate("segev:4")
    service.generate("segev:4")
    assert [r.status for r in repo.runs()] == ["succeeded", "skipped"]


def test_a_successful_report_survives_a_broken_audit_table(packs):
    """The audit row is best-effort; losing it must not lose the report."""

    class NoAudit(InMemoryReportRepository):
        def record_generation_run(self, run):
            raise RepositoryError("generation_runs missing")

    repo = NoAudit()
    outcome = make_service(packs, repository=repo).generate("segev:4")
    assert outcome.status == "succeeded"
    assert repo.get_latest_report("segev:4") is not None


def test_a_failed_save_is_reported_rather_than_silently_swallowed(packs):
    class ReadOnly(InMemoryReportRepository):
        def save_report(self, report):
            raise RepositoryError("insert denied")

    outcome = make_service(packs, repository=ReadOnly()).generate("segev:4")
    assert outcome.status == "succeeded"
    assert outcome.persisted is False
    assert "insert denied" in (outcome.error or "")


def test_generation_without_a_backend_is_an_error_not_a_crash(packs):
    outcome = make_service(packs, backend_factory=None).generate("segev:4")
    assert outcome.status == "error"
    assert outcome.persisted is False


def test_missing_crewai_surfaces_as_generation_unavailable(packs):
    def factory():
        raise GenerationUnavailableError("crewai is not installed")

    outcome = make_service(packs, backend_factory=factory).generate("segev:4")
    assert outcome.status == "error"
    assert "crewai" in (outcome.error or "")


# ---- transient provider handling -------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "503 UNAVAILABLE: the model is overloaded",
        "Gemini is experiencing high demand, please try again later",
        "502 Bad Gateway",
        "Read timed out",
    ],
)
def test_provider_blips_are_classified_transient(message):
    assert is_transient(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED: Your prepayment credits are depleted",
        "API key not valid. Please pass a valid API key.",
        "model gemini-2.5-flash is no longer available to new users",
        "ValueError: something ordinary",
    ],
)
def test_provider_refusals_are_not_retried(message):
    assert is_transient(RuntimeError(message)) is False


def test_a_transient_failure_is_retried_and_can_succeed(packs):
    class FlakyOnce(StubBackend):
        name = "flaky"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def run_triage(self, pack, feedback=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("503 UNAVAILABLE: model overloaded, high demand")
            return super().run_triage(pack, feedback)

    backend = FlakyOnce()
    outcome = make_service(packs, backend_factory=lambda: backend).generate("segev:4")
    assert outcome.status == "succeeded"
    assert outcome.transient_retries == 1
    assert backend.calls == 2


def test_retries_are_bounded(packs):
    class AlwaysDown(StubBackend):
        name = "down"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def run_triage(self, pack, feedback=None):
            self.calls += 1
            raise RuntimeError("503 UNAVAILABLE")

    backend = AlwaysDown()
    outcome = make_service(packs, backend_factory=lambda: backend).generate("segev:4")
    assert outcome.status == "error"
    assert backend.calls == 3
    assert outcome.transient_retries == 2


def test_a_non_transient_failure_is_not_retried(packs):
    class Broke(StubBackend):
        name = "broke"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def run_triage(self, pack, feedback=None):
            self.calls += 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED: Your prepayment credits are depleted")

    backend = Broke()
    outcome = make_service(packs, backend_factory=lambda: backend).generate("segev:4")
    assert outcome.status == "error"
    assert backend.calls == 1
    assert outcome.transient_retries == 0


# ---- clock / metadata -------------------------------------------------------


def test_generated_at_uses_the_injected_clock(packs):
    service = make_service(packs, clock=lambda: "2026-08-19T04:05:06Z")
    outcome = service.generate("segev:4")
    assert outcome.report.generated_at == "2026-08-19T04:05:06Z"


def test_stored_metadata_records_versions_and_model(packs):
    repo = InMemoryReportRepository()
    make_service(packs, repository=repo).generate("segev:4")
    stored = repo.get_latest_report("segev:4")
    assert stored.report_version == "report-v1"
    assert stored.evidence_version == "packs-v1"
    assert stored.definition_version == "agents-v1"
    assert stored.model_name == "test-model"
    assert stored.backend == "stub"


def test_service_can_be_constructed_directly_without_the_helpers(tmp_path):
    """Guard the public constructor signature the CLI also uses."""
    from basketball_scout.agents.pack_store import PackStore

    write_synthetic_packs(tmp_path)
    service = ReportService(
        pack_store=PackStore(tmp_path),
        repository=InMemoryReportRepository(),
        backend_factory=lambda: StubBackend(),
    )
    assert service.generate("segev:4").status == "succeeded"
