"""Rendering — the step that makes "agents perform no arithmetic" literally true.

Every number in a report is looked up here from the pack, so these tests are the
guard against a wrong figure ever reaching a reader."""

from __future__ import annotations

from basketball_scout.agents.render import evidence_summary, render_markdown, render_report
from basketball_scout.agents.schemas import ValidationResult
from basketball_scout.agents.validation import (
    apply_resolved_confidence,
    apply_resolved_strengths,
    validate_report,
)

from agents_factories import make_item, make_pack, make_report, make_tactical, make_triage


def _chain(pack=None):
    pack = pack or make_pack(items=[make_item(f"EV.season.m{i}") for i in range(8)])
    triage = make_triage(pack, n=8)
    tactical = apply_resolved_strengths(pack, make_tactical(triage))
    report = make_report(tactical)
    validation = validate_report(pack, triage, tactical, report)
    return pack, triage, tactical, report, validation


def test_evidence_summary_uses_preformatted_values_only():
    item = make_item("EV.season.efg_pct")
    summary = evidence_summary(item)
    assert summary["value"] == item.display_value
    assert summary["league_rank"] == "3 of 14"


def test_masked_win_loss_is_reported_as_unavailable_not_as_a_number():
    """The masking has to survive rendering, or the leak reappears at the last step."""
    item = make_item("EV.season.efg_pct", agent_rankable=False, effect_size=None)
    summary = evidence_summary(item)
    assert summary["win_loss"]["available"] is False
    assert "effect_size" not in summary["win_loss"]


def test_key_evidence_is_computed_from_implications_not_authored():
    pack, triage, tactical, report, validation = _chain()
    rendered = render_report(pack, triage, tactical, report, validation)
    cited = {r for c in report.all_claims() for r in c.implication_refs}
    cited |= {r for rec in report.recommendations for r in rec.implication_refs}
    expected = {
        ref
        for imp in tactical.implications
        if imp.implication_id in cited
        for ref in imp.supports_refs
    }
    assert {e["evidence_id"] for e in rendered["key_evidence"]} == expected


def test_key_evidence_deduplicates_shared_evidence():
    pack, triage, tactical, report, validation = _chain()
    rendered = render_report(pack, triage, tactical, report, validation)
    ids = [e["evidence_id"] for e in rendered["key_evidence"]]
    assert len(ids) == len(set(ids))


def test_claim_strength_shown_is_the_resolved_one_not_the_proposal():
    pack = make_pack(items=[make_item("EV.season.a", validation_state="partial", reliability_tier="low")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    for imp in tactical.implications:
        imp.proposed_claim_strength = "established"
    tactical = apply_resolved_strengths(pack, tactical)
    report = make_report(tactical)
    rendered = render_report(pack, triage, tactical, report, ValidationResult())
    shown = {c["claim_strength"] for c in rendered["sections"]["strengths"]}
    assert "established" not in shown


def test_confidence_shown_is_the_resolved_one_not_the_proposal():
    """Mirrors test_claim_strength_shown_is_the_resolved_one_not_the_proposal:
    a coach must never see a confidence the evidence cannot support, and the
    correction must be silent (deterministic capping), not an R8 warning
    surfaced to them."""
    pack = make_pack(items=[make_item("EV.season.a", reliability_tier="low")])
    triage = make_triage(pack, n=8)
    tactical = apply_resolved_strengths(pack, make_tactical(triage))
    report = make_report(tactical)
    for rec in report.recommendations:
        rec.confidence = "high"
    report = apply_resolved_confidence(pack, tactical, report)

    rendered = render_report(pack, triage, tactical, report, ValidationResult())
    shown = {r["confidence"] for r in rendered["recommendations"]}
    assert shown == {"low"}, f"a low-reliability recommendation must render as low confidence, got {shown}"


def test_confidence_falls_back_to_the_proposal_when_never_resolved():
    """render.py must degrade gracefully if apply_resolved_confidence() was
    never called — the fallback exists so a report is never left with no
    confidence at all, not so a report can skip resolution in production."""
    pack, triage, tactical, report, validation = _chain()
    for rec in report.recommendations:
        rec.confidence = "moderate"
        rec.resolved_confidence = None
    rendered = render_report(pack, triage, tactical, report, validation)
    assert {r["confidence"] for r in rendered["recommendations"]} == {"moderate"}


def test_rendered_report_is_json_safe():
    import json

    pack, triage, tactical, report, validation = _chain()
    json.dumps(render_report(pack, triage, tactical, report, validation))  # must not raise


def test_rendered_report_carries_provenance_and_unavailable_evidence():
    pack, triage, tactical, report, validation = _chain()
    rendered = render_report(pack, triage, tactical, report, validation)
    assert rendered["generated_from"]["pack_id"] == pack.pack_id
    assert rendered["generated_from"]["record"] == f"{pack.wins}-{pack.losses}"
    assert rendered["unavailable_evidence"], "a report must state what it could not see"


def test_markdown_contains_the_canonical_numbers_and_the_evidence_table():
    pack, triage, tactical, report, validation = _chain()
    markdown = render_markdown(render_report(pack, triage, tactical, report, validation))
    assert "# Scouting Report" in markdown
    assert "## Key Evidence" in markdown
    assert "## Not Available In This Data" in markdown
    assert "52.0%" in markdown  # the display_value, straight from the pack


def test_markdown_reports_validation_outcome():
    pack, triage, tactical, report, validation = _chain()
    markdown = render_markdown(render_report(pack, triage, tactical, report, validation))
    assert "Hard rejections" in markdown
