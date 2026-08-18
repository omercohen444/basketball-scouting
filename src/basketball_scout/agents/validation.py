"""Deterministic post-agent validation.

Pure functions over (pack, agent output). No I/O, no provider, no model — every
rule here is exercisable offline with synthetic input, which is what keeps the
test suite credential-free and network-free per CLAUDE.md §8.

Scope is deliberately the MVP-critical set. Rules that would require parsing
free prose reliably are **warnings**, never rejections: a fragile linguistic
heuristic that blocks a valid report is worse than one that lets a stylistic
slip through, because the numbers themselves are attached deterministically by
``render.py`` and cannot be wrong.

Two rules are absent because the schema makes them unnecessary — prefer
structural impossibility to detection:

* "the Head Scout introduces no new evidence" — it cites ``implication_id``s and
  never evidence ids, so it structurally cannot.
* "quoted numbers match the source" — agents emit no numbers at all.
"""

from __future__ import annotations

import re
from typing import Iterable

from .schemas import (
    CLAIM_RANK,
    CONFIDENCE_RANK,
    PACK_STATE_NO_WIN_LOSS,
    PROVENANCE_RANK,
    RELIABILITY_RANK,
    ClaimStrength,
    EvidencePack,
    Finding,
    ScoutingReport,
    TacticalImplication,
    TacticalOutput,
    TriageOutput,
    ValidationResult,
)

MIN_SIGNALS = 8
MAX_SIGNALS = 12
MIN_RECOMMENDATIONS = 3
MAX_RECOMMENDATIONS = 5

# --- prose denylists ---------------------------------------------------------
#
# Kept tight and unambiguous on purpose. Every phrase below is one this dataset
# genuinely cannot support, and none has a common innocent team-level reading.

PERSONNEL_TERMS = (
    "point guard", "shooting guard", "small forward", "power forward",
    "star player", "best player", "key player", "leading scorer", "top scorer",
    "starting five", "starting lineup", "bench unit", "roster", "personnel",
)

VIDEO_TERMS = (
    "film", "footage", "on tape", "the tape", "video shows", "we saw",
    "watching them", "on video",
)

SCHEME_TERMS = (
    "drop coverage", "zone defense", "2-3 zone", "1-3-1", "man-to-man",
    "switch everything", "switching scheme", "hedge the", "hard hedge",
    "ice the ball screen", "pick and roll coverage",
    "attack switches", "attacks switches",
)

# Advanced statistics a model may reach for from world knowledge that this
# pipeline does not compute. Catches the realistic hallucination, without
# pretending to parse arbitrary prose for "any metric".
# Every entry must be an unambiguous multi-word phrase. Short acronyms are
# deliberately excluded: an earlier draft listed "per " for Player Efficiency
# Rating and immediately false-positived on the metric label "Points Off
# Turnovers (per game)". A denylist that blocks valid reports is worse than a
# missing one, because the numbers are attached by render.py and cannot be wrong.
UNSUPPORTED_METRIC_TERMS = (
    "true shooting", "usage rate", "player efficiency rating", "win shares",
    "plus-minus", "plus minus", "box plus minus", "defensive win shares",
    "rim rate", "rim share", "shot zone", "shot chart", "paint touches",
    "deflections", "contested shot", "closeout",
)

OUTCOME_FRAMING_TERMS = (
    "in wins", "in losses", "in their wins", "in their losses",
    "when they win", "when they lose", "when winning", "when losing",
    "drives their win", "leads to win", "separates their win",
    "difference between winning and losing", "win-loss", "wins versus loss",
    "wins vs loss",
)


def _hits(text: str, terms: Iterable[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if t in low]


def _finding(rule: str, severity: str, message: str, where: str | None = None) -> Finding:
    return Finding(rule=rule, severity=severity, message=message, where=where)  # type: ignore[arg-type]


# ---- R8 / claim strength ----------------------------------------------------

def resolve_claim_strength(
    pack: EvidencePack, implication: TacticalImplication
) -> tuple[ClaimStrength, str | None]:
    """Recompute the defensible claim strength from provenance alone.

    The agent's ``proposed_claim_strength`` is only ever a proposal: this returns
    ``min(proposed, ceiling)``. It can lower a claim, never raise one — so a model
    that over-claims is corrected rather than trusted, and a model that
    under-claims is left alone.
    """
    index = pack.index()
    supports = [index[r] for r in implication.supports_refs if r in index]
    if not supports:
        return "speculative", "no resolvable supporting evidence"

    weakest_provenance = min(PROVENANCE_RANK[s.validation_state] for s in supports)
    weakest_tier = min(RELIABILITY_RANK[s.reliability_tier] for s in supports)

    reasons: list[str] = []
    ceiling: ClaimStrength = "established"

    if weakest_provenance < PROVENANCE_RANK["validated_deterministic"]:
        ceiling = "indicated"
        reasons.append("rests on provisional or partial evidence")
    if weakest_tier == RELIABILITY_RANK["low"]:
        ceiling = "indicated"
        reasons.append("rests on low-reliability evidence")

    proposed = implication.proposed_claim_strength
    resolved: ClaimStrength = min(proposed, ceiling, key=lambda c: CLAIM_RANK[c])  # type: ignore[assignment]

    # An "indicated" claim asserts something the data does not directly measure,
    # so it needs corroboration from more than one item.
    if resolved == "indicated" and len(supports) < 2:
        resolved = "speculative"
        reasons.append("only one supporting evidence item for an inferred tendency")

    if resolved != proposed:
        return resolved, "; ".join(reasons) or "provenance ceiling"
    return resolved, None


# ---- stage validators -------------------------------------------------------

def validate_triage(pack: EvidencePack, triage: TriageOutput) -> ValidationResult:
    findings: list[Finding] = []
    index = pack.index()
    allowed = set(pack.screening.candidate_ids)
    seen_ids: set[str] = set()

    for signal in triage.signals:
        where = signal.signal_id
        if signal.signal_id in seen_ids:
            findings.append(_finding("R1", "reject", f"duplicate signal_id {signal.signal_id!r}", where))
        seen_ids.add(signal.signal_id)

        for ref in signal.evidence_refs:
            if ref not in index:
                findings.append(_finding("R1", "reject", f"unknown evidence_id {ref!r}", where))
            elif ref not in allowed:
                findings.append(
                    _finding("R1", "reject",
                             f"evidence_id {ref!r} was not in the supplied candidate set", where))

        findings.extend(_prose_findings(f"{signal.headline}\n{signal.why_kept}", pack, where, allow_scheme=False))

    n = len(triage.signals)
    if n < MIN_SIGNALS or n > MAX_SIGNALS:
        findings.append(
            _finding("R7", "reject", f"kept {n} signals; required {MIN_SIGNALS}-{MAX_SIGNALS}", "triage"))

    covered = {index[r].metric_name for s in triage.signals for r in s.evidence_refs if r in index}
    if len(covered) < 4:
        findings.append(
            _finding("W-coverage", "warning",
                     f"signals span only {len(covered)} distinct metrics; report may read monotone", "triage"))

    return ValidationResult(findings=findings)


def validate_tactical(
    pack: EvidencePack, triage: TriageOutput, tactical: TacticalOutput
) -> ValidationResult:
    findings: list[Finding] = []
    index = pack.index()
    unavailable = pack.unavailable_index()
    signal_ids = {s.signal_id for s in triage.signals}

    for imp in tactical.implications:
        where = imp.implication_id

        for ref in imp.signal_refs:
            if ref not in signal_ids:
                findings.append(_finding("R1", "reject", f"unknown signal_id {ref!r}", where))

        for ref in imp.supports_refs:
            if ref in unavailable:
                findings.append(
                    _finding("R2", "reject",
                             f"unavailable/deferred evidence {ref!r} used as support "
                             f"(permitted only in limitation_refs)", where))
            elif ref not in index:
                findings.append(_finding("R1", "reject", f"unknown evidence_id {ref!r}", where))

        for ref in imp.limitation_refs:
            if ref not in unavailable and ref not in index:
                findings.append(_finding("R1", "reject", f"unknown limitation ref {ref!r}", where))

        for ref in imp.counter_evidence_refs:
            if ref not in index:
                findings.append(_finding("R1", "reject", f"unknown counter-evidence id {ref!r}", where))

        resolved, reason = resolve_claim_strength(pack, imp)
        if CLAIM_RANK[resolved] < CLAIM_RANK[imp.proposed_claim_strength]:
            findings.append(
                _finding("R8", "warning",
                         f"claim strength downgraded {imp.proposed_claim_strength} -> {resolved} ({reason})",
                         where))

        findings.extend(_prose_findings(f"{imp.tendency}\n{imp.claim_basis}", pack, where, allow_scheme=False))

    return ValidationResult(findings=findings)


def validate_report(
    pack: EvidencePack,
    triage: TriageOutput,
    tactical: TacticalOutput,
    report: ScoutingReport,
) -> ValidationResult:
    findings: list[Finding] = []
    implication_ids = {i.implication_id for i in tactical.implications}

    for claim in report.all_claims():
        where = claim.text[:48]
        if not claim.implication_refs:
            findings.append(_finding("R5", "reject", "report claim has no supporting implication", where))
        for ref in claim.implication_refs:
            if ref not in implication_ids:
                findings.append(_finding("R1", "reject", f"unknown implication_id {ref!r}", where))
        findings.extend(_prose_findings(claim.text, pack, where, allow_scheme=False))

    n = len(report.recommendations)
    if n < MIN_RECOMMENDATIONS or n > MAX_RECOMMENDATIONS:
        findings.append(
            _finding("R7", "reject",
                     f"{n} recommendations; required {MIN_RECOMMENDATIONS}-{MAX_RECOMMENDATIONS}", "report"))

    index = pack.index()
    by_implication = {i.implication_id: i for i in tactical.implications}

    for rec in report.recommendations:
        where = rec.recommendation_id
        if not rec.implication_refs:
            findings.append(_finding("R5", "reject", "recommendation has no supporting implication", where))
        for ref in rec.implication_refs:
            if ref not in implication_ids:
                findings.append(_finding("R1", "reject", f"unknown implication_id {ref!r}", where))

        # A directive is advice to our own team, so scheme vocabulary is allowed
        # there and nowhere else. The rationale is still a claim about them.
        findings.extend(_prose_findings(rec.directive, pack, where, allow_scheme=True))
        findings.extend(_prose_findings(rec.rationale, pack, where, allow_scheme=False))

        # Confidence may not exceed the reliability of the weakest evidence it
        # transitively rests on.
        tiers = [
            index[r].reliability_tier
            for ref in rec.implication_refs
            if ref in by_implication
            for r in by_implication[ref].supports_refs
            if r in index
        ]
        if tiers:
            weakest = min(tiers, key=lambda t: RELIABILITY_RANK[t])
            if CONFIDENCE_RANK[rec.confidence] > RELIABILITY_RANK[weakest]:
                findings.append(
                    _finding("R8", "warning",
                             f"confidence {rec.confidence!r} exceeds weakest supporting "
                             f"evidence reliability {weakest!r}", where))

        weak_samples = [
            index[r].evidence_id
            for ref in rec.implication_refs
            if ref in by_implication
            for r in by_implication[ref].supports_refs
            if r in index and not index[r].win_loss.sample_sufficient
        ]
        if weak_samples:
            findings.append(
                _finding("W-sample", "warning",
                         f"rests on evidence with insufficient W/L sample: {sorted(set(weak_samples))}", where))

    # An implication argued as both a strength and a vulnerability is sometimes
    # legitimate (one bundle can hold an offensive positive and a defensive
    # negative), so this is a warning, not a rejection. It is worth surfacing
    # because it is also the shape a mischaracterisation takes: the first live
    # run produced "offensive efficiency is highly stable" from a bundle whose
    # own cited effect size was ~1.0. Pure set logic — no prose heuristics.
    strength_refs = {r for c in report.strengths for r in c.implication_refs}
    vulnerability_refs = {r for c in report.vulnerabilities for r in c.implication_refs}
    if dual := strength_refs & vulnerability_refs:
        findings.append(
            _finding("W-dual-framing", "warning",
                     f"implication(s) {sorted(dual)} argued as both a strength and a "
                     f"vulnerability; check the framing matches the cited effect sizes", "report"))

    findings.extend(_prose_findings(report.executive_summary, pack, "executive_summary", allow_scheme=False))

    if re.search(r"\d", report.executive_summary):
        findings.append(
            _finding("W-numeral", "warning",
                     "executive summary contains a numeral; numbers should come from the "
                     "renderer, not the model", "executive_summary"))

    cited = {r for c in report.all_claims() for r in c.implication_refs}
    cited |= {r for rec in report.recommendations for r in rec.implication_refs}
    distinct_evidence = {
        r for ref in cited if ref in by_implication for r in by_implication[ref].supports_refs
    }
    if len(distinct_evidence) < MIN_SIGNALS:
        findings.append(
            _finding("W-thin", "warning",
                     f"report rests on only {len(distinct_evidence)} distinct evidence items", "report"))

    return ValidationResult(findings=findings)


def _prose_findings(
    text: str, pack: EvidencePack, where: str | None, *, allow_scheme: bool
) -> list[Finding]:
    """Shared prose checks (R3, R4, R6)."""
    findings: list[Finding] = []

    if hits := _hits(text, PERSONNEL_TERMS):
        findings.append(
            _finding("R4", "reject",
                     f"player/personnel claim ({', '.join(hits)}); this dataset is team-level only", where))

    if hits := _hits(text, VIDEO_TERMS):
        findings.append(
            _finding("R4", "warning",
                     f"video-adjacent language ({', '.join(hits)}); no video evidence exists in this MVP", where))

    if not allow_scheme and (hits := _hits(text, SCHEME_TERMS)):
        findings.append(
            _finding("R4", "reject",
                     f"scheme/coverage claim about the opponent ({', '.join(hits)}); play-by-play "
                     f"carries no scheme information", where))

    if hits := _hits(text, UNSUPPORTED_METRIC_TERMS):
        findings.append(
            _finding("R3", "reject",
                     f"references a metric this pipeline does not compute ({', '.join(hits)})", where))

    if PACK_STATE_NO_WIN_LOSS in pack.pack_states and (hits := _hits(text, OUTCOME_FRAMING_TERMS)):
        findings.append(
            _finding("R6", "reject",
                     f"win/loss framing ({', '.join(hits)}) but this team has no rankable W/L "
                     f"evidence ({pack.wins}-{pack.losses})", where))

    return findings


def apply_resolved_strengths(pack: EvidencePack, tactical: TacticalOutput) -> TacticalOutput:
    """Stamp the Python-resolved claim strength onto each implication.

    Called before rendering so the report can never display a strength the
    evidence does not carry."""
    for imp in tactical.implications:
        resolved, reason = resolve_claim_strength(pack, imp)
        imp.resolved_claim_strength = resolved
        imp.downgrade_reason = reason
    return tactical
