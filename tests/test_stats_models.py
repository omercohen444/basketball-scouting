"""TeamGameStats / component dataclass to_dict/from_dict round-trips.

The on-disk JSON shape is a tracked contract (store.py persists it directly),
so a round-trip test earns its keep the same way test_manifest.py's does.
"""

from __future__ import annotations

from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats


def make_components(**overrides) -> TeamGameComponents:
    base = dict(fgm=30, fga=70, fg3m=8, fg3a=25, ftm=15, fta=20,
                orb=10, drb=30, ast=18, tov=12, pf=16, points=83)
    base.update(overrides)
    return TeamGameComponents(**base)


def make_metrics(**overrides) -> DerivedMetrics:
    base = dict(offensive_rating=110.0, defensive_rating=100.0, net_rating=10.0,
                pace=88.0, efg_pct=0.5, tov_pct=0.13, orb_pct=0.28,
                ft_rate=0.28, fg3a_rate=0.35, ast_to_ratio=1.5)
    base.update(overrides)
    return DerivedMetrics(**base)


def make_stats(**overrides) -> TeamGameStats:
    base = dict(
        internal_game_id="segev:136", source_provider="segev", source_game_id="136",
        season="2025-26", game_date="2026-01-11T21:05:00",
        team_id="segev:2", team_name="MACCABI TEL AVIV",
        opponent_id="segev:4", opponent_name="HAPOEL JERUSALEM",
        is_home=True, final_score_for=95, final_score_against=84, win=True,
        regulation_periods=4, ot_periods=0, game_minutes=40.0,
        possessions_for=86.3, possessions_against=85.9,
        components_for=make_components(), components_against=make_components(points=84),
        metrics=make_metrics(), action_counts={"shot": 140, "rebound": 82},
    )
    base.update(overrides)
    return TeamGameStats(**base)


def test_components_round_trip():
    c = make_components()
    assert TeamGameComponents.from_dict(c.to_dict()) == c


def test_metrics_round_trip():
    m = make_metrics()
    assert DerivedMetrics.from_dict(m.to_dict()) == m


def test_team_game_stats_round_trip():
    s = make_stats()
    restored = TeamGameStats.from_dict(s.to_dict())
    assert restored == s


def test_derived_fields_on_components():
    c = make_components(fgm=30, fg3m=8, orb=10, drb=30)
    assert c.fg2m == 22
    assert c.fg2a == 45
    assert c.trb == 40
