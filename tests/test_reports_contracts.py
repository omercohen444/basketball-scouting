"""The public report contract.

Two things matter here: the projection keeps everything a reader needs to judge
the evidence, and it drops everything a caller has no business seeing.
"""

from __future__ import annotations

import json

import pytest
from agents_factories import make_pack, make_report, make_tactical, make_triage

from basketball_scout.agents.render import render_report
from basketball_scout.agents.schemas import Finding, ValidationResult
from basketball_scout.agents.validation import apply_resolved_strengths
from basketball_scout.reports.contracts import (
    REPORT_CONTRACT_VERSION,
    SECTION_KEYS,
    PublicReport,
    build_public_report,
)


def build(**overrides) -> PublicReport:
    pack = overrides.pop("pack", None) or make_pack()
    triage = make_triage(pack)
    tactical = apply_resolved_strengths(pack, make_tactical(triage))
    report = make_report(tactical)
    validation = overrides.pop("validation", None) or ValidationResult(
        findings=[Finding(rule="W-thin", severity="warning", message="thin", where="report")]
    )
    rendered = render_report(pack, triage, tactical, report, validation)
    kwargs = {
        "report_id": "11111111-2222-3333-4444-555555555555",
        "generated_at": "2026-08-19T03:00:00Z",
        "backend": "stub",
        "model_name": "test-model",
        "pack_hash": "sha256:deadbeef",
    }
    kwargs.update(overrides)
    return build_public_report(rendered, **kwargs)


def test_projection_preserves_the_reader_facing_substance():
    report = build()
    assert report.report_version == REPORT_CONTRACT_VERSION
    assert report.team_id == "segev:4"
    assert report.team_name == "HAPOEL JERUSALEM"
    assert report.executive_summary
    assert report.scope_note
    assert report.recommendations
    assert report.key_evidence
    assert report.unavailable_evidence
    assert report.provenance.pack_id.startswith("segev:4|")
    assert report.provenance.record == "18-8"
    assert report.provenance.games_n == 26
    assert report.provenance.pack_hash == "sha256:deadbeef"


def test_every_section_key_exists_even_when_empty():
    sections = build().sections
    for key in SECTION_KEYS:
        assert hasattr(sections, key)
    titled = dict((k, t) for k, t, _ in sections.items())
    assert "strengths" in titled


def test_evidence_cards_carry_reliability_and_sample_context():
    card = build().key_evidence[0]
    assert card.value  # pre-formatted by the deterministic layer
    assert card.reliability in ("high", "moderate", "low")
    assert card.validation_state
    assert card.sample_games > 0
    assert card.league_rank  # "3 of 14"


def test_masked_win_loss_is_reported_as_unavailable_not_omitted():
    """A team with no rankable W/L evidence must still say so on each card."""
    pack = make_pack(pack_states=["no_win_loss_evidence"])
    for item in pack.evidence:
        item.win_loss.agent_rankable = False
        item.win_loss.effect_size = None
        item.win_loss.effect_status = "masked_no_wl_evidence"

    report = build(pack=pack)
    assert "no_win_loss_evidence" in report.provenance.pack_states
    for card in report.key_evidence:
        assert card.win_loss.available is False
        assert card.win_loss.effect_size is None
        assert card.win_loss.reason == "masked_no_wl_evidence"


def test_validation_summary_counts_and_keeps_warnings_visible():
    validation = ValidationResult(
        findings=[
            Finding(rule="W-thin", severity="warning", message="thin", where="report"),
            Finding(rule="R8", severity="warning", message="confidence too high", where="R1"),
        ]
    )
    report = build(validation=validation)
    assert report.validation.ok is True
    assert report.validation.rejects_n == 0
    assert report.validation.warnings_n == 2
    assert {n.rule for n in report.validation.warnings} == {"W-thin", "R8"}


def test_validation_note_drops_the_where_locator():
    """``where`` can echo raw claim text; rule and message are enough."""
    note = build().validation.warnings[0]
    assert set(note.model_dump()) == {"rule", "message"}


def test_serialized_report_contains_no_internal_or_secret_material():
    payload = json.dumps(build().model_dump(mode="json")).lower()
    for forbidden in (
        "api_key", "apikey", "authorization", "bearer ", "backstory",
        "system_prompt", "task_prompt", "traceback", "supabase", "gemini_api",
        "c:\\\\", "/src/basketball_scout",
    ):
        assert forbidden not in payload, forbidden


def test_contract_is_closed_to_unknown_fields():
    payload = build().model_dump(mode="json")
    payload["injected"] = "surprise"
    with pytest.raises(Exception):
        PublicReport.model_validate(payload)


def test_report_round_trips_through_json_unchanged():
    """Storage writes ``model_dump`` and reads ``model_validate``; if that is not
    lossless, saved reports silently degrade."""
    original = build()
    revived = PublicReport.model_validate(json.loads(original.model_dump_json()))
    assert revived == original
