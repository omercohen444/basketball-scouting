"""Anti-hallucination validation — the rules that decide whether agent output
may become a report. Every case here is synthetic: no model, no network, no key."""

from __future__ import annotations

import pytest

from basketball_scout.agents.schemas import (
    PACK_STATE_NO_WIN_LOSS,
    DataSignal,
    KeyToWin,
    ReportClaim,
    TacticalImplication,
    TacticalOption,
    TacticalOutput,
    TriageOutput,
    UnavailableEvidence,
)
from basketball_scout.agents.validation import (
    apply_resolved_confidence,
    apply_resolved_strengths,
    resolve_claim_strength,
    resolve_recommendation_confidence,
    validate_report,
    validate_tactical,
    validate_triage,
)

from agents_factories import make_item, make_pack, make_report, make_tactic, make_tactical, make_triage


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


def test_scheme_vocabulary_is_allowed_in_a_key_objective():
    """Advice to our own team is not a factual claim about the opponent."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Use drop coverage against their ball screens.",
        why_it_matters="Their profile in the cited areas supports it.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert validate_report(pack, triage, tactical, report).ok


def test_scheme_vocabulary_in_why_it_matters_is_still_rejected():
    """why_it_matters describes them, so the exemption must not leak into it."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Pressure the ball.",
        why_it_matters="They run a 2-3 zone whenever they trail.",
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
    """4-5, not 3-5: the recommendations ARE the report's "Keys to Win", and a
    coach reading 2-3 bullets is under-served. 3 is now explicitly invalid —
    it used to be the floor."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)

    assert "R7" in _rules(validate_report(pack, triage, tactical, make_report(tactical, recommendations=2)))
    assert "R7" in _rules(validate_report(pack, triage, tactical, make_report(tactical, recommendations=3)))
    assert "R7" in _rules(validate_report(pack, triage, tactical, make_report(tactical, recommendations=6)))
    assert validate_report(pack, triage, tactical, make_report(tactical, recommendations=4)).ok
    assert validate_report(pack, triage, tactical, make_report(tactical, recommendations=5)).ok


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


def test_same_implication_framed_as_both_strength_and_vulnerability_warns():
    """Observed on the first live run: one implication was argued as both a
    strength and a vulnerability from the same bundle. That specific
    contradiction — calling a metric "stable" against a cited effect size
    that says otherwise — is now its own hard rejection (R13, below); this
    test isolates the orthogonal, softer signal: the SAME implication backing
    both a strength and a vulnerability claim, regardless of wording."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    shared = tactical.implications[0].implication_id
    report.strengths = [ReportClaim(text="A clear strength here.", implication_refs=[shared])]
    report.vulnerabilities = [ReportClaim(text="Also a vulnerability here.", implication_refs=[shared])]

    result = validate_report(pack, triage, tactical, report)
    assert any(f.rule == "W-dual-framing" for f in result.warnings)
    assert result.ok, "dual framing is sometimes legitimate, so it must not block"


def test_distinct_implications_per_section_do_not_warn():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [ReportClaim(text="Strong here.", implication_refs=["T1"])]
    report.vulnerabilities = [ReportClaim(text="Weak there.", implication_refs=["T2"])]

    result = validate_report(pack, triage, tactical, report)
    assert not any(f.rule == "W-dual-framing" for f in result.warnings)


def test_numeral_in_executive_summary_warns():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical, summary="They shoot 52.0% from the field.")
    result = validate_report(pack, triage, tactical, report)
    assert any(f.rule == "W-numeral" for f in result.warnings)
    assert result.ok  # a warning, never a block


# ---- R9: calibrated intensity language ---------------------------------------
#
# Every case below constructs the evidence explicitly (percentile/effect) so
# the calibration threshold, not team-specific tuning, is what's under test.


def test_r9_tier3_word_rejected_when_evidence_is_not_extreme():
    """The exact defect this rule exists for: a below-average-but-not-rare
    share (rank not near last, no large win/loss split) called 'elite'."""
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=60.0, effect_size=0.3)])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "They are an elite rebounding team.")])
    assert "R9" in _rules(validate_triage(pack, triage))


def test_r9_tier3_word_allowed_with_an_extreme_league_rank():
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=95.0, effect_size=0.1)])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "They are an elite rebounding team.")])
    assert "R9" not in _rules(validate_triage(pack, triage))


def test_r9_tier3_bar_is_tightened_past_rank_two_of_fourteen():
    """The exact case that prompted a second tightening pass: rank 2 of 14
    (percentile 92.3, extremity 42.3) used to clear tier 3 and produced
    'exceptional offensive rebounding' — real, but the preferred phrasing for
    that is the objectively-verifiable 'among the league leaders', not a
    degree-word. Confirms it no longer clears the bar on its own."""
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=92.3, effect_size=0.14)])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "They are an elite rebounding team.")])
    assert "R9" in _rules(validate_triage(pack, triage))


def test_r9_tier3_word_allowed_with_a_large_win_loss_effect():
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=55.0, effect_size=1.1)])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "They are an elite rebounding team.")])
    assert "R9" not in _rules(validate_triage(pack, triage))


def test_r9_low_reliability_evidence_cannot_buy_intensity():
    pack = make_pack(items=[
        make_item("EV.season.a", league_percentile=99.0, effect_size=2.0, reliability_tier="low")
    ])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "They are an elite rebounding team.")])
    assert "R9" in _rules(validate_triage(pack, triage))


def test_r9_tier2_word_allowed_below_the_tier3_bar():
    """A clear top-quartile result earns 'significant' (tier 2) but not
    'elite' (tier 3) — the same evidence, two different bars."""
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=80.0, effect_size=0.1)])
    tier2 = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "A significant rebounding edge.")])
    tier3 = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "An elite rebounding edge.")])
    assert "R9" not in _rules(validate_triage(pack, tier2))
    assert "R9" in _rules(validate_triage(pack, tier3))


def test_r9_bundling_a_weak_item_dilutes_a_strong_one():
    """One genuinely extreme item alone would clear tier 3 comfortably; cited
    alongside an unrelated middling item, the MEAN no longer clears even
    tier 2. Deliberately conservative when a claim bundles several pieces of
    evidence — not a bug."""
    pack = make_pack(items=[
        make_item("EV.season.a", league_percentile=95.0, effect_size=0.9),
        make_item("EV.season.b", league_percentile=51.0, effect_size=0.05),
    ])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a", "EV.season.b"], "A significant combined edge.")]
    )
    assert "R9" in _rules(validate_triage(pack, triage))


def test_r9_ordinary_language_is_never_gated():
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=51.0, effect_size=0.01)])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a"], "They lean toward this area more than the league norm.")]
    )
    assert "R9" not in _rules(validate_triage(pack, triage))


def test_r9_applies_at_the_tactical_stage():
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=52.0, effect_size=0.1)])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"])])
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="An elite, dominant tendency.",
            proposed_claim_strength="established", claim_basis="b",
            signal_refs=["S1"], supports_refs=["EV.season.a"],
        )
    ])
    assert "R9" in _rules(validate_tactical(pack, triage, tactical))


def test_r9_applies_to_report_claims():
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=52.0, effect_size=0.1)])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [
        ReportClaim(text="An elite, dominant strength.",
                    implication_refs=[tactical.implications[0].implication_id])
    ]
    assert "R9" in _rules(validate_report(pack, triage, tactical, report))


def test_r9_applies_to_why_it_matters_but_not_objective():
    """Same split as scheme vocabulary: the objective is advice to OUR team,
    why_it_matters is a claim about them."""
    pack = make_pack(items=[make_item("EV.season.a", league_percentile=52.0, effect_size=0.1)])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Play with elite defensive discipline.",
        why_it_matters="They are an elite scoring team in this area.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    result = validate_report(pack, triage, tactical, report)
    assert "R9" in _rules(result)
    # exactly one R9 finding — the objective's identical word is exempt.
    assert sum(1 for f in result.rejects if f.rule == "R9") == 1


# ---- R10: causal language about a correlational split -----------------------


def test_r10_causal_claim_about_a_win_loss_split_is_rejected():
    pack = make_pack(wins=18, losses=8)
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]],
                "Their poor shooting causes them to lose close games.")
    ])
    assert "R10" in _rules(validate_triage(pack, triage))


def test_r10_applies_even_when_win_loss_evidence_genuinely_exists():
    """Distinct from R6, which only blocks outcome framing when a team has NO
    rankable W/L evidence at all. This fires regardless — the split being
    real does not make it a cause."""
    pack = make_pack(wins=18, losses=8)
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Turnovers lead to a loss late in games.")
    ])
    result = validate_triage(pack, triage)
    assert "R10" in _rules(result)
    assert "R6" not in _rules(result)


def test_r10_bare_win_loss_difference_is_not_causal_language():
    """"in wins"/"in losses" alone describes a difference, not a cause, and
    must stay usable — exactly the phrasing a "Keys to Win" section needs."""
    pack = make_pack(wins=18, losses=8)
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "Their shooting is far better in wins.")
    ])
    assert "R10" not in _rules(validate_triage(pack, triage))


# ---- R11: descriptive claims needing evidence this dataset does not have ---


@pytest.mark.parametrize(
    "phrase",
    [
        "Their half-court offense is their primary identity.",
        "They run a lot of half court sets in the fourth quarter.",
        "Their transition attack is fast by design.",
        "They are designed to push the pace at every opportunity.",
        "Opposing shooters face a difficult shot contest from this defense.",
        "Their perimeter defense forces tough looks.",
        "The coaching intent here is clearly to slow the game down.",
    ],
)
def test_r11_unsupported_evidence_claim_is_rejected(phrase):
    pack = make_pack()
    triage = TriageOutput(signals=[_signal("S1", [pack.screening.candidate_ids[0]], phrase)])
    assert "R11" in _rules(validate_triage(pack, triage))


def test_r11_exempt_in_a_key_objective():
    """Forcing them into the half-court is legitimate advice to OUR team and
    claims nothing about their demonstrated tendencies.

    This used to also say "and contest every shot", which R17 now rejects for
    an unrelated reason (a technique belongs in a tactic, not an objective).
    Trimmed so this test asserts only the R11 exemption it exists for."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Force them into the half-court.",
        why_it_matters="Their profile in the cited areas supports it.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert validate_report(pack, triage, tactical, report).ok


def test_r11_still_rejected_in_why_it_matters():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Pressure the ball.",
        why_it_matters="Their half-court offense struggles late in games.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R11" in _rules(validate_report(pack, triage, tactical, report))


def test_r11_a_cited_metric_may_still_be_referenced_plainly():
    """Citing an actually-available metric (fast-break points) is fine — only
    layering an unsupported classification/intent on top of it is not."""
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]],
                "They generate a meaningful share of their points in transition.")
    ])
    assert "R11" not in _rules(validate_triage(pack, triage))


# ---- R12: a tactic may not reach for evidence its Key does not cite --------


def test_r12_tactic_citing_evidence_outside_its_key_is_rejected():
    pack = make_pack(items=[make_item("EV.season.a"), make_item("EV.season.b")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    key_ref = tactical.implications[0].implication_id
    other_ref = tactical.implications[-1].implication_id
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=[key_ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [other_ref if other_ref != key_ref else key_ref])],
    )
    if key_ref == other_ref:
        pytest.skip("fixture produced a single implication; nothing to smuggle")
    assert "R12" in _rules(validate_report(pack, triage, tactical, report))


def test_r12_tactic_citing_its_own_keys_evidence_is_allowed():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    key_ref = tactical.implications[0].implication_id
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=[key_ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [key_ref])],
    )
    assert "R12" not in _rules(validate_report(pack, triage, tactical, report))


def test_r12_prose_rules_apply_to_tactic_method_and_mechanism():
    """method is scheme-exempt (advice to us); mechanism is not (still a claim
    about them) — same split as objective/why_it_matters."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    key_ref = tactical.implications[0].implication_id

    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=[key_ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [key_ref], method="Use drop coverage against their ball screens.")],
    )
    assert validate_report(pack, triage, tactical, report).ok

    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=[key_ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [key_ref], mechanism="They run a 2-3 zone whenever they trail.")],
    )
    assert "R4" in _rules(validate_report(pack, triage, tactical, report))


# ---- R13: stability language vs a materially large win/loss split ----------
#
# Mirror image of R9: gates STABILITY words behind evidence that genuinely IS
# stable, reading the same mean |effect_size| R9 already computes off the
# other end.


def test_r13_stability_word_rejected_against_a_materially_large_wl_split():
    """The exact defect this rule exists for: 'remains stable' cited against
    an item whose own win/loss effect size is large."""
    pack = make_pack(items=[make_item("EV.season.a", effect_size=0.9)])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a"], "Their offensive efficiency remains stable.")]
    )
    assert "R13" in _rules(validate_triage(pack, triage))


def test_r13_stability_word_allowed_with_a_small_wl_effect():
    pack = make_pack(items=[make_item("EV.season.a", effect_size=0.3)])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a"], "Their offensive efficiency remains stable.")]
    )
    assert "R13" not in _rules(validate_triage(pack, triage))


def test_r13_bundling_a_small_effect_item_can_dilute_below_the_bar():
    """Same mean-not-max convention as R9, read in the opposite direction: one
    large-effect item bundled with a near-zero item can bring the mean back
    under the stability bar."""
    pack = make_pack(items=[
        make_item("EV.season.a", effect_size=1.2),
        make_item("EV.season.b", effect_size=0.1),
    ])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a", "EV.season.b"], "Their combined profile remains stable.")]
    )
    assert "R13" not in _rules(validate_triage(pack, triage))


def test_r13_low_reliability_evidence_does_not_trigger_the_guard():
    pack = make_pack(items=[make_item("EV.season.a", effect_size=2.0, reliability_tier="low")])
    triage = TriageOutput(signals=[_signal("S1", ["EV.season.a"], "Their profile remains stable.")])
    assert "R13" not in _rules(validate_triage(pack, triage))


def test_r13_ordinary_language_about_a_changing_metric_is_never_gated():
    pack = make_pack(items=[make_item("EV.season.a", effect_size=2.0)])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a"], "This differs sharply between their wins and losses.")]
    )
    assert "R13" not in _rules(validate_triage(pack, triage))


def test_r13_consistent_is_deliberately_excluded_from_the_lexicon():
    """'consistent' means cross-signal agreement elsewhere in this codebase
    (StubBackend's and the shared factory's claim_basis text) and is too
    generic to gate safely — only unambiguous stability-of-performance words
    are in STABILITY_TERMS."""
    pack = make_pack(items=[make_item("EV.season.a", effect_size=2.0)])
    triage = TriageOutput(
        signals=[_signal("S1", ["EV.season.a"], "A consistent direction across the cited measures.")]
    )
    assert "R13" not in _rules(validate_triage(pack, triage))


def test_r13_applies_to_report_claims():
    pack = make_pack(items=[make_item("EV.season.a", effect_size=1.0)])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [
        ReportClaim(text="Their scoring stays remarkably steady.",
                    implication_refs=[tactical.implications[0].implication_id])
    ]
    assert "R13" in _rules(validate_report(pack, triage, tactical, report))


# ---- R14: Key objectives may not invoke unsupported constructs -------------


@pytest.mark.parametrize("word", ["rhythm", "intensity", "momentum"])
def test_r14_unsupported_construct_rejected_in_objective(word):
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective=f"Control the game's {word} throughout.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R14" in _rules(validate_report(pack, triage, tactical, report))


def test_r14_applies_to_why_it_matters_too_not_only_the_objective():
    """Originally objective-scoped. Widened after a live Strengths claim read
    "capable of establishing positive momentum early" — the same category
    error, in a sentence the objective-only rule never looked at."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Prepare for the cited tendency.",
        why_it_matters="Their momentum builds throughout each half.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R14" in _rules(validate_report(pack, triage, tactical, report))


def test_r14_applies_to_a_narrative_claim():
    """The exact live defect, in the section it actually appeared in."""
    pack = make_pack(items=[make_item("EV.season.net_rating", metric_name="net_rating")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [
        ReportClaim(
            text="They are capable of establishing positive momentum early.",
            implication_refs=[tactical.implications[0].implication_id],
        )
    ]
    rules = _rules(validate_report(pack, triage, tactical, report))
    assert "R14" in rules, "momentum is unmeasurable wherever it is claimed"
    assert "R15" in rules, "'early' needs first-half evidence, and net rating is season-scope"


def test_r14_applies_at_the_triage_and_tactical_stages():
    pack = make_pack()
    triage = TriageOutput(signals=[
        _signal("S1", [pack.screening.candidate_ids[0]], "They build momentum through the game.")
    ])
    assert "R14" in _rules(validate_triage(pack, triage))

    clean = TriageOutput(signals=[_signal("S1", [pack.screening.candidate_ids[0]])])
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="They play with sustained intensity.",
            proposed_claim_strength="indicated", claim_basis="b",
            signal_refs=["S1"], supports_refs=[pack.screening.candidate_ids[0]],
        )
    ])
    assert "R14" in _rules(validate_tactical(pack, clean, tactical))


def test_r14_r15_match_whole_words_not_substrings():
    """Caught the moment the temporal rule was applied beyond objectives: the
    stub backend's own "the cited evidence isolates this tendency" tripped
    'late', and "clearly"/"nearly" trip 'early'. A rule that rejects valid
    prose is worse than one that misses a stylistic slip."""
    pack = make_pack(items=[make_item("EV.season.a")])
    triage = TriageOutput(signals=[
        _signal("S1", ["EV.season.a"],
                "The evidence isolates this clearly and nearly always correlates.")
    ])
    rules = _rules(validate_triage(pack, triage))
    assert "R15" not in rules

    # ...while the real words still register.
    real = TriageOutput(signals=[
        _signal("S1", ["EV.season.a"], "They fall away late in close games.")
    ])
    assert "R15" in _rules(validate_triage(pack, real))


def test_r15_does_not_gate_a_temporal_word_in_a_tactic_method():
    """"Attack early in the shot clock" is about the clock, not the time of
    game, so a method cannot be checked against evidence scope. The construct
    check still applies there."""
    pack = make_pack(items=[make_item("EV.season.efg_pct", metric_name="efg_pct")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    ref = tactical.implications[0].implication_id
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Prepare for the cited tendency.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [ref], method="Attack early in the shot clock.")],
    )
    assert "R15" not in _rules(validate_report(pack, triage, tactical, report))


# ---- R15: a Key's temporal qualifier needs matching scoped evidence --------


def test_r15_temporal_qualifier_rejected_without_matching_scope():
    pack = make_pack(items=[make_item("EV.season.a")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Attack their defense early to build a lead.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R15" in _rules(validate_report(pack, triage, tactical, report))


def test_r15_temporal_qualifier_allowed_with_matching_scope():
    pack = make_pack(items=[make_item("EV.1H.net_rating")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Attack their defense early to build a lead.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R15" not in _rules(validate_report(pack, triage, tactical, report))


def test_r15_late_qualifier_rejected_without_clutch_or_q4_scope():
    pack = make_pack(items=[make_item("EV.season.a")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Exploit their late execution in close games.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R15" in _rules(validate_report(pack, triage, tactical, report))


def test_r15_late_qualifier_allowed_with_clutch_scope():
    pack = make_pack(items=[make_item("EV.clutch.efg_pct")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Exploit their late execution in close games.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R15" not in _rules(validate_report(pack, triage, tactical, report))


def test_r15_ordinary_language_is_never_gated():
    pack = make_pack(items=[make_item("EV.season.a")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Force difficult shot attempts on every possession.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R15" not in _rules(validate_report(pack, triage, tactical, report))


# ---- R16: the objective must be about what its evidence measures -----------
#
# Citing a valid id is not the same as citing a RELEVANT one. Every case here
# resolves cleanly through R1/R12 and is still wrong.


def _key_report(tactical, objective: str, *, refs=None):
    """A report whose first Key carries `objective`, citing `refs` (default:
    the first implication)."""
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective=objective,
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=refs or [tactical.implications[0].implication_id],
        confidence="moderate",
    )
    return report


def test_r16_offense_objective_backed_only_by_defensive_evidence_is_rejected():
    """The exact live defect: 'lower their offensive efficiency' supported
    only by Defensive Rating and Net Rating. Every id resolved; the sentence
    was still about the wrong side of the ball."""
    pack = make_pack(items=[
        make_item("EV.season.defensive_rating", metric_name="defensive_rating"),
        make_item("EV.season.net_rating", metric_name="net_rating"),
    ])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, "Focus on defensive execution to lower their offensive efficiency.")
    assert "R16" in _rules(validate_report(pack, triage, tactical, report))


def test_r16_same_objective_passes_once_offensive_evidence_is_cited():
    pack = make_pack(items=[
        make_item("EV.season.offensive_rating", metric_name="offensive_rating"),
        make_item("EV.season.net_rating", metric_name="net_rating"),
    ])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, "Focus on defensive execution to lower their offensive efficiency.")
    assert "R16" not in _rules(validate_report(pack, triage, tactical, report))


def test_r16_our_own_side_of_the_ball_never_constrains_the_key():
    """'defensive execution' with no possessive is OUR defence — advice to us,
    not a claim about them, so it must not demand defensive evidence. Only
    'their ...' phrasing commits the Key to a family."""
    pack = make_pack(items=[make_item("EV.season.orb_pct", metric_name="orb_pct")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, "Improve our defensive execution on the glass.")
    assert "R16" not in _rules(validate_report(pack, triage, tactical, report))


@pytest.mark.parametrize(
    "objective,metric_name",
    [
        ("Limit their second-chance opportunities.", "orb_pct"),
        ("Increase their turnover rate.", "tov_pct"),
        ("Limit their free-throw opportunities.", "ft_rate"),
        ("Prevent their scoring runs.", "largest_scoring_run_for"),
        ("Slow their pace.", "pace"),
        ("Limit their transition opportunities.", "provider_fast_break_points"),
        ("Attack their defense.", "defensive_rating"),
    ],
)
def test_r16_matching_family_always_passes(objective, metric_name):
    pack = make_pack(items=[make_item(f"EV.season.{metric_name}", metric_name=metric_name)])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, objective)
    assert "R16" not in _rules(validate_report(pack, triage, tactical, report))


def test_r16_net_rating_alone_does_not_satisfy_a_specific_side_of_the_ball():
    """Net rating is a margin. It is real evidence, but it is not evidence
    ABOUT their offence or their defence specifically — which is precisely how
    the live defect slipped through."""
    pack = make_pack(items=[make_item("EV.season.net_rating", metric_name="net_rating")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, "Lower their offensive efficiency.")
    assert "R16" in _rules(validate_report(pack, triage, tactical, report))


def test_r16_an_unmapped_metric_constrains_nothing():
    """A metric absent from METRIC_FAMILIES must not silently fail every Key —
    the map is an allowlist of known families, not a completeness assertion."""
    pack = make_pack(items=[make_item("EV.season.brand_new", metric_name="brand_new")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, "Prepare for the cited tendency.")
    assert "R16" not in _rules(validate_report(pack, triage, tactical, report))


def test_r16_does_not_apply_to_why_it_matters():
    """R16 governs the objective's target. why_it_matters is prose about the
    evidence and is policed by the claim rules instead."""
    pack = make_pack(items=[make_item("EV.season.defensive_rating", metric_name="defensive_rating")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Prepare for the cited tendency.",
        why_it_matters="Their offensive efficiency separates their wins from their losses.",
        implication_refs=[tactical.implications[0].implication_id],
        confidence="moderate",
    )
    assert "R16" not in _rules(validate_report(pack, triage, tactical, report))


# ---- R17: an objective states an outcome, never a technique ----------------


@pytest.mark.parametrize(
    "objective",
    [
        "Execute disciplined defense to contest their shot attempts.",
        "Box out on every shot to end their possessions.",
        "Trap the ball handler to end possessions.",
        "Double-team to end their possessions.",
        "Hedge to slow their attack.",
        "Close out to end their possessions.",
    ],
)
def test_r17_technique_in_the_objective_is_rejected(objective):
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = _key_report(tactical, objective)
    assert "R17" in _rules(validate_report(pack, triage, tactical, report))


def test_r17_outcome_verbs_are_never_gated():
    """The verbs an objective legitimately needs must stay usable."""
    pack = make_pack(items=[make_item("EV.season.orb_pct", metric_name="orb_pct")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    for objective in (
        "Limit their second-chance opportunities.",
        "Force them off the offensive glass.",
        "Secure the defensive glass to end their possessions.",
        "Control the glass to reduce their extra possessions.",
    ):
        report = _key_report(tactical, objective)
        assert "R17" not in _rules(validate_report(pack, triage, tactical, report)), objective


def test_r17_the_same_technique_is_allowed_inside_a_tactic():
    """The whole point of the rule: the vocabulary is not banned, it is
    RELOCATED. A tactic is exactly where a method belongs."""
    pack = make_pack(items=[make_item("EV.season.orb_pct", metric_name="orb_pct")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    key_ref = tactical.implications[0].implication_id
    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Limit their second-chance opportunities.",
        why_it_matters="Supported by the cited deterministic evidence.",
        implication_refs=[key_ref], confidence="moderate",
        tactics=[make_tactic("R1T1", [key_ref],
                             method="Box out immediately on every shot release.")],
    )
    assert "R17" not in _rules(validate_report(pack, triage, tactical, report))


# ---- R18: internal audit vocabulary must not reach the coach ---------------


def test_r18_claim_strength_word_used_as_a_modifier_is_rejected():
    """The live leak: the head scout is told each implication's claim_strength
    so it can sound appropriately tentative, and wrote the word itself into
    the report — "they possess an indicated league-leading capacity"."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [
        ReportClaim(text="They possess an indicated capacity for large scoring runs.",
                    implication_refs=[tactical.implications[0].implication_id])
    ]
    assert "R18" in _rules(validate_report(pack, triage, tactical, report))


def test_r18_ordinary_verb_use_of_indicate_stays_legal():
    """Only the modifier form leaks. "the evidence indicates" is normal English
    and rejecting it would be theater."""
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report.strengths = [
        ReportClaim(text="The evidence indicates a clear edge on the glass.",
                    implication_refs=[tactical.implications[0].implication_id])
    ]
    assert "R18" not in _rules(validate_report(pack, triage, tactical, report))


def test_r18_applies_to_why_it_matters_and_the_summary():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    ref = tactical.implications[0].implication_id

    report = make_report(tactical, summary="An established edge on the glass defines them.")
    assert "R18" in _rules(validate_report(pack, triage, tactical, report))

    report = make_report(tactical)
    report.recommendations[0] = KeyToWin(
        recommendation_id="R1", priority=1,
        objective="Prepare for the cited tendency.",
        why_it_matters="They show an indicated edge on the offensive glass.",
        implication_refs=[ref], confidence="moderate",
    )
    assert "R18" in _rules(validate_report(pack, triage, tactical, report))


# ---- R8 / recommendation confidence resolution -------------------------------


def test_resolve_recommendation_confidence_caps_to_the_weakest_reliability():
    pack = make_pack(items=[make_item("EV.season.a", reliability_tier="low")])
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="established",
            claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a"],
        )
    ])
    rec = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=["T1"], confidence="high",
    )
    resolved, reason = resolve_recommendation_confidence(pack, tactical, rec)
    assert resolved == "low"
    assert reason is not None and "low" in reason


def test_resolve_recommendation_confidence_never_raises_an_understatement():
    """Python may only lower a confidence. A model that under-claims is left
    alone, exactly like resolve_claim_strength."""
    pack = make_pack(items=[make_item("EV.season.a", reliability_tier="high")])
    tactical = TacticalOutput(implications=[
        TacticalImplication(
            implication_id="T1", tendency="t", proposed_claim_strength="established",
            claim_basis="b", signal_refs=["S1"], supports_refs=["EV.season.a"],
        )
    ])
    rec = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=["T1"], confidence="moderate",
    )
    resolved, reason = resolve_recommendation_confidence(pack, tactical, rec)
    assert resolved == "moderate"
    assert reason is None


def test_resolve_recommendation_confidence_is_a_noop_with_no_resolvable_support():
    pack = make_pack()
    tactical = TacticalOutput(implications=[])
    rec = KeyToWin(
        recommendation_id="R1", priority=1, objective="Do this.", why_it_matters="Because of this.",
        implication_refs=["T-missing"], confidence="high",
    )
    resolved, reason = resolve_recommendation_confidence(pack, tactical, rec)
    assert resolved == "high"
    assert reason is None


def test_apply_resolved_confidence_stamps_every_recommendation():
    pack = make_pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    report = apply_resolved_confidence(pack, tactical, report)
    assert all(rec.resolved_confidence is not None for rec in report.recommendations)


def test_confidence_downgrade_is_auto_capped_and_reported_as_a_warning():
    """The point of goal 4: the cap is applied deterministically (tested via
    apply_resolved_confidence / render.py elsewhere); R8 firing here is pure
    audit trail and must never block the report."""
    pack = make_pack(items=[make_item("EV.season.a", reliability_tier="low")])
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    report = make_report(tactical)
    for rec in report.recommendations:
        rec.confidence = "high"
    result = validate_report(pack, triage, tactical, report)
    assert any(f.rule == "R8" and "auto-capped" in f.message for f in result.warnings)
    assert result.ok, "the cap is applied automatically; it must never block the report"
