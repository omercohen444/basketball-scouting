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


class RecommendationView(BaseModel):
    """One game-plan priority — advice to *our* team."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    priority: int
    directive: str
    rationale: str
    confidence: Literal["high", "moderate", "low"] = "moderate"
    implication_refs: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCard] = Field(default_factory=list)


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


def _recommendation_view(raw: dict[str, Any]) -> RecommendationView:
    return RecommendationView(
        recommendation_id=raw["recommendation_id"],
        priority=raw.get("priority") or 0,
        directive=raw["directive"],
        rationale=raw.get("rationale") or "",
        confidence=raw.get("confidence") or "moderate",
        implication_refs=list(raw.get("implication_refs") or []),
        evidence=[_evidence_card(e) for e in raw.get("evidence") or []],
    )


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
        key_evidence=[_evidence_card(e) for e in rendered.get("key_evidence") or []],
        caveats=list(rendered.get("caveats") or []),
        unavailable_evidence=[
            UnavailableEvidenceView(id=u["id"], label=u["label"], reason=u["reason"])
            for u in rendered.get("unavailable_evidence") or []
        ],
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
