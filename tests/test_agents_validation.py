"""Anti-hallucination validation — the rules that decide whether agent output
may become a report. Every case here is synthetic: no model, no network, no key."""

from __future__ import annotations

from basketball_scout.agents.schemas import (
    PACK_STATE_NO_WIN_LOSS,
    DataSignal,
    Recommendation,
    ReportClaim,
    TacticalImplication,
    TacticalOutput,
    TriageOutput,
    UnavailableEvidence,
)
from basketball_scout.agents.validation import (
    apply_resolved_strengths,
    resolve_claim_strength,
    validate_report,
    validate_tactical,
    validate_triage,
)

from agents_factories import make_item, make_pack, make_report, make_tactical, make_triage


def _rules(result) -> set[str]:
    return {f.rule for f in result.rejects}


def _signal(sid: str, refs: list[str], text: str = "A neutral observation.") -> DataSignal:
    return DataSignal(
        signal_id=sid, signal_kind="league_extreme", headline=text,
        why_kept="Because the league position supports it.", evidence_refs=refs, priority_rank=1,
    )


# ---- happy path -------------------------------------------------------------


def test_wellformed_chain_produces_no_rejections():
    pack = make_pack(items=[make_item(f"EV.season.m{i}") for i in range(8)])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)

    assert validate_triage(pack, triage).ok
    assert validate_tactical(pack, triage, tactical).ok
    assert validate_report(pack, triage, tactical, report).ok


# ---- R1: dangling references ------------------------------------------------


def test_r1_unknown_evidence_id_is_rejected():
    pack = make_pack()
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.does_not_exist"])])
    assert "R1" in _rules(validate_triage(pack, triage))


def test_r1_evidence_outside_the_candidate_set_is_rejected():
    """Triage may drop and reorder, never introduce. Python owns the pool."""
    pack = make_pack(items=[make_item("EV.season.a"), make_item("EV.season.b")])
    pack.screening.candidate_ids = ["EV.season.a"]
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.b"])])
    assert "R1" in _rules(validate_triage(pack, triage))


def test_r1_duplicate_signal_ids_are_rejected():
    pack = make_pack()
    ref = pack.screening.candidate_ids[0]
    triage = TriageOutput(signals=[_signal("S1", [ref]), _signal("S1", [ref])])
    assert "R1" in _rules(validate_triage(pack, triage))


def test_r1_unknown_signal_ref_in_implication_is_rejected():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="indicated",
            claim_basis="b", signal_refs=["S99"], supports_refs=[pack.screening.candidate_ids[0]],
        )
    ])
    assert "R1" in _rules(validate_tactical(pack, triage, tactical))


def test_r1_unknown_implication_ref_in_report_is_rejected():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [ReportClaim(text="x", implication_refs=["T404"])]
    assert "R1" in _rules(validate_report(pack, triage, tactical, report))


# ---- R2: deferred evidence --------------------------------------------------


def test_r2_deferred_evidence_cannot_support_a_claim():
    pack = make_pack(unavailable=[
        UnavailableEvidence(evidence_id="NA.video", label="Video", reason="removed"),
    ])
    triage = make_triage(pack, n=8)
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="They contest shots well.",
            proposed_claim_strength="established", claim_basis="b",
            signal_refs=["S1"], supports_refs=["NA.video"],
        )
    ])
    assert "R2" in _rules(validate_tactical(pack, triage, tactical))


def test_deferred_evidence_may_be_acknowledged_as_a_limitation():
    """The whole point of unavailable_evidence is to let a report say 'we cannot
    see this'. Blocking that would defeat the mechanism."""
    pack = make_pack(unavailable=[
        UnavailableEvidence(evidence_id="NA.video", label="Video", reason="removed"),
    ])
    triage = make_triage(pack, n=8)
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="Their profile leans one way.",
            proposed_claim_strength="indicated", claim_basis="b", signal_refs=["S1"],
            supports_refs=pack.screening.candidate_ids[:2],
            limitation_refs=["NA.video"],
        )
    ])
    assert validate_tactical(pack, triage, tactical).ok


# ---- R3 / R4: prose claims the data cannot support --------------------------


def test_r3_unsupported_metric_is_rejected():
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their true shooting is elite.")
    ])
    assert "R3" in _rules(validate_triage(pack, triage))


def test_r3_flags_metrics_declared_unavailable():
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their rim share is top of the league.")
    ])
    assert "R3" in _rules(validate_triage(pack, triage))


def test_r4_player_level_claim_is_rejected():
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their point guard drives the offense.")
    ])
    assert "R4" in _rules(validate_triage(pack, triage))


def test_r4_scheme_claim_about_the_opponent_is_rejected():
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "They attack switches relentlessly.")
    ])
    assert "R4" in _rules(validate_triage(pack, triage))


def test_scheme_vocabulary_is_allowed_in_a_recommendation_directive():
    """Advice to our own team is not a factual claim about the opponent."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = Recommendation(
        recommendation_id="R1", priority=1,
        directive="Use drop coverage against their ball screens.",
        rationale="Their profile in the cited areas supports it.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert validate_report(pack, triage, tactical, report).ok


def test_scheme_vocabulary_in_a_rationale_is_still_rejected():
    """The rationale describes them, so the exemption must not leak into it."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = Recommendation(
        recommendation_id="R1", priority=1, directive="Pressure the ball.",
        rationale="They run a 2-3 zone whenever they trail.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R4" in _rules(validate_report(pack, triage, tactical, report))


def test_video_language_warns_but_does_not_block():
    """The video layer is gone and 'film study is unavailable' is a legitimate
    sentence — rejecting it would be theater."""
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "No film is available for this opponent.")
    ])
    result = validate_triage(pack, triage)
    assert "R4" not in _rules(result)
    assert any(f.rule == "R4" and f.severity == "warning" for f in result.warnings)


# ---- R5 / R7: structural report rules ---------------------------------------


def test_r7_rejects_too_few_and_too_many_recommendations():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)

    assert "R7" in _rules(validate_report(pack, triage, tactical, make_report(tactical, recommendations=2)))
    assert "R7" in _rules(validate_report(pack, triage, tactical, make_report(tactical, recommendations=6)))
    assert validate_report(pack, triage, tactical, make_report(tactical, recommendations=4)).ok


def test_r7_rejects_a_signal_count_outside_the_band():
    pack = make_pack(items=[make_item(f"EV.season.m{i}") for i in range(20)])
    assert "R7" in _rules(validate_triage(pack, make_triage(pack, n=3)))
    assert "R7" in _rules(validate_triage(pack, make_triage(pack, n=15)))


# ---- R6: outcome framing without W/L evidence -------------------------------


def test_r6_outcome_framing_blocked_when_team_has_no_rankable_wl_evidence():
    """A 24-2 team has too few losses for any rankable W/L signal. Claiming a
    metric 'separates their wins from their losses' would be unsupportable."""
    pack = make_pack(pack_states=[PACK_STATE_NO_WIN_LOSS], wins=24, losses=2)
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their shooting is far better in wins.")
    ])
    assert "R6" in _rules(validate_triage(pack, triage))


def test_r6_allows_outcome_framing_when_wl_evidence_exists():
    pack = make_pack(wins=18, losses=8)
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their shooting is far better in wins.")
    ])
    assert "R6" not in _rules(validate_triage(pack, triage))


# ---- R8: claim strength resolution ------------------------------------------


def test_established_survives_when_all_evidence_is_high_provenance():
    pack = make_pack(items=[make_item("EV.season.a"), make_item("EV.season.b")])
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="established",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a", "EV.season.b"],
    )
    assert resolve_claim_strength(pack, imp)[0] == "established"


def test_provisional_evidence_caps_a_claim_at_indicated():
    pack = make_pack(items=[
        make_item("EV.season.a"),
        make_item("EV.season.zone", validation_state="provisional_deterministic",
                  reliability_tier="moderate"),
    ])
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="established",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a", "EV.season.zone"],
    )
    strength, reason = resolve_claim_strength(pack, imp)
    assert strength == "indicated"
    assert "provisional" in (reason or "")


def test_low_reliability_evidence_caps_a_claim_at_indicated():
    pack = make_pack(items=[make_item("EV.season.a"), make_item("EV.clutch.b", reliability_tier="low")])
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="established",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a", "EV.clutch.b"],
    )
    assert resolve_claim_strength(pack, imp)[0] == "indicated"


def test_an_inferred_tendency_needs_more_than_one_supporting_item():
    pack = make_pack(items=[make_item("EV.season.zone", validation_state="provisional_deterministic")])
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="indicated",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.zone"],
    )
    assert resolve_claim_strength(pack, imp)[0] == "speculative"


def test_resolution_never_upgrades_a_modest_proposal():
    """Python may only lower a claim. A model that under-claims is left alone."""
    pack = make_pack(items=[make_item("EV.season.a"), make_item("EV.season.b")])
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="speculative",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a", "EV.season.b"],
    )
    assert resolve_claim_strength(pack, imp)[0] == "speculative"


def test_unresolvable_support_collapses_to_speculative():
    pack = make_pack()
    imp = TacticalImplication(
        implication_id="T1", tendency="t", proposed_claim_strength="established",
        claim_basis="b", signal_refs=["S1"], supports_refs=["EV.nope"],
    )
    assert resolve_claim_strength(pack, imp)[0] == "speculative"


def test_apply_resolved_strengths_stamps_every_implication():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = apply_resolved_strengths(pack, make_tactical(triage))
    assert all(i.resolved_claim_strength is not None for i in tactical.implications)


def test_downgrade_is_reported_as_a_warning_not_a_silent_edit():
    pack = make_pack(items=[make_item("EV.season.a", validation_state="partial", reliability_tier="low")])
    triage = make_triage(pack, n=8)
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="established",
            claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a"],
        )
    ])
    result = validate_tactical(pack, triage, tactical)
    assert any(f.rule == "R8" for f in result.warnings)


# ---- warnings ---------------------------------------------------------------


def test_confidence_above_evidence_reliability_warns():
    pack = make_pack(items=[make_item("EV.season.a", reliability_tier="low")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0].confidence = "high"
    result = validate_report(pack, triage, tactical, report)
    assert any(f.rule == "R8" and "confidence" in f.message for f in result.warnings)


def test_numeral_in_executive_summary_warns():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical, summary="They shoot 52.0% from the field.")
    result = validate_report(pack, triage, tactical, report)
    assert any(f.rule == "W-numeral" for f in result.warnings)
    assert result.ok  # a warning, never a block
