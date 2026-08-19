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
from ..stats import segments
from ..stats.league_context import DEFAULT_DIRECTIONS
from ..stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from ..stats.possession import Possession, build_possessions
from ..stats.segment_metrics import (
    build_canonical_aggregate_metrics,
    build_segment_components,
    compute_segment_metrics,
)
from ..stats.store import load_game
from .schema import (
    ANALYTICS_ARTIFACT_VERSION,
    INDEX_FILENAME,
    METRICS,
    OUTCOMES,
    SEGMENTS,
    AnalyticsArtifact,
    AnalyticsIndex,
    AnalyticsIndexEntry,
    BoxComponents,
    GameRow,
    SegmentCell,
    TeamAnalytics,
    classify_sample,
)

EXPECTED_TEAMS = 14
EXPECTED_GAMES_PER_TEAM = 26

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
class TeamGameBundle:
    """One team-game: the stored record plus both sides' possessions."""

    stats: TeamGameStats
    team_possessions: list[Possession]
    opponent_possessions: list[Possession]
    regulation_periods: int


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

        built = build_possessions(raw["actions"], regulation_periods=home_stats.regulation_periods)
        by_side: dict[str, list[Possession]] = {"home": [], "away": []}
        for p in built.possessions:
            by_side[p.offense_team].append(p)

        for stats, side, other in ((home_stats, "home", "away"), (away_stats, "away", "home")):
            by_team.setdefault(stats.team_id, []).append(
                TeamGameBundle(
                    stats=stats,
                    team_possessions=by_side[side],
                    opponent_possessions=by_side[other],
                    regulation_periods=stats.regulation_periods,
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
    return SegmentCell(
        segment="full", outcome=outcome,
        games=len(stats_rows), possessions=possessions,
        sample_state=classify_sample(possessions, len(stats_rows)),
        metrics=_present(build_canonical_aggregate_metrics(stats_rows)),
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
            for metric in METRICS:
                values = {tid: c.metrics[metric] for tid, c in eligible.items() if metric in c.metrics}
                if len(values) < 2:
                    continue
                lower_better = DEFAULT_DIRECTIONS.get(metric) == "lower_is_better"
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
    """Build every team's artifact and the index. Raises on a partial cache."""
    by_team, team_names, season = load_league_possessions(settings, stats_dir=stats_dir)
    assert_complete_league(by_team)

    teams = {
        team_id: build_team_analytics(team_id, bundles, team_names.get(team_id, team_id), season)
        for team_id, bundles in sorted(by_team.items())
    }
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
