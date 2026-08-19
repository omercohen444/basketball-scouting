"""Synthetic builders for the analytics-artifact tests.

Everything here is fabricated in memory — no cached play-by-play, no stats
directory, no network — so the analytics suite runs on a machine that has only
the repository, exactly like the rest of the offline suite.

Shared rather than per-file because several test modules need the same
possession/bundle shape, and a hand-rolled `Possession` is 20 fields wide.
"""

from __future__ import annotations

from basketball_scout.analytics.build import (
    GameFacts,
    TeamGameBundle,
    build_from_bundles,
    write_all,
)
from basketball_scout.stats.dynamics import GameDynamics
from basketball_scout.stats.runs_droughts import DroughtsProfile, RunsProfile
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
    ended_by: str | None = None,
) -> Possession:
    """One possession whose offense leads by ``margin`` at its start.

    ``ended_by`` matters for rebounding: a team's defensive rebounds are derived
    as the opponent possessions that ended in one, so a fixture whose
    possessions all end in a made basket has no contested defensive glass and no
    defensive rebound rate at all.
    """
    home_lead = margin if offense == "home" else -margin
    return Possession(
        possession_index=index,
        quarter=quarter,
        offense_team=offense,
        defense_team="away" if offense == "home" else "home",
        start_clock_s=start_clock_s,
        end_clock_s=start_clock_s - 14.0,
        ended_by=ended_by or ("turnover" if turnover else "made_fg"),
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


def make_facts(**overrides) -> GameFacts:
    """One team-game's identity facts, plausible and internally consistent.

    Written by hand rather than derived from synthetic actions: the real
    derivation is already covered against real data, and what the artifact tests
    need here is a bundle whose profile block folds to sensible totals.

    The internal consistency matters — ``fga`` equals the zone attempts plus the
    unclassified count, and the zone points imply a believable eFG% — because
    the build asserts several of those relationships.
    """
    base = dict(
        fga=60,
        zone_attempts={"lane_2pt": 26, "midrange_2pt": 8, "corner_3": 6, "atb_3": 20},
        zone_points={"lane_2pt": 30, "midrange_2pt": 6, "corner_3": 9, "atb_3": 21},
        rim_attempts=24,
        unclassified=0,
        opp_fga=58,
        fb_fga=7, fb_fgm=5, fb_points=11,
        fb_fga_allowed=6, fb_fgm_allowed=3, fb_points_allowed=7,
        turnovers_by_type={"bad-pass": 6, "ball-handling": 4, "travelling": 1, "other": 1},
        forced_by_type={"bad-pass": 5, "ball-handling": 4, "travelling": 2},
        points_off_turnovers=16,
        opponent_turnovers=11,
        second_chance_points=10,
        oreb_possessions=11,
        scoring_oreb_possessions=5,
        fast_break_points=11,
        assisted_fgm=18, unassisted_fgm=12,
        assisted_3pm=8, unassisted_3pm=2,
        runs=RunsProfile(largest_scoring_run_for=9, largest_scoring_run_against=8,
                         runs_8_plus_for=1, runs_8_plus_against=1),
        droughts=DroughtsProfile(drought_count_3m_plus=1, longest_scoring_drought_seconds=205.0,
                                 fg_drought_count_3m_plus=2, longest_fg_drought_seconds=260.0),
        dynamics=GameDynamics(times_tied=4, lead_changes=5, largest_lead=12, largest_deficit=6,
                              trailed_by_10_plus=False, led_by_10_plus=True, team_won=True),
    )
    base.update(overrides)
    return GameFacts(**base)


def make_bundle(
    *,
    team_possessions: list[Possession] | None = None,
    opponent_possessions: list[Possession] | None = None,
    facts: GameFacts | None = None,
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
    stats = make_stats(**stats_kwargs)
    if facts is None:
        # The dynamics flags have to agree with the game's own result, or the
        # comeback fold counts a win the team did not have.
        facts = make_facts(
            dynamics=GameDynamics(
                times_tied=4, lead_changes=5, largest_lead=12, largest_deficit=6,
                trailed_by_10_plus=not stats.win, led_by_10_plus=stats.win,
                team_won=stats.win,
            )
        )
    return TeamGameBundle(
        stats=stats,
        team_possessions=team_possessions,
        opponent_possessions=opponent_possessions,
        regulation_periods=4,
        facts=facts,
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


def write_synthetic_analytics(out_dir) -> None:
    """A complete synthetic league written as artifacts, for the web tests.

    Real committed artifacts are never loaded into the web suite — a test about
    routing should not depend on the league's actual numbers — but the routes
    still need *something* structurally valid to render.
    """
    by_team = make_league()
    names = {tid: f"TEAM {tid.split(':')[-1]}" for tid in by_team}
    artifacts, index = build_from_bundles(by_team, names, "2025-26")
    write_all(artifacts, index, out_dir)
