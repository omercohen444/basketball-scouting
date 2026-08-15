"""Stability profile: descriptive statistics and applicability gates."""

from __future__ import annotations

import pytest

from basketball_scout.stats.stability import build_stability_profile


def test_basic_stats():
    p = build_stability_profile([1.0, 2.0, 3.0, 4.0, 5.0])
    assert p.games == 5
    assert p.mean == 3.0
    assert p.median == 3.0
    assert p.min == 1.0 and p.max == 5.0


def test_std_none_below_two_games():
    p = build_stability_profile([5.0])
    assert p.games == 1
    assert p.std is None  # not fabricated as 0


def test_std_defined_with_two_or_more_games():
    p = build_stability_profile([1.0, 3.0])
    assert p.std is not None


def test_iqr_none_below_four_games():
    p = build_stability_profile([1.0, 2.0, 3.0])
    assert p.iqr is None


def test_iqr_defined_with_four_or_more_games():
    p = build_stability_profile([1.0, 2.0, 3.0, 4.0])
    assert p.iqr is not None


def test_empty_input_yields_all_none_with_zero_games():
    p = build_stability_profile([])
    assert p.games == 0
    assert p.mean is None and p.std is None and p.iqr is None


def test_coefficient_of_variation_none_when_mean_is_zero():
    p = build_stability_profile([-1.0, 1.0])
    assert p.mean == 0.0
    assert p.coefficient_of_variation is None  # division by zero avoided, not fabricated


def test_coefficient_of_variation_defined_for_nonzero_mean():
    p = build_stability_profile([8.0, 10.0, 12.0])
    assert p.coefficient_of_variation == pytest.approx(p.std / p.mean)


def test_two_extreme_games_produce_high_std_relative_to_a_stable_series():
    stable = build_stability_profile([10.0, 10.1, 9.9, 10.0, 10.0])
    volatile = build_stability_profile([10.0, 10.0, 30.0, 10.0, -10.0])
    assert stable.mean == pytest.approx(volatile.mean, abs=0.1)
    assert stable.std < volatile.std  # same-ish mean, very different consistency
