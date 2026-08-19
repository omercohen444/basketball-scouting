"""Agent-layer contracts: the schemas must accept valid shapes and reject
malformed ones, and must stay JSON round-trippable for the later API/PDF stage."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from basketball_scout.agents.schemas import (
    CLAIM_RANK,
    CONFIDENCE_RANK,
    PROVENANCE_RANK,
    RELIABILITY_RANK,
    DataSignal,
    EvidencePack,
    KeyToWin,
    ReportClaim,
    ScoutingReport,
    TacticalImplication,
    TacticalOption,
    ValidationResult,
    Finding,
)

from agents_factories import make_item, make_pack


def test_evidence_pack_index_and_lookup():
    pack = make_pack(items=[make_item("EV.season.efg_pct"), make_item("EV.season.tov_pct")])
    assert set(pack.index()) == {"EV.season.efg_pct", "EV.season.tov_pct"}
    assert pack.get("EV.season.efg_pct") is not None
    assert pack.get("EV.nope") is None


def test_evidence_pack_is_json_serializable():
    pack = make_pack(items=[make_item("EV.season.efg_pct")])
    json.dumps(pack.model_dump(mode="json"))  # must not raise


def test_data_signal_requires_at_least_one_evidence_ref():
    with pytest.raises(ValidationError):
        DataSignal(
            signal_id="S1", signal_kind="league_extreme", headline="h", why_kept="w",
            evidence_refs=[], priority_rank=1,
        )


def test_tactical_implication_requires_supports_and_signals():
    with pytest.raises(ValidationError):
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="indicated",
            claim_basis="b", signal_refs=[], supports_refs=["EV.season.efg_pct"],
        )
    with pytest.raises(ValidationError):
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="indicated",
            claim_basis="b", signal_refs=["S1"], supports_refs=[],
        )


def test_report_claim_requires_an_implication_ref():
    with pytest.raises(ValidationError):
        ReportClaim(text="they are good", implication_refs=[])


def test_key_to_win_requires_an_implication_ref():
    with pytest.raises(ValidationError):
        KeyToWin(
            recommendation_id="R1", priority=1, objective="d", why_it_matters="r",
            implication_refs=[], confidence="moderate",
        )


def test_key_to_win_defaults_to_zero_tactics():
    key = KeyToWin(
        recommendation_id="R1", priority=1, objective="d", why_it_matters="r",
        implication_refs=["T1"], confidence="moderate",
    )
    assert key.tactics == []


def test_key_to_win_rejects_more_than_two_tactics():
    tactic = lambda i: TacticalOption(  # noqa: E731
        tactic_id=f"T{i}", method="m", mechanism="w", implication_refs=["T1"]
    )
    with pytest.raises(ValidationError):
        KeyToWin(
            recommendation_id="R1", priority=1, objective="d", why_it_matters="r",
            implication_refs=["T1"], confidence="moderate",
            tactics=[tactic(1), tactic(2), tactic(3)],
        )


def test_key_to_win_allows_up_to_two_tactics():
    tactic = lambda i: TacticalOption(  # noqa: E731
        tactic_id=f"T{i}", method="m", mechanism="w", implication_refs=["T1"]
    )
    key = KeyToWin(
        recommendation_id="R1", priority=1, objective="d", why_it_matters="r",
        implication_refs=["T1"], confidence="moderate",
        tactics=[tactic(1), tactic(2)],
    )
    assert len(key.tactics) == 2


def test_tactical_option_requires_an_implication_ref():
    with pytest.raises(ValidationError):
        TacticalOption(tactic_id="T1", method="m", mechanism="w", implication_refs=[])


def test_unknown_field_is_rejected_not_silently_kept():
    """extra="forbid" everywhere: a model inventing a field must fail loudly,
    not have it quietly dropped where a validator would never see it."""
    with pytest.raises(ValidationError):
        DataSignal(
            signal_id="S1", signal_kind="league_extreme", headline="h", why_kept="w",
            evidence_refs=["EV.season.efg_pct"], priority_rank=1, effect_size=0.9,
        )


def test_scouting_report_has_no_key_evidence_field():
    """key_evidence is computed by render.py from implication_refs. If a model
    could author it, 'introduces no new evidence' would stop being structural."""
    assert "key_evidence" not in ScoutingReport.model_fields


def test_report_all_claims_spans_every_claim_section():
    claim = lambda t: ReportClaim(text=t, implication_refs=["T1"])  # noqa: E731
    report = ScoutingReport(
        report_id="R", team_id="segev:4", team_name="X", scope_note="s",
        executive_summary="e",
        offensive_identity=[claim("a")], strengths=[claim("b")],
        vulnerabilities=[claim("c")], transition_notes=[claim("d")],
        turnover_notes=[claim("e")],
    )
    assert [c.text for c in report.all_claims()] == ["a", "b", "c", "d", "e"]


def test_rank_ladders_are_ordered_consistently():
    """The ladders are load-bearing for every downgrade decision."""
    assert PROVENANCE_RANK["provider_fact"] > PROVENANCE_RANK["validated_deterministic"]
    assert PROVENANCE_RANK["validated_deterministic"] > PROVENANCE_RANK["provisional_deterministic"]
    assert PROVENANCE_RANK["provisional_deterministic"] > PROVENANCE_RANK["partial"]
    assert PROVENANCE_RANK["partial"] > PROVENANCE_RANK["deferred"]
    assert RELIABILITY_RANK["high"] > RELIABILITY_RANK["moderate"] > RELIABILITY_RANK["low"]
    assert CLAIM_RANK["established"] > CLAIM_RANK["indicated"] > CLAIM_RANK["speculative"]
    assert CONFIDENCE_RANK["high"] > CONFIDENCE_RANK["moderate"] > CONFIDENCE_RANK["low"]


def test_validation_result_partitions_by_severity():
    result = ValidationResult(findings=[
        Finding(rule="R1", severity="reject", message="bad"),
        Finding(rule="W-thin", severity="warning", message="meh"),
    ])
    assert len(result.rejects) == 1
    assert len(result.warnings) == 1
    assert result.ok is False
    assert ValidationResult(findings=[result.findings[1]]).ok is True


def test_validation_results_merge():
    a = ValidationResult(findings=[Finding(rule="R1", severity="reject", message="a")])
    b = ValidationResult(findings=[Finding(rule="R2", severity="warning", message="b")])
    assert len(a.merged(b).findings) == 2
