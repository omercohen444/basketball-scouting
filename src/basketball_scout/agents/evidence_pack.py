"""Deterministic EvidencePack builder — the agent layer's only source of numbers.

This module performs **no new analytics**. Every value comes from
``stats.evidence.build_evidence``, which itself only extracts an already-computed
per-game value and runs it through the accepted ``league_context`` /
``stability`` / ``winloss`` machinery. What this module adds is purely the
*agent contract*:

* a stable, readable ``evidence_id``
* one central formatter (so ``0.5166`` is rendered ``51.7%`` in exactly one place)
* a deterministic ``reliability_tier``
* **masking** of ``effect_size`` whenever ``is_agent_rankable`` is ``False``
* a pre-ranked ``screening`` shortlist the Triage agent must choose within
* an explicit ``unavailable_evidence`` list

Design note — why every item goes through ``build_evidence``, including the
"profile shape" ones (scoring mix, second chance, transition, assisted): the
per-game profile objects already hang off ``GameEnrichment``, so a one-line
extractor gives those metrics the *same* league-context/stability/recent/W-L
treatment as the ten core metrics, with no second aggregation path to keep in
sync. Aggregation is therefore always "unweighted mean of per-game values"
(``build_evidence``'s default) except for the ten core season metrics, which use
the volume-weighted canonical aggregate — exactly as the stats layer already
decided. ``value_definition`` on the underlying object records which.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import Settings, load_settings
from ..stats.enrichment import build_game_enrichment
from ..stats.evidence import build_evidence, compute_signal_flags
from ..stats.store import load_game
from ..stats.winloss import is_agent_rankable
from .schemas import (
    PACK_STATE_NO_WIN_LOSS,
    RELIABILITY_RANK,
    Direction,
    EvidenceItem,
    EvidencePack,
    FlagsBlock,
    RecentBlock,
    ReliabilityTier,
    Screening,
    StabilityBlock,
    UnavailableEvidence,
    ValidationState,
    WinLossBlock,
)

# Mirrors stats.winloss.AGENT_RANKABLE_MIN_WINS/LOSSES. A team below either
# threshold cannot produce a single rankable W/L signal, so the whole pack
# degrades rather than silently emitting 15 undefined comparisons.
MIN_WINS_FOR_WL = 3
MIN_LOSSES_FOR_WL = 3

SCREENING_TOP_N = 20

# Reliability thresholds — deliberately coarse and documented, not tuned.
LOW_SAMPLE_GAMES = 5
MODERATE_SAMPLE_GAMES = 10
LOW_SAMPLE_POSSESSIONS = 50
HIGH_CV = 0.60
MODERATE_CV = 0.35

LIMITATION_LEGEND: dict[str, str] = {
    "fast_break_one_directional": (
        "The provider fast-break flag is one-directional: a False (or missing) value means only "
        "'the provider did not classify this as a fast break'. It never means half-court, set "
        "offense, or secondary transition."
    ),
    "neutral_direction": (
        "This metric has no inherently good or bad direction. A high or low league percentile "
        "describes style, not quality, and must not be phrased as a strength or weakness."
    ),
    "unweighted_segment_mean": (
        "Segment values are the unweighted mean of per-game segment values, so a game with very "
        "few segment possessions counts as much as a game with many."
    ),
    "small_segment_sample": (
        "This segment carries few possessions per game; treat it as directional, not precise."
    ),
}


# ---- formatting -------------------------------------------------------------

def format_value(value: float | None, unit: str) -> str:
    """The single formatting authority for the whole agent layer.

    Percentages arrive as fractions from ``formulas.py`` (eFG% is ``0.5166``),
    so the ``x100`` lives here and nowhere else."""
    if value is None:
        return "n/a"
    if unit in ("pct", "share"):
        return f"{value * 100:.1f}%"
    if unit == "per100":
        return f"{value:.1f}"
    if unit == "possessions":
        return f"{value:.1f}"
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "count_per_game":
        return f"{value:.1f}"
    return f"{value:.3f}"


def format_rank(rank: int | None, eligible: int) -> str | None:
    if rank is None or eligible <= 1:
        return None
    return f"{rank} of {eligible}"


# ---- metric registry --------------------------------------------------------

Extractor = Callable[[Any, Any], float | None]


@dataclass(frozen=True)
class MetricSpec:
    """One evidence row's full definition. Adding a metric is one entry here."""

    metric_name: str
    label: str
    unit: str
    direction: Direction
    scope: str
    extractor: Extractor
    validation_state: ValidationState = "validated_deterministic"
    limitation_codes: tuple[str, ...] = ()
    canonical: bool = False
    # segment_type/segment_value handed to build_evidence (season items use "season")
    segment_type: str = "season"
    segment_value: str = "season"
    # Coefficient of variation is std/|mean|, so it is only interpretable for a
    # metric on a strictly positive scale. A difference metric (net rating) sits
    # naturally near zero, which inflates CV without bound and would mislabel a
    # perfectly solid season figure as volatile. Declared per metric rather than
    # inferred, so the exclusion is visible.
    cv_applicable: bool = True


def _season(field: str) -> Extractor:
    return lambda s, e: getattr(s.metrics, field, None)


def _segment(seg_type: str, seg_value: str, field: str) -> Extractor:
    def _extract(s, e):
        metrics = e.segment_metrics.get((seg_type, seg_value))
        sample = e.segment_samples.get((seg_type, seg_value), {})
        if metrics is None or sample.get("possessions_n", 0) == 0:
            return None
        return getattr(metrics, field, None)

    return _extract


def _profile(attr: str, field: str) -> Extractor:
    """Read a field off one of ``GameEnrichment``'s per-game profile objects."""

    def _extract(s, e):
        obj = getattr(e, attr, None)
        if obj is None:
            return None
        value = getattr(obj, field, None)
        return float(value) if value is not None else None

    return _extract


# Ten core season metrics — the PROJECT_SPEC set, volume-weighted canonical
# aggregate (the only place use_canonical_aggregate applies).
_CORE: list[MetricSpec] = [
    MetricSpec("offensive_rating", "Offensive Rating", "per100", "higher_is_better", "season", _season("offensive_rating"), canonical=True),
    MetricSpec("defensive_rating", "Defensive Rating", "per100", "lower_is_better", "season", _season("defensive_rating"), canonical=True),
    MetricSpec("net_rating", "Net Rating", "per100", "higher_is_better", "season", _season("net_rating"), canonical=True, cv_applicable=False),
    MetricSpec("pace", "Pace", "possessions", "neutral", "season", _season("pace"), canonical=True, limitation_codes=("neutral_direction",)),
    MetricSpec("efg_pct", "Effective FG%", "pct", "higher_is_better", "season", _season("efg_pct"), canonical=True),
    MetricSpec("tov_pct", "Turnover Rate", "pct", "lower_is_better", "season", _season("tov_pct"), canonical=True),
    MetricSpec("orb_pct", "Offensive Rebound %", "pct", "higher_is_better", "season", _season("orb_pct"), canonical=True),
    MetricSpec("ft_rate", "Free Throw Rate", "ratio", "neutral", "season", _season("ft_rate"), canonical=True, limitation_codes=("neutral_direction",)),
    MetricSpec("fg3a_rate", "3PA Rate", "share", "neutral", "season", _season("fg3a_rate"), canonical=True, limitation_codes=("neutral_direction",)),
    MetricSpec("ast_to_ratio", "Assist/Turnover Ratio", "ratio", "higher_is_better", "season", _season("ast_to_ratio"), canonical=True),
]

# Situational cuts — the same bounded, high-value set the accepted enrichment-v2
# preview already used. Not a Cartesian explosion.
_SEGMENTS: list[MetricSpec] = [
    MetricSpec("efg_pct", "Clutch Effective FG%", "pct", "higher_is_better", "clutch",
               _segment("clutch", "clutch", "efg_pct"), segment_type="clutch", segment_value="clutch",
               limitation_codes=("unweighted_segment_mean",)),
    MetricSpec("tov_pct", "Clutch Turnover Rate", "pct", "lower_is_better", "clutch",
               _segment("clutch", "clutch", "tov_pct"), segment_type="clutch", segment_value="clutch",
               limitation_codes=("unweighted_segment_mean",)),
    MetricSpec("net_rating", "Q4 Net Rating", "per100", "higher_is_better", "Q4",
               _segment("quarter", "Q4", "net_rating"), segment_type="quarter", segment_value="Q4",
               limitation_codes=("unweighted_segment_mean",), cv_applicable=False),
    MetricSpec("efg_pct", "Effective FG% When Trailing 6+", "pct", "higher_is_better", "behind_6_plus",
               _segment("score_state", "behind_6_plus", "efg_pct"), segment_type="score_state",
               segment_value="behind_6_plus", limitation_codes=("unweighted_segment_mean",)),
    MetricSpec("net_rating", "First-Half Net Rating", "per100", "higher_is_better", "1H",
               _segment("half", "1H", "net_rating"), segment_type="half", segment_value="1H",
               limitation_codes=("unweighted_segment_mean",), cv_applicable=False),
]

# Profile-shape metrics — style/identity, read straight off GameEnrichment's
# per-game profile objects.
_PROFILE: list[MetricSpec] = [
    MetricSpec("scoring_share_2pt", "Share of Points from 2PT", "share", "neutral", "season",
               _profile("shot_mix", "scoring_share_2pt"), limitation_codes=("neutral_direction",)),
    MetricSpec("scoring_share_3pt", "Share of Points from 3PT", "share", "neutral", "season",
               _profile("shot_mix", "scoring_share_3pt"), limitation_codes=("neutral_direction",)),
    MetricSpec("scoring_share_ft", "Share of Points from FT", "share", "neutral", "season",
               _profile("shot_mix", "scoring_share_ft"), limitation_codes=("neutral_direction",)),
    MetricSpec("points_off_turnovers", "Points Off Turnovers (per game)", "count_per_game",
               "higher_is_better", "season", _profile("points_off_turnovers", "points_off_turnovers")),
    MetricSpec("second_chance_points", "Second Chance Points (per game)", "count_per_game",
               "higher_is_better", "season", _profile("second_chance", "second_chance_points")),
    MetricSpec("points_per_second_chance_possession", "Points per Second-Chance Possession", "ratio",
               "higher_is_better", "season", _profile("second_chance", "points_per_second_chance_possession")),
    MetricSpec("provider_fast_break_points", "Fast Break Points (per game)", "count_per_game",
               "higher_is_better", "season", _profile("fast_break", "provider_fast_break_points"),
               limitation_codes=("fast_break_one_directional",)),
    MetricSpec("assisted_fgm_pct", "Assisted Share of Made Field Goals", "pct", "neutral", "season",
               _profile("assisted", "assisted_fgm_pct"), limitation_codes=("neutral_direction",)),
    MetricSpec("largest_scoring_run_for", "Largest Scoring Run (per game)", "count_per_game",
               "higher_is_better", "season", _profile("runs", "largest_scoring_run_for")),
    MetricSpec("runs_8_plus_for", "8+ Point Runs Made (per game)", "count_per_game",
               "higher_is_better", "season", _profile("runs", "runs_8_plus_for")),
]

METRIC_SPECS: list[MetricSpec] = [*_CORE, *_SEGMENTS, *_PROFILE]


# What a scout might reasonably expect and this pack deliberately does not have.
# Declaring these suppresses gap-filling far better than silence.
UNAVAILABLE: list[UnavailableEvidence] = [
    UnavailableEvidence(
        evidence_id="NA.shot_zone_share",
        label="Rim / shot-zone share (season)",
        reason=(
            "Coarse shot-zone geometry is validated only at game scope and is still "
            "provisional_deterministic pending fresh blind human labelling. Season-scope "
            "aggregation was deliberately not built for this MVP checkpoint."
        ),
    ),
    UnavailableEvidence(
        evidence_id="NA.shot_distance",
        label="Shot distance",
        reason="Distance carries +/-1m uncertainty (validation_state 'partial') and is not aggregated at season scope.",
        validation_state="partial",
    ),
    UnavailableEvidence(
        evidence_id="NA.possession_type_half_court",
        label="Half-court / set-offense possession type",
        reason=(
            "No half-court classification exists. The provider fast-break flag is one-directional: "
            "its absence never implies half-court or set offense."
        ),
    ),
    UnavailableEvidence(
        evidence_id="NA.last_passer",
        label="Last-passer identity / pass origin",
        reason="Audit found a linked passer for only 38/62 made and 0/78 missed field-goal attempts. Not implemented.",
    ),
    UnavailableEvidence(
        evidence_id="NA.player_level",
        label="Player-level and lineup analytics",
        reason="This MVP is team-level only by locked product decision. No per-player claim is supportable.",
    ),
    UnavailableEvidence(
        evidence_id="NA.video",
        label="Video-derived metrics (shot contest, creation, on-ball pressure)",
        reason="The video layer failed reliability validation and was removed from the MVP. No video evidence exists.",
    ),
    UnavailableEvidence(
        evidence_id="NA.scheme",
        label="Defensive scheme / coverage / play-calling",
        reason=(
            "Play-by-play carries no scheme, coverage, or personnel information. Any claim about "
            "switching, drop coverage, zone, or coaching intent is unsupportable."
        ),
    ),
]


# ---- reliability ------------------------------------------------------------

def reliability_tier(
    *,
    validation_state: str,
    sample_games: int,
    sample_possessions: int | None,
    coefficient_of_variation: float | None,
) -> ReliabilityTier:
    """Deterministic reliability, computed once here so no agent ever judges it.

    A ceiling is imposed by provenance, then sample size and volatility can only
    lower it further."""
    if validation_state == "partial":
        ceiling = "low"
    elif validation_state == "provisional_deterministic":
        ceiling = "moderate"
    else:
        ceiling = "high"

    tier = "high"
    if sample_games < LOW_SAMPLE_GAMES:
        tier = "low"
    elif sample_games < MODERATE_SAMPLE_GAMES:
        tier = "moderate"

    if sample_possessions is not None and sample_possessions < LOW_SAMPLE_POSSESSIONS:
        tier = "low"

    if coefficient_of_variation is not None:
        cv = abs(coefficient_of_variation)
        if cv > HIGH_CV:
            tier = "low"
        elif cv > MODERATE_CV and tier == "high":
            tier = "moderate"

    return min(tier, ceiling, key=lambda t: RELIABILITY_RANK[t])  # type: ignore[return-value]


# ---- league data loading ----------------------------------------------------

@dataclass(frozen=True)
class LeagueData:
    """Everything the pack builder needs, loaded once and reused for all teams."""

    pairs: dict[str, list]
    team_names: dict[str, str]
    season: str

    def record(self, team_id: str) -> tuple[int, int]:
        games = self.pairs.get(team_id, [])
        wins = sum(1 for s, _ in games if s.win)
        return wins, len(games) - wins

    def date_range(self, team_id: str) -> str:
        dates = sorted(s.game_date for s, _ in self.pairs.get(team_id, []))
        return f"{dates[0]} to {dates[-1]}" if dates else "n/a"


def load_league_data(settings: Settings | None = None, *, stats_dir: Path | None = None) -> LeagueData:
    """Rebuild every team's ``(TeamGameStats, GameEnrichment)`` pairs from cache.

    No network calls: reads the already-ingested ``data/processed/stats`` records
    and the cached raw PBP alongside them. Mirrors the loading loop the accepted
    enrichment-v2 sweep already uses.
    """
    settings = settings or load_settings()
    stats_dir = stats_dir or (settings.data_dir / "processed" / "stats")

    pairs: dict[str, list] = defaultdict(list)
    team_names: dict[str, str] = {}
    seasons: set[str] = set()

    for path in sorted(stats_dir.glob("*.json")):
        home_stats, away_stats = load_game(path)
        raw_path = settings.raw_pbp_dir / f"segev_{home_stats.source_game_id}.json"
        if not raw_path.is_file():
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))

        home_e, away_e = build_game_enrichment(
            {"gameInfo": raw["gameInfo"], "actions": raw["actions"]},
            internal_game_id=home_stats.internal_game_id,
            team_id=home_stats.team_id,
            opponent_id=away_stats.team_id,
            team_side="home",
            win=home_stats.win,
            game_date=home_stats.game_date,
            regulation_periods=home_stats.regulation_periods,
            ot_periods=home_stats.ot_periods,
        )
        pairs[home_stats.team_id].append((home_stats, home_e))
        pairs[away_stats.team_id].append((away_stats, away_e))
        team_names[home_stats.team_id] = home_stats.team_name
        team_names[away_stats.team_id] = away_stats.team_name
        seasons.add(home_stats.season)

    return LeagueData(
        pairs=dict(pairs),
        team_names=team_names,
        season=sorted(seasons)[-1] if seasons else "unknown",
    )


# ---- pack construction ------------------------------------------------------

def _sample_possessions(pairs: list, spec: MetricSpec) -> int | None:
    """Total possessions behind a *segment* value. ``None`` for season scope,
    where ``sample_games`` is already the meaningful sample statement."""
    if spec.segment_type == "season":
        return None
    key = (spec.segment_type, spec.segment_value)
    total = 0
    for _stats, enrichment in pairs:
        total += (enrichment.segment_samples.get(key) or {}).get("possessions_n", 0)
    return total


def _build_item(
    spec: MetricSpec,
    team_id: str,
    league_pairs: dict[str, list],
    *,
    wl_available: bool,
) -> EvidenceItem:
    evidence = build_evidence(
        team_id,
        spec.metric_name,
        spec.segment_type,
        spec.segment_value,
        "for",
        spec.extractor,
        league_pairs,
        direction=spec.direction,
        use_canonical_aggregate=spec.canonical,
    )
    flags = compute_signal_flags(evidence)
    ctx = evidence.league_context
    stab = evidence.stability
    wl = evidence.win_loss

    rankable = is_agent_rankable(wl)
    if not wl_available:
        effect_status = "masked_no_wl_evidence"
    elif rankable:
        effect_status = "rankable"
    else:
        effect_status = "not_rankable"

    # Mask the effect unless it genuinely cleared the rankability gate.
    show_effect = rankable and wl_available
    wl_block = WinLossBlock(
        agent_rankable=show_effect,
        effect_status=effect_status,
        win_average_display=format_value(wl.win_average, spec.unit) if show_effect else None,
        loss_average_display=format_value(wl.loss_average, spec.unit) if show_effect else None,
        effect_size=wl.effect_size if show_effect else None,
        favorable_in_wins=wl.favorable_in_wins if show_effect else None,
        sample_wins=wl.sample_wins,
        sample_losses=wl.sample_losses,
        sample_sufficient=wl.sample_sufficient,
    )

    possessions = _sample_possessions(league_pairs[team_id], spec)
    tier = reliability_tier(
        validation_state=spec.validation_state,
        sample_games=stab.games,
        sample_possessions=possessions,
        coefficient_of_variation=stab.coefficient_of_variation if spec.cv_applicable else None,
    )

    limitation_codes = list(spec.limitation_codes)
    if possessions is not None and possessions < LOW_SAMPLE_POSSESSIONS:
        limitation_codes.append("small_segment_sample")

    return EvidenceItem(
        evidence_id=f"EV.{spec.scope}.{spec.metric_name}",
        metric_name=spec.metric_name,
        metric_label=spec.label,
        scope=spec.scope,
        value=evidence.value,
        display_value=format_value(evidence.value, spec.unit),
        typical_game_value=format_value(stab.mean, spec.unit) if stab.mean is not None else None,
        unit=spec.unit,
        direction=spec.direction,
        league_rank=ctx.rank if ctx else None,
        league_percentile=ctx.percentile if ctx else None,
        eligible_teams=ctx.eligible_teams if ctx else 0,
        league_mean_display=format_value(ctx.league_mean, spec.unit) if ctx else None,
        sample_games=stab.games,
        sample_possessions=possessions,
        validation_state=spec.validation_state,
        reliability_tier=tier,
        stability=StabilityBlock(
            games=stab.games,
            std=stab.std,
            coefficient_of_variation=stab.coefficient_of_variation,
            min_display=format_value(stab.min, spec.unit) if stab.min is not None else None,
            max_display=format_value(stab.max, spec.unit) if stab.max is not None else None,
            applicable=stab.games >= 2,
        ),
        win_loss=wl_block,
        recent=RecentBlock(
            last5_minus_season=evidence.recent.last5_minus_season,
            last10_minus_season=evidence.recent.last10_minus_season,
        ),
        flags=FlagsBlock(
            league_extreme=flags.league_extreme,
            win_loss_signal=flags.win_loss_signal if wl_available else None,
            recent_shift=flags.recent_shift,
            stable_pattern=flags.stable_pattern,
        ),
        limitation_codes=limitation_codes,
    )


def _extremeness(item: EvidenceItem) -> float:
    """Distance from the middle of the league, 0..50. Used only for ordering."""
    if item.league_percentile is None:
        return -1.0
    return abs(item.league_percentile - 50.0)


def build_screening(items: list[EvidenceItem], *, top_n: int = SCREENING_TOP_N) -> Screening:
    """Deterministic pre-ranking — the candidate pool the Triage agent may pick from.

    Ordering is by the same signals ``rank_actionable_signals``/``compute_signal_flags``
    already use, then by ``evidence_id`` so the result is stable across runs.
    """
    by_effect = sorted(
        [i for i in items if i.win_loss.effect_size is not None],
        key=lambda i: (-abs(i.win_loss.effect_size or 0.0), i.evidence_id),
    )
    by_extreme = sorted(
        [i for i in items if i.flags.league_extreme],
        key=lambda i: (-_extremeness(i), i.evidence_id),
    )
    by_recent = sorted(
        [i for i in items if i.flags.recent_shift],
        key=lambda i: (-abs(i.recent.last5_minus_season or 0.0), i.evidence_id),
    )

    # Interleave the three lists so no single criterion crowds out the others,
    # then top up with the most league-extreme remaining items.
    candidates: list[str] = []
    for source in (by_effect, by_extreme, by_recent):
        for item in source[:8]:
            if item.evidence_id not in candidates:
                candidates.append(item.evidence_id)

    if len(candidates) < top_n:
        filler = sorted(items, key=lambda i: (-_extremeness(i), i.evidence_id))
        for item in filler:
            if len(candidates) >= top_n:
                break
            if item.evidence_id not in candidates:
                candidates.append(item.evidence_id)

    return Screening(
        candidate_ids=candidates[:top_n],
        top_league_extremes=[i.evidence_id for i in by_extreme[:5]],
        top_win_loss_effects=[i.evidence_id for i in by_effect[:5]],
        top_recent_shifts=[i.evidence_id for i in by_recent[:5]],
    )


def build_evidence_pack(team_id: str, league: LeagueData) -> EvidencePack:
    """Assemble one team's complete, agent-ready evidence pack."""
    if team_id not in league.pairs:
        raise KeyError(f"unknown team_id {team_id!r}; known: {sorted(league.pairs)}")

    wins, losses = league.record(team_id)
    wl_available = wins >= MIN_WINS_FOR_WL and losses >= MIN_LOSSES_FOR_WL

    items = [
        _build_item(spec, team_id, league.pairs, wl_available=wl_available)
        for spec in METRIC_SPECS
    ]

    pack_states: list[str] = []
    if not wl_available:
        pack_states.append(PACK_STATE_NO_WIN_LOSS)

    used_codes = {code for item in items for code in item.limitation_codes}

    return EvidencePack(
        pack_id=f"{team_id}|{league.season}|agents-v1",
        team_id=team_id,
        team_name=league.team_names.get(team_id, team_id),
        season=league.season,
        games_n=wins + losses,
        wins=wins,
        losses=losses,
        date_range=league.date_range(team_id),
        evidence=items,
        screening=build_screening(items),
        unavailable_evidence=list(UNAVAILABLE),
        pack_states=pack_states,
        limitation_legend={c: LIMITATION_LEGEND[c] for c in sorted(used_codes) if c in LIMITATION_LEGEND},
    )
