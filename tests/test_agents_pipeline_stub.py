"""The full three-agent chain, offline.

The stub backend is not a convenience — it is how the contract, the validation,
the claim resolution and the rendering stay testable without a provider, a key,
or a network. If this file passes, everything except the model's prose is proven.
"""

from __future__ import annotations

import pytest

from basketball_scout.agents.pipeline import (
    MAX_REPAIR_ATTEMPTS,
    PipelineError,
    StubBackend,
    run_pipeline,
)
from basketball_scout.agents.schemas import PACK_STATE_NO_WIN_LOSS, TriageOutput

from agents_factories import make_item, make_pack


def _pack(**kwargs):
    items = [make_item(f"EV.season.m{i}") for i in range(12)]
    return make_pack(items=items, **kwargs)


def test_full_chain_runs_clean_with_no_provider_calls():
    result = run_pipeline(_pack(), StubBackend())
    assert result.validation.ok
    assert result.backend == "stub"
    assert result.stage_attempts == {"triage": 1, "tactical": 1, "head_scout": 1}


def test_chain_produces_all_three_artifacts():
    result = run_pipeline(_pack(), StubBackend())
    assert 8 <= len(result.triage.signals) <= 12
    assert result.tactical.implications
    assert 3 <= len(result.report.recommendations) <= 5


def test_every_implication_gets_a_resolved_strength_before_rendering():
    result = run_pipeline(_pack(), StubBackend())
    assert all(i.resolved_claim_strength is not None for i in result.tactical.implications)


def test_chain_degrades_for_a_team_without_win_loss_evidence():
    """A 24-2 team must still produce a valid report — just one that makes no
    win/loss claims."""
    pack = _pack(pack_states=[PACK_STATE_NO_WIN_LOSS], wins=24, losses=2)
    result = run_pipeline(pack, StubBackend())
    assert result.validation.ok
    assert result.rendered["generated_from"]["pack_states"] == [PACK_STATE_NO_WIN_LOSS]


def test_rendered_output_and_markdown_are_produced():
    result = run_pipeline(_pack(), StubBackend())
    assert result.rendered["key_evidence"]
    assert result.markdown.startswith("# Scouting Report")


class _AlwaysInvalidBackend(StubBackend):
    """Emits an unknown evidence id every time — the repair can never succeed."""

    name = "always-invalid"

    def run_triage(self, pack, feedback=None):
        triage = super().run_triage(pack, feedback)
        triage.signals[0].evidence_refs = ["EV.season.does_not_exist"]
        return triage


def test_a_stage_that_cannot_be_repaired_fails_loudly():
    """Never emit a partially-valid report: a second failure raises."""
    with pytest.raises(PipelineError) as exc:
        run_pipeline(_pack(), _AlwaysInvalidBackend())
    assert "triage" in str(exc.value)


class _FixableBackend(StubBackend):
    """Invalid on the first attempt, correct once handed the findings."""

    name = "fixable"

    def __init__(self):
        super().__init__()
        self.attempts = 0
        self.feedback_seen: list[str] = []

    def run_triage(self, pack, feedback=None):
        self.attempts += 1
        if feedback:
            self.feedback_seen.extend(feedback)
        triage = super().run_triage(pack, feedback)
        if self.attempts == 1:
            triage.signals[0].evidence_refs = ["EV.season.does_not_exist"]
        return triage


def test_one_repair_attempt_is_offered_and_the_findings_are_handed_back():
    backend = _FixableBackend()
    result = run_pipeline(_pack(), backend)
    assert result.validation.ok
    assert result.stage_attempts["triage"] == 2
    assert any("does_not_exist" in f for f in backend.feedback_seen), (
        "the repair attempt must receive the actual findings, not a bare retry"
    )


def test_repair_budget_is_exactly_one_attempt():
    assert MAX_REPAIR_ATTEMPTS == 1


def test_stub_never_cites_evidence_outside_the_candidate_pool():
    pack = _pack()
    allowed = set(pack.screening.candidate_ids)
    triage: TriageOutput = StubBackend().run_triage(pack)
    assert {r for s in triage.signals for r in s.evidence_refs} <= allowed
