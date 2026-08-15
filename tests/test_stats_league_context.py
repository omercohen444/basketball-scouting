"""League-relative rank/percentile: direction, ties, insufficient-sample handling."""

from __future__ import annotations

from basketball_scout.stats.league_context import build_league_context


def test_higher_is_better_ranks_the_max_value_first():
    values = {"a": 10.0, "b": 20.0, "c": 15.0}
    ctx = build_league_context("b", values, direction="higher_is_better")
    assert ctx.rank == 1
    assert ctx.percentile == 100.0


def test_lower_is_better_ranks_the_min_value_first():
    values = {"a": 10.0, "b": 20.0, "c": 15.0}
    ctx = build_league_context("a", values, direction="lower_is_better")
    assert ctx.rank == 1
    assert ctx.percentile == 100.0


def test_worst_team_gets_rank_n_and_zero_percentile():
    values = {"a": 10.0, "b": 20.0, "c": 15.0}
    ctx = build_league_context("a", values, direction="higher_is_better")
    assert ctx.rank == 3
    assert ctx.percentile == 0.0


def test_ties_share_the_best_rank_and_skip_the_next():
    # a and b tied for best (higher_is_better); c is third distinctly.
    values = {"a": 20.0, "b": 20.0, "c": 10.0}
    ctx_a = build_league_context("a", values, direction="higher_is_better")
    ctx_c = build_league_context("c", values, direction="higher_is_better")
    assert ctx_a.rank == 1
    assert ctx_c.rank == 3  # skips rank 2 -- two teams already hold rank 1


def test_none_values_excluded_not_coerced():
    values = {"a": 10.0, "b": None, "c": 15.0}
    ctx = build_league_context("a", values, direction="higher_is_better")
    assert ctx.eligible_teams == 2  # b excluded, not treated as 0


def test_none_when_team_itself_has_no_value():
    values = {"a": 10.0, "b": 20.0}
    assert build_league_context("missing", values, direction="higher_is_better") is None


def test_none_when_fewer_than_two_eligible_teams():
    values = {"a": 10.0, "b": None}
    assert build_league_context("a", values, direction="higher_is_better") is None


def test_neutral_direction_still_produces_a_percentile():
    values = {"a": 10.0, "b": 20.0}
    ctx = build_league_context("b", values, direction="neutral")
    assert ctx.percentile == 100.0
    assert ctx.direction == "neutral"


def test_league_mean_and_median_computed_over_eligible_values_only():
    values = {"a": 10.0, "b": 20.0, "c": None}
    ctx = build_league_context("a", values, direction="higher_is_better")
    assert ctx.league_mean == 15.0
    assert ctx.league_median == 15.0
