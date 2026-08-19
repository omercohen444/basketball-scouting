"""The public report contract.

Two things matter here: the projection keeps everything a reader needs to judge
the evidence, and it drops everything a caller has no business seeing.
"""

from __future__ import annotations

import json

import pytest
from agents_factories import make_item, make_pack, make_report, make_tactical, make_triage

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


def test_recommendation_view_carries_objective_why_it_matters_and_tactics():
    """End to end through the real pipeline (StubBackend alternates 0/1
    tactics), not the synthetic factory — proves render.py -> contracts.py
    actually plumbs the new shape, not just that the Pydantic model accepts it."""
    from basketball_scout.agents.pipeline import StubBackend, run_pipeline

    pack = make_pack(items=[make_item(f"EV.season.m{i}") for i in range(12)])
    result = run_pipeline(pack, StubBackend())
    report = build_public_report(
        result.rendered, report_id="x", generated_at="2026-08-19T00:00:00Z",
        backend="stub", pack_hash="sha256:deadbeef",
    )

    assert all(rec.objective for rec in report.recommendations)
    assert all(rec.why_it_matters for rec in report.recommendations)
    with_tactics = [rec for rec in report.recommendations if rec.tactics]
    without_tactics = [rec for rec in report.recommendations if not rec.tactics]
    assert with_tactics and without_tactics, "the stub must exercise both code paths"
    for rec in with_tactics:
        for tactic in rec.tactics:
            assert tactic.method
            assert tactic.mechanism
            # The public contract exposes a tactic's EVIDENCE, not raw
            # implication ids — the coach reads the evidence card, not an id.
            assert tactic.evidence, "a tactic's mechanism must carry its own evidence cards"


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


def test_date_range_display_drops_tip_off_times():
    """The stored value keeps the deterministic layer's full timestamps; only
    the HTML and PDF shorten it."""
    report = build()
    assert report.provenance.date_range == "2025-10-12 to 2026-05-27"

    with_times = report.provenance.model_copy(
        update={"date_range": "2025-10-12T16:00:00 to 2026-05-27T20:50:00"}
    )
    assert with_times.date_range_display == "2025-10-12 to 2026-05-27"
    assert with_times.date_range == "2025-10-12T16:00:00 to 2026-05-27T20:50:00"


def test_date_range_display_is_safe_when_the_range_is_missing_or_odd():
    from basketball_scout.reports.contracts import ReportProvenance

    assert ReportProvenance(pack_id="p").date_range_display == ""
    assert ReportProvenance(pack_id="p", date_range="n/a").date_range_display == "n/a"


def test_date_range_display_is_not_serialized():
    """A computed property must not leak into the stored/served payload."""
    assert "date_range_display" not in build().model_dump(mode="json")["provenance"]


# ---- coach-facing curation ---------------------------------------------------
#
# The report is for a coach, not an instructor auditing the pipeline: detailed
# validation, hashes, model info, and long analyst-facing "why we don't have
# this" explanations must not appear in what these two functions produce.


def test_verbose_limitation_legend_text_is_stripped_from_caveats():
    """The full legend text — as render.py attaches it for the audit
    artifact — must never survive into the coach-facing caveats verbatim."""
    from basketball_scout.agents.evidence_pack import LIMITATION_LEGEND
    from basketball_scout.reports.contracts import _coach_caveats

    legend_text = LIMITATION_LEGEND["neutral_direction"]
    out = _coach_caveats([legend_text, "Small sample in the final stretch of the season."], [])
    assert legend_text not in out
    assert "Small sample in the final stretch of the season." in out


def test_short_coach_note_appears_only_for_codes_actually_cited():
    from basketball_scout.reports.contracts import COACH_LIMITATION_NOTES, _coach_caveats

    card_with_code = build().key_evidence[0].model_copy(update={"limitations": ["neutral_direction"]})
    card_without = build().key_evidence[0].model_copy(update={"limitations": []})

    with_note = _coach_caveats([], [card_with_code])
    without_note = _coach_caveats([], [card_without])

    assert COACH_LIMITATION_NOTES["neutral_direction"] in with_note
    assert COACH_LIMITATION_NOTES["neutral_direction"] not in without_note


def test_caveats_have_no_duplicates_across_model_and_coach_notes():
    from basketball_scout.reports.contracts import _coach_caveats

    card = build().key_evidence[0].model_copy(update={"limitations": ["neutral_direction"]})
    out = _coach_caveats(["Same note.", "Same note."], [card, card])
    assert out.count("Same note.") == 1


def test_unavailable_evidence_is_grouped_into_a_handful_of_short_bullets():
    """The real product ships 7 unavailable_evidence entries with paragraph-
    length reasons; a coach should see a handful of one-sentence bullets."""
    report = build()
    assert 1 <= len(report.unavailable_evidence) <= 4
    for item in report.unavailable_evidence:
        assert len(item.reason) < 160, f"{item.label!r} reason reads like analyst material: {item.reason!r}"
        # None of the raw evidence_pack.py jargon should survive.
        for jargon in ("provisional_deterministic", "validation_state", "+/-1m", "38/62"):
            assert jargon not in item.reason


def test_unavailable_evidence_grouping_covers_every_known_id():
    from basketball_scout.reports.contracts import _UNAVAILABLE_GROUPS

    grouped_ids = {gid for _label, _reason, ids in _UNAVAILABLE_GROUPS for gid in ids}
    assert grouped_ids == {
        "NA.shot_zone_share", "NA.shot_distance", "NA.possession_type_half_court",
        "NA.player_level", "NA.last_passer", "NA.video", "NA.scheme",
    }


def test_ungrouped_unavailable_id_falls_back_rather_than_vanishing():
    from basketball_scout.reports.contracts import _coach_unavailable

    out = _coach_unavailable([{"id": "NA.brand_new_thing", "label": "Brand new thing", "reason": "Not built yet."}])
    assert len(out) == 1
    assert out[0].label == "Brand new thing"


def test_no_validation_or_provenance_jargon_reaches_the_full_report_caveats():
    """Belt and braces on the whole pipeline: build a report the way the real
    service does and confirm none of the four legend strings survive."""
    from basketball_scout.agents.evidence_pack import LIMITATION_LEGEND

    report = build()
    full_text = " ".join(report.caveats)
    for legend_text in LIMITATION_LEGEND.values():
        assert legend_text not in full_text
