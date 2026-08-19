"""Synthetic builders for the analytics-artifact tests.

Everything here is fabricated in memory — no cached play-by-play, no stats
directory, no network — so the analytics suite runs on a machine that has only
the repository, exactly like the rest of the offline suite.

Shared rather than per-file because several test modules need the same
possession/bundle shape, and a hand-rolled `Possession` is 20 fields wide.
"""

from __future__ import annotations

from basketball_scout.analytics.build import TeamGameBundle
from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from basketball_scout.stats.possession import Possession


def make_possession(
    *,
    index: int = 0,
    quarter: int = 1,
    offense: str = "home",
    margin: int = 0,
    start_clock_s: float = 400.0,
    points: int = 2,
    fgm: int = 1,
    fga: int = 2,
    fg3m: int = 0,
    fg3a: int = 0,
    fta: int = 0,
    ftm: int = 0,
    orb: int = 0,
    turnover: bool = False,
) -> Possession:
    """One possession whose offense leads by ``margin`` at its start."""
    home_lead = margin if offense == "home" else -margin
    return Possession(
        possession_index=index,
        quarter=quarter,
        offense_team=offense,
        defense_team="away" if offense == "home" else "home",
        start_clock_s=start_clock_s,
        end_clock_s=start_clock_s - 14.0,
        ended_by="turnover" if turnover else "made_fg",
        points=points, fgm=fgm, fga=fga, fg3m=fg3m, fg3a=fg3a,
        ftm=ftm, fta=fta, orb=orb, turnover=turnover,
        score_before_home=100 + max(home_lead, 0) if home_lead >= 0 else 100,
        score_before_away=100 if home_lead >= 0 else 100 + (-home_lead),
        score_after_home=100, score_after_away=100,
    )


def make_components(**overrides) -> TeamGameComponents:
    base = dict(fgm=30, fga=60, fg3m=8, fg3a=20, ftm=15, fta=20,
                orb=10, drb=25, ast=18, tov=12, pf=18, points=83)
    base.update(overrides)
    return TeamGameComponents(**base)


def make_stats(
    *,
    game_id: str = "segev:100",
    team_id: str = "segev:4",
    opponent_id: str = "segev:8",
    game_date: str = "2025-12-06T19:10:00",
    win: bool = True,
    is_home: bool = True,
    score_for: int = 83,
    score_against: int = 75,
) -> TeamGameStats:
    return TeamGameStats(
        internal_game_id=game_id,
        source_provider="segev",
        source_game_id=game_id.split(":")[-1],
        season="2025-26",
        game_date=game_date,
        team_id=team_id,
        team_name="TEST TEAM",
        opponent_id=opponent_id,
        opponent_name="TEST OPPONENT",
        is_home=is_home,
        final_score_for=score_for,
        final_score_against=score_against,
        win=win,
        regulation_periods=4,
        ot_periods=0,
        game_minutes=40.0,
        possessions_for=72.0,
        possessions_against=72.0,
        components_for=make_components(points=score_for),
        components_against=make_components(points=score_against),
        metrics=make_metrics(),
    )


def make_metrics(**overrides) -> DerivedMetrics:
    base = dict(offensive_rating=115.3, defensive_rating=104.2, net_rating=11.1,
                pace=72.0, efg_pct=0.55, tov_pct=0.145, orb_pct=0.31,
                ft_rate=0.33, fg3a_rate=0.36, ast_to_ratio=1.5)
    base.update(overrides)
    return DerivedMetrics(**base)


def make_bundle(
    *,
    team_possessions: list[Possession] | None = None,
    opponent_possessions: list[Possession] | None = None,
    **stats_kwargs,
) -> TeamGameBundle:
    """One team-game. Defaults to a quarter's worth of even, tied possessions."""
    if team_possessions is None:
        team_possessions = [
            make_possession(index=i, quarter=1, offense="home", margin=0) for i in range(10)
        ]
    if opponent_possessions is None:
        opponent_possessions = [
            make_possession(index=i, quarter=1, offense="away", margin=0) for i in range(10)
        ]
    return TeamGameBundle(
        stats=make_stats(**stats_kwargs),
        team_possessions=team_possessions,
        opponent_possessions=opponent_possessions,
        regulation_periods=4,
    )


def make_league(teams: int = 14, games: int = 26) -> dict[str, list[TeamGameBundle]]:
    """A complete synthetic league, sized to pass the completeness guard."""
    return {
        f"segev:{t}": [
            make_bundle(game_id=f"segev:{t}00{g}", team_id=f"segev:{t}", win=(g % 2 == 0))
            for g in range(games)
        ]
        for t in range(2, 2 + teams)
    }
