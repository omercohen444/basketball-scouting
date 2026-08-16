"""Deterministic Scouting Feature Pack — the canonical product-facing contract.

Consolidation, not new analytics. Every field here is either a direct
provider fact or an already-validated deterministic derivation from
``geometry.py``, ``fastbreak.py``, ``possession.py`` or ``scoring_sources.py``
— reused, never recomputed with a second parallel implementation. See
CLAUDE.md and the 2026-08-16 "Deterministic Scouting Feature Pack" work
package for the exact scope boundary.

Two levels, kept as separate object families per the existing repo
convention (raw events vs. aggregate summaries are never merged into one
object — see ``models.py``'s ``TeamGameComponents``/``TeamGameStats`` split):

* **Event/possession facts** — :class:`DeterministicShotFact` (one per FGA)
  and :class:`DeterministicPossessionFact` (one per possession). Built by
  :func:`build_shot_facts` / :func:`build_possession_facts`, walking the same
  raw action stream ``possession.py`` already validated — never a second,
  divergent possession/shot-boundary algorithm.

* **Team/game/season aggregations** — :class:`TeamScoutingSummary`, which
  *references* the already-existing ``FastBreakProfile``, ``AssistedProfile``,
  ``ShotScoringMix``, ``SecondChanceProfile`` and ``PointsOffTurnoversProfile``
  objects a caller already built via ``enrichment.build_game_enrichment`` (or
  season-level equivalents), plus exactly two genuinely new-but-trivial
  aggregations this pack needed and nothing else already computed:
  :class:`ShotZoneDistribution` and :class:`TransitionShotFacts` — both plain
  groupby/count/rate arithmetic over already-computed shot facts, not new
  basketball research.

Every field carries a :class:`MetricProvenance` so a downstream agent/report
can always tell provider fact vs. deterministic Python derivation vs. (later,
elsewhere) a video observation — never collapsed into one unlabeled source
string. See ``VALIDATION STATE TAXONOMY`` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..pbp.geometry import ShotGeometry, build_shot_geometry
from .fastbreak import FastBreakShotEvent, build_fastbreak_events, classify as classify_fast_break
from .formulas import effective_fg_pct, three_point_rate
from .possession import Possession, find_and1_shot_ids, parse_clock_mmss
from .scoring_sources import (
    AssistedProfile,
    FastBreakProfile,
    PointsOffTurnoversProfile,
    SecondChanceProfile,
    ShotScoringMix,
)

_TEAM_SIDE = {1: "home", 2: "away"}
_OTHER_SIDE = {"home": "away", "away": "home"}

# ---- Validation state taxonomy (brief §16) ---------------------------------
#
# Distinct from a generic "confidence score" — these are the specific,
# already-decided states this project's metrics are actually in, not a
# speculative scale. A downstream consumer branches on these, not on prose.
ValidationState = Literal[
    "provider_fact",              # verbatim source field, no derivation at all
    "validated_deterministic",    # Python derivation, cross-game validated, no open gate
    "provisional_deterministic",  # Python derivation, structural gates pass, fresh
                                   # held-out validation still pending (CP2.4 hardening)
    "partial",                    # some checks pass, promotion held pending a
                                   # specific, named open question
    "deferred",                   # intentionally not implemented for the MVP
]


@dataclass(frozen=True)
class MetricProvenance:
    """Traceability bundle attached to every non-trivial field in this pack.

    ``sample_n`` here means "how many events/games this derivation method was
    validated against" (a property of the *method*), not a per-instance
    count — e.g. every ``coarse_shot_zone`` fact carries the same
    ``sample_n=1104`` because that is the size of the CP2.4 validation
    sample the geometry rule was checked against, not "how many shots this
    one game has."
    """

    source: str
    validation_state: ValidationState
    derivation_version: str
    sample_n: int | None = None
    denominator: int | None = None
    coverage: float | None = None
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "validation_state": self.validation_state,
            "derivation_version": self.derivation_version,
            "sample_n": self.sample_n,
            "denominator": self.denominator,
            "coverage": self.coverage,
            "limitations": list(self.limitations),
        }


# Shared provenance constants — asserted once, reused everywhere, so the
# stated validation state for e.g. "official 2PT/3PT" can never drift to a
# different value at a different call site.
PROVENANCE_PROVIDER_SCORING = MetricProvenance(
    source="segev_provider", validation_state="provider_fact", derivation_version="stats-v1",
)
PROVENANCE_FAST_BREAK = MetricProvenance(
    source="segev_provider_flag", validation_state="validated_deterministic",
    derivation_version="cp2.4", sample_n=1594,
    limitations=(
        "fast_break=False (or missing) means only 'provider did not classify this as a "
        "fast break' — never half_court, defense_set, or secondary_transition (deferred).",
    ),
)
PROVENANCE_SHOT_ZONE = MetricProvenance(
    source="pbp_geometry", validation_state="provisional_deterministic",
    derivation_version="cp2.4-hardening", sample_n=1104,
    limitations=(
        "seed-211 coarse-zone agreement 20/20 after grid-tolerance hardening, but seed-211 "
        "both diagnosed and confirmed that fix — a tuned diagnostic set, not unbiased "
        "held-out validation; fresh blind human-labeled validation is still required for "
        "final KEEP.",
        "RA-vs-Paint fine boundary intentionally deferred post-MVP.",
    ),
)
PROVENANCE_DISTANCE = MetricProvenance(
    source="pbp_geometry", validation_state="partial",
    derivation_version="cp2.4", sample_n=1104,
    limitations=(
        "±1m distance uncertainty; centimeter precision is never claimed.",
        "Distance-eligibility 'uncertain' band (2.5-3.5m for 2PT) is by design, not a gap.",
    ),
)
PROVENANCE_ASSIST_OFFICIAL = MetricProvenance(
    source="segev_provider", validation_state="provider_fact", derivation_version="stats-v1",
)
PROVENANCE_LAST_PASSER_UNAVAILABLE = MetricProvenance(
    source="n/a", validation_state="deferred", derivation_version="n/a",
    limitations=(
        "Generic last-passer identity is not reliably available: game-136 audit found a "
        "linked passer for only 38/62 made FGA and 0/78 missed/blocked FGA. Not "
        "implemented here — official assist (provider fact) is exposed instead.",
    ),
)
PROVENANCE_POSSESSION_ENGINE = MetricProvenance(
    source="pbp_possession_engine", validation_state="validated_deterministic",
    derivation_version="stats-v1",
)
PROVENANCE_ZONE_DISTRIBUTION_AGG = MetricProvenance(
    source="pbp_geometry_aggregate", validation_state="provisional_deterministic",
    derivation_version="cp2.4-hardening",
    limitations=("Inherits coarse_shot_zone's provisional state — see PROVENANCE_SHOT_ZONE.",),
)
PROVENANCE_TRANSITION_FROM_SHOTS = MetricProvenance(
    source="segev_provider_flag_aggregate", validation_state="validated_deterministic",
    derivation_version="cp2.4",
    limitations=("Inherits fast_break's provider-flag limitation — see PROVENANCE_FAST_BREAK.",),
)


def team_id_map_from_game_info(game_info: dict[str, Any], *, source_provider: str = "segev") -> dict[str, str]:
    """``{"home": "segev:2", "away": "segev:4"}`` — the exact
    ``f"{source_provider}:{team_id}"`` convention ``engine.py`` already uses,
    reused here (not reinvented) so this pack's ``team_id`` values join
    directly with ``TeamGameStats.team_id`` with no translation step.
    """
    home = game_info.get("homeTeam") or {}
    away = game_info.get("awayTeam") or {}
    return {
        "home": f"{source_provider}:{home.get('id')}",
        "away": f"{source_provider}:{away.get('id')}",
    }


# ---- Event-level: shot facts -----------------------------------------------

@dataclass(frozen=True)
class ShotFactProvenance:
    scoring: MetricProvenance
    shot_zone: MetricProvenance
    distance: MetricProvenance
    fast_break: MetricProvenance
    assist: MetricProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "scoring": self.scoring.to_dict(),
            "shot_zone": self.shot_zone.to_dict(),
            "distance": self.distance.to_dict(),
            "fast_break": self.fast_break.to_dict(),
            "assist": self.assist.to_dict(),
        }


@dataclass(frozen=True)
class DeterministicShotFact:
    """One field-goal attempt, fully deterministic, no video dependency."""

    # identity
    game_id: str
    action_id: int
    event_id: str  # f"{game_id}:{action_id}" — stable join key
    team_id: str
    opponent_id: str
    player_id: int | None
    quarter: int
    game_clock: str | None  # raw "MM:SS"
    game_clock_s: float | None

    # shot
    is_field_goal_attempt: bool
    made: bool
    missed: bool
    blocked: bool
    official_points: int  # 2 or 3 — the attempt's family, authoritative
    shot_type: str | None
    and_one: bool
    official_assist: bool

    # location (geometry.py — coordinates never override official_points)
    coarse_shot_zone: str | None
    rim_attempt: bool
    shot_distance_m: float | None
    distance_uncertainty_m: float | None
    distance_eligibility: str

    # transition
    fast_break: bool

    provenance: ShotFactProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id, "action_id": self.action_id, "event_id": self.event_id,
            "team_id": self.team_id, "opponent_id": self.opponent_id, "player_id": self.player_id,
            "quarter": self.quarter, "game_clock": self.game_clock, "game_clock_s": self.game_clock_s,
            "is_field_goal_attempt": self.is_field_goal_attempt,
            "made": self.made, "missed": self.missed, "blocked": self.blocked,
            "official_points": self.official_points, "shot_type": self.shot_type,
            "and_one": self.and_one, "official_assist": self.official_assist,
            "coarse_shot_zone": self.coarse_shot_zone, "rim_attempt": self.rim_attempt,
            "shot_distance_m": self.shot_distance_m, "distance_uncertainty_m": self.distance_uncertainty_m,
            "distance_eligibility": self.distance_eligibility,
            "fast_break": self.fast_break,
            "provenance": self.provenance.to_dict(),
        }


def _assisted_shot_action_ids(actions: list[dict[str, Any]]) -> set[int]:
    """Shot action ids with a linked ``assist`` action (``parentActionId``).

    Independently derived here (a five-line ``parentActionId`` scan) rather
    than importing ``pbp/canonical.py``'s equivalent helper — that module is
    the video pipeline's own contract (out of bounds for this track per
    CLAUDE.md); the logic is trivial enough that reuse-by-import would trade
    a real dependency for saving four lines.
    """
    ids: set[int] = set()
    for action in actions:
        if action.get("type") == "assist":
            parent = action.get("parentActionId")
            if isinstance(parent, int) and parent:
                ids.add(parent)
    return ids


def build_shot_facts(
    game_id: str, actions: list[dict[str, Any]], *, team_id_map: dict[str, str]
) -> list[DeterministicShotFact]:
    """One :class:`DeterministicShotFact` per ``type=="shot"`` action.

    Reuses, rather than recomputes: ``geometry.build_shot_geometry`` (coarse
    zone/distance), ``fastbreak.build_fastbreak_events``/``classify`` (the
    validated fast-break signal), and ``possession.find_and1_shot_ids`` (the
    182-game-validated and-1 adjacency query). Only the identity fields and
    the trivial ``made``/``blocked`` passthrough are assembled fresh here.
    """
    ordered = sorted(
        (a for a in actions if isinstance(a.get("quarter"), int) and isinstance(a.get("id"), int)),
        key=lambda a: (a["quarter"], a["id"]),
    )
    and1_ids = find_and1_shot_ids(ordered)
    assisted_ids = _assisted_shot_action_ids(actions)
    fb_events_by_action_id: dict[int, FastBreakShotEvent] = {
        e.action_id: e for e in build_fastbreak_events(game_id, actions) if e.action_type == "shot"
    }

    facts: list[DeterministicShotFact] = []
    for action in ordered:
        if action.get("type") != "shot":
            continue
        params = action.get("parameters") or {}
        team_num = params.get("team")
        side = _TEAM_SIDE.get(team_num)
        if side is None:
            continue  # no team attribution — same drop convention as boxscore.py

        action_id = action["id"]
        outcome = params.get("made")
        made = outcome == "made"
        blocked = outcome == "blocked"
        missed = outcome == "missed" or blocked
        official_points = int(params.get("points") or 0)
        shot_type = params.get("type")

        geometry: ShotGeometry = build_shot_geometry(
            params.get("coordX"), params.get("coordY"),
            official_points=official_points, shot_type=shot_type,
        )

        fb_event = fb_events_by_action_id.get(action_id)
        fast_break = classify_fast_break(fb_event).is_fast_break if fb_event is not None else False

        facts.append(DeterministicShotFact(
            game_id=game_id, action_id=action_id, event_id=f"{game_id}:{action_id}",
            team_id=team_id_map.get(side, side), opponent_id=team_id_map.get(_OTHER_SIDE[side], _OTHER_SIDE[side]),
            player_id=action.get("playerId"), quarter=action["quarter"],
            game_clock=action.get("quarterTime"), game_clock_s=parse_clock_mmss(action.get("quarterTime")),
            is_field_goal_attempt=True, made=made, missed=missed, blocked=blocked,
            official_points=official_points, shot_type=shot_type,
            and_one=action_id in and1_ids, official_assist=action_id in assisted_ids,
            coarse_shot_zone=geometry.coarse_zone, rim_attempt=geometry.is_rim_attempt,
            shot_distance_m=geometry.distance_m, distance_uncertainty_m=geometry.distance_uncertainty_m,
            distance_eligibility=geometry.distance_eligibility,
            fast_break=fast_break,
            provenance=ShotFactProvenance(
                scoring=PROVENANCE_PROVIDER_SCORING, shot_zone=PROVENANCE_SHOT_ZONE,
                distance=PROVENANCE_DISTANCE, fast_break=PROVENANCE_FAST_BREAK,
                assist=PROVENANCE_ASSIST_OFFICIAL,
            ),
        ))
    return facts


# ---- Event-level: possession facts -----------------------------------------

@dataclass(frozen=True)
class DeterministicPossessionFact:
    """One completed possession, repackaged from ``possession.Possession``
    for the product contract — no possession-boundary logic lives here."""

    game_id: str
    possession_id: str  # f"{game_id}:{possession_index}"
    possession_index: int
    quarter: int
    offense_team_id: str
    defense_team_id: str
    start_clock_s: float | None
    end_clock_s: float | None
    duration_s: float | None
    ended_by: str
    possession_points: int
    possession_scored: bool
    fga: int
    fgm: int
    turnover: bool
    followed_opponent_turnover: bool
    had_offensive_rebound: bool
    fast_break_points: int
    provenance: MetricProvenance = PROVENANCE_POSSESSION_ENGINE

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id, "possession_id": self.possession_id,
            "possession_index": self.possession_index, "quarter": self.quarter,
            "offense_team_id": self.offense_team_id, "defense_team_id": self.defense_team_id,
            "start_clock_s": self.start_clock_s, "end_clock_s": self.end_clock_s,
            "duration_s": self.duration_s, "ended_by": self.ended_by,
            "possession_points": self.possession_points, "possession_scored": self.possession_scored,
            "fga": self.fga, "fgm": self.fgm, "turnover": self.turnover,
            "followed_opponent_turnover": self.followed_opponent_turnover,
            "had_offensive_rebound": self.had_offensive_rebound,
            "fast_break_points": self.fast_break_points,
            "provenance": self.provenance.to_dict(),
        }


def build_possession_facts(
    game_id: str, possessions: list[Possession], *, team_id_map: dict[str, str]
) -> list[DeterministicPossessionFact]:
    facts = []
    for p in possessions:
        duration = None
        if p.start_clock_s is not None and p.end_clock_s is not None:
            duration = p.start_clock_s - p.end_clock_s  # clock counts down
        facts.append(DeterministicPossessionFact(
            game_id=game_id, possession_id=f"{game_id}:{p.possession_index}",
            possession_index=p.possession_index, quarter=p.quarter,
            offense_team_id=team_id_map.get(p.offense_team, p.offense_team),
            defense_team_id=team_id_map.get(p.defense_team, p.defense_team),
            start_clock_s=p.start_clock_s, end_clock_s=p.end_clock_s, duration_s=duration,
            ended_by=p.ended_by, possession_points=p.points, possession_scored=p.points > 0,
            fga=p.fga, fgm=p.fgm, turnover=p.turnover,
            followed_opponent_turnover=p.followed_opponent_turnover,
            had_offensive_rebound=p.had_offensive_rebound, fast_break_points=p.fast_break_points,
        ))
    return facts


# ---- Aggregation: shot-zone distribution + transition-from-shots ----------

@dataclass(frozen=True)
class ShotZoneDistribution:
    lane_2pt: int
    midrange_2pt: int
    corner_3: int
    atb_3: int
    unclassified: int
    rim_attempts: int
    fga_total: int
    lane_2pt_share: float | None
    midrange_2pt_share: float | None
    corner_3_share: float | None
    atb_3_share: float | None
    rim_attempt_share: float | None
    provenance: MetricProvenance = PROVENANCE_ZONE_DISTRIBUTION_AGG

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_2pt": self.lane_2pt, "midrange_2pt": self.midrange_2pt,
            "corner_3": self.corner_3, "atb_3": self.atb_3, "unclassified": self.unclassified,
            "rim_attempts": self.rim_attempts, "fga_total": self.fga_total,
            "lane_2pt_share": self.lane_2pt_share, "midrange_2pt_share": self.midrange_2pt_share,
            "corner_3_share": self.corner_3_share, "atb_3_share": self.atb_3_share,
            "rim_attempt_share": self.rim_attempt_share,
            "provenance": self.provenance.to_dict(),
        }


def build_shot_zone_distribution(shot_facts: list[DeterministicShotFact]) -> ShotZoneDistribution:
    total = len(shot_facts)
    counts = {"lane_2pt": 0, "midrange_2pt": 0, "corner_3": 0, "atb_3": 0}
    unclassified = 0
    rim = 0
    for f in shot_facts:
        if f.coarse_shot_zone in counts:
            counts[f.coarse_shot_zone] += 1
        else:
            unclassified += 1
        if f.rim_attempt:
            rim += 1

    def share(n: int) -> float | None:
        return (n / total) if total > 0 else None

    return ShotZoneDistribution(
        lane_2pt=counts["lane_2pt"], midrange_2pt=counts["midrange_2pt"],
        corner_3=counts["corner_3"], atb_3=counts["atb_3"], unclassified=unclassified,
        rim_attempts=rim, fga_total=total,
        lane_2pt_share=share(counts["lane_2pt"]), midrange_2pt_share=share(counts["midrange_2pt"]),
        corner_3_share=share(counts["corner_3"]), atb_3_share=share(counts["atb_3"]),
        rim_attempt_share=share(rim),
    )


@dataclass(frozen=True)
class TransitionShotFacts:
    """Provider-flag-derived transition FGA facts — complementary to
    ``scoring_sources.FastBreakProfile`` (which is points-only, from
    ``Possession.fast_break_points``): this is the FGA-count/rate view the
    brief's TRANSITION aggregation asked for, computed directly from the
    per-shot ``fast_break`` flag rather than duplicating the points path.
    """

    fast_break_fga: int
    fast_break_fgm: int
    fast_break_points: int
    fast_break_fga_rate: float | None  # share of all FGA that are fast-break
    fast_break_fg_pct: float | None
    provenance: MetricProvenance = PROVENANCE_TRANSITION_FROM_SHOTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "fast_break_fga": self.fast_break_fga, "fast_break_fgm": self.fast_break_fgm,
            "fast_break_points": self.fast_break_points,
            "fast_break_fga_rate": self.fast_break_fga_rate, "fast_break_fg_pct": self.fast_break_fg_pct,
            "provenance": self.provenance.to_dict(),
        }


def build_transition_shot_facts(shot_facts: list[DeterministicShotFact]) -> TransitionShotFacts:
    total = len(shot_facts)
    fb_facts = [f for f in shot_facts if f.fast_break]
    fga = len(fb_facts)
    fgm = sum(1 for f in fb_facts if f.made)
    points = sum(f.official_points for f in fb_facts if f.made)
    return TransitionShotFacts(
        fast_break_fga=fga, fast_break_fgm=fgm, fast_break_points=points,
        fast_break_fga_rate=(fga / total) if total > 0 else None,
        fast_break_fg_pct=(fgm / fga) if fga > 0 else None,
    )


# ---- Team/game/season aggregation ------------------------------------------

@dataclass(frozen=True)
class TeamScoutingSummary:
    """Product-facing team-level summary for one scope (a game, a window, a
    season) — assembled from already-existing profile objects a caller built
    via ``enrichment.build_game_enrichment`` (or a season aggregate of the
    same), plus the two new trivial shot-fact aggregations above. This
    object recomputes nothing that ``scoring_sources.py`` already computes.
    """

    team_id: str
    opponent_id: str | None
    game_id: str | None  # None for a multi-game aggregation scope
    games_n: int

    fga: int
    fg3a: int
    fg3a_rate: float | None
    efg_pct: float | None

    shot_zone: ShotZoneDistribution
    transition_from_shots: TransitionShotFacts

    possessions_n: int
    turnovers_n: int
    scoring_possessions_n: int

    provider_fast_break: FastBreakProfile | None = None
    assisted: AssistedProfile | None = None
    shot_mix: ShotScoringMix | None = None
    second_chance: SecondChanceProfile | None = None
    points_off_turnovers: PointsOffTurnoversProfile | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id, "opponent_id": self.opponent_id, "game_id": self.game_id,
            "games_n": self.games_n, "fga": self.fga, "fg3a": self.fg3a,
            "fg3a_rate": self.fg3a_rate, "efg_pct": self.efg_pct,
            "shot_zone": self.shot_zone.to_dict(), "transition_from_shots": self.transition_from_shots.to_dict(),
            "possessions_n": self.possessions_n, "turnovers_n": self.turnovers_n,
            "scoring_possessions_n": self.scoring_possessions_n,
            "provider_fast_break": self.provider_fast_break.to_dict() if self.provider_fast_break else None,
            "assisted": self.assisted.to_dict() if self.assisted else None,
            "shot_mix": self.shot_mix.to_dict() if self.shot_mix else None,
            "second_chance": self.second_chance.to_dict() if self.second_chance else None,
            "points_off_turnovers": self.points_off_turnovers.to_dict() if self.points_off_turnovers else None,
        }


def build_team_scouting_summary(
    team_id: str,
    shot_facts: list[DeterministicShotFact],
    possession_facts: list[DeterministicPossessionFact],
    *,
    opponent_id: str | None = None,
    game_id: str | None = None,
    games_n: int = 1,
    provider_fast_break: FastBreakProfile | None = None,
    assisted: AssistedProfile | None = None,
    shot_mix: ShotScoringMix | None = None,
    second_chance: SecondChanceProfile | None = None,
    points_off_turnovers: PointsOffTurnoversProfile | None = None,
) -> TeamScoutingSummary:
    """``shot_facts``/``possession_facts`` must already be filtered to this
    team's own offense (the same convention ``scoring_sources.py`` uses for
    ``team_possessions``). The five profile objects are optional pass-
    throughs — a caller who already built a ``GameEnrichment`` supplies its
    ``fast_break``/``assisted``/``shot_mix``/``second_chance``/
    ``points_off_turnovers`` fields directly; this function never rebuilds
    them.
    """
    fga = len(shot_facts)
    fg3a = sum(1 for f in shot_facts if f.official_points == 3)
    fgm = sum(1 for f in shot_facts if f.made)
    fg3m = sum(1 for f in shot_facts if f.made and f.official_points == 3)

    zone_dist = build_shot_zone_distribution(shot_facts)
    transition = build_transition_shot_facts(shot_facts)

    return TeamScoutingSummary(
        team_id=team_id, opponent_id=opponent_id, game_id=game_id, games_n=games_n,
        fga=fga, fg3a=fg3a, fg3a_rate=three_point_rate(fg3a, fga), efg_pct=effective_fg_pct(fgm, fg3m, fga),
        shot_zone=zone_dist, transition_from_shots=transition,
        possessions_n=len(possession_facts),
        turnovers_n=sum(1 for p in possession_facts if p.turnover),
        scoring_possessions_n=sum(1 for p in possession_facts if p.possession_scored),
        provider_fast_break=provider_fast_break, assisted=assisted, shot_mix=shot_mix,
        second_chance=second_chance, points_off_turnovers=points_off_turnovers,
    )
