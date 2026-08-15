"""Evidence object assembly and candidate signal flags."""

from __future__ import annotations

import pytest

from basketball_scout.stats.evidence import (
    LARGE_EFFECT_SIZE,
    LEAGUE_EXTREME_PERCENTILE,
    build_evidence,
    build_recent_delta,
    compute_signal_flags,
)
from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats

EMPTY_COMPONENTS = TeamGameComponents(
    fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0, ast=0, tov=0, pf=0, points=0
)
_counter = iter(range(10_000))


def make_stats(team_id: str, win: bool, value: float, date: str) -> TeamGameStats:
    metrics = DerivedMetrics(
        offensive_rating=100.0, defensive_rating=100.0, net_rating=0.0, pace=90.0,
        efg_pct=value, tov_pct=0.13, orb_pct=0.28, ft_rate=0.25, fg3a_rate=0.35, ast_to_ratio=1.5,
    )
    return TeamGameStats(
        internal_game_id=f"segev:{next(_counter)}", source_provider="segev", source_game_id="1",
        season="2025-26", game_date=date, team_id=team_id, team_name="T",
        opponent_id="segev:opp", opponent_name="OPP", is_home=True,
        final_score_for=90, final_score_against=80, win=win, regulation_periods=4, ot_periods=0,
        game_minutes=40.0, possessions_for=90.0, possessions_against=90.0,
        components_for=EMPTY_COMPONENTS, components_against=EMPTY_COMPONENTS, metrics=metrics,
    )


def efg_extractor(stats, enrichment):
    return stats.metrics.efg_pct


def make_stats_with_shots(team_id: str, win: bool, fgm: int, fga: int, date: str) -> TeamGameStats:
    """Like make_stats, but with REAL components_for so
    build_canonical_aggregate_metrics has real numerator/denominator data
    to sum, not the EMPTY_COMPONENTS placeholder."""
    efg = fgm / fga
    components = TeamGameComponents(
        fgm=fgm, fga=fga, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0, ast=0, tov=1, pf=0, points=fgm * 2,
    )
    metrics = DerivedMetrics(
        offensive_rating=100.0, defensive_rating=100.0, net_rating=0.0, pace=90.0,
        efg_pct=efg, tov_pct=0.13, orb_pct=0.28, ft_rate=0.25, fg3a_rate=0.35, ast_to_ratio=1.5,
    )
    return TeamGameStats(
        internal_game_id=f"segev:{next(_counter)}", source_provider="segev", source_game_id="1",
        season="2025-26", game_date=date, team_id=team_id, team_name="T",
        opponent_id="segev:opp", opponent_name="OPP", is_home=True,
        final_score_for=90, final_score_against=80, win=win, regulation_periods=4, ot_periods=0,
        game_minutes=40.0, possessions_for=90.0, possessions_against=90.0,
        components_for=components, components_against=EMPTY_COMPONENTS, metrics=metrics,
    )


def test_recent_delta_basic_arithmetic():
    pairs = [(make_stats("t", True, 0.5 + i * 0.01, f"2025-10-{i+1:02d}"), None) for i in range(15)]
    delta = build_recent_delta(pairs, efg_extractor)
    assert delta.sample_season == 15
    assert delta.sample_last_10 == 10
    assert delta.sample_last_5 == 5
    assert delta.last5_minus_season == pytest.approx(delta.last_5_value - delta.season_value)
    assert delta.last10_minus_season == pytest.approx(delta.last_10_value - delta.season_value)
    assert delta.last5_minus_last10 == pytest.approx(delta.last_5_value - delta.last_10_value)


def test_recent_delta_none_when_no_games():
    delta = build_recent_delta([], efg_extractor)
    assert delta.season_value is None
    assert delta.last5_minus_season is None
    assert delta.sample_season == 0


def test_build_evidence_produces_league_context_and_win_loss():
    league_pairs = {
        "t1": [(make_stats("t1", True, 0.60, "2025-10-01"), None)] * 5
              + [(make_stats("t1", False, 0.40, "2025-10-02"), None)] * 5,
        "t2": [(make_stats("t2", True, 0.50, "2025-10-01"), None)] * 10,
    }
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better")
    assert ev.sample_games == 10
    assert ev.league_context is not None
    assert ev.win_loss.sample_wins == 5 and ev.win_loss.sample_losses == 5


def test_evidence_json_serializable():
    import json
    league_pairs = {"t1": [(make_stats("t1", True, 0.5, "2025-10-01"), None)] * 3,
                     "t2": [(make_stats("t2", True, 0.4, "2025-10-01"), None)] * 3}
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs)
    json.dumps(ev.to_dict())  # must not raise


# ---- Signal flags -----------------------------------------------------

def test_league_extreme_flag_true_at_top_percentile():
    league_pairs = {f"t{i}": [(make_stats(f"t{i}", True, 0.3 + i * 0.02, "2025-10-01"), None)] * 3 for i in range(10)}
    ev = build_evidence("t9", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better")
    flags = compute_signal_flags(ev)
    assert ev.league_context.percentile == 100.0
    assert flags.league_extreme is True


def test_league_extreme_flag_false_in_the_middle():
    league_pairs = {f"t{i}": [(make_stats(f"t{i}", True, 0.3 + i * 0.02, "2025-10-01"), None)] * 3 for i in range(10)}
    ev = build_evidence("t4", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better")
    flags = compute_signal_flags(ev)
    assert 10.0 < ev.league_context.percentile < 90.0
    assert flags.league_extreme is False


def test_league_extreme_is_two_tailed_low_end_also_flags():
    """2026-08-15 audit: being unusually LOW must flag exactly like being
    unusually HIGH — league_extreme is about statistical extremeness in
    either direction, not "bad performance"."""
    league_pairs = {f"t{i}": [(make_stats(f"t{i}", True, 0.3 + i * 0.02, "2025-10-01"), None)] * 3 for i in range(10)}
    ev = build_evidence("t0", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better")
    flags = compute_signal_flags(ev)
    assert ev.league_context.percentile == 0.0  # worst by value
    assert flags.league_extreme is True  # still flagged -- extreme, not "good"


def test_league_extreme_has_no_good_bad_semantics_for_neutral_metrics():
    """A neutral-direction metric (e.g. Pace) must flag extremeness at
    BOTH tails identically, with no directional bias baked into the flag
    itself — extreme != good, extreme != bad."""
    league_pairs = {f"t{i}": [(make_stats(f"t{i}", True, 0.3 + i * 0.02, "2025-10-01"), None)] * 3 for i in range(10)}
    ev_low = build_evidence("t0", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                             direction="neutral")
    ev_high = build_evidence("t9", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                              direction="neutral")
    flags_low = compute_signal_flags(ev_low)
    flags_high = compute_signal_flags(ev_high)
    assert flags_low.league_extreme is True
    assert flags_high.league_extreme is True
    # Both tails are flagged the same way -- the flag carries no
    # "good"/"bad" judgment, only "direction" on the LeagueContext does.
    assert ev_low.league_context.direction == "neutral"
    assert ev_high.league_context.direction == "neutral"


def test_win_loss_signal_none_when_not_agent_rankable():
    league_pairs = {
        "t1": [(make_stats("t1", True, 0.5, "2025-10-01"), None)] * 2
              + [(make_stats("t1", False, 0.4, "2025-10-02"), None)] * 5,  # only 2 wins < 3
        "t2": [(make_stats("t2", True, 0.4, "2025-10-01"), None)] * 3,
    }
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs)
    flags = compute_signal_flags(ev)
    assert flags.win_loss_signal is None  # undefined, not False


def test_stable_pattern_flag_uses_documented_cv_threshold():
    league_pairs = {"t1": [(make_stats("t1", True, 0.50, "2025-10-01"), None)] * 10,
                     "t2": [(make_stats("t2", True, 0.30, "2025-10-01"), None)] * 3}
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better")
    flags = compute_signal_flags(ev)
    assert ev.stability.coefficient_of_variation == 0.0
    assert flags.stable_pattern is True


def test_flags_reference_documented_constants_not_magic_numbers():
    assert LEAGUE_EXTREME_PERCENTILE == 10.0
    assert LARGE_EFFECT_SIZE == 0.8


# ---- Aggregate vs. per-game stability semantics (2026-08-15 hardening) ----

def test_unweighted_mean_of_ratios_differs_from_weighted_aggregate_ratio():
    """Concrete demonstration that "mean of per-game ratios" (what `value`/
    `stability.mean` compute today) and "sum(numerator)/sum(denominator)"
    (a weighted aggregate) are legitimately DIFFERENT numbers for a ratio
    metric when per-game attempt volume is uneven. This package does not
    force these to be equal anywhere; this test exists to make the
    divergence concrete, not to assert anything about build_evidence's
    current (unweighted) convention being wrong.
    """
    # Game 1: low volume, high rate. Game 2: high volume, lower rate.
    games = [(1, 2), (40, 100)]  # (made, attempted)

    unweighted_mean = sum(m / a for m, a in games) / len(games)
    weighted_aggregate = sum(m for m, _ in games) / sum(a for _, a in games)

    assert unweighted_mean == pytest.approx(0.45)
    assert weighted_aggregate == pytest.approx(41 / 102)
    assert unweighted_mean != pytest.approx(weighted_aggregate)


def test_evidence_value_and_stability_mean_share_the_documented_convention():
    """In the current implementation `value` and `stability.mean` ARE the
    same number, because both come from the identical unweighted per-game
    series — this is the v1-matching convention `value_definition`
    documents explicitly, not an unexplained coincidence."""
    league_pairs = {"t1": [(make_stats("t1", True, 0.5, "2025-10-01"), None),
                            (make_stats("t1", True, 0.7, "2025-10-02"), None)],
                     "t2": [(make_stats("t2", True, 0.4, "2025-10-01"), None)] * 2}
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs)
    assert ev.value == pytest.approx(ev.stability.mean)
    assert ev.value_definition == "unweighted_mean_of_per_game_values"


# ---- Canonical aggregate vs. unweighted mean (2026-08-15 final semantic
# integrity check — real materially-different values required) -------------

def test_canonical_aggregate_materially_differs_from_unweighted_mean_with_real_data():
    """The exact scenario from the audit: game A 1/2 (.500), game B 40/100
    (.400). Unweighted mean = .450. Canonical (volume-weighted) aggregate
    = 41/102 = .4020. These must differ, and `value` must report the
    canonical one when use_canonical_aggregate=True."""
    league_pairs = {
        "t1": [(make_stats_with_shots("t1", True, fgm=1, fga=2, date="2025-10-01"), None),
               (make_stats_with_shots("t1", False, fgm=40, fga=100, date="2025-10-02"), None)],
        "t2": [(make_stats_with_shots("t2", True, fgm=10, fga=20, date="2025-10-01"), None)] * 2,
    }
    ev_canonical = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                                   direction="higher_is_better", use_canonical_aggregate=True)
    ev_unweighted = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                                    direction="higher_is_better", use_canonical_aggregate=False)

    assert ev_unweighted.value == pytest.approx(0.45)
    assert ev_canonical.value == pytest.approx(41 / 102)
    assert ev_canonical.value != pytest.approx(ev_unweighted.value)

    # stability.mean is UNCHANGED by use_canonical_aggregate -- it always
    # reflects the per-game distribution, regardless of what `value` uses.
    assert ev_canonical.stability.mean == pytest.approx(0.45)
    assert ev_canonical.stability.mean == pytest.approx(ev_unweighted.stability.mean)

    assert ev_canonical.value_definition == "canonical_aggregate_ratio"
    assert ev_unweighted.value_definition == "unweighted_mean_of_per_game_values"
    assert ev_canonical.stability_definition == "unweighted_mean_of_per_game_values"


def test_canonical_aggregate_used_for_league_context_ranking_consistently():
    """League rank/percentile for the main metric must be computed on the
    SAME convention as `value` — every team's canonical aggregate, not a
    mix of canonical-for-this-team and unweighted-for-the-rest."""
    league_pairs = {
        "t1": [(make_stats_with_shots("t1", True, fgm=1, fga=2, date="2025-10-01"), None),
               (make_stats_with_shots("t1", False, fgm=40, fga=100, date="2025-10-02"), None)],
        "t2": [(make_stats_with_shots("t2", True, fgm=45, fga=100, date="2025-10-01"), None)] * 2,
    }
    ev = build_evidence("t1", "efg_pct", "season", "season", "for", efg_extractor, league_pairs,
                         direction="higher_is_better", use_canonical_aggregate=True)
    # t1 canonical = 41/102 = .4020; t2 canonical = 45/100 = .450 -> t2 ranks above t1.
    assert ev.league_context.rank == 2
    assert ev.league_context.league_mean == pytest.approx((41 / 102 + 0.45) / 2)
