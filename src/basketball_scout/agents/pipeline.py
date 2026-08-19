"""Pipeline orchestration: pack -> three agents -> validate -> render.

The backend is pluggable on purpose. :class:`StubBackend` is deterministic and
makes zero provider calls, so the entire chain — schemas, validation, claim
resolution, rendering — is exercisable offline and in CI. ``crew.py`` supplies
the real CrewAI backend with an identical signature.

Retry policy is exactly one repair attempt per stage: on rejection the findings
are handed back verbatim and the stage is re-run once. A second failure fails the
run loudly rather than emitting a partially-valid report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .schemas import (
    DataSignal,
    EvidencePack,
    KeyToWin,
    ReportClaim,
    ScoutingReport,
    TacticalImplication,
    TacticalOption,
    TacticalOutput,
    TriageOutput,
    ValidationResult,
)
from .render import render_markdown, render_report
from .validation import (
    apply_resolved_confidence,
    apply_resolved_strengths,
    validate_report,
    validate_tactical,
    validate_triage,
)

MAX_REPAIR_ATTEMPTS = 1


class AgentBackend(Protocol):
    """What the pipeline needs from any agent implementation."""

    name: str

    def run_triage(self, pack: EvidencePack, feedback: list[str] | None = None) -> TriageOutput: ...

    def run_tactical(
        self, pack: EvidencePack, triage: TriageOutput, feedback: list[str] | None = None
    ) -> TacticalOutput: ...

    def run_head_scout(
        self,
        pack: EvidencePack,
        triage: TriageOutput,
        tactical: TacticalOutput,
        feedback: list[str] | None = None,
    ) -> ScoutingReport: ...


class PipelineError(RuntimeError):
    """A stage still failed validation after its repair attempt."""


@dataclass
class PipelineResult:
    pack: EvidencePack
    triage: TriageOutput
    tactical: TacticalOutput
    report: ScoutingReport
    validation: ValidationResult
    rendered: dict[str, Any]
    markdown: str
    backend: str
    stage_attempts: dict[str, int] = field(default_factory=dict)

    @property
    def provider_calls(self) -> int:
        return sum(self.stage_attempts.values())


def _run_stage(name: str, call, validate, attempts: dict[str, int]):
    """Run one stage, validating and allowing a single repair attempt."""
    feedback: list[str] | None = None
    last: ValidationResult | None = None

    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        attempts[name] = attempt + 1
        output = call(feedback)
        result = validate(output)
        if result.ok:
            return output, result
        last = result
        feedback = [str(f) for f in result.rejects]

    raise PipelineError(
        f"stage {name!r} still invalid after {MAX_REPAIR_ATTEMPTS + 1} attempts: "
        + "; ".join(f.message for f in (last.rejects if last else []))
    )


def run_pipeline(pack: EvidencePack, backend: AgentBackend) -> PipelineResult:
    attempts: dict[str, int] = {}

    triage, triage_result = _run_stage(
        "triage",
        lambda fb: backend.run_triage(pack, fb),
        lambda out: validate_triage(pack, out),
        attempts,
    )

    tactical, tactical_result = _run_stage(
        "tactical",
        lambda fb: backend.run_tactical(pack, triage, fb),
        lambda out: validate_tactical(pack, triage, out),
        attempts,
    )
    tactical = apply_resolved_strengths(pack, tactical)

    report, report_result = _run_stage(
        "head_scout",
        lambda fb: backend.run_head_scout(pack, triage, tactical, fb),
        lambda out: validate_report(pack, triage, tactical, out),
        attempts,
    )
    report = apply_resolved_confidence(pack, tactical, report)

    validation = triage_result.merged(tactical_result).merged(report_result)
    rendered = render_report(pack, triage, tactical, report, validation)

    return PipelineResult(
        pack=pack,
        triage=triage,
        tactical=tactical,
        report=report,
        validation=validation,
        rendered=rendered,
        markdown=render_markdown(rendered),
        backend=backend.name,
        stage_attempts=attempts,
    )


# ---- deterministic stub backend --------------------------------------------


def _signal_kind(item) -> str:
    if item.win_loss.agent_rankable and item.win_loss.effect_size is not None:
        return "win_loss_differentiator"
    if item.flags.league_extreme:
        return "league_extreme"
    if item.flags.recent_shift:
        return "recent_shift"
    if item.flags.stable_pattern:
        return "stability_note"
    return "profile_shape"


class StubBackend:
    """Deterministic stand-in for the three agents.

    Produces structurally valid, evidence-grounded output with no model and no
    network. Its prose is deliberately flat — the point is to prove the contract,
    the validation and the rendering, not to write good scouting copy.
    """

    name = "stub"

    def __init__(self, *, signals_n: int = 10, implications_n: int = 5, recommendations_n: int = 4):
        self.signals_n = signals_n
        self.implications_n = implications_n
        self.recommendations_n = recommendations_n

    def run_triage(self, pack: EvidencePack, feedback: list[str] | None = None) -> TriageOutput:
        index = pack.index()
        chosen = [cid for cid in pack.screening.candidate_ids if cid in index][: self.signals_n]
        signals = []
        for rank, eid in enumerate(chosen, start=1):
            item = index[eid]
            signals.append(
                DataSignal(
                    signal_id=f"S{rank}",
                    signal_kind=_signal_kind(item),
                    headline=f"{item.metric_label} stands out relative to the rest of the league.",
                    why_kept=(
                        f"Selected because its league position and sample make it one of the more "
                        f"distinctive parts of this team's {item.scope} profile."
                    ),
                    evidence_refs=[eid],
                    priority_rank=rank,
                    caveats=([] if item.reliability_tier == "high"
                             else [f"reliability tier: {item.reliability_tier}"]),
                )
            )
        return TriageOutput(signals=signals)

    def run_tactical(
        self, pack: EvidencePack, triage: TriageOutput, feedback: list[str] | None = None
    ) -> TacticalOutput:
        implications = []
        signals = triage.signals
        # Pair consecutive signals so most implications carry >=2 supports, which
        # is what an "indicated" tendency requires.
        for i in range(min(self.implications_n, max(1, len(signals) - 1))):
            pair = signals[i : i + 2] or signals[i : i + 1]
            supports = [ref for s in pair for ref in s.evidence_refs]
            implications.append(
                TacticalImplication(
                    implication_id=f"T{i + 1}",
                    tendency=(
                        "This team's profile in these areas differs enough from the league norm "
                        "to be worth preparing for."
                    ),
                    proposed_claim_strength="indicated" if len(supports) >= 2 else "established",
                    claim_basis="Consistent direction across the cited deterministic measures.",
                    signal_refs=[s.signal_id for s in pair],
                    supports_refs=supports,
                    limitation_refs=[],
                )
            )
        return TacticalOutput(implications=implications)

    def run_head_scout(
        self,
        pack: EvidencePack,
        triage: TriageOutput,
        tactical: TacticalOutput,
        feedback: list[str] | None = None,
    ) -> ScoutingReport:
        ids = [i.implication_id for i in tactical.implications]

        def claim(text: str, idx: int) -> ReportClaim:
            return ReportClaim(text=text, implication_refs=[ids[idx % len(ids)]])

        # Cycles through `ids` via modulo, so it always reaches recommendations_n
        # regardless of how many implications exist (duplicate citation across
        # recommendations is fine — the stub only needs to be structurally
        # valid, not varied). Alternates 0 and 1 tactics so both code paths
        # (a Key with no mechanically-linked tactic, and one that has exactly
        # one) are exercised offline without any custom test fixtures.
        def _tactics(n: int, ref: str) -> list[TacticalOption]:
            if n % 2:
                return []
            return [
                TacticalOption(
                    tactic_id=f"R{n + 1}T1",
                    method="Assign a specific defender to own this matchup possession by possession.",
                    mechanism="The cited evidence isolates this tendency closely enough to prepare a direct counter.",
                    implication_refs=[ref],
                )
            ]

        recommendations = [
            KeyToWin(
                recommendation_id=f"R{n + 1}",
                priority=n + 1,
                objective=f"Prepare specifically for the tendency described in {ids[n % len(ids)]}.",
                why_it_matters="The supporting deterministic evidence separates this team from the league norm.",
                implication_refs=[ids[n % len(ids)]],
                confidence="moderate",
                tactics=_tactics(n, ids[n % len(ids)]),
            )
            for n in range(self.recommendations_n if ids else 0)
        ]

        return ScoutingReport(
            report_id=f"RPT.{pack.team_id}.stub",
            team_id=pack.team_id,
            team_name=pack.team_name,
            scope_note=(
                f"Deterministic play-by-play evidence across {pack.games_n} games "
                f"({pack.date_range}). No video evidence."
            ),
            executive_summary=(
                "This opponent's statistical profile shows several league-relative distinctions "
                "that shape how a game plan should be built. The sections below separate what the "
                "data establishes from what it merely indicates."
            ),
            offensive_identity=[claim("Their scoring profile has a distinct shape relative to the league.", 0)],
            strengths=[claim("They hold a clear league-relative advantage in the cited areas.", 1)],
            vulnerabilities=[claim("They sit below the league norm in the cited areas.", 2)],
            transition_notes=[claim("Their transition profile differs from the league midpoint.", 3)],
            turnover_notes=[claim("Their turnover profile is a meaningful part of their identity.", 4)],
            recommendations=recommendations,
            caveats=["Generated by the deterministic stub backend; prose is placeholder, evidence is real."],
        )
