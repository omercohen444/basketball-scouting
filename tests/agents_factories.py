"""Synthetic builders for agent-layer tests.

Shared rather than per-file (a deliberate departure from the stats tests' local
factories) because five test modules need the same EvidenceItem/EvidencePack
shape, and duplicating a 40-field constructor five times would guarantee drift.

Everything here is synthetic — no cached games, no fixtures, no network — so the
agent-layer suite runs on a machine with no data directory and no credentials.
"""

from __future__ import annotations

from basketball_scout.agents.schemas import (
    DataSignal,
    EvidenceItem,
    EvidencePack,
    FlagsBlock,
    KeyToWin,
    RecentBlock,
    ReportClaim,
    ScoutingReport,
    Screening,
    StabilityBlock,
    TacticalImplication,
    TacticalOption,
    TacticalOutput,
    TriageOutput,
    UnavailableEvidence,
    WinLossBlock,
)


def make_item(
    evidence_id: str = "EV.season.efg_pct",
    *,
    metric_name: str | None = None,
    validation_state: str = "validated_deterministic",
    reliability_tier: str = "high",
    effect_size: float | None = 1.2,
    agent_rankable: bool = True,
    league_extreme: bool | None = True,
    league_percentile: float | None = 84.6,
    sample_games: int = 26,
    sample_possessions: int | None = None,
    sample_sufficient: bool = True,
    direction: str = "higher_is_better",
    limitation_codes: list[str] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        metric_name=metric_name or evidence_id.split(".")[-1],
        metric_label=evidence_id.split(".")[-1].replace("_", " ").title(),
        scope=evidence_id.split(".")[1],
        value=0.52,
        display_value="52.0%",
        typical_game_value="51.5%",
        unit="pct",
        direction=direction,  # type: ignore[arg-type]
        league_rank=3,
        league_percentile=league_percentile,
        eligible_teams=14,
        league_mean_display="49.0%",
        sample_games=sample_games,
        sample_possessions=sample_possessions,
        validation_state=validation_state,  # type: ignore[arg-type]
        reliability_tier=reliability_tier,  # type: ignore[arg-type]
        stability=StabilityBlock(games=sample_games, std=0.04, coefficient_of_variation=0.08,
                                 min_display="44.0%", max_display="61.0%", applicable=True),
        win_loss=WinLossBlock(
            agent_rankable=agent_rankable,
            effect_status="rankable" if agent_rankable else "not_rankable",
            win_average_display="55.0%" if agent_rankable else None,
            loss_average_display="47.0%" if agent_rankable else None,
            effect_size=effect_size if agent_rankable else None,
            favorable_in_wins=True if agent_rankable else None,
            sample_wins=18, sample_losses=8, sample_sufficient=sample_sufficient,
        ),
        recent=RecentBlock(last5_minus_season=0.01, last10_minus_season=0.005),
        flags=FlagsBlock(league_extreme=league_extreme, win_loss_signal=agent_rankable,
                         recent_shift=False, stable_pattern=True),
        limitation_codes=limitation_codes or [],
    )


def make_pack(
    *,
    items: list[EvidenceItem] | None = None,
    pack_states: list[str] | None = None,
    team_id: str = "segev:4",
    wins: int = 18,
    losses: int = 8,
    unavailable: list[UnavailableEvidence] | None = None,
) -> EvidencePack:
    items = items if items is not None else [
        make_item("EV.season.efg_pct"),
        make_item("EV.season.tov_pct", direction="lower_is_better"),
        make_item("EV.season.orb_pct"),
        make_item("EV.clutch.efg_pct"),
    ]
    return EvidencePack(
        pack_id=f"{team_id}|2025-26|agents-v1",
        team_id=team_id,
        team_name="HAPOEL JERUSALEM",
        season="2025-26",
        games_n=wins + losses,
        wins=wins,
        losses=losses,
        date_range="2025-10-12 to 2026-05-27",
        evidence=items,
        screening=Screening(candidate_ids=[i.evidence_id for i in items]),
        unavailable_evidence=unavailable if unavailable is not None else [
            UnavailableEvidence(evidence_id="NA.video", label="Video", reason="removed from MVP"),
        ],
        pack_states=pack_states or [],
    )


def make_triage(pack: EvidencePack, n: int = 8) -> TriageOutput:
    ids = pack.screening.candidate_ids
    return TriageOutput(signals=[
        DataSignal(
            signal_id=f"S{i + 1}",
            signal_kind="league_extreme",
            headline="A distinctive part of their profile.",
            why_kept="League position and sample support it.",
            evidence_refs=[ids[i % len(ids)]],
            priority_rank=i + 1,
        )
        for i in range(n)
    ])


def make_tactical(triage: TriageOutput, *, supports_per: int = 2) -> TacticalOutput:
    signals = triage.signals
    implications = []
    for i in range(max(1, len(signals) // supports_per)):
        pair = signals[i * supports_per : (i + 1) * supports_per] or signals[:1]
        implications.append(
            TacticalImplication(
                implication_id=f"T{i + 1}",
                tendency="They lean on this area more than the league norm.",
                proposed_claim_strength="indicated",
                claim_basis="Consistent direction across cited measures.",
                signal_refs=[s.signal_id for s in pair],
                supports_refs=[r for s in pair for r in s.evidence_refs],
            )
        )
    return TacticalOutput(implications=implications)


def make_tactic(
    tactic_id: str,
    implication_refs: list[str],
    *,
    method: str = "Assign a specific defender to own this matchup possession by possession.",
    mechanism: str = "The cited evidence isolates this tendency closely enough to prepare a direct counter.",
) -> TacticalOption:
    return TacticalOption(
        tactic_id=tactic_id, method=method, mechanism=mechanism, implication_refs=implication_refs
    )


def make_report(tactical: TacticalOutput, *, recommendations: int = 4, summary: str = "A synthesis without figures.") -> ScoutingReport:
    ids = [i.implication_id for i in tactical.implications]
    return ScoutingReport(
        report_id="RPT.test",
        team_id="segev:4",
        team_name="HAPOEL JERUSALEM",
        scope_note="Deterministic play-by-play only.",
        executive_summary=summary,
        offensive_identity=[ReportClaim(text="Their shape is distinctive.", implication_refs=[ids[0]])],
        strengths=[ReportClaim(text="They are strong here.", implication_refs=[ids[0]])],
        vulnerabilities=[ReportClaim(text="They are exposed here.", implication_refs=[ids[-1]])],
        transition_notes=[],
        turnover_notes=[],
        recommendations=[
            KeyToWin(
                recommendation_id=f"R{n + 1}", priority=n + 1,
                objective="Prepare for the cited tendency.",
                why_it_matters="Supported by the cited deterministic evidence.",
                implication_refs=[ids[n % len(ids)]],
                confidence="moderate",
            )
            for n in range(recommendations)
        ],
    )
