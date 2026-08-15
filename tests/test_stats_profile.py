"""profile.py: window selection (chronological, not by game id), aggregation."""

from __future__ import annotations

from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from basketball_scout.stats.profile import select_window
from basketball_scout.stats.winloss import ACTIONABLE, is_agent_rankable
from basketball_scout.stats.winloss import compute_signal_from_pairs

EMPTY_COMPONENTS = TeamGameComponents(
    fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0, ast=0, tov=0, pf=0, points=0
)
EMPTY_METRICS = DerivedMetrics(
    offensive_rating=100.0, defensive_rating=100.0, net_rating=0.0, pace=90.0,
    efg_pct=0.5, tov_pct=0.13, orb_pct=0.28, ft_rate=0.25, fg3a_rate=0.35, ast_to_ratio=1.5,
)


def make_stats(source_game_id: str, game_date: str, is_home: bool = True) -> TeamGameStats:
    return TeamGameStats(
        internal_game_id=f"segev:{source_game_id}", source_provider="segev", source_game_id=source_game_id,
        season="2025-26", game_date=game_date, team_id="segev:2", team_name="T",
        opponent_id="segev:opp", opponent_name="OPP", is_home=is_home,
        final_score_for=90, final_score_against=80, win=True, regulation_periods=4, ot_periods=0,
        game_minutes=40.0, possessions_for=90.0, possessions_against=90.0,
        components_for=EMPTY_COMPONENTS, components_against=EMPTY_COMPONENTS, metrics=EMPTY_METRICS,
    )


def test_window_orders_by_game_date_not_source_game_id():
    # Deliberately out-of-id-order dates: game_id 200 played BEFORE game_id 50.
    stats_a = make_stats("200", "2025-10-01")
    stats_b = make_stats("50", "2025-12-01")
    pairs = [(stats_b, None), (stats_a, None)]  # inserted in id order, not date order
    ordered = select_window(pairs, "full_season")
    assert [p[0].source_game_id for p in ordered] == ["200", "50"]  # chronological


def test_last_5_and_last_10_take_the_most_recent():
    pairs = [(make_stats(str(i), f"2025-10-{i:02d}"), None) for i in range(1, 16)]
    last5 = select_window(pairs, "last_5")
    last10 = select_window(pairs, "last_10")
    assert [p[0].source_game_id for p in last5] == [str(i) for i in range(11, 16)]
    assert len(last10) == 10
    assert last10[-1][0].source_game_id == "15"


def test_last_5_returns_available_sample_when_fewer_games_exist():
    pairs = [(make_stats(str(i), f"2025-10-{i:02d}"), None) for i in range(1, 4)]
    last5 = select_window(pairs, "last_5")
    assert len(last5) == 3  # not fabricated up to 5


def test_home_away_window_filters_correctly():
    pairs = [
        (make_stats("1", "2025-10-01", is_home=True), None),
        (make_stats("2", "2025-10-02", is_home=False), None),
        (make_stats("3", "2025-10-03", is_home=True), None),
    ]
    home = select_window(pairs, "home")
    away = select_window(pairs, "away")
    assert {p[0].source_game_id for p in home} == {"1", "3"}
    assert {p[0].source_game_id for p in away} == {"2"}


def test_unknown_window_raises():
    import pytest
    with pytest.raises(ValueError):
        select_window([], "not_a_real_window")


# ---- Generic segmented W/L signal + agent-rankable threshold ---------------

def test_compute_signal_from_pairs_drops_none_values():
    pairs = [(0.5, True), (None, True), (0.3, False), (None, False)]
    sig = compute_signal_from_pairs("test_metric", ACTIONABLE, pairs)
    assert sig.sample_wins == 1
    assert sig.sample_losses == 1


def test_agent_rankable_requires_min_3_and_3_and_defined_effect():
    pairs_ok = [(0.5 + i * 0.01, True) for i in range(4)] + [(0.3 + i * 0.01, False) for i in range(4)]
    sig_ok = compute_signal_from_pairs("m", ACTIONABLE, pairs_ok)
    assert is_agent_rankable(sig_ok) is True

    pairs_too_few = [(0.5, True), (0.5, True), (0.3, False), (0.3, False)]  # only 2 wins
    sig_few = compute_signal_from_pairs("m", ACTIONABLE, pairs_too_few)
    assert is_agent_rankable(sig_few) is False
