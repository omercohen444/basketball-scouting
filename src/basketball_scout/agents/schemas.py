"""Typed contracts for the agent layer.

Pydantic v2 throughout this package (the ``stats`` package stays on frozen
dataclasses) — CrewAI's ``output_pydantic`` needs Pydantic models, and having
one convention per package keeps the seam obvious.

Two families:

* **Deterministic input** — :class:`EvidenceItem` / :class:`EvidencePack`, built
  by ``evidence_pack.py`` before any agent runs. This is the single source of
  every number in the final report.
* **Agent output** — :class:`DataSignal`, :class:`TacticalImplication`,
  :class:`KeyToWin`, :class:`ScoutingReport`. Deliberately small, and
  deliberately *number-free*: agents carry ``evidence_refs``, and ``render.py``
  attaches the canonical values afterwards.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- shared vocabularies ---------------------------------------------------

# Mirrors stats.scouting_features.ValidationState exactly — kept as a separate
# Literal rather than imported so the agent contract does not silently change
# shape if the stats taxonomy is extended for an unrelated reason.
ValidationState = Literal[
    "provider_fact",
    "validated_deterministic",
    "provisional_deterministic",
    "partial",
    "deferred",
]

# Ordering matters: index = strength. Used by validation.py's provenance ladder.
PROVENANCE_RANK: dict[str, int] = {
    "deferred": 0,
    "partial": 1,
    "provisional_deterministic": 2,
    "validated_deterministic": 3,
    "provider_fact": 4,
}

ReliabilityTier = Literal["high", "moderate", "low"]
RELIABILITY_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2}

ClaimStrength = Literal["established", "indicated", "speculative"]
CLAIM_RANK: dict[str, int] = {"speculative": 0, "indicated": 1, "established": 2}

Confidence = Literal["high", "moderate", "low"]
CONFIDENCE_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2}

Direction = Literal["higher_is_better", "lower_is_better", "neutral"]

# Pack-level states a consumer must branch on.
PACK_STATE_NO_WIN_LOSS = "no_win_loss_evidence"


# ---- deterministic evidence -------------------------------------------------


class StabilityBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    games: int
    std: float | None = None
    coefficient_of_variation: float | None = None
    min_display: str | None = None
    max_display: str | None = None
    applicable: bool = False


class WinLossBlock(BaseModel):
    """W/L comparison. ``effect_size`` is ``None`` whenever ``agent_rankable``
    is ``False`` — the raw number is deliberately *masked*, not merely flagged,
    because an unmasked effect that failed the rankability gate will otherwise
    be quoted downstream (observed on ``segev:2``: 13 of 15 items carried a
    numeric effect while the flag was ``None``)."""

    model_config = ConfigDict(extra="forbid")

    agent_rankable: bool
    effect_status: Literal["rankable", "not_rankable", "masked_no_wl_evidence"]
    win_average_display: str | None = None
    loss_average_display: str | None = None
    effect_size: float | None = None
    favorable_in_wins: bool | None = None
    sample_wins: int = 0
    sample_losses: int = 0
    sample_sufficient: bool = False


class RecentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last5_minus_season: float | None = None
    last10_minus_season: float | None = None


class FlagsBlock(BaseModel):
    """Screening heuristics from ``stats.evidence.compute_signal_flags``.

    ``None`` means *undefined*, never ``False`` — the distinction is load-bearing
    (see that module's HEURISTIC_DISCLAIMER)."""

    model_config = ConfigDict(extra="forbid")

    league_extreme: bool | None = None
    win_loss_signal: bool | None = None
    recent_shift: bool | None = None
    stable_pattern: bool | None = None


class EvidenceItem(BaseModel):
    """One deterministic fact, fully self-describing.

    ``display_value`` is the canonical, pre-formatted headline figure — the only
    numeric form any consumer should ever print. ``typical_game_value`` is the
    *unweighted per-game mean* and answers a different question; the two are not
    interchangeable and must never both be called "the average" (see
    ``stats.evidence.EvidenceObject``'s class docstring)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    metric_name: str
    metric_label: str
    scope: str
    perspective: str = "for"

    value: float | None = None
    display_value: str
    typical_game_value: str | None = None
    unit: str
    direction: Direction

    league_rank: int | None = None
    league_percentile: float | None = None
    eligible_teams: int = 0
    league_mean_display: str | None = None

    sample_games: int = 0
    sample_possessions: int | None = None

    validation_state: ValidationState
    reliability_tier: ReliabilityTier

    stability: StabilityBlock
    win_loss: WinLossBlock
    recent: RecentBlock
    flags: FlagsBlock

    limitation_codes: list[str] = Field(default_factory=list)


class UnavailableEvidence(BaseModel):
    """Something a scout would reasonably expect, declared explicitly absent.

    Listing these suppresses gap-filling from world knowledge far better than
    silence does — and gives claims a legitimate way to *acknowledge* a limit
    (via ``limitation_refs``) without citing it as support."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    label: str
    reason: str
    validation_state: ValidationState = "deferred"


class Screening(BaseModel):
    """Deterministic pre-ranking. The Evidence Triage agent may only choose
    within ``candidate_ids`` — it cannot introduce anything Python did not
    already select, so it structurally cannot miss a top signal."""

    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(default_factory=list)
    top_league_extremes: list[str] = Field(default_factory=list)
    top_win_loss_effects: list[str] = Field(default_factory=list)
    top_recent_shifts: list[str] = Field(default_factory=list)


class EvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    team_id: str
    team_name: str
    season: str
    games_n: int
    wins: int
    losses: int
    date_range: str
    definition_version: str = "agents-v1"
    source: str = "segev"

    evidence: list[EvidenceItem] = Field(default_factory=list)
    screening: Screening = Field(default_factory=Screening)
    unavailable_evidence: list[UnavailableEvidence] = Field(default_factory=list)
    pack_states: list[str] = Field(default_factory=list)
    limitation_legend: dict[str, str] = Field(default_factory=dict)

    # -- lookup helpers (not serialized state, just convenience) --------------

    def index(self) -> dict[str, EvidenceItem]:
        return {item.evidence_id: item for item in self.evidence}

    def unavailable_index(self) -> dict[str, UnavailableEvidence]:
        return {u.evidence_id: u for u in self.unavailable_evidence}

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return self.index().get(evidence_id)

    @property
    def has_win_loss_evidence(self) -> bool:
        return PACK_STATE_NO_WIN_LOSS not in self.pack_states

    def metric_names(self) -> set[str]:
        return {item.metric_name for item in self.evidence}


# ---- agent outputs ----------------------------------------------------------


SignalKind = Literal[
    "league_extreme",
    "win_loss_differentiator",
    "recent_shift",
    "profile_shape",
    "stability_note",
]


class DataSignal(BaseModel):
    """Evidence Triage output — one kept candidate, with a reason."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    signal_kind: SignalKind
    headline: str
    why_kept: str
    evidence_refs: list[str] = Field(min_length=1)
    priority_rank: int
    caveats: list[str] = Field(default_factory=list)


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals: list[DataSignal] = Field(default_factory=list)


class TacticalImplication(BaseModel):
    """Tactical Scout output.

    ``proposed_claim_strength`` is exactly that — a proposal. Python recomputes
    the final strength and may only lower it (``validation.resolve_claim_strength``)."""

    model_config = ConfigDict(extra="forbid")

    implication_id: str
    tendency: str
    proposed_claim_strength: ClaimStrength
    claim_basis: str
    signal_refs: list[str] = Field(min_length=1)
    supports_refs: list[str] = Field(min_length=1)
    limitation_refs: list[str] = Field(default_factory=list)
    counter_evidence_refs: list[str] = Field(default_factory=list)
    scope_caveat: str | None = None

    # Filled in by validation.resolve_claim_strength(); never model-authored.
    resolved_claim_strength: ClaimStrength | None = None
    downgrade_reason: str | None = None


class TacticalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implications: list[TacticalImplication] = Field(default_factory=list)


class TacticalOption(BaseModel):
    """One optional, specific method for achieving a :class:`KeyToWin`'s
    objective. 0-2 per key — a tactic requires a genuine mechanical link from
    the cited evidence to THIS method over the many others that could plausibly
    address the same objective, and most objectives do not have one.

    ``mechanism`` is the load-bearing field: it must explain *why this specific
    method* follows from the evidence, not just restate that the objective
    matters. ``implication_refs`` is structurally constrained by
    ``validation.py`` to be a subset of the parent key's own refs — a tactic
    can never smuggle in evidence the objective itself does not already rest
    on. Scheme vocabulary is permitted in ``method`` (advice to us), not in
    ``mechanism`` (still explaining something about them)."""

    model_config = ConfigDict(extra="forbid")

    tactic_id: str
    method: str
    mechanism: str
    implication_refs: list[str] = Field(min_length=1)


class KeyToWin(BaseModel):
    """One evidence-supported game objective — what the report used to call a
    "recommendation". Deliberately three parts, and they must not blur:

    * ``objective`` — what to accomplish. Advice to *our* team, so scheme
      vocabulary is permitted here, same as the old ``directive``.
    * ``why_it_matters`` — the measured, correlational evidence behind it. A
      claim about *them*; scheme vocabulary is not permitted.
    * ``tactics`` — 0-2 optional, specific methods for achieving the
      objective. Most objectives will have none, because a single correct
      method rarely follows uniquely from a correlation — see
      :class:`TacticalOption`.

    ``confidence`` is exactly that — a proposal. Python recomputes the final
    confidence and may only lower it (``validation.resolve_recommendation_
    confidence``), the same discipline already applied to claim strength."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    priority: int
    objective: str
    why_it_matters: str
    implication_refs: list[str] = Field(min_length=1)
    confidence: Confidence
    tactics: list[TacticalOption] = Field(default_factory=list, max_length=2)

    # Filled in by validation.resolve_recommendation_confidence(); never
    # model-authored. render.py displays this, never the raw proposal.
    resolved_confidence: Confidence | None = None
    confidence_downgrade_reason: str | None = None


class ReportClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    implication_refs: list[str] = Field(min_length=1)


class ScoutingReport(BaseModel):
    """Head Scout output.

    Note what is *absent*: there is no ``key_evidence`` field for the model to
    author. It is computed in ``render.py`` by expanding ``implication_refs``,
    which is what makes "introduces no new evidence" true by construction rather
    than a rule to be policed."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    team_id: str
    team_name: str
    scope_note: str
    executive_summary: str
    offensive_identity: list[ReportClaim] = Field(default_factory=list)
    strengths: list[ReportClaim] = Field(default_factory=list)
    vulnerabilities: list[ReportClaim] = Field(default_factory=list)
    transition_notes: list[ReportClaim] = Field(default_factory=list)
    turnover_notes: list[ReportClaim] = Field(default_factory=list)
    recommendations: list[KeyToWin] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    def all_claims(self) -> list[ReportClaim]:
        return [
            *self.offensive_identity,
            *self.strengths,
            *self.vulnerabilities,
            *self.transition_notes,
            *self.turnover_notes,
        ]


# ---- validation results -----------------------------------------------------

Severity = Literal["reject", "warning"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    severity: Severity
    message: str
    where: str | None = None

    def __str__(self) -> str:  # pragma: no cover - debug convenience
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.rule} ({self.severity}){loc}: {self.message}"


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)

    @property
    def rejects(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "reject"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.rejects

    def messages(self) -> list[str]:
        return [str(f) for f in self.findings]

    def merged(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(findings=[*self.findings, *other.findings])


def to_jsonable(model: BaseModel) -> dict[str, Any]:
    """Uniform serialization entry point, so callers never mix ``.dict()`` and
    ``.model_dump()`` conventions across this package."""
    return model.model_dump(mode="json")
