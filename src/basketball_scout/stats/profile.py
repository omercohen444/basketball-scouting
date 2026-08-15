"""The public query interface: raw enrichment data -> structured, JSON-
serializable team profiles a future agent can consume directly.

A future agent never needs to know a possession, a quarter marker, or a
`userTime` field exists — it calls :func:`build_team_profile` and receives
plain dicts/numbers with explicit sample counts everywhere. This module
contains no new arithmetic of its own beyond averaging already-computed
per-game values across a window — exactly the same "one value per game,
then mean" convention the original ``winloss.py`` already uses, applied
consistently to every segment so there is one aggregation rule in the whole
package, not several.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from .enrichment import GameEnrichment
from .models import DerivedMetrics, TeamGameStats
from .winloss import ACTIONABLE, compute_signal_from_pairs, is_agent_rankable, rank_signals

WINDOWS = ("full_season", "last_10", "last_5", "home", "away")

_METRIC_FIELDS = [f.name for f in fields(DerivedMetrics)]

TeamGamePair = tuple[TeamGameStats, GameEnrichment]


def select_window(pairs: list[TeamGamePair], window: str) -> list[TeamGamePair]:
    """Order by actual game date/time (never Segev id), then apply the window."""
    ordered = sorted(pairs, key=lambda pair: pair[0].game_date)
    if window == "full_season":
        return ordered
    if window == "last_10":
        return ordered[-10:]
    if window == "last_5":
        return ordered[-5:]
    if window == "home":
        return [p for p in ordered if p[0].is_home]
    if window == "away":
        return [p for p in ordered if not p[0].is_home]
    raise ValueError(f"unknown window {window!r}; expected one of {WINDOWS}")


def _average_metrics(values: list[DerivedMetrics]) -> DerivedMetrics:
    out = {}
    for field_name in _METRIC_FIELDS:
        vals = [getattr(m, field_name) for m in values if getattr(m, field_name) is not None]
        out[field_name] = (sum(vals) / len(vals)) if vals else None
    return DerivedMetrics(**out)


def _basic_profile(pairs: list[TeamGamePair]) -> dict[str, Any]:
    games_n = len(pairs)
    wins = sum(1 for stats, _ in pairs if stats.win)
    metrics = _average_metrics([stats.metrics for stats, _ in pairs])
    return {
        "games_n": games_n,
        "wins": wins,
        "losses": games_n - wins,
        "win_pct": (wins / games_n) if games_n else None,
        "metrics": metrics.to_dict(),
    }


def _segment_summary(pairs: list[TeamGamePair], seg_type: str, seg_value: str) -> dict[str, Any]:
    key = (seg_type, seg_value)
    used_metrics: list[DerivedMetrics] = []
    sample_totals: dict[str, int] = {}
    games_with_sample = 0
    for _, enrichment in pairs:
        sample = enrichment.segment_samples.get(key)
        metrics = enrichment.segment_metrics.get(key)
        if not sample or sample.get("possessions_n", 0) == 0 or metrics is None:
            continue
        games_with_sample += 1
        used_metrics.append(metrics)
        for k, v in sample.items():
            sample_totals[k] = sample_totals.get(k, 0) + v
    return {
        "segment_type": seg_type,
        "segment_value": seg_value,
        "games_n": games_with_sample,
        "sample": sample_totals,
        "metrics": _average_metrics(used_metrics).to_dict() if used_metrics else DerivedMetrics(
            offensive_rating=None, defensive_rating=None, net_rating=None, pace=None,
            efg_pct=None, tov_pct=None, orb_pct=None, ft_rate=None, fg3a_rate=None, ast_to_ratio=None,
        ).to_dict(),
    }


def _aggregate_scoring_source(pairs: list[TeamGamePair], attr: str, fields_to_sum: list[str]) -> dict[str, Any]:
    games_n = len(pairs)
    totals: dict[str, float] = {f: 0 for f in fields_to_sum}
    for _, enrichment in pairs:
        profile = getattr(enrichment, attr)
        if profile is None:
            continue
        d = profile.to_dict()
        for f in fields_to_sum:
            v = d.get(f)
            if v is not None:
                totals[f] += v
    return {"games_n": games_n, **totals}


def _runs_droughts_summary(pairs: list[TeamGamePair]) -> dict[str, Any]:
    games_n = len(pairs)
    runs = [e.runs for _, e in pairs if e.runs is not None]
    droughts = [e.droughts for _, e in pairs if e.droughts is not None]
    return {
        "games_n": games_n,
        "runs": {
            "average_largest_run_for": (sum(r.largest_scoring_run_for for r in runs) / games_n) if games_n else None,
            "average_largest_run_against": (sum(r.largest_scoring_run_against for r in runs) / games_n) if games_n else None,
            "max_run_for": max((r.largest_scoring_run_for for r in runs), default=0),
            "max_run_against": max((r.largest_scoring_run_against for r in runs), default=0),
            "runs_8_plus_for_per_game": (sum(r.runs_8_plus_for for r in runs) / games_n) if games_n else None,
            "runs_8_plus_against_per_game": (sum(r.runs_8_plus_against for r in runs) / games_n) if games_n else None,
            "games_with_8_plus_run_for": sum(1 for r in runs if r.runs_8_plus_for > 0),
            "games_with_8_plus_run_against": sum(1 for r in runs if r.runs_8_plus_against > 0),
        },
        "droughts": {
            "drought_count_3m_plus_per_game": (sum(d.drought_count_3m_plus for d in droughts) / games_n) if games_n else None,
            "longest_scoring_drought_seconds_max": max((d.longest_scoring_drought_seconds for d in droughts), default=0.0),
            "fg_drought_count_3m_plus_per_game": (sum(d.fg_drought_count_3m_plus for d in droughts) / games_n) if games_n else None,
            "longest_fg_drought_seconds_max": max((d.longest_fg_drought_seconds for d in droughts), default=0.0),
        },
    }


def _dynamics_summary(pairs: list[TeamGamePair]) -> dict[str, Any]:
    from .dynamics import build_comeback_profile

    dyn = [e.dynamics for _, e in pairs if e.dynamics is not None]
    games_n = len(pairs)
    comeback = build_comeback_profile(dyn)
    return {
        "games_n": games_n,
        "times_tied_avg": (sum(d.times_tied for d in dyn) / games_n) if games_n else None,
        "lead_changes_avg": (sum(d.lead_changes for d in dyn) / games_n) if games_n else None,
        "largest_lead_max": max((d.largest_lead for d in dyn), default=0),
        "largest_deficit_max": max((d.largest_deficit for d in dyn), default=0),
        "comeback": comeback.to_dict(),
    }


_SCORING_SOURCE_ATTRS = {
    "points_off_turnovers": ["points_off_turnovers", "opponent_turnovers"],
    "second_chance": ["offensive_rebound_possessions", "second_chance_points",
                      "fga_after_oreb", "multi_oreb_possessions", "additional_orebs",
                      "scoring_oreb_possessions"],
    "fast_break": ["provider_fast_break_points"],
    "assisted": ["assisted_fgm", "unassisted_fgm", "assisted_2pm", "unassisted_2pm",
                 "assisted_3pm", "unassisted_3pm", "total_provider_assists",
                 "resolved_shot_attributed_assists", "unresolved_assist_count"],
}


def build_team_profile(team_id: str, pairs: list[TeamGamePair], *, window: str = "full_season") -> dict[str, Any]:
    """The single entry point: everything a future agent needs for one team/window.

    ``pairs`` must already be filtered to a single team (both
    ``TeamGameStats.team_id`` and ``GameEnrichment.team_id`` for that team's
    own perspective in every game).
    """
    team_ids = {stats.team_id for stats, _ in pairs}
    if len(team_ids) > 1:
        raise ValueError(f"build_team_profile requires a single team_id, got {sorted(team_ids)}")

    window_pairs = select_window(pairs, window)
    team_name = window_pairs[0][0].team_name if window_pairs else None

    profile: dict[str, Any] = {
        "team_id": team_id,
        "team_name": team_name,
        "window": window,
        "definition_version": "enrichment-v1",
        "source": "segev",
        "basic": _basic_profile(window_pairs),
        "game_flow": {
            "quarters": {q: _segment_summary(window_pairs, "quarter", q) for q in ("Q1", "Q2", "Q3", "Q4", "OT")},
            "halves": {h: _segment_summary(window_pairs, "half", h) for h in ("1H", "2H")},
        },
        "clutch": _segment_summary(window_pairs, "clutch", "clutch"),
        "score_state": {
            b: _segment_summary(window_pairs, "score_state", b)
            for b in ("ahead_6_plus", "ahead_1_5", "tied", "behind_1_5", "behind_6_plus")
        },
        "close_score": _segment_summary(window_pairs, "close_score", "close_score"),
        "late_close": _segment_summary(window_pairs, "late_close", "late_close"),
        "scoring_sources": {
            attr: _aggregate_scoring_source(window_pairs, attr, fields_to_sum)
            for attr, fields_to_sum in _SCORING_SOURCE_ATTRS.items()
        },  # "assisted"'s rate is recomputed below from the summed counts
        # Shot/scoring mix: rates only make sense recomputed from summed raw
        # counts, not averaged as independent per-game ratios (a 10-shot
        # game and a 90-shot game should not weigh equally in a season 3PA
        # share) — see _season_shot_mix.
        "shot_mix": _season_shot_mix(window_pairs),
        "runs_droughts": _runs_droughts_summary(window_pairs),
        "dynamics": _dynamics_summary(window_pairs),
    }

    assisted = profile["scoring_sources"]["assisted"]
    total = assisted.get("total_provider_assists") or 0
    unresolved = assisted.get("unresolved_assist_count") or 0
    assisted["unresolved_assist_rate"] = (unresolved / total) if total > 0 else None

    # Rates recomputed from summed season counts, not averaged as
    # independent per-game ratios — same convention as shot_mix/assisted
    # above (a 1-OREB-possession game and a 15-OREB-possession game must
    # not weigh equally in the season conversion rate).
    sc = profile["scoring_sources"]["second_chance"]
    oreb_poss = sc.get("offensive_rebound_possessions") or 0
    sc_points = sc.get("second_chance_points") or 0
    fga_after = sc.get("fga_after_oreb") or 0
    sc["points_per_second_chance_possession"] = (sc_points / oreb_poss) if oreb_poss > 0 else None
    sc["fga_after_oreb_per_oreb_possession"] = (fga_after / oreb_poss) if oreb_poss > 0 else None
    scoring_oreb = sc.get("scoring_oreb_possessions") or 0
    conversion = (scoring_oreb / oreb_poss) if oreb_poss > 0 else None
    sc["second_chance_scoring_conversion"] = conversion
    sc["zero_point_rate_after_oreb"] = (1.0 - conversion) if conversion is not None else None

    return profile


def _season_shot_mix(pairs: list[TeamGamePair]) -> dict[str, Any]:
    fga = fg3a = fg2m = fg3m = ftm = total_points = 0
    for stats, _ in pairs:
        c = stats.components_for
        fga += c.fga
        fg3a += c.fg3a
        fg3m += c.fg3m
        fg2m += c.fgm - c.fg3m
        ftm += c.ftm
        total_points += c.points
    return {
        "games_n": len(pairs),
        "fg2a_share": (fga - fg3a) / fga if fga > 0 else None,
        "fg3a_share": fg3a / fga if fga > 0 else None,
        "scoring_share_2pt": (2 * fg2m / total_points) if total_points > 0 else None,
        "scoring_share_3pt": (3 * fg3m / total_points) if total_points > 0 else None,
        "scoring_share_ft": (ftm / total_points) if total_points > 0 else None,
    }


def build_top_wl_differentiators(
    pairs: list[TeamGamePair], *, top_n: int = 10
) -> dict[str, Any]:
    """Segmented W/L signals (§18) across ``pairs`` (normally full_season).

    Computes actionable-metric signals for the season overall plus every
    precomputed segment, then returns the agent-rankable ones (n_wins>=3,
    n_losses>=3, finite non-zero pooled variance — §18) sorted by
    |effect_size|, capped at ``top_n``. All computed signals are returned
    too (``all_signals``), so a caller can see what was excluded and why.
    """
    all_signals = []

    # Season-level actionable metrics (already covered by rank_actionable_signals,
    # duplicated here with segment_type="season" for a uniform shape).
    for field_name in ("efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio"):
        win_pairs = [(getattr(stats.metrics, field_name), stats.win) for stats, _ in pairs]
        sig = compute_signal_from_pairs(
            f"season:{field_name}", ACTIONABLE, win_pairs, lower_is_better=field_name == "tov_pct"
        )
        all_signals.append(sig)

    segment_keys = (
        [("quarter", q) for q in ("Q1", "Q2", "Q3", "Q4")]
        + [("half", h) for h in ("1H", "2H")]
        + [("clutch", "clutch")]
        + [("score_state", b) for b in ("ahead_6_plus", "ahead_1_5", "tied", "behind_1_5", "behind_6_plus")]
    )
    for seg_type, seg_value in segment_keys:
        for field_name in ("efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio"):
            value_win_pairs = []
            for stats, enrichment in pairs:
                metrics = enrichment.segment_metrics.get((seg_type, seg_value))
                sample = enrichment.segment_samples.get((seg_type, seg_value))
                if metrics is None or not sample or sample.get("possessions_n", 0) == 0:
                    continue
                value_win_pairs.append((getattr(metrics, field_name), stats.win))
            if not value_win_pairs:
                continue
            sig = compute_signal_from_pairs(
                f"{seg_type}:{seg_value}:{field_name}", ACTIONABLE, value_win_pairs,
                lower_is_better=field_name == "tov_pct",
            )
            all_signals.append(sig)

    # Points off turnovers as an example scoring-source W/L signal.
    pot_pairs = [
        (e.points_off_turnovers.points_off_turnovers if e.points_off_turnovers else None, stats.win)
        for stats, e in pairs
    ]
    all_signals.append(compute_signal_from_pairs("scoring_source:points_off_turnovers", ACTIONABLE, pot_pairs))

    rankable = [s for s in all_signals if is_agent_rankable(s)]
    ranked = rank_signals(rankable)[:top_n]

    return {
        "top_ranked": [s.to_dict() for s in ranked],
        "all_signals": [s.to_dict() for s in all_signals],
    }
