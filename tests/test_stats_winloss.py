"""Win-vs-loss signal computation: averages, sample counts, standardized
effect size, and actionable-factor ranking.

Rewritten 2026-08-15 (management review): raw |difference| ranking was
replaced with a standardized effect size (pooled-sd normalized), and the
default scouting ranking is restricted to ACTIONABLE metrics so Net Rating
(near-tautologically different between wins and losses) doesn't dominate.
"""

from __future__ import annotations

import math

import pytest

from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from basketball_scout.stats.winloss import (
    ACTIONABLE,
    MIN_SUFFICIENT_SAMPLE,
    OUTCOME_CONTEXT,
    compute_signals,
    games_for_team,
    rank_actionable_signals,
    rank_signals,
)

EMPTY_COMPONENTS = TeamGameComponents(
    fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0, ast=0, tov=0, pf=0, points=0
)

_counter = iter(range(1_000_000))


def make_game(
    team_id: str, win: bool, *,
    off_rtg=110.0, def_rtg=100.0, pace=90.0,
    efg=0.5, tov_pct=0.13, orb_pct=0.28, ft_rate=0.25, fg3a_rate=0.35, ast_to=1.5,
) -> TeamGameStats:
    metrics = DerivedMetrics(
        offensive_rating=off_rtg, defensive_rating=def_rtg, net_rating=off_rtg - def_rtg,
        pace=pace, efg_pct=efg, tov_pct=tov_pct, orb_pct=orb_pct, ft_rate=ft_rate,
        fg3a_rate=fg3a_rate, ast_to_ratio=ast_to,
    )
    return TeamGameStats(
        internal_game_id=f"segev:{next(_counter)}", source_provider="segev", source_game_id="1",
        season="2025-26", game_date="2026-01-01", team_id=team_id, team_name="T",
        opponent_id="segev:opp", opponent_name="OPP", is_home=True,
        final_score_for=90, final_score_against=80, win=win,
        regulation_periods=4, ot_periods=0, game_minutes=40.0,
        possessions_for=90.0, possessions_against=90.0,
        components_for=EMPTY_COMPONENTS, components_against=EMPTY_COMPONENTS,
        metrics=metrics,
    )


# ---- basic averages / raw difference (unchanged contract) ----------------

def test_games_for_team_filters_by_team_id():
    games = [make_game("segev:2", True), make_game("segev:4", False)]
    filtered = games_for_team(games, "segev:2")
    assert len(filtered) == 1
    assert filtered[0].team_id == "segev:2"


def test_compute_signals_rejects_mixed_teams():
    games = [make_game("segev:2", True), make_game("segev:4", False)]
    with pytest.raises(ValueError, match="single team_id"):
        compute_signals(games)


def test_win_and_loss_averages_and_raw_difference():
    games = [
        make_game("segev:2", True, off_rtg=120),
        make_game("segev:2", True, off_rtg=110),
        make_game("segev:2", False, off_rtg=90),
    ]
    signals = compute_signals(games)
    off_rtg_signal = next(s for s in signals if s.metric == "offensive_rating")
    assert off_rtg_signal.win_average == pytest.approx(115.0)
    assert off_rtg_signal.loss_average == pytest.approx(90.0)
    assert off_rtg_signal.difference == pytest.approx(25.0)
    assert off_rtg_signal.sample_wins == 2
    assert off_rtg_signal.sample_losses == 1
    assert off_rtg_signal.favorable_in_wins is True


def test_compute_signals_returns_all_ten_with_correct_categories():
    games = [make_game("segev:2", True), make_game("segev:2", False)]
    signals = compute_signals(games)
    assert len(signals) == 10
    outcome = {s.metric for s in signals if s.category == OUTCOME_CONTEXT}
    actionable = {s.metric for s in signals if s.category == ACTIONABLE}
    assert outcome == {"offensive_rating", "defensive_rating", "net_rating", "pace"}
    assert actionable == {"efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio"}


def test_lower_is_better_metric_flips_favorable_direction():
    games = [
        make_game("segev:2", True, off_rtg=110, def_rtg=90),
        make_game("segev:2", False, off_rtg=100, def_rtg=110),
    ]
    signals = compute_signals(games)
    def_rtg_signal = next(s for s in signals if s.metric == "defensive_rating")
    assert def_rtg_signal.difference == pytest.approx(-20.0)
    assert def_rtg_signal.favorable_in_wins is True


def test_no_losses_yields_none_loss_average_and_none_difference():
    games = [make_game("segev:2", True)]
    signals = compute_signals(games)
    for s in signals:
        assert s.loss_average is None
        assert s.difference is None
        assert s.favorable_in_wins is None
        assert s.effect_size is None
        assert s.effect_note == "insufficient_sample_for_variance"


def test_sample_sufficient_flag_respects_threshold():
    games = [make_game("segev:2", True) for _ in range(MIN_SUFFICIENT_SAMPLE - 1)]
    games += [make_game("segev:2", False) for _ in range(MIN_SUFFICIENT_SAMPLE)]
    signals = compute_signals(games)
    assert all(not s.sample_sufficient for s in signals)


# ---- standardized effect size ---------------------------------------------

def test_effect_size_matches_hand_computed_pooled_sd():
    # win eFG%: 0.50, 0.60, 0.55 (mean 0.55, var = ((.05)^2+(.05)^2+0^2)/2 = 0.0025)
    # loss eFG%: 0.40, 0.45       (mean 0.425, var = ((.025)^2*2)/1 = 0.00125)
    games = [
        make_game("segev:2", True, efg=0.50),
        make_game("segev:2", True, efg=0.60),
        make_game("segev:2", True, efg=0.55),
        make_game("segev:2", False, efg=0.40),
        make_game("segev:2", False, efg=0.45),
    ]
    signals = compute_signals(games)
    efg_signal = next(s for s in signals if s.metric == "efg_pct")

    win_vals = [0.50, 0.60, 0.55]
    loss_vals = [0.40, 0.45]
    n1, n2 = len(win_vals), len(loss_vals)
    mean1, mean2 = sum(win_vals) / n1, sum(loss_vals) / n2
    var1 = sum((v - mean1) ** 2 for v in win_vals) / (n1 - 1)
    var2 = sum((v - mean2) ** 2 for v in loss_vals) / (n2 - 1)
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    expected_pooled_sd = math.sqrt(pooled_var)
    expected_effect = (mean1 - mean2) / expected_pooled_sd

    assert efg_signal.pooled_std == pytest.approx(expected_pooled_sd)
    assert efg_signal.effect_size == pytest.approx(expected_effect)
    assert efg_signal.effect_note is None


def test_effect_size_preserves_sign_for_lower_is_better_metric():
    # defensive_rating LOWER in wins -> difference negative -> effect negative,
    # sign must be preserved (not silently flipped to look "positive/good").
    games = [
        make_game("segev:2", True, def_rtg=90), make_game("segev:2", True, def_rtg=92),
        make_game("segev:2", False, def_rtg=110), make_game("segev:2", False, def_rtg=112),
    ]
    signals = compute_signals(games)
    def_rtg_signal = next(s for s in signals if s.metric == "defensive_rating")
    assert def_rtg_signal.difference < 0
    assert def_rtg_signal.effect_size < 0


def test_effect_size_none_when_either_group_has_fewer_than_two_samples():
    games = [
        make_game("segev:2", True, efg=0.50),  # only 1 win
        make_game("segev:2", False, efg=0.40),
        make_game("segev:2", False, efg=0.42),
    ]
    signals = compute_signals(games)
    efg_signal = next(s for s in signals if s.metric == "efg_pct")
    assert efg_signal.difference is not None  # raw difference IS still computable
    assert efg_signal.effect_size is None
    assert efg_signal.pooled_std is None
    assert efg_signal.effect_note == "insufficient_sample_for_variance"


def test_effect_size_none_when_pooled_variance_is_zero():
    # Every win and every loss has the exact same eFG% -> zero variance in
    # both groups -> pooled_sd is 0 -> effect must be None, not inf/nan.
    games = [
        make_game("segev:2", True, efg=0.50), make_game("segev:2", True, efg=0.50),
        make_game("segev:2", False, efg=0.40), make_game("segev:2", False, efg=0.40),
    ]
    signals = compute_signals(games)
    efg_signal = next(s for s in signals if s.metric == "efg_pct")
    assert efg_signal.difference == pytest.approx(0.10)
    assert efg_signal.pooled_std == 0.0
    assert efg_signal.effect_size is None
    assert efg_signal.effect_note == "zero_pooled_variance"


def test_effect_size_zero_is_a_real_value_not_none():
    games = [
        make_game("segev:2", True, efg=0.50), make_game("segev:2", True, efg=0.60),
        make_game("segev:2", False, efg=0.50), make_game("segev:2", False, efg=0.60),
    ]
    signals = compute_signals(games)
    efg_signal = next(s for s in signals if s.metric == "efg_pct")
    assert efg_signal.difference == pytest.approx(0.0)
    assert efg_signal.effect_size == pytest.approx(0.0)
    assert efg_signal.effect_note is None


# ---- ranking ---------------------------------------------------------------

def test_rank_signals_sorts_by_absolute_effect_descending_and_keeps_sign():
    games = [
        make_game("segev:2", True, efg=0.60, tov_pct=0.12),
        make_game("segev:2", True, efg=0.58, tov_pct=0.11),
        make_game("segev:2", False, efg=0.40, tov_pct=0.30),
        make_game("segev:2", False, efg=0.39, tov_pct=0.29),
    ]
    signals = compute_signals(games)
    ranked = rank_signals([s for s in signals if s.category == ACTIONABLE])
    effects = [abs(s.effect_size) if s.effect_size is not None else -1 for s in ranked]
    assert effects == sorted(effects, reverse=True)
    tov_signal = next(s for s in ranked if s.metric == "tov_pct")
    assert tov_signal.effect_size < 0  # lower TOV% in wins -> negative raw diff, sign preserved


def test_rank_actionable_signals_excludes_outcome_context_metrics():
    games = [make_game("segev:2", True) for _ in range(6)] + [make_game("segev:2", False) for _ in range(6)]
    ranked = rank_actionable_signals(games)
    assert {s.metric for s in ranked} == {"efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio"}
    assert "net_rating" not in {s.metric for s in ranked}


def test_effect_size_ranking_differs_from_raw_difference_ranking():
    """The whole point of the redesign: a metric with a small raw difference
    but very low within-group variance can outrank one with a larger raw
    difference but noisy values — impossible under pure |difference| ranking.
    """
    games = [
        # ft_rate: small, extremely consistent difference (0.30 vs 0.28, tight)
        make_game("segev:2", True, ft_rate=0.301, fg3a_rate=0.10),
        make_game("segev:2", True, ft_rate=0.300, fg3a_rate=0.80),
        make_game("segev:2", True, ft_rate=0.299, fg3a_rate=0.05),
        make_game("segev:2", False, ft_rate=0.281, fg3a_rate=0.75),
        make_game("segev:2", False, ft_rate=0.280, fg3a_rate=0.15),
        make_game("segev:2", False, ft_rate=0.279, fg3a_rate=0.60),
    ]
    signals = compute_signals(games)
    ft_rate = next(s for s in signals if s.metric == "ft_rate")
    fg3a_rate = next(s for s in signals if s.metric == "fg3a_rate")

    # Raw |difference| ranking would put fg3a_rate first (bigger raw gap
    # given its wide spread) -- but its huge within-group noise collapses
    # its standardized effect, while ft_rate's tiny but rock-solid gap
    # produces a large standardized effect.
    assert abs(fg3a_rate.difference) > abs(ft_rate.difference)
    ranked = rank_actionable_signals(games)
    ranked_by_effect = [s.metric for s in ranked]
    raw_ranked = sorted(
        [s for s in signals if s.category == ACTIONABLE],
        key=lambda s: abs(s.difference) if s.difference is not None else -1,
        reverse=True,
    )
    raw_ranked_metrics = [s.metric for s in raw_ranked]
    assert ranked_by_effect != raw_ranked_metrics
    assert ft_rate.effect_size is not None and abs(ft_rate.effect_size) > 1.0
    assert fg3a_rate.effect_size is not None and abs(fg3a_rate.effect_size) < abs(ft_rate.effect_size)


# ---- metric categorisation ---------------------------------------------------


def test_category_for_separates_outcome_context_from_actionable():
    """A win/loss comparison of ORtg/DRtg/Net/Pace is near-tautological: a team
    does outscore its opponents in the games it wins. Ranking those alongside
    genuine descriptive factors made "the biggest difference" say nothing.
    """
    from basketball_scout.stats.winloss import ACTIONABLE, OUTCOME_CONTEXT, category_for

    for metric in ("offensive_rating", "defensive_rating", "net_rating", "pace"):
        assert category_for(metric) == OUTCOME_CONTEXT, metric

    for metric in ("efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio"):
        assert category_for(metric) == ACTIONABLE, metric


def test_category_for_accepts_a_segment_qualified_signal_name():
    """Callers build ids as f"{segment_type}:{segment_value}:{metric}", so the
    lookup has to survive the prefix rather than silently defaulting."""
    from basketball_scout.stats.winloss import ACTIONABLE, OUTCOME_CONTEXT, category_for

    assert category_for("clutch:clutch:efg_pct") == ACTIONABLE
    assert category_for("season:season:net_rating") == OUTCOME_CONTEXT
    assert category_for("quarter:Q4:net_rating") == OUTCOME_CONTEXT


def test_an_unknown_metric_defaults_to_actionable():
    """Unknown names are far more likely to be descriptive factors than one of
    the four fixed outcome measures, and this preserves prior behaviour rather
    than introducing a new failure mode."""
    from basketball_scout.stats.winloss import ACTIONABLE, category_for

    assert category_for("second_chance_points") == ACTIONABLE
    assert category_for("something:brand_new") == ACTIONABLE
