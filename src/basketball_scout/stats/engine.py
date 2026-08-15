"""Orchestration: one raw Segev ``getActions`` result -> two TeamGameStats.

The only module that wires together source extraction (``boxscore.py``), the
data model (``models.py``) and the formulas (``formulas.py``) into complete
team-game records. No arithmetic happens here that isn't already in
``formulas.py`` — this module's job is assembly, identity and provenance.
"""

from __future__ import annotations

from typing import Any

from .boxscore import build_components
from .formulas import (
    REGULATION_PERIODS,
    ast_to_ratio,
    defensive_rating,
    effective_fg_pct,
    estimate_possessions,
    free_throw_rate,
    game_minutes,
    net_rating,
    off_reb_pct,
    offensive_rating,
    pace,
    three_point_rate,
    turnover_pct,
)
from .models import DerivedMetrics, TeamGameComponents, TeamGameStats


class EngineError(ValueError):
    """Raised when a raw source result cannot be turned into team-game stats."""


def _derive_metrics(
    components: TeamGameComponents,
    opponent: TeamGameComponents,
    possessions_for: float,
    possessions_against: float,
    minutes: float,
) -> DerivedMetrics:
    off_rtg = offensive_rating(components.points, possessions_for)
    def_rtg = defensive_rating(opponent.points, possessions_against)
    return DerivedMetrics(
        offensive_rating=off_rtg,
        defensive_rating=def_rtg,
        net_rating=net_rating(off_rtg, def_rtg),
        pace=pace(possessions_for, possessions_against, minutes),
        efg_pct=effective_fg_pct(components.fgm, components.fg3m, components.fga),
        tov_pct=turnover_pct(components.tov, components.fga, components.fta),
        orb_pct=off_reb_pct(components.orb, opponent.drb),
        ft_rate=free_throw_rate(components.fta, components.fga),
        fg3a_rate=three_point_rate(components.fg3a, components.fga),
        ast_to_ratio=ast_to_ratio(components.ast, components.tov),
    )


def _quarters_to_periods(actions: list[dict[str, Any]], regulation: int) -> int:
    """Overtime periods = highest observed quarter number beyond regulation.

    Assumes FIBA-style sequential OT quarter numbering (5, 6, ...) continuing
    from regulation — the only numbering observed in real Segev data so far.
    A game with no PBP quarter markers at all reports 0 OT periods.
    """
    quarters = [a.get("quarter") for a in actions if isinstance(a.get("quarter"), int)]
    if not quarters:
        return 0
    return max(0, max(quarters) - regulation)


def build_team_game_stats(
    result: dict[str, Any],
    *,
    season: str,
    source_provider: str = "segev",
) -> tuple[TeamGameStats, TeamGameStats]:
    """Build ``(home, away)`` TeamGameStats from one raw Segev ``getActions`` result.

    ``result`` is the dict shape returned by
    ``pbp.segev.fetch_actions``/``fetch_and_cache_actions`` (i.e. it must
    already contain ``gameInfo`` and ``actions`` — this function does not
    fetch anything itself, so it works identically on live or cached data).
    """
    game_info = result.get("gameInfo")
    actions = result.get("actions")
    if not isinstance(game_info, dict) or not isinstance(actions, list):
        raise EngineError(
            "result missing 'gameInfo'/'actions' — fetch getActions before building stats"
        )

    home_team = game_info.get("homeTeam") or {}
    away_team = game_info.get("awayTeam") or {}
    source_game_id = str(game_info.get("gameId") or game_info.get("id") or "")
    if not source_game_id:
        raise EngineError("gameInfo has no usable game id")

    regulation = int(game_info.get("numberOfQuarters") or REGULATION_PERIODS)
    ot_periods = _quarters_to_periods(actions, regulation)
    minutes = game_minutes(regulation, ot_periods)

    home_components, away_components, action_counts = build_components(actions)

    if home_components.points == away_components.points:
        # A tie is impossible in a finished basketball game (FIBA plays OT
        # until decided). Equal aggregated points means the PBP is
        # incomplete or malformed — surfacing that loudly beats silently
        # picking a winner or fabricating a result.
        raise EngineError(
            f"game {source_game_id}: aggregated score tied "
            f"{home_components.points}-{away_components.points} — "
            "PBP is likely incomplete for this game"
        )

    home_win = home_components.points > away_components.points
    home_poss = estimate_possessions(home_components, away_components)
    away_poss = estimate_possessions(away_components, home_components)

    internal_game_id = f"{source_provider}:{source_game_id}"
    game_date = str(game_info.get("time") or "")

    home_stats = TeamGameStats(
        internal_game_id=internal_game_id,
        source_provider=source_provider,
        source_game_id=source_game_id,
        season=season,
        game_date=game_date,
        team_id=f"{source_provider}:{home_team.get('id')}",
        team_name=str(home_team.get("name") or ""),
        opponent_id=f"{source_provider}:{away_team.get('id')}",
        opponent_name=str(away_team.get("name") or ""),
        is_home=True,
        final_score_for=home_components.points,
        final_score_against=away_components.points,
        win=home_win,
        regulation_periods=regulation,
        ot_periods=ot_periods,
        game_minutes=minutes,
        possessions_for=home_poss,
        possessions_against=away_poss,
        components_for=home_components,
        components_against=away_components,
        metrics=_derive_metrics(home_components, away_components, home_poss, away_poss, minutes),
        action_counts=action_counts,
    )
    away_stats = TeamGameStats(
        internal_game_id=internal_game_id,
        source_provider=source_provider,
        source_game_id=source_game_id,
        season=season,
        game_date=game_date,
        team_id=f"{source_provider}:{away_team.get('id')}",
        team_name=str(away_team.get("name") or ""),
        opponent_id=f"{source_provider}:{home_team.get('id')}",
        opponent_name=str(home_team.get("name") or ""),
        is_home=False,
        final_score_for=away_components.points,
        final_score_against=home_components.points,
        win=not home_win,
        regulation_periods=regulation,
        ot_periods=ot_periods,
        game_minutes=minutes,
        possessions_for=away_poss,
        possessions_against=home_poss,
        components_for=away_components,
        components_against=home_components,
        metrics=_derive_metrics(away_components, home_components, away_poss, home_poss, minutes),
        action_counts=action_counts,
    )
    return home_stats, away_stats
