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
    Confidence,
    EvidenceItem,
    EvidencePack,
    Finding,
    KeyToWin,
    ScoutingReport,
    TacticalImplication,
    TacticalOutput,
    TriageOutput,
    ValidationResult,
)

MIN_SIGNALS = 8
MAX_SIGNALS = 12
# 4-5, not 3-5: the head scout's recommendations ARE the report's "Keys to
# Win" (see reports/contracts.py / prompts.head_scout_system_prompt) — a coach
# reading 2-3 bullets is under-served.
MIN_RECOMMENDATIONS = 4
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

# A win/loss split is a correlation: two subsets of the same team's games,
# grouped by an outcome that many other things also affected. Checked
# everywhere, independent of OUTCOME_FRAMING_TERMS/R6 above (which is only
# about whether outcome framing may be used AT ALL for a team with no
# rankable W/L evidence) — this is about HOW it may be phrased when it is
# legitimately used. "They shoot worse in losses" is a difference; "their
# shooting causes them to lose" is a causal claim the data cannot support.
CAUSAL_TERMS = (
    "causes them to", "causes their", "causing them to", "caused their",
    "lead to a win", "lead to a loss", "lead to winning", "lead to losing",
    "leads to a win", "leads to a loss", "leads to winning", "leads to losing",
    "result in a win", "result in a loss",
    "results in a win", "results in a loss",
    "makes them win", "makes them lose",
    "forces a win", "forces a loss",
    "is why they win", "is why they lose",
    "the reason they win", "the reason they lose",
    "drives their win", "drives their loss",
    "because they win", "because they lose",
)

# Descriptive claims that need evidence this dataset structurally does not
# have — matches the product's own declared UNAVAILABLE list one-to-one
# (evidence_pack.py: NA.possession_type_half_court, NA.video, NA.scheme).
# "Unless directly supported by an available metric" is enforced by the same
# mechanism as everywhere else in this module: a metric that IS available
# (e.g. "Fast Break Points (per game)") can still be cited freely, because
# citing it does not require any of these phrases — only the unsupported
# CLAIM OF INTENT/CLASSIFICATION on top of a real metric trips this ("by
# design" on top of a fast-break count; "half-court offense" as a possession
# classification we never computed).
#
# Exempt in advice-to-us text (objective/method), same as SCHEME_TERMS: "force
# them into the half-court" is a legitimate instruction to our own team and
# claims nothing about their demonstrated tendencies.
UNSUPPORTED_EVIDENCE_TERMS = (
    # possession type / shot detail this dataset cannot classify
    "half-court offense", "half court offense",
    "half-court set", "half court set", "half-court sets", "half court sets",
    "half-court identity", "set offense", "in the half court",
    "half-court execution",
    # claims of intentionality behind a raw, one-directional signal
    "by design", "designed to", "coaching intent",
    # requires video/tracking data that does not exist
    "shot contest", "shot contests", "perimeter defense", "defensive perimeter",
)


def _hits(text: str, terms: Iterable[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if t in low]


def _word_hits(text: str, terms: Iterable[str]) -> list[str]:
    """Substring matching, but only on whole words.

    ``_hits`` is fine for the multi-word denylists above, where an accidental
    substring match is implausible. Single common words are a different matter:
    a plain ``"late" in text`` fires inside "isolates", and ``"early"`` fires
    inside "clearly" and "nearly". Both were live false positives the moment
    the temporal rule was applied beyond Key objectives.
    """
    low = text.lower()
    return [t for t in terms if re.search(rf"\b{re.escape(t)}\b", low)]


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


# ---- R8 / recommendation confidence -----------------------------------------

def resolve_recommendation_confidence(
    pack: EvidencePack, tactical: TacticalOutput, rec: KeyToWin
) -> tuple[Confidence, str | None]:
    """Cap confidence at the reliability of the weakest evidence it rests on.

    Mirrors :func:`resolve_claim_strength`: the agent's confidence is only ever
    a proposal, and this can lower it, never raise it. Reliability and
    confidence share the same three-value scale (high/moderate/low), so the
    cap is a direct ``min()`` over that shared rank space — no separate mapping
    to invent or keep in sync.
    """
    index = pack.index()
    by_implication = {i.implication_id: i for i in tactical.implications}
    tiers = [
        index[r].reliability_tier
        for ref in rec.implication_refs
        if ref in by_implication
        for r in by_implication[ref].supports_refs
        if r in index
    ]
    if not tiers:
        return rec.confidence, None

    weakest = min(tiers, key=lambda t: RELIABILITY_RANK[t])
    resolved: Confidence = min(rec.confidence, weakest, key=lambda c: CONFIDENCE_RANK[c])  # type: ignore[assignment]
    if resolved != rec.confidence:
        return resolved, f"weakest supporting evidence reliability is {weakest!r}"
    return resolved, None


# ---- R9 / calibrated intensity language -------------------------------------
#
# A general, evidence-driven gate on how STRONG a claim's wording may be —
# deliberately not an "does this specific adjective correctly describe this
# specific effect size" heuristic. That kind of fragile semantic matching was
# considered and rejected for W-dual-framing above ("deliberately not an
# adjective-versus-effect-size heuristic"). This is coarser and mechanical: a
# fixed lexicon of degree-words, each requiring a numeric bar the cited
# evidence must clear on at least one of two axes the deterministic layer
# already computed — nothing new is measured, nothing is invented per term.
#
#   * league-relative extremity: |percentile - 50|, 0..50. 50 means literally
#     first or literally last in the league.
#   * win/loss effect size: |effect_size|, the same number already on every
#     evidence card. 0.5 / 0.8 are the standard "medium"/"large" thresholds
#     from that convention (Cohen's d), not tuned for this dataset.
#
# Aggregated as a MEAN across a claim's cited evidence, not a max: a single
# strong item should not license bold language about a bundle that also rests
# on two middling ones. Low-reliability items are dropped from the average
# entirely, so thin evidence cannot buy strong language by inflating it.
#
# Thresholds tightened a second pass after live review: rank 2-of-14 (percentile
# 92.3, extremity 42.3) originally cleared the tier-3 bar and produced
# "exceptional offensive rebounding" — genuinely defensible, but the preferred
# phrasing for that is the objectively-verifiable "among the league leaders",
# not a degree-word. Tier 3 now requires something closer to literally first
# or last (extremity 45+, i.e. roughly top-1-of-14 rather than top-2), so a
# vague intensity word is reserved for the rare case where "league-leading"
# undersells it. See ``TIER3_PREFERRED_ALTERNATIVES`` in prompts.py.

TIER2_MIN_PERCENTILE_EXTREMITY = 30.0  # top/bottom ~30th percentile
TIER2_MIN_EFFECT = 0.6  # a notch above Cohen's "medium"
TIER3_MIN_PERCENTILE_EXTREMITY = 45.0  # essentially first or last in the league
TIER3_MIN_EFFECT = 1.0  # a full standard deviation, well past Cohen's "large"

# term -> minimum tier it requires. Anything not listed is tier 1 and
# unrestricted: ordinary comparative language ("more", "tends to", "above
# average") needs no numeric backing beyond what the evidence already is.
# Kept tight and unambiguous, like the other denylists in this module.
#
# Precise, rank-based phrasing ("league-leading", "among the league leaders",
# "below league average", "one of the smallest/largest shares") is deliberately
# NOT in this lexicon — it needs no gate because it is already a near-literal
# restatement of the rank/percentile data, not a degree judgment layered on
# top of it. That is the alternative the prompt steers agents toward instead
# of reaching for these words.
INTENSITY_LEXICON: dict[str, int] = {
    # tier 3 — reserve for a genuine league or win/loss extreme
    "extremely": 3, "extreme": 3,
    "elite": 3,
    "exceptional": 3, "exceptionally": 3,
    "massive": 3, "massively": 3,
    "major": 3,
    "dramatic": 3, "dramatically": 3,
    "severe": 3, "severely": 3,
    "rarely": 3,
    "overwhelming": 3, "overwhelmingly": 3,
    "dominant": 3, "dominate": 3, "dominates": 3,
    "explosive": 3, "explosively": 3,
    "outstanding": 3,
    "phenomenal": 3,
    "always": 3, "never": 3,
    # tier 2 — needs at least a real, quantified difference
    "highly": 2,
    "significantly": 2, "significant": 2,
    "substantially": 2, "substantial": 2,
    "strongly": 2,
    "considerably": 2, "considerable": 2,
    "excel": 2, "excels": 2, "excellent": 2,
}


def _intensity_support(items: Iterable[EvidenceItem]) -> tuple[float, float]:
    """``(mean league-extremity, mean |win/loss effect|)`` over the
    non-low-reliability items. Either mean clearing its tier's threshold is
    enough — a claim can be earned by being a league outlier, by a large
    win/loss split, or both."""
    usable = [i for i in items if i.reliability_tier != "low"]
    if not usable:
        return 0.0, 0.0
    extremities = [abs(i.league_percentile - 50.0) for i in usable if i.league_percentile is not None]
    effects = [
        abs(i.win_loss.effect_size)
        for i in usable
        if i.win_loss.agent_rankable and i.win_loss.effect_size is not None
    ]
    mean_extremity = sum(extremities) / len(extremities) if extremities else 0.0
    mean_effect = sum(effects) / len(effects) if effects else 0.0
    return mean_extremity, mean_effect


def _clears_intensity_tier(tier: int, items: Iterable[EvidenceItem]) -> bool:
    if tier < 2:
        return True
    min_pct = TIER3_MIN_PERCENTILE_EXTREMITY if tier >= 3 else TIER2_MIN_PERCENTILE_EXTREMITY
    min_effect = TIER3_MIN_EFFECT if tier >= 3 else TIER2_MIN_EFFECT
    mean_extremity, mean_effect = _intensity_support(items)
    return mean_extremity >= min_pct or mean_effect >= min_effect


def _intensity_findings(text: str, items: list[EvidenceItem], where: str | None) -> list[Finding]:
    low = text.lower()
    findings: list[Finding] = []
    for term, tier in sorted(INTENSITY_LEXICON.items()):
        if term not in low:
            continue
        if _clears_intensity_tier(tier, items):
            continue
        mean_extremity, mean_effect = _intensity_support(items)
        min_pct = TIER3_MIN_PERCENTILE_EXTREMITY if tier >= 3 else TIER2_MIN_PERCENTILE_EXTREMITY
        min_effect = TIER3_MIN_EFFECT if tier >= 3 else TIER2_MIN_EFFECT
        findings.append(
            _finding(
                "R9", "reject",
                f"{term!r} implies a degree the cited evidence does not reach (league "
                f"extremity {mean_extremity:.0f}/50, win-loss effect {mean_effect:.2f}; "
                f"needs {min_pct:.0f}+ or {min_effect:.1f}+ on non-low-reliability evidence). "
                f"Use a milder word instead, e.g. 'notably', 'a clear tendency toward', "
                f"'above/below the league average'.",
                where,
            )
        )
    return findings


# ---- R13 / stability language vs a materially large win/loss split ---------
#
# Mirror image of R9: instead of gating BIG words behind evidence that is
# genuinely extreme, this gates STABILITY words behind evidence that is NOT
# stable. A live run produced "their offensive efficiency remains stable"
# citing an Offensive Rating item whose OWN win/loss effect size was ~1.0 (W
# 121.8 / L 110.8) — a materially large split by any reading, the opposite of
# stable. Same aggregation convention as R9 (mean |effect_size| across
# non-low-reliability cited items, not max, via the same ``_intensity_support``
# helper) so the same "one strong item can't decide the whole claim"
# conservatism applies in both directions.
#
# Deliberately excludes "consistent"/"constant" as bare synonyms of
# "unchanging" — "consistent" is already load-bearing filler elsewhere in this
# codebase (StubBackend and the shared test factory both use "consistent
# direction across cited measures" to mean cross-signal agreement, not
# win/loss stability of a metric) and is too generic to gate safely.

STABILITY_MIN_MATERIAL_EFFECT = 0.8  # Cohen's "large", the standard cutoff

STABILITY_TERMS = (
    "remains stable", "stays stable", "is stable", "stable",
    "remains constant", "stays constant", "constant",
    "steady", "unchanged",
    "little difference", "minimal difference", "little variation",
    "no significant difference", "no meaningful difference",
)


def _stability_findings(text: str, items: list[EvidenceItem], where: str | None) -> list[Finding]:
    if not (hits := _hits(text, STABILITY_TERMS)):
        return []
    _, mean_effect = _intensity_support(items)
    if mean_effect < STABILITY_MIN_MATERIAL_EFFECT:
        return []
    return [
        _finding(
            "R13", "reject",
            f"{', '.join(hits)!r} implies unchanged performance, but the cited evidence shows a "
            f"materially large win/loss difference (mean effect {mean_effect:.2f} >= "
            f"{STABILITY_MIN_MATERIAL_EFFECT:.1f} on non-low-reliability evidence); describe the "
            f"wins/losses difference instead of claiming stability.",
            where,
        )
    ]


# ---- R14 / R15 — Key objectives may not introduce constructs or timeframes
# the cited evidence does not support -----------------------------------------
#
# The objective is advice to our own team, so it skips the standard claim
# checks (see the allow_scheme=True comment in validate_report) — but it must
# still not smuggle in narrative constructs no metric in this pipeline
# measures ("rhythm", "intensity", "momentum" — nothing here quantifies
# effort, flow, or carry-over between plays, at any claim strength, so this is
# an unconditional denylist like UNSUPPORTED_EVIDENCE_TERMS), nor a temporal
# qualifier ("early", "late", ...) unless the Key's own cited evidence is
# actually scoped to that timeframe (see evidence_pack.METRIC_SPECS: "1H" for
# first-half/early, "clutch"/"Q4" for late/closing) — the same "cite it or
# don't claim it" discipline R12 already enforces for tactic evidence,
# applied to time instead of evidence identity.

UNSUPPORTED_CONSTRUCT_TERMS = ("rhythm", "intensity", "momentum")

# ---- R16 / a Key objective must be about what its evidence actually measures -
#
# Citing valid evidence is not the same as citing RELEVANT evidence. A live run
# produced the objective "Focus on defensive execution to lower their offensive
# efficiency" supported only by Defensive Rating and Net Rating — every id
# resolved, R12 was satisfied, and the sentence was still wrong: it targets
# their OFFENSE while the evidence measures their DEFENSE and their overall
# margin.
#
# The fix is deliberately not a semantic parser. Two small deterministic maps:
# metric_name -> the families it measures, and an unambiguous opponent-directed
# phrase -> the family it commits the objective to. If the objective commits to
# a family, at least one cited item must actually be in it.
#
# Keyed by ``metric_name`` rather than adding a field to evidence_pack's
# MetricSpec: this is an interpretation-layer concern, and the deterministic
# layer stays untouched. A metric absent from this map constrains nothing.
METRIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "offensive_rating": ("offense",),
    "defensive_rating": ("defense",),
    "net_rating": ("overall",),
    "pace": ("tempo",),
    "efg_pct": ("offense",),
    "tov_pct": ("turnovers",),
    "orb_pct": ("rebounding",),
    "ft_rate": ("free_throw", "offense"),
    "fg3a_rate": ("offense",),
    "ast_to_ratio": ("offense", "turnovers"),
    "scoring_share_2pt": ("offense",),
    "scoring_share_3pt": ("offense",),
    "scoring_share_ft": ("free_throw", "offense"),
    "points_off_turnovers": ("offense", "turnovers"),
    "second_chance_points": ("rebounding", "offense"),
    "points_per_second_chance_possession": ("rebounding", "offense"),
    "provider_fast_break_points": ("transition", "offense"),
    "assisted_fgm_pct": ("offense",),
    "largest_scoring_run_for": ("runs", "offense"),
    "runs_8_plus_for": ("runs", "offense"),
}

# Only opponent-directed phrases ("their ...") are listed. That matters: an
# objective is advice to US, so a bare "defensive execution" is OUR defense and
# must not commit the Key to anything, while "their defensive efficiency" is a
# statement about what we are targeting in THEM and must be backed.
OBJECTIVE_TARGET_FAMILIES: dict[str, tuple[str, ...]] = {
    "their offensive efficiency": ("offense",),
    "their offensive output": ("offense",),
    "their offensive rating": ("offense",),
    "their offense": ("offense",),
    "their scoring efficiency": ("offense",),
    "their shooting": ("offense",),
    "their defensive efficiency": ("defense",),
    "their defensive rating": ("defense",),
    "their defense": ("defense",),
    "their offensive rebounding": ("rebounding",),
    "their offensive glass": ("rebounding",),
    "their second-chance": ("rebounding",),
    "their second chance": ("rebounding",),
    "their turnover": ("turnovers",),
    "their free-throw": ("free_throw",),
    "their free throw": ("free_throw",),
    "their scoring runs": ("runs",),
    "their runs": ("runs",),
    "their pace": ("tempo",),
    "their tempo": ("tempo",),
    "their transition": ("transition",),
    "their fast break": ("transition",),
}

# ---- R17 / a Key objective states an outcome, never a technique -------------
#
# The layer separation the report is built on: an objective is the measurable
# thing we want to happen, a tactic is one specific method for causing it. A
# live run produced "Execute disciplined defense in late-game situations to
# contest their clutch shot attempts" — the clutch evidence supports the
# late-game objective, but "contest" is a technique, and nothing in this
# dataset measures shot contests at all.
#
# Deliberately only unambiguous physical techniques. Outcome verbs an objective
# legitimately needs — limit, reduce, force, prevent, secure, control, attack,
# exploit, defend, match — are untouched. Named coverages ("drop coverage")
# are governed by SCHEME_TERMS, which already permits them in advice-to-us
# text; this list is about a technique applied to a play, not a scheme name.
# All of these stay legal inside ``tactic.method``, which is where they belong.
OBJECTIVE_TECHNIQUE_TERMS = (
    "contest", "contesting", "contested",
    "box out", "boxing out", "box-out", "boxout",
    "trap", "trapping",
    "double-team", "double team", "double-teaming",
    "full-court press", "full court press",
    "hedge", "hedging",
    "close out", "closing out", "closeout",
    "blitz", "blitzing",
    "switch onto",
)

# term -> the evidence scopes that would justify using it in a Key's
# objective. Anything not listed here is unrestricted ordinary language.
TEMPORAL_QUALIFIER_SCOPES: dict[str, tuple[str, ...]] = {
    "early": ("1H",),
    "opening": ("1H",),
    "first half": ("1H",),
    "first-half": ("1H",),
    "late": ("clutch", "Q4"),
    "closing": ("clutch", "Q4"),
    "down the stretch": ("clutch", "Q4"),
    "fourth quarter": ("Q4",),
    "final minutes": ("clutch",),
}


# ---- R18 / internal audit vocabulary must not reach the coach ---------------
#
# ``claim_strength`` is pipeline metadata: the tactical scout proposes it, the
# validator re-resolves it, and the report layer keeps it in JSON for audit but
# never renders it (see reports/contracts.py). The head scout is nonetheless
# told each implication's strength so it can phrase an inferred tendency more
# tentatively — and a live run took that literally, writing "they possess an
# indicated league-leading capacity" straight into the coach-facing prose.
#
# Matching the modifier form only ("an indicated ", not bare "indicated")
# keeps the ordinary verb usage legal: "the evidence indicates a shift" is
# fine, "an indicated tendency" is the leak.
INTERNAL_VOCABULARY_TERMS = (
    "an indicated ", "an established ", "a speculative ",
    "claim strength", "claim_strength",
    "proposed_claim_strength", "resolved_claim_strength",
)


def _internal_vocabulary_findings(text: str, where: str | None) -> list[Finding]:
    if not (hits := _hits(text, INTERNAL_VOCABULARY_TERMS)):
        return []
    return [
        _finding(
            "R18", "reject",
            f"internal audit vocabulary in coach-facing prose ({', '.join(hits)}); "
            f"claim strength is pipeline metadata the coach never sees — express the "
            f"uncertainty in plain language instead ('the data points toward', "
            f"'on the available evidence')",
            where,
        )
    ]


def _construct_findings(
    text: str, items: list[EvidenceItem], where: str | None, *, temporal: bool = True
) -> list[Finding]:
    """R14/R15 — unmeasurable constructs and unearned timeframes.

    Applied wherever an agent describes the opponent, not only inside a Key's
    objective. Both defects were first seen in an objective and both then
    reappeared elsewhere: a live Strengths claim read "capable of establishing
    positive momentum early, as shown by a positive net rating" — an
    unmeasurable construct and an unbacked timeframe, in a sentence no
    objective-scoped rule would ever look at.

    ``temporal=False`` for ``tactic.method``, the one place a temporal word is
    usually about the shot clock ("attack early in the clock") rather than the
    time of game, and so cannot be checked against evidence scope.
    """
    findings: list[Finding] = []
    if hits := _word_hits(text, UNSUPPORTED_CONSTRUCT_TERMS):
        findings.append(
            _finding(
                "R14", "reject",
                f"invokes a construct this dataset cannot measure ({', '.join(hits)}); "
                f"no metric here quantifies effort, flow, or carry-over between plays",
                where,
            )
        )
    if not temporal:
        return findings

    scopes_present = {i.scope for i in items}
    present_terms = set(_word_hits(text, TEMPORAL_QUALIFIER_SCOPES))
    for term, allowed_scopes in sorted(TEMPORAL_QUALIFIER_SCOPES.items()):
        if term not in present_terms or scopes_present & set(allowed_scopes):
            continue
        findings.append(
            _finding(
                "R15", "reject",
                f"{term!r} is a temporal qualifier the cited evidence does not support "
                f"(needs a {'/'.join(allowed_scopes)}-scoped item; cited scopes: "
                f"{sorted(scopes_present) or ['none']})",
                where,
            )
        )
    return findings


def _objective_findings(text: str, items: list[EvidenceItem], where: str | None) -> list[Finding]:
    """R16/R17 — what a Key's objective specifically may say. Deliberately
    objective-only; R14/R15 live in ``_construct_findings`` and apply broadly."""
    findings: list[Finding] = []
    low = text.lower()

    # R16 — the objective must be about what its evidence actually measures.
    families_present: set[str] = set()
    for item in items:
        families_present.update(METRIC_FAMILIES.get(item.metric_name, ()))
    for phrase, required in sorted(OBJECTIVE_TARGET_FAMILIES.items()):
        if phrase not in low or families_present & set(required):
            continue
        findings.append(
            _finding(
                "R16", "reject",
                f"objective targets {phrase!r} but none of its cited evidence measures "
                f"{'/'.join(required)} (cited families: {sorted(families_present) or ['none']}); "
                f"cite evidence about that side of the ball, or state the objective the "
                f"evidence actually supports",
                where,
            )
        )

    # R17 — an objective states an outcome; a technique belongs in a tactic.
    if hits := _hits(text, OBJECTIVE_TECHNIQUE_TERMS):
        findings.append(
            _finding(
                "R17", "reject",
                f"objective names a specific technique ({', '.join(hits)}); a Key states the "
                f"measurable outcome to target and the method belongs in a tactic, where this "
                f"vocabulary is allowed",
                where,
            )
        )
    return findings


def _items_for_implications(
    refs: Iterable[str],
    by_implication: dict[str, TacticalImplication],
    index: dict[str, EvidenceItem],
) -> list[EvidenceItem]:
    """implication ids -> the deduplicated evidence items behind them.

    Shared by every head-scout-stage prose check (claims, recommendation
    rationale, executive summary) so all three read "the evidence this
    sentence is actually grounded in" the same way render.py does.
    """
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for ref in refs:
        imp = by_implication.get(ref)
        if imp is None:
            continue
        for eid in imp.supports_refs:
            if eid in index and eid not in seen:
                seen.add(eid)
                items.append(index[eid])
    return items


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

        items = [index[r] for r in signal.evidence_refs if r in index]
        text = f"{signal.headline}\n{signal.why_kept}"
        findings.extend(_prose_findings(text, pack, where, allow_scheme=False))
        findings.extend(_intensity_findings(text, items, where))
        findings.extend(_stability_findings(text, items, where))
        findings.extend(_construct_findings(text, items, where))

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

        items = [index[r] for r in imp.supports_refs if r in index]
        text = f"{imp.tendency}\n{imp.claim_basis}"
        findings.extend(_prose_findings(text, pack, where, allow_scheme=False))
        findings.extend(_intensity_findings(text, items, where))
        findings.extend(_stability_findings(text, items, where))
        findings.extend(_construct_findings(text, items, where))

    return ValidationResult(findings=findings)


def validate_report(
    pack: EvidencePack,
    triage: TriageOutput,
    tactical: TacticalOutput,
    report: ScoutingReport,
) -> ValidationResult:
    findings: list[Finding] = []
    implication_ids = {i.implication_id for i in tactical.implications}
    index = pack.index()
    by_implication = {i.implication_id: i for i in tactical.implications}

    for claim in report.all_claims():
        where = claim.text[:48]
        if not claim.implication_refs:
            findings.append(_finding("R5", "reject", "report claim has no supporting implication", where))
        for ref in claim.implication_refs:
            if ref not in implication_ids:
                findings.append(_finding("R1", "reject", f"unknown implication_id {ref!r}", where))
        findings.extend(_prose_findings(claim.text, pack, where, allow_scheme=False))
        findings.extend(_internal_vocabulary_findings(claim.text, where))
        claim_items = _items_for_implications(claim.implication_refs, by_implication, index)
        findings.extend(_intensity_findings(claim.text, claim_items, where))
        findings.extend(_stability_findings(claim.text, claim_items, where))
        findings.extend(_construct_findings(claim.text, claim_items, where))

    n = len(report.recommendations)
    if n < MIN_RECOMMENDATIONS or n > MAX_RECOMMENDATIONS:
        findings.append(
            _finding("R7", "reject",
                     f"{n} recommendations; required {MIN_RECOMMENDATIONS}-{MAX_RECOMMENDATIONS}", "report"))

    for rec in report.recommendations:
        where = rec.recommendation_id
        if not rec.implication_refs:
            findings.append(_finding("R5", "reject", "recommendation has no supporting implication", where))
        for ref in rec.implication_refs:
            if ref not in implication_ids:
                findings.append(_finding("R1", "reject", f"unknown implication_id {ref!r}", where))

        rec_items = _items_for_implications(rec.implication_refs, by_implication, index)

        # The objective is advice to our own team, so scheme vocabulary is
        # allowed there and nowhere else. why_it_matters is still a claim
        # about them. The objective still may not invent an unsupported
        # construct or an unearned timeframe (R14/R15) — that is not a claim
        # ABOUT the opponent, it is a constraint on what "achieving this
        # objective" is even allowed to mean given the evidence behind it.
        findings.extend(_prose_findings(rec.objective, pack, where, allow_scheme=True))
        findings.extend(_objective_findings(rec.objective, rec_items, where))
        findings.extend(_construct_findings(rec.objective, rec_items, where))
        findings.extend(_internal_vocabulary_findings(rec.objective, where))
        findings.extend(_prose_findings(rec.why_it_matters, pack, where, allow_scheme=False))
        findings.extend(_internal_vocabulary_findings(rec.why_it_matters, where))

        # Intensity/stability calibration applies to why_it_matters (a claim
        # about them), not the objective (advice to us) — same split as
        # scheme vocabulary.
        findings.extend(_intensity_findings(rec.why_it_matters, rec_items, where))
        findings.extend(_stability_findings(rec.why_it_matters, rec_items, where))
        findings.extend(_construct_findings(rec.why_it_matters, rec_items, where))

        rec_ref_set = set(rec.implication_refs)
        for tactic in rec.tactics:
            twhere = f"{rec.recommendation_id}/{tactic.tactic_id}"
            for ref in tactic.implication_refs:
                if ref not in implication_ids:
                    findings.append(_finding("R1", "reject", f"unknown implication_id {ref!r}", twhere))
            # A tactic may only cite evidence its own Key to Win already rests
            # on — it cannot smuggle in fresh evidence to justify an otherwise
            # arbitrary tactical choice. Structural, not a judgment call about
            # whether the link is "really" mechanical.
            smuggled = set(tactic.implication_refs) - rec_ref_set
            if smuggled:
                findings.append(
                    _finding("R12", "reject",
                             f"tactic cites implication(s) {sorted(smuggled)} its own Key to Win "
                             f"does not rest on; a tactic may only draw on evidence already "
                             f"backing the objective it serves", twhere))

            # method is advice to us (scheme vocabulary allowed); mechanism
            # explains the link and is still a claim about them.
            findings.extend(_prose_findings(tactic.method, pack, twhere, allow_scheme=True))
            findings.extend(_internal_vocabulary_findings(tactic.method, twhere))
            findings.extend(_prose_findings(tactic.mechanism, pack, twhere, allow_scheme=False))
            findings.extend(_internal_vocabulary_findings(tactic.mechanism, twhere))
            tactic_items = _items_for_implications(tactic.implication_refs, by_implication, index)
            findings.extend(_intensity_findings(tactic.mechanism, tactic_items, twhere))
            findings.extend(_stability_findings(tactic.mechanism, tactic_items, twhere))
            findings.extend(_construct_findings(tactic.method, tactic_items, twhere, temporal=False))
            findings.extend(_construct_findings(tactic.mechanism, tactic_items, twhere))

        # Confidence may not exceed the reliability of the weakest evidence it
        # transitively rests on. Corrected deterministically (see
        # apply_resolved_confidence, called after this stage validates) —
        # this finding is audit trail, not something the coach ever sees.
        _, downgrade_reason = resolve_recommendation_confidence(pack, tactical, rec)
        if downgrade_reason is not None:
            findings.append(
                _finding("R8", "warning",
                         f"confidence {rec.confidence!r} auto-capped ({downgrade_reason})", where))

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

    cited = {r for c in report.all_claims() for r in c.implication_refs}
    cited |= {r for rec in report.recommendations for r in rec.implication_refs}

    findings.extend(_prose_findings(report.executive_summary, pack, "executive_summary", allow_scheme=False))
    findings.extend(_internal_vocabulary_findings(report.executive_summary, "executive_summary"))
    # The summary synthesizes the whole report rather than one implication, so
    # its evidence pool for calibration purposes is everything the report
    # cites anywhere — the same union used for W-thin below.
    summary_items = _items_for_implications(cited, by_implication, index)
    findings.extend(_intensity_findings(report.executive_summary, summary_items, "executive_summary"))
    findings.extend(_stability_findings(report.executive_summary, summary_items, "executive_summary"))
    findings.extend(_construct_findings(report.executive_summary, summary_items, "executive_summary"))

    if re.search(r"\d", report.executive_summary):
        findings.append(
            _finding("W-numeral", "warning",
                     "executive summary contains a numeral; numbers should come from the "
                     "renderer, not the model", "executive_summary"))

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

    if hits := _hits(text, CAUSAL_TERMS):
        findings.append(
            _finding("R10", "reject",
                     f"causal language about a correlational win/loss split ({', '.join(hits)}); "
                     f"describe the difference, never state or imply what caused it", where))

    # Same exemption as SCHEME_TERMS: advice-to-us text (objective/method) may
    # instruct forcing them into the half-court or contesting shots — that
    # claims nothing about their demonstrated tendencies.
    if not allow_scheme and (hits := _hits(text, UNSUPPORTED_EVIDENCE_TERMS)):
        findings.append(
            _finding("R11", "reject",
                     f"describes evidence this dataset does not have ({', '.join(hits)}); "
                     f"half-court/possession-type, shot-contest/perimeter-defense and "
                     f"intentionality claims about the opponent are unsupported unless a "
                     f"specific available metric backs them", where))

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


def apply_resolved_confidence(
    pack: EvidencePack, tactical: TacticalOutput, report: ScoutingReport
) -> ScoutingReport:
    """Stamp the Python-resolved confidence onto each recommendation.

    Called before rendering so a recommendation can never DISPLAY a confidence
    the evidence does not carry. The raw model-proposed value stays on
    ``confidence`` for audit; ``render.py`` always reads ``resolved_confidence``
    (falling back to the proposal only if this was never called)."""
    for rec in report.recommendations:
        resolved, reason = resolve_recommendation_confidence(pack, tactical, rec)
        rec.resolved_confidence = resolved
        rec.confidence_downgrade_reason = reason
    return report
