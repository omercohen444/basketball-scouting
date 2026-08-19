"""The public report contract — what the API serves, the PDF prints, and the
database stores.

Why a separate contract rather than serving ``render_report()``'s dict directly:
that dict is an *internal* artifact whose shape may follow the agent layer, and
an untyped dict gives a frontend nothing to build against. This module pins a
typed, versioned, public-safe view.

What is deliberately **not** here: prompts, raw provider payloads, agent
identifiers, API keys, stack traces, internal file paths, the model-authored
``report_id`` (the canonical id is a UUID minted at save time), and the
``where`` locator on validation findings (it can echo raw claim text).

What *is* preserved, because a scouting report without it is not honest:
reliability tiers, validation states, sample sizes, league context, caveats,
declared-unavailable evidence, and the validation summary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agents.evidence_pack import LIMITATION_LEGEND

# Bump when this contract changes shape in a way a consumer would notice.
REPORT_CONTRACT_VERSION = "report-v1"

SECTION_KEYS = (
    "offensive_identity",
    "strengths",
    "vulnerabilities",
    "transition_notes",
    "turnover_notes",
)

SECTION_TITLES: dict[str, str] = {
    "offensive_identity": "Offensive Identity",
    "strengths": "Strengths",
    "vulnerabilities": "Vulnerabilities",
    "transition_notes": "Transition",
    "turnover_notes": "Turnovers",
}


class WinLossCard(BaseModel):
    """Wins-vs-losses split for one metric.

    ``available`` is false whenever the deterministic layer masked the effect —
    the numbers are then genuinely absent, not hidden, and ``reason`` says why.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    in_wins: str | None = None
    in_losses: str | None = None
    effect_size: float | None = None
    favorable_in_wins: bool | None = None
    sample: str | None = None
    reason: str | None = None


class EvidenceCard(BaseModel):
    """One deterministic fact, pre-formatted for display.

    Every value here is a *string the backend already formatted*. A frontend must
    never recompute or reformat these — that is the boundary that keeps the
    authoritative numbers deterministic.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    metric: str
    scope: str
    value: str
    league_rank: str | None = None
    league_percentile: float | None = None
    league_average: str | None = None
    sample_games: int = 0
    sample_possessions: int | None = None
    reliability: Literal["high", "moderate", "low"] = "moderate"
    validation_state: str = "validated_deterministic"
    direction: str = "neutral"
    win_loss: WinLossCard = Field(default_factory=WinLossCard)
    limitations: list[str] = Field(default_factory=list)


class ClaimView(BaseModel):
    """One statement in a report section, with the evidence it rests on."""

    model_config = ConfigDict(extra="forbid")

    text: str
    claim_strength: Literal["established", "indicated", "speculative"] = "speculative"
    implication_refs: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCard] = Field(default_factory=list)


class TacticalOptionView(BaseModel):
    """One optional, specific method for achieving a Key to Win's objective.
    0-2 per key — present only when the agent layer found a clear mechanical
    link from the cited evidence to this specific method."""

    model_config = ConfigDict(extra="forbid")

    tactic_id: str
    method: str
    mechanism: str
    evidence: list[EvidenceCard] = Field(default_factory=list)


class RecommendationView(BaseModel):
    """One "Key to Win": an evidence-supported game objective, why it matters,
    and 0-2 optional tactics for achieving it."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    priority: int
    objective: str
    why_it_matters: str
    confidence: Literal["high", "moderate", "low"] = "moderate"
    implication_refs: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCard] = Field(default_factory=list)
    tactics: list[TacticalOptionView] = Field(default_factory=list)


class ReportSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offensive_identity: list[ClaimView] = Field(default_factory=list)
    strengths: list[ClaimView] = Field(default_factory=list)
    vulnerabilities: list[ClaimView] = Field(default_factory=list)
    transition_notes: list[ClaimView] = Field(default_factory=list)
    turnover_notes: list[ClaimView] = Field(default_factory=list)

    def items(self) -> list[tuple[str, str, list[ClaimView]]]:
        """``(key, display title, claims)`` for non-empty sections, in order."""
        return [
            (key, SECTION_TITLES[key], getattr(self, key))
            for key in SECTION_KEYS
            if getattr(self, key)
        ]


class ValidationNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    message: str


class ValidationSummary(BaseModel):
    """User-facing validation outcome.

    Warnings are shown, not hidden: a warning is the deterministic layer telling
    the reader where to be careful, which is exactly the sort of thing a scouting
    report should say out loud.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    rejects_n: int = 0
    warnings_n: int = 0
    warnings: list[ValidationNote] = Field(default_factory=list)


class UnavailableEvidenceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    reason: str


class ReportProvenance(BaseModel):
    """Where the numbers came from. Shown to the reader, not just stored."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str
    pack_hash: str = ""
    source: str = "segev"
    season: str = "unknown"
    record: str = ""
    games_n: int = 0
    date_range: str = ""
    definition_version: str = "unknown"
    evidence_version: str = "unknown"
    pack_states: list[str] = Field(default_factory=list)

    @property
    def date_range_display(self) -> str:
        """``date_range`` with the tip-off times dropped.

        The stored value is the deterministic layer's, timestamps and all, and
        stays that way — this is a presentation concern, so it is a computed
        property (not serialized, not stored) that the HTML and PDF use.
        """
        return " to ".join(part.strip().split("T")[0] for part in self.date_range.split(" to "))


class PublicReport(BaseModel):
    """The complete, frontend-safe scouting report."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    report_version: str = REPORT_CONTRACT_VERSION
    team_id: str
    team_name: str
    season: str
    generated_at: str
    backend: str = "unknown"
    model_name: str | None = None

    scope_note: str = ""
    executive_summary: str = ""
    sections: ReportSections = Field(default_factory=ReportSections)
    recommendations: list[RecommendationView] = Field(default_factory=list)
    key_evidence: list[EvidenceCard] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    unavailable_evidence: list[UnavailableEvidenceView] = Field(default_factory=list)
    provenance: ReportProvenance
    validation: ValidationSummary = Field(default_factory=ValidationSummary)

    @property
    def record(self) -> str:
        return self.provenance.record


class ReportSummary(BaseModel):
    """Lightweight descriptor — used in listings, never the full report."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    team_id: str
    team_name: str
    season: str
    generated_at: str
    report_version: str
    warnings_n: int = 0


class TeamSummary(BaseModel):
    """One selectable opponent, plus whether a saved report exists for it."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    season: str
    games_n: int = 0
    wins: int = 0
    losses: int = 0
    record: str = ""
    has_report: bool = False
    latest_report_id: str | None = None
    latest_generated_at: str | None = None


# ---- construction from the agent layer's render output ----------------------


def _evidence_card(raw: dict[str, Any]) -> EvidenceCard:
    wl_raw = raw.get("win_loss") or {}
    if wl_raw.get("available") is False or "in_wins" not in wl_raw:
        win_loss = WinLossCard(available=False, reason=wl_raw.get("reason"))
    else:
        win_loss = WinLossCard(
            available=True,
            in_wins=wl_raw.get("in_wins"),
            in_losses=wl_raw.get("in_losses"),
            effect_size=wl_raw.get("effect_size"),
            favorable_in_wins=wl_raw.get("favorable_in_wins"),
            sample=wl_raw.get("sample"),
        )
    return EvidenceCard(
        evidence_id=raw["evidence_id"],
        metric=raw["metric"],
        scope=raw["scope"],
        value=raw["value"],
        league_rank=raw.get("league_rank"),
        league_percentile=raw.get("league_percentile"),
        league_average=raw.get("league_average"),
        sample_games=raw.get("sample_games") or 0,
        sample_possessions=raw.get("sample_possessions"),
        reliability=raw.get("reliability") or "moderate",
        validation_state=raw.get("validation_state") or "validated_deterministic",
        direction=raw.get("direction") or "neutral",
        win_loss=win_loss,
        limitations=list(raw.get("limitations") or []),
    )


def _claim_view(raw: dict[str, Any]) -> ClaimView:
    return ClaimView(
        text=raw["text"],
        claim_strength=raw.get("claim_strength") or "speculative",
        implication_refs=list(raw.get("implication_refs") or []),
        evidence=[_evidence_card(e) for e in raw.get("evidence") or []],
    )


def _tactic_view(raw: dict[str, Any]) -> TacticalOptionView:
    return TacticalOptionView(
        tactic_id=raw["tactic_id"],
        method=raw["method"],
        mechanism=raw.get("mechanism") or "",
        evidence=[_evidence_card(e) for e in raw.get("evidence") or []],
    )


def _recommendation_view(raw: dict[str, Any]) -> RecommendationView:
    return RecommendationView(
        recommendation_id=raw["recommendation_id"],
        priority=raw.get("priority") or 0,
        objective=raw["objective"],
        why_it_matters=raw.get("why_it_matters") or "",
        confidence=raw.get("confidence") or "moderate",
        implication_refs=list(raw.get("implication_refs") or []),
        evidence=[_evidence_card(e) for e in raw.get("evidence") or []],
        tactics=[_tactic_view(t) for t in raw.get("tactics") or []],
    )


# ---- coach-facing curation ---------------------------------------------------
#
# The scouting report is for a coach reading it before a game, not for an
# instructor auditing the pipeline. Detailed validation, hashes, model/backend
# identifiers and long analyst-facing "why we don't have this" explanations
# stay in the full stored/served PublicReport (audit still needs them — see
# ReportProvenance/ValidationSummary) but are curated down to short, concrete
# notes for the two coach-facing surfaces (report.html, pdf.py). Both mappings
# below are keyed by the FIXED codes/ids evidence_pack.py already emits —
# identical for every team, never re-tuned per team.

COACH_LIMITATION_NOTES: dict[str, str] = {
    "neutral_direction": (
        "Some metrics (pace, shot mix, free-throw rate) describe style, not quality — "
        "no direction is inherently good or bad."
    ),
    "unweighted_segment_mean": (
        "Situational splits (clutch, trailing, etc.) are small-sample averages — "
        "read them as directional, not precise."
    ),
    "small_segment_sample": "Some situational evidence rests on a small number of possessions.",
    "fast_break_one_directional": "Transition frequency may be undercounted.",
}

# The verbose analyst-facing form of the same 4 codes, for exact-match removal
# from the caveats render.py already assembled for the full/audit artifact.
_LEGEND_TEXTS: set[str] = set(LIMITATION_LEGEND.values())

# (short label, one-sentence coach reason, the fixed NA.* ids it covers).
# evidence_pack.UNAVAILABLE is a hardcoded, product-wide list — identical for
# all 14 teams — so this grouping is general by construction.
_UNAVAILABLE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Shot location & possession type",
        "No shot-zone, shot-distance, or half-court/transition breakdown beyond what's shown above.",
        ("NA.shot_zone_share", "NA.shot_distance", "NA.possession_type_half_court"),
    ),
    (
        "Player & lineup detail",
        "Team-level only — no player, lineup, or pass-tracking data.",
        ("NA.player_level", "NA.last_passer"),
    ),
    (
        "Video-derived metrics",
        "No shot contest, shot creation, or on-ball pressure data — no video was analyzed.",
        ("NA.video",),
    ),
    (
        "Defensive scheme",
        "No coverage, personnel, or play-calling data — play-by-play doesn't capture it.",
        ("NA.scheme",),
    ),
)


# Data Limits must state each limitation once. Exact-string dedupe is not
# enough: the model writes its own caveat about small clutch samples, and the
# canonical note for ``unweighted_segment_mean`` says the same thing in
# different words, so the coach reads it twice. Each topic below claims a set
# of keywords; the first caveat to touch a topic keeps it and later ones are
# dropped. Model caveats are ordered first, so a report-specific wording wins
# over the generic note it duplicates.
_CAVEAT_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("situational_sample", (
        "clutch", "trailing", "segment", "situational", "small sample",
        "smaller sample", "smaller subset", "small number of possessions",
    )),
    ("style_not_quality", ("style, not quality", "neutral", "inherently good", "shot mix")),
    ("transition_count", ("transition", "fast break")),
    ("correlation_not_cause", ("correlation", "causation", "does not imply", "not imply")),
)

# The four _UNAVAILABLE_GROUPS render as their own bullets directly beneath the
# caveats, so a model caveat that merely restates one of them is read twice.
# Acknowledging an absence is legitimate (the prompt explicitly allows it) —
# it just must not be said in both halves of the same short section.
_UNAVAILABLE_ECHOES: tuple[str, ...] = (
    "video-derived", "no video", "shot contest", "on-ball pressure", "shot creation",
    "defensive scheme", "scheme or coverage", "coverage or", "play-calling",
    "player-level", "player or lineup", "lineup data", "lineup analytics",
    "team-level only", "pass-tracking",
    "shot-zone", "shot zone", "shot location", "shot-distance", "shot distance",
)

# A coach reads this section last and briefly. More than a few lines and it
# stops being read at all, which is worse than a shorter list.
MAX_COACH_CAVEATS = 3


def _echoes_unavailable(text: str) -> bool:
    """True when a caveat only restates one of the fixed unavailable groups,
    which are rendered as their own bullets in the same section."""
    low = text.lower()
    return any(term in low for term in _UNAVAILABLE_ECHOES)


def _caveat_topic(text: str) -> str | None:
    low = text.lower()
    for topic, keywords in _CAVEAT_TOPICS:
        if any(k in low for k in keywords):
            return topic
    return None


def _coach_caveats(rendered_caveats: list[str], key_evidence: list[EvidenceCard]) -> list[str]:
    """Genuinely agent-authored notes, plus a short substitute for whichever
    limitation codes are actually present on evidence THIS report cites —
    never the verbose legend text render.py attaches for the full artifact.

    Deduplicated by topic, not just by exact string, and capped: this section
    exists to change how the numbers above are read, not to enumerate every
    caveat the pipeline could name."""
    model_caveats = [
        c for c in rendered_caveats
        if c not in _LEGEND_TEXTS and not _echoes_unavailable(c)
    ]
    cited_codes = {code for card in key_evidence for code in card.limitations}
    coach_notes = [COACH_LIMITATION_NOTES[code] for code in sorted(cited_codes) if code in COACH_LIMITATION_NOTES]

    seen: set[str] = set()
    seen_topics: set[str] = set()
    out: list[str] = []
    for caveat in (*model_caveats, *coach_notes):
        if caveat in seen:
            continue
        topic = _caveat_topic(caveat)
        if topic is not None and topic in seen_topics:
            continue
        seen.add(caveat)
        if topic is not None:
            seen_topics.add(topic)
        out.append(caveat)
        if len(out) >= MAX_COACH_CAVEATS:
            break
    return out


def _coach_unavailable(raw_items: list[dict[str, Any]]) -> list[UnavailableEvidenceView]:
    """Collapse the fixed, product-wide unavailable-evidence list (7 items,
    each with a paragraph explaining a validation state) into a handful of
    short notes a coach can read in seconds. Anything not covered by a group
    — e.g. a future NA.* id — falls back to its own label/reason verbatim
    rather than being silently dropped."""
    present_ids = {item["id"] for item in raw_items}
    grouped_ids: set[str] = set()
    out: list[UnavailableEvidenceView] = []

    for label, reason, group_ids in _UNAVAILABLE_GROUPS:
        matched = [gid for gid in group_ids if gid in present_ids]
        if not matched:
            continue
        grouped_ids.update(matched)
        out.append(UnavailableEvidenceView(id="+".join(matched), label=label, reason=reason))

    for item in raw_items:
        if item["id"] not in grouped_ids:
            out.append(UnavailableEvidenceView(id=item["id"], label=item["label"], reason=item["reason"]))
    return out


def build_public_report(
    rendered: dict[str, Any],
    *,
    report_id: str,
    generated_at: str,
    backend: str,
    model_name: str | None = None,
    pack_hash: str = "",
) -> PublicReport:
    """Convert ``render.render_report()`` output into the public contract.

    Nothing is recomputed here — every number was already formatted by
    ``render.py`` from the deterministic pack. This is a projection, and the
    fields it drops are dropped on purpose (see the module docstring).
    """
    src = rendered.get("generated_from") or {}
    validation = rendered.get("validation") or {}
    warnings = [
        ValidationNote(rule=w.get("rule", "unknown"), message=w.get("message", ""))
        for w in validation.get("warnings") or []
    ]

    sections_raw = rendered.get("sections") or {}
    sections = ReportSections(
        **{
            key: [_claim_view(c) for c in sections_raw.get(key) or []]
            for key in SECTION_KEYS
        }
    )
    key_evidence = [_evidence_card(e) for e in rendered.get("key_evidence") or []]

    return PublicReport(
        report_id=report_id,
        report_version=REPORT_CONTRACT_VERSION,
        team_id=rendered["team_id"],
        team_name=rendered["team_name"],
        season=src.get("season") or "unknown",
        generated_at=generated_at,
        backend=backend,
        model_name=model_name,
        scope_note=rendered.get("scope_note") or "",
        executive_summary=rendered.get("executive_summary") or "",
        sections=sections,
        recommendations=[
            _recommendation_view(r) for r in rendered.get("recommendations") or []
        ],
        key_evidence=key_evidence,
        caveats=_coach_caveats(list(rendered.get("caveats") or []), key_evidence),
        unavailable_evidence=_coach_unavailable(list(rendered.get("unavailable_evidence") or [])),
        provenance=ReportProvenance(
            pack_id=src.get("pack_id") or "unknown",
            pack_hash=pack_hash,
            source=src.get("source") or "segev",
            season=src.get("season") or "unknown",
            record=src.get("record") or "",
            games_n=src.get("games_n") or 0,
            date_range=src.get("date_range") or "",
            definition_version=src.get("definition_version") or "unknown",
            evidence_version=src.get("definition_version") or "unknown",
            pack_states=list(src.get("pack_states") or []),
        ),
        validation=ValidationSummary(
            ok=bool(validation.get("ok", True)),
            rejects_n=len(validation.get("rejects") or []),
            warnings_n=len(warnings),
            warnings=warnings,
        ),
    )
