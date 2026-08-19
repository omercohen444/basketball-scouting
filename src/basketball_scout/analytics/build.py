"""Build the analytics artifacts from the local play-by-play cache.

Offline and build-time only. Requires ``data/raw/pbp`` and
``data/processed/stats``, both git-ignored — which is exactly why the output is
a committed artifact: a deployment has the artifact and neither input.

Nothing here can move an evidence pack. It reads the same two caches the pack
builder reads and writes to a different directory.

This module loads possessions itself rather than going through
``agents.evidence_pack.load_league_data``, which builds full ``GameEnrichment``
records and then discards the possessions. The segment grid needs the
possessions (to volume-weight, and to union score-state bins for
leading/trailing), and needs none of the enrichment — so loading directly is
both simpler and less work.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import Settings, load_settings
from ..stats import formulas, segments
from ..stats.dynamics import GameDynamics, build_game_dynamics
from ..stats.league_context import DEFAULT_DIRECTIONS
from ..stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from ..stats.possession import Possession, build_possessions
from ..stats.runs_droughts import DroughtsProfile, RunsProfile, build_droughts_profile, build_runs_profile
from ..stats.scouting_features import build_shot_facts, team_id_map_from_game_info
from ..stats.scoring_sources import (
    build_assisted_profile,
    build_fast_break_profile,
    build_points_off_turnovers_profile,
    build_second_chance_profile,
)
from ..stats.scoring_timeline import build_scoring_timeline
from ..stats.segment_metrics import (
    build_canonical_aggregate_metrics,
    build_segment_components,
    compute_segment_metrics,
)
from ..stats.stability import build_stability_profile
from ..stats.store import load_game
from ..stats.turnover_taxonomy import build_turnover_taxonomy
from .schema import (
    ANALYTICS_ARTIFACT_VERSION,
    CELL_METRICS,
    CV_APPLICABLE,
    INDEX_FILENAME,
    METRICS,
    OPPONENT_DIRECTIONS,
    OPPONENT_METRICS,
    OUTCOMES,
    SEGMENTS,
    AnalyticsArtifact,
    AnalyticsIndex,
    AnalyticsIndexEntry,
    BoxComponents,
    ComebackBlock,
    GameRow,
    RunsDroughtsProfile,
    ScoringSourceProfile,
    SegmentCell,
    ShotZoneProfile,
    StabilityEntry,
    TeamAnalytics,
    TeamProfile,
    TransitionProfile,
    TurnoverProfile,
    classify_sample,
)

EXPECTED_TEAMS = 14
EXPECTED_GAMES_PER_TEAM = 26

# The 2025-26 regular season holds exactly this many turnover events, each
# attributed to exactly one team. Verified three independent ways: raw
# `type == "turnover"` actions over the 182 accepted games, `components_for.tov`
# summed over the 364 team-game records, and possessions flagged `turnover`.
#
# Worth pinning because the raw cache also holds 115 games from other
# competitions — Winner Cup, playoffs, preseason, second division, youth and
# women's — which together carry 8,041 turnovers. Loading the wrong population
# is the one silent way this profile could go wrong, and the number would still
# look plausible.
EXPECTED_LEAGUE_TURNOVERS = 5205

# Quarters and halves have a defined elapsed time, so Pace is meaningful. The
# rest do not — there is no rigorous denominator for "minutes spent trailing",
# and a fabricated one would be worse than an absent cell.
_SEGMENT_MINUTES: dict[str, float | None] = {
    "q1": 10.0, "q2": 10.0, "q3": 10.0, "q4": 10.0,
    "h1": 20.0, "h2": 20.0,
    "close": None, "leading": None, "trailing": None, "clutch": None,
}

Predicate = Callable[[Possession], bool]


class IncompleteLeagueError(RuntimeError):
    """The source cache does not hold the full league.

    Raised before anything is written. The loader skips a game whose raw
    play-by-play file is missing, which would otherwise produce an artifact
    that looks complete and is quietly wrong — a partial cache must fail the
    build, not ship.
    """


@dataclass(frozen=True)
class GameFacts:
    """One team-game's already-aggregated identity facts.

    Deliberately aggregates, not event lists. The season fold only ever sums
    these, so carrying 24,432 shot facts through the whole build to add them up
    at the end would cost memory for nothing.

    Every field comes from a module that already exists and is already tested;
    this build adds no basketball arithmetic of its own.
    """

    # shot zones + rim, from pbp/geometry via scouting_features
    fga: int
    zone_attempts: dict[str, int]
    zone_points: dict[str, int]
    rim_attempts: int
    unclassified: int
    # provider fast-break flag, both directions
    opp_fga: int
    fb_fga: int
    fb_fgm: int
    fb_points: int
    fb_fga_allowed: int
    fb_fgm_allowed: int
    fb_points_allowed: int
    # provider turnover taxonomy, own and forced
    turnovers_by_type: dict[str, int]
    forced_by_type: dict[str, int]
    # scoring sources, from stats/scoring_sources over the same possessions
    points_off_turnovers: int
    opponent_turnovers: int
    second_chance_points: int
    oreb_possessions: int
    scoring_oreb_possessions: int
    fast_break_points: int
    assisted_fgm: int
    unassisted_fgm: int
    assisted_3pm: int
    unassisted_3pm: int
    # scoring rhythm
    runs: RunsProfile
    droughts: DroughtsProfile
    dynamics: GameDynamics


@dataclass(frozen=True)
class TeamGameBundle:
    """One team-game: the stored record, both sides' possessions, and the
    season-scope facts this game contributes."""

    stats: TeamGameStats
    team_possessions: list[Possession]
    opponent_possessions: list[Possession]
    regulation_periods: int
    facts: GameFacts | None = None


_ZONES: tuple[str, ...] = ("lane_2pt", "midrange_2pt", "corner_3", "atb_3")


def build_game_facts(
    *,
    team_id: str,
    opponent_id: str,
    team_side: str,
    team_won: bool,
    regulation_periods: int,
    team_possessions: list[Possession],
    opponent_possessions: list[Possession],
    shot_facts: list,
    plays: list,
    turnover_counts: tuple[dict[str, int], dict[str, int]],
) -> GameFacts:
    """One team-game's identity facts.

    ``shot_facts``, ``plays`` and ``turnover_counts`` are built once per game by
    the caller and handed to both sides, so the expensive per-game passes happen
    once rather than twice.
    """
    mine = [f for f in shot_facts if f.team_id == team_id]
    theirs = [f for f in shot_facts if f.team_id == opponent_id]

    zone_attempts = {z: 0 for z in _ZONES}
    zone_points = {z: 0 for z in _ZONES}
    rim = unclassified = 0
    for f in mine:
        zone = f.coarse_shot_zone
        if zone in zone_attempts:
            zone_attempts[zone] += 1
            if f.made:
                zone_points[zone] += f.official_points
        else:
            unclassified += 1
        if f.rim_attempt:
            rim += 1

    fb_mine = [f for f in mine if f.fast_break]
    fb_theirs = [f for f in theirs if f.fast_break]

    home_tov, away_tov = turnover_counts
    own_tov = home_tov if team_side == "home" else away_tov
    forced_tov = away_tov if team_side == "home" else home_tov

    pot = build_points_off_turnovers_profile(team_possessions, opponent_possessions, games_n=1)
    second = build_second_chance_profile(team_possessions, games_n=1)
    fast = build_fast_break_profile(team_possessions, games_n=1)
    assisted = build_assisted_profile(team_possessions)

    return GameFacts(
        fga=len(mine),
        zone_attempts=zone_attempts,
        zone_points=zone_points,
        rim_attempts=rim,
        unclassified=unclassified,
        opp_fga=len(theirs),
        fb_fga=len(fb_mine),
        fb_fgm=sum(1 for f in fb_mine if f.made),
        fb_points=sum(f.official_points for f in fb_mine if f.made),
        fb_fga_allowed=len(fb_theirs),
        fb_fgm_allowed=sum(1 for f in fb_theirs if f.made),
        fb_points_allowed=sum(f.official_points for f in fb_theirs if f.made),
        turnovers_by_type=dict(own_tov),
        forced_by_type=dict(forced_tov),
        points_off_turnovers=pot.points_off_turnovers,
        opponent_turnovers=pot.opponent_turnovers,
        second_chance_points=second.second_chance_points,
        oreb_possessions=second.offensive_rebound_possessions,
        scoring_oreb_possessions=second.scoring_oreb_possessions,
        fast_break_points=fast.provider_fast_break_points,
        assisted_fgm=assisted.assisted_fgm,
        unassisted_fgm=assisted.unassisted_fgm,
        assisted_3pm=assisted.assisted_3pm,
        unassisted_3pm=assisted.unassisted_3pm,
        runs=build_runs_profile(plays, team_side=team_side),
        droughts=build_droughts_profile(
            plays, team_side=team_side, regulation_periods=regulation_periods
        ),
        dynamics=build_game_dynamics(plays, team_side=team_side, team_won=team_won),
    )


# ---- loading ----------------------------------------------------------------


def load_league_possessions(
    settings: Settings | None = None, *, stats_dir: Path | None = None
) -> tuple[dict[str, list[TeamGameBundle]], dict[str, str], str]:
    """``(bundles_by_team, team_names, season)`` from the local caches."""
    settings = settings or load_settings()
    stats_dir = stats_dir or (settings.data_dir / "processed" / "stats")

    by_team: dict[str, list[TeamGameBundle]] = {}
    team_names: dict[str, str] = {}
    seasons: set[str] = set()

    for path in sorted(stats_dir.glob("*.json")):
        home_stats, away_stats = load_game(path)
        raw_path = settings.raw_pbp_dir / f"segev_{home_stats.source_game_id}.json"
        if not raw_path.is_file():
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))

        actions = raw["actions"]
        built = build_possessions(actions, regulation_periods=home_stats.regulation_periods)
        by_side: dict[str, list[Possession]] = {"home": [], "away": []}
        for p in built.possessions:
            by_side[p.offense_team].append(p)

        # The three per-game passes, done once and shared by both sides.
        team_id_map = team_id_map_from_game_info(raw["gameInfo"])
        shot_facts = build_shot_facts(home_stats.internal_game_id, actions, team_id_map=team_id_map)
        plays = build_scoring_timeline(actions)
        turnover_counts = build_turnover_taxonomy(actions)

        for stats, side, other in ((home_stats, "home", "away"), (away_stats, "away", "home")):
            opponent_id = away_stats.team_id if side == "home" else home_stats.team_id
            by_team.setdefault(stats.team_id, []).append(
                TeamGameBundle(
                    stats=stats,
                    team_possessions=by_side[side],
                    opponent_possessions=by_side[other],
                    regulation_periods=stats.regulation_periods,
                    facts=build_game_facts(
                        team_id=stats.team_id,
                        opponent_id=opponent_id,
                        team_side=side,
                        team_won=stats.win,
                        regulation_periods=stats.regulation_periods,
                        team_possessions=by_side[side],
                        opponent_possessions=by_side[other],
                        shot_facts=shot_facts,
                        plays=plays,
                        turnover_counts=turnover_counts,
                    ),
                )
            )
            team_names[stats.team_id] = stats.team_name
            seasons.add(stats.season)

    return by_team, team_names, (sorted(seasons)[-1] if seasons else "unknown")


def assert_complete_league(by_team: dict[str, list[TeamGameBundle]]) -> None:
    """Fail loudly on a partial cache, before anything is written."""
    problems: list[str] = []
    if len(by_team) != EXPECTED_TEAMS:
        problems.append(f"expected {EXPECTED_TEAMS} teams, found {len(by_team)}")
    for team_id in sorted(by_team):
        n = len(by_team[team_id])
        if n != EXPECTED_GAMES_PER_TEAM:
            problems.append(f"{team_id}: expected {EXPECTED_GAMES_PER_TEAM} games, found {n}")
    if problems:
        raise IncompleteLeagueError(
            "refusing to write analytics artifacts from an incomplete league:\n  "
            + "\n  ".join(problems)
            + "\nThe raw play-by-play cache is probably partial; the loader skips "
              "a game whose raw file is missing rather than failing."
        )


# ---- segment predicates -----------------------------------------------------


def segment_predicates(segment: str, regulation_periods: int) -> tuple[Predicate, Predicate]:
    """``(team_pred, opponent_pred)`` for one segment.

    The opponent predicate is not always the team predicate. Score-state
    segments are *signed*: while this team leads by four the opponent trails by
    four, so pairing "our leading possessions" with "their leading possessions"
    would compare two different stretches of the game. Mirroring negates the
    opponent's own margin, which stays correct wherever the bin edges sit.

    Every other segment is symmetric: quarters and halves are clock facts, and
    close/clutch test ``abs(margin)``, which reads the same from either bench.
    """
    if len(segment) == 2 and segment[0] == "q" and segment[1].isdigit():
        want = segment.upper()
        pred: Predicate = lambda p: segments.quarter_segment(p, regulation_periods) == want
        return pred, pred
    if segment in ("h1", "h2"):
        want = "1H" if segment == "h1" else "2H"
        pred = lambda p: segments.half_segment(p, regulation_periods) == want
        return pred, pred
    if segment == "close":
        # The dedicated predicate, never the union of three bins — those
        # disagree on 1,102 possessions because behind_1_5 stops at -4.
        pred = lambda p: segments.is_close_score(p)
        return pred, pred
    if segment == "clutch":
        pred = lambda p: segments.is_clutch(p, regulation_periods)
        return pred, pred
    if segment == "leading":
        return (_margin_at_least(1), _margin_at_most(-1))
    if segment == "trailing":
        return (_margin_at_most(-1), _margin_at_least(1))
    raise ValueError(f"unknown segment {segment!r}")


def _margin_at_least(threshold: int) -> Predicate:
    def pred(p: Possession) -> bool:
        margin = segments.offense_margin_at_start(p)
        return margin is not None and margin >= threshold
    return pred


def _margin_at_most(threshold: int) -> Predicate:
    def pred(p: Possession) -> bool:
        margin = segments.offense_margin_at_start(p)
        return margin is not None and margin <= threshold
    return pred


# ---- aggregation ------------------------------------------------------------


class _AggregateRow:
    """Duck-types the three attributes ``build_canonical_aggregate_metrics``
    reads, so per-game *segment* components flow through the identical
    volume-weighted path the season aggregate already uses."""

    __slots__ = ("components_for", "components_against", "game_minutes")

    def __init__(self, cf: TeamGameComponents, ca: TeamGameComponents, minutes: float):
        self.components_for = cf
        self.components_against = ca
        self.game_minutes = minutes


def _present(m: DerivedMetrics) -> dict[str, float]:
    """Present metrics only — a missing value is omitted, never nulled, so a
    view model structurally cannot render it."""
    return {
        name: round(float(v), 4)
        for name in METRICS
        if (v := getattr(m, name, None)) is not None
    }


def opponent_metrics(
    components_for: TeamGameComponents, components_against: TeamGameComponents
) -> dict[str, float]:
    """The four defensive factors over one already-summed scope.

    A pure derivation from the ``components_against`` that
    ``build_segment_components`` already returns — no new arithmetic, the same
    four functions in ``formulas.py`` the offensive side uses, so the two halves
    of a four-factors table are computed identically and are comparable.

    ``components_for.drb`` is this team's defensive rebounds over the scope
    (segment cells derive it as the opponent possessions that ended in one), and
    ``components_against.orb`` is what the opponent kept — together they are the
    contested defensive glass.
    """
    ca, cf = components_against, components_for
    values = {
        "opp_efg_pct": formulas.effective_fg_pct(ca.fgm, ca.fg3m, ca.fga),
        "opp_tov_pct": formulas.turnover_pct(ca.tov, ca.fga, ca.fta),
        "drb_pct": formulas.off_reb_pct(cf.drb, ca.orb),
        "opp_ft_rate": formulas.free_throw_rate(ca.fta, ca.fga),
    }
    return {k: round(float(v), 4) for k, v in values.items() if v is not None}


def _sum_box(items: list[TeamGameComponents]) -> TeamGameComponents:
    return TeamGameComponents(
        fgm=sum(c.fgm for c in items), fga=sum(c.fga for c in items),
        fg3m=sum(c.fg3m for c in items), fg3a=sum(c.fg3a for c in items),
        ftm=sum(c.ftm for c in items), fta=sum(c.fta for c in items),
        orb=sum(c.orb for c in items), drb=sum(c.drb for c in items),
        ast=sum(c.ast for c in items), tov=sum(c.tov for c in items),
        pf=sum(c.pf for c in items), points=sum(c.points for c in items),
    )


def _mean_of(per_game: list[DerivedMetrics]) -> dict[str, float]:
    """The legacy unweighted per-game mean, kept for reconciliation only."""
    out: dict[str, float] = {}
    for name in METRICS:
        vals = [v for m in per_game if (v := getattr(m, name, None)) is not None]
        if vals:
            out[name] = round(sum(vals) / len(vals), 4)
    return out


def _components(c: TeamGameComponents) -> BoxComponents:
    return BoxComponents(
        fgm=c.fgm, fga=c.fga, fg3m=c.fg3m, fg3a=c.fg3a, ftm=c.ftm, fta=c.fta,
        orb=c.orb, drb=c.drb, ast=c.ast, tov=c.tov, pf=c.pf, points=c.points,
    )


def build_segment_cell(segment: str, outcome: str, bundles: list[TeamGameBundle]) -> SegmentCell:
    """One (segment, outcome) cell, volume-weighted."""
    minutes = _SEGMENT_MINUTES.get(segment)
    rows: list[_AggregateRow] = []
    per_game: list[DerivedMetrics] = []
    possessions = 0

    for bundle in bundles:
        team_pred, opp_pred = segment_predicates(segment, bundle.regulation_periods)
        team_poss = [p for p in bundle.team_possessions if team_pred(p)]
        if not team_poss:
            continue  # a game the team never entered this state — not a zero
        opp_poss = [p for p in bundle.opponent_possessions if opp_pred(p)]

        cf, ca = build_segment_components(team_poss, opp_poss)
        rows.append(_AggregateRow(cf, ca, minutes or 0.0))
        per_game.append(compute_segment_metrics(team_poss, opp_poss, segment_minutes=minutes))
        possessions += len(team_poss)

    if not rows:
        return SegmentCell(segment=segment, outcome=outcome, sample_state="insufficient")

    metrics = _present(build_canonical_aggregate_metrics(rows))
    if minutes is None:
        metrics.pop("pace", None)  # no defined elapsed time; do not invent one

    # The defensive half, from components this loop already built. Summed first
    # and divided once, exactly like the offensive half.
    metrics.update(opponent_metrics(
        _sum_box([r.components_for for r in rows]),
        _sum_box([r.components_against for r in rows]),
    ))

    return SegmentCell(
        segment=segment, outcome=outcome,
        games=len(rows), possessions=possessions,
        sample_state=classify_sample(possessions, len(rows)),
        metrics=metrics,
        unweighted={k: v for k, v in _mean_of(per_game).items() if k in metrics},
    )


def build_full_cell(outcome: str, bundles: list[TeamGameBundle]) -> SegmentCell:
    """``full`` comes from the stored game records, not summed possessions.

    Possession-derived season components reconcile exactly with the box score
    on every field except defensive rebounds, which drift by up to five over a
    season. Driving ``full`` off the stored records makes the site's season row
    identical to the reports'. The trade — Q1+Q2+Q3+Q4 not re-summing to
    ``full`` to the last decimal — is documented rather than hidden.
    """
    stats_rows = [b.stats for b in bundles]
    if not stats_rows:
        return SegmentCell(segment="full", outcome=outcome, sample_state="insufficient")

    possessions = int(round(sum(s.possessions_for for s in stats_rows)))
    metrics = _present(build_canonical_aggregate_metrics(stats_rows))
    metrics.update(opponent_metrics(
        _sum_box([s.components_for for s in stats_rows]),
        _sum_box([s.components_against for s in stats_rows]),
    ))
    return SegmentCell(
        segment="full", outcome=outcome,
        games=len(stats_rows), possessions=possessions,
        sample_state=classify_sample(possessions, len(stats_rows)),
        metrics=metrics,
        unweighted=_mean_of([s.metrics for s in stats_rows]),
    )


def build_team_analytics(
    team_id: str, bundles: list[TeamGameBundle], team_name: str, season: str
) -> TeamAnalytics:
    ordered = sorted(bundles, key=lambda b: b.stats.game_date)
    wins = sum(1 for b in ordered if b.stats.win)

    games = [
        GameRow(
            game_id=b.stats.internal_game_id,
            game_date=b.stats.game_date,
            team_id=b.stats.team_id,
            opponent_id=b.stats.opponent_id,
            opponent_name=b.stats.opponent_name,
            is_home=b.stats.is_home,
            win=b.stats.win,
            score_for=b.stats.final_score_for,
            score_against=b.stats.final_score_against,
            possessions_for=round(b.stats.possessions_for, 3),
            possessions_against=round(b.stats.possessions_against, 3),
            components_for=_components(b.stats.components_for),
            components_against=_components(b.stats.components_against),
            metrics=_present(b.stats.metrics),
            times_tied=b.facts.dynamics.times_tied if b.facts else 0,
            lead_changes=b.facts.dynamics.lead_changes if b.facts else 0,
            largest_lead=b.facts.dynamics.largest_lead if b.facts else 0,
            largest_deficit=b.facts.dynamics.largest_deficit if b.facts else 0,
        )
        for b in ordered
    ]

    cells: dict[str, SegmentCell] = {}
    for outcome in OUTCOMES:
        if outcome == "all":
            selected = ordered
        elif outcome == "wins":
            selected = [b for b in ordered if b.stats.win]
        else:
            selected = [b for b in ordered if not b.stats.win]

        cells[f"full:{outcome}"] = build_full_cell(outcome, selected)
        for segment in SEGMENTS:
            if segment == "full":
                continue
            cells[f"{segment}:{outcome}"] = build_segment_cell(segment, outcome, selected)

    dates = sorted(b.stats.game_date for b in ordered)
    return TeamAnalytics(
        team_id=team_id, team_name=team_name, season=season,
        wins=wins, losses=len(ordered) - wins, games_n=len(ordered),
        date_range=f"{dates[0]} to {dates[-1]}" if dates else "n/a",
        games=games, cells=cells,
        profile=build_team_profile(ordered),
    )


# ---- season identity profile ------------------------------------------------


def _merge_counts(dicts: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def build_team_profile(bundles: list[TeamGameBundle]) -> TeamProfile:
    """Fold the per-game facts into one season identity block.

    Every field is a sum or a max over values another module already computed.
    Nothing is averaged here — rates are derived in the view layer from these
    counts, so the file stays auditable and rounding happens once.
    """
    facts = [b.facts for b in bundles if b.facts is not None]
    if not facts:
        return TeamProfile()

    zone_attempts = _merge_counts([f.zone_attempts for f in facts])
    zone_points = _merge_counts([f.zone_points for f in facts])

    shots = ShotZoneProfile(
        fga=sum(f.fga for f in facts),
        lane_2pt=zone_attempts.get("lane_2pt", 0),
        midrange_2pt=zone_attempts.get("midrange_2pt", 0),
        corner_3=zone_attempts.get("corner_3", 0),
        atb_3=zone_attempts.get("atb_3", 0),
        unclassified=sum(f.unclassified for f in facts),
        rim_attempts=sum(f.rim_attempts for f in facts),
        zone_attempts={z: zone_attempts.get(z, 0) for z in _ZONES},
        zone_points={z: zone_points.get(z, 0) for z in _ZONES},
    )

    transition = TransitionProfile(
        fga=sum(f.fga for f in facts),
        opp_fga=sum(f.opp_fga for f in facts),
        fb_fga=sum(f.fb_fga for f in facts),
        fb_fgm=sum(f.fb_fgm for f in facts),
        fb_points=sum(f.fb_points for f in facts),
        fb_fga_allowed=sum(f.fb_fga_allowed for f in facts),
        fb_fgm_allowed=sum(f.fb_fgm_allowed for f in facts),
        fb_points_allowed=sum(f.fb_points_allowed for f in facts),
    )

    own_tov = _merge_counts([f.turnovers_by_type for f in facts])
    forced_tov = _merge_counts([f.forced_by_type for f in facts])
    turnovers = TurnoverProfile(
        total=sum(own_tov.values()), by_type=own_tov,
        forced_total=sum(forced_tov.values()), forced_by_type=forced_tov,
    )

    box = _sum_box([b.stats.components_for for b in bundles])
    scoring = ScoringSourceProfile(
        points=box.points,
        points_2pt=2 * (box.fgm - box.fg3m),
        points_3pt=3 * box.fg3m,
        points_ft=box.ftm,
        points_off_turnovers=sum(f.points_off_turnovers for f in facts),
        opponent_turnovers=sum(f.opponent_turnovers for f in facts),
        second_chance_points=sum(f.second_chance_points for f in facts),
        oreb_possessions=sum(f.oreb_possessions for f in facts),
        scoring_oreb_possessions=sum(f.scoring_oreb_possessions for f in facts),
        fast_break_points=sum(f.fast_break_points for f in facts),
        assisted_fgm=sum(f.assisted_fgm for f in facts),
        unassisted_fgm=sum(f.unassisted_fgm for f in facts),
        assisted_3pm=sum(f.assisted_3pm for f in facts),
        unassisted_3pm=sum(f.unassisted_3pm for f in facts),
    )

    runs = RunsDroughtsProfile(
        games=len(facts),
        largest_run_for_sum=sum(f.runs.largest_scoring_run_for for f in facts),
        largest_run_against_sum=sum(f.runs.largest_scoring_run_against for f in facts),
        largest_run_for_max=max(f.runs.largest_scoring_run_for for f in facts),
        largest_run_against_max=max(f.runs.largest_scoring_run_against for f in facts),
        runs_8_plus_for=sum(f.runs.runs_8_plus_for for f in facts),
        runs_8_plus_against=sum(f.runs.runs_8_plus_against for f in facts),
        scoring_droughts_3m=sum(f.droughts.drought_count_3m_plus for f in facts),
        fg_droughts_3m=sum(f.droughts.fg_drought_count_3m_plus for f in facts),
        longest_scoring_drought_s=max(f.droughts.longest_scoring_drought_seconds for f in facts),
        longest_fg_drought_s=max(f.droughts.longest_fg_drought_seconds for f in facts),
    )

    # Opportunity denominators, never games played: a team that never trailed by
    # ten cannot have failed to come back from it.
    trailing = [f for f in facts if f.dynamics.trailed_by_10_plus]
    leading = [f for f in facts if f.dynamics.led_by_10_plus]
    comeback = ComebackBlock(
        games_trailing_10_plus=len(trailing),
        comeback_wins=sum(1 for f in trailing if f.dynamics.team_won),
        games_leading_10_plus=len(leading),
        blown_leads=sum(1 for f in leading if not f.dynamics.team_won),
    )

    return TeamProfile(
        shots=shots, transition=transition, turnovers=turnovers,
        scoring=scoring, runs=runs, comeback=comeback,
        stability=build_stability(bundles),
    )


def build_stability(bundles: list[TeamGameBundle]) -> dict[str, StabilityEntry]:
    """Game-to-game spread for each core metric.

    Deliberately the unweighted per-game distribution, not the volume-weighted
    season value: "how consistent is this team" is a question about typical
    nights, where a ten-shot game and a ninety-shot game genuinely do count
    equally. That is the same reasoning stats/evidence.py already applies.
    """
    out: dict[str, StabilityEntry] = {}
    for metric in METRICS:
        values = [
            v for b in bundles
            if (v := getattr(b.stats.metrics, metric, None)) is not None
        ]
        if not values:
            continue
        p = build_stability_profile(values)
        applicable = CV_APPLICABLE.get(metric, True)
        out[metric] = StabilityEntry(
            games=p.games,
            mean=round(p.mean, 4) if p.mean is not None else None,
            std=round(p.std, 4) if p.std is not None else None,
            cv=round(p.coefficient_of_variation, 4)
            if (applicable and p.coefficient_of_variation is not None) else None,
            cv_applicable=applicable,
            min=round(p.min, 4) if p.min is not None else None,
            max=round(p.max, 4) if p.max is not None else None,
        )
    return out


def assert_profiles_complete(
    teams: dict[str, TeamAnalytics], *, real_league: bool = False
) -> None:
    """Fail loudly if the identity profile came out empty or implausible.

    The completeness guard upstream checks that the right *games* are present.
    This checks that the facts derived from them actually arrived: a silently
    empty profile block would render as a team with no shots and no turnovers,
    which reads as a league anomaly rather than a stale build.

    ``real_league`` additionally pins the season turnover total. That check only
    applies to a build from the actual cache — synthetic bundles are a different
    league by construction, and asserting a real-world constant against them
    would be a fixture requirement rather than a data guard.
    """
    problems: list[str] = []
    for team_id in sorted(teams):
        p = teams[team_id].profile
        if p.shots.fga <= 0:
            problems.append(f"{team_id}: no shot attempts in the profile")
        if p.shots.unclassified:
            # Coordinate coverage is 100% across the season. Anything else means
            # the geometry stopped classifying, which must not ship quietly.
            problems.append(f"{team_id}: {p.shots.unclassified} shots without a zone")
        if p.turnovers.total <= 0:
            problems.append(f"{team_id}: no turnovers in the profile")
        if p.transition.fga != p.shots.fga:
            problems.append(f"{team_id}: transition and shot attempt totals disagree")
        if not p.stability:
            problems.append(f"{team_id}: no stability entries")

    if real_league:
        league_turnovers = sum(t.profile.turnovers.total for t in teams.values())
        if league_turnovers != EXPECTED_LEAGUE_TURNOVERS:
            problems.append(
                f"league turnover total is {league_turnovers}, expected "
                f"{EXPECTED_LEAGUE_TURNOVERS} — the wrong game population was loaded"
            )

    if problems:
        raise IncompleteLeagueError(
            "refusing to write analytics artifacts with an incomplete profile:\n  "
            + "\n  ".join(problems)
        )


def stamp_league_ranks(teams: dict[str, TeamAnalytics]) -> None:
    """Stamp league rank and percentile onto every cell, in place.

    Ranked only over the teams with a usable sample in the *same* cell — a team
    with an insufficient sample is left unranked rather than ranked last,
    because comparing a two-game sample to a twenty-game one is not a ranking.
    """
    for segment in SEGMENTS:
        for outcome in OUTCOMES:
            key = f"{segment}:{outcome}"
            eligible = {
                tid: t.cells[key]
                for tid, t in teams.items()
                if key in t.cells and t.cells[key].sample_state != "insufficient"
            }
            if len(eligible) < 2:
                continue
            for metric in CELL_METRICS:
                values = {tid: c.metrics[metric] for tid, c in eligible.items() if metric in c.metrics}
                if len(values) < 2:
                    continue
                direction = OPPONENT_DIRECTIONS.get(metric) or DEFAULT_DIRECTIONS.get(metric)
                lower_better = direction == "lower_is_better"
                order = sorted(values.items(), key=lambda kv: kv[1], reverse=not lower_better)
                n = len(order)
                for position, (tid, _v) in enumerate(order, start=1):
                    cell = eligible[tid]
                    cell.ranks[metric] = position
                    cell.percentiles[metric] = round(100.0 * (n - position) / (n - 1), 1)
                    cell.eligible_teams = n


# ---- artifact envelope ------------------------------------------------------


def _canonical_json(model) -> str:
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def content_hash(team: TeamAnalytics) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(team).encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def artifact_filename(team_id: str) -> str:
    return f"analytics_{team_id.replace(':', '_')}.json"


def build_all(
    settings: Settings | None = None, *, stats_dir: Path | None = None
) -> tuple[dict[str, AnalyticsArtifact], AnalyticsIndex]:
    """Build every team's artifact and the index from the cached league."""
    by_team, team_names, season = load_league_possessions(settings, stats_dir=stats_dir)
    return build_from_bundles(by_team, team_names, season, real_league=True)


def build_from_bundles(
    by_team: dict[str, list[TeamGameBundle]],
    team_names: dict[str, str],
    season: str,
    *,
    real_league: bool = False,
) -> tuple[dict[str, AnalyticsArtifact], AnalyticsIndex]:
    """The build itself, over bundles already in memory. Raises on a partial cache.

    Separate from :func:`build_all` so the completeness guard and the whole
    aggregation path can be exercised against synthetic bundles, with no cached
    play-by-play on disk.
    """
    assert_complete_league(by_team)

    teams = {
        team_id: build_team_analytics(team_id, bundles, team_names.get(team_id, team_id), season)
        for team_id, bundles in sorted(by_team.items())
    }
    assert_profiles_complete(teams, real_league=real_league)
    stamp_league_ranks(teams)

    generated_at = utc_now_iso()
    artifacts: dict[str, AnalyticsArtifact] = {}
    entries: list[AnalyticsIndexEntry] = []
    for team_id, team in teams.items():
        artifacts[team_id] = AnalyticsArtifact(
            artifact_version=ANALYTICS_ARTIFACT_VERSION,
            content_hash=content_hash(team),
            generated_at=generated_at,
            team=team,
        )
        entries.append(
            AnalyticsIndexEntry(
                team_id=team_id, team_name=team.team_name,
                file=artifact_filename(team_id),
                content_hash=artifacts[team_id].content_hash,
                games_n=team.games_n, wins=team.wins, losses=team.losses,
            )
        )

    index = AnalyticsIndex(
        artifact_version=ANALYTICS_ARTIFACT_VERSION,
        season=season, generated_at=generated_at,
        teams=sorted(entries, key=lambda e: e.team_id),
    )
    return artifacts, index


def write_all(artifacts: dict[str, AnalyticsArtifact], index: AnalyticsIndex, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for team_id, artifact in artifacts.items():
        path = out_dir / artifact_filename(team_id)
        path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    (out_dir / INDEX_FILENAME).write_text(
        json.dumps(index.model_dump(mode="json"), indent=1, ensure_ascii=False), encoding="utf-8"
    )
