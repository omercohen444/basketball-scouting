"""EvidencePack builder: formatting, reliability tiers, effect masking, screening.

All synthetic — no cached games, no network, no credentials."""

from __future__ import annotations

from basketball_scout.agents.evidence_pack import (
    LIMITATION_LEGEND,
    METRIC_SPECS,
    MIN_LOSSES_FOR_WL,
    MIN_WINS_FOR_WL,
    UNAVAILABLE,
    build_screening,
    format_rank,
    format_value,
    reliability_tier,
)

from agents_factories import make_item

# ---- formatting -------------------------------------------------------------


def test_percentages_arrive_as_fractions_and_are_scaled_once():
    """formulas.py returns eFG% as 0.5166, not 51.66 — the x100 must live in
    exactly one place or every display site drifts."""
    assert format_value(0.5166, "pct") == "51.7%"
    assert format_value(0.342, "share") == "34.2%"


def test_non_percentage_units_keep_their_natural_scale():
    assert format_value(118.43, "per100") == "118.4"
    assert format_value(1.4231, "ratio") == "1.42"
    assert format_value(14.27, "count_per_game") == "14.3"


def test_missing_value_never_renders_as_zero():
    """A missing value and a zero value mean different things; conflating them
    would let a report claim a team scored nothing."""
    assert format_value(None, "pct") == "n/a"
    assert format_value(0.0, "pct") == "0.0%"


def test_rank_display_needs_a_real_league():
    assert format_rank(3, 14) == "3 of 14"
    assert format_rank(None, 14) is None
    assert format_rank(1, 1) is None  # a rank against a league of one is meaningless


# ---- reliability ------------------------------------------------------------


def test_full_season_high_consistency_metric_is_high_reliability():
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=26,
        sample_possessions=None, coefficient_of_variation=0.08,
    ) == "high"


def test_provenance_imposes_a_ceiling_that_sample_size_cannot_lift():
    assert reliability_tier(
        validation_state="provisional_deterministic", sample_games=26,
        sample_possessions=None, coefficient_of_variation=0.05,
    ) == "moderate"
    assert reliability_tier(
        validation_state="partial", sample_games=26,
        sample_possessions=None, coefficient_of_variation=0.05,
    ) == "low"


def test_small_sample_lowers_reliability():
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=4,
        sample_possessions=None, coefficient_of_variation=0.05,
    ) == "low"
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=8,
        sample_possessions=None, coefficient_of_variation=0.05,
    ) == "moderate"


def test_thin_segment_possessions_lower_reliability_even_with_many_games():
    """A clutch cut can span 20 games and still carry very few possessions —
    sample_games alone would overstate it."""
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=20,
        sample_possessions=30, coefficient_of_variation=0.05,
    ) == "low"


def test_high_volatility_lowers_reliability():
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=26,
        sample_possessions=None, coefficient_of_variation=0.9,
    ) == "low"


def test_cv_of_none_is_ignored_not_treated_as_zero():
    """Metrics that legitimately cross zero (net rating) pass cv_applicable=False,
    which arrives here as None. An earlier draft mislabelled the league leader's
    net rating 'low reliability' because CV = std/|mean| explodes near zero."""
    assert reliability_tier(
        validation_state="validated_deterministic", sample_games=26,
        sample_possessions=None, coefficient_of_variation=None,
    ) == "high"


def test_net_rating_specs_opt_out_of_cv():
    for spec in METRIC_SPECS:
        if spec.metric_name == "net_rating":
            assert spec.cv_applicable is False, f"{spec.scope}:{spec.metric_name} must opt out of CV"


# ---- registry integrity -----------------------------------------------------


def test_every_evidence_id_is_unique():
    ids = [f"EV.{s.scope}.{s.metric_name}" for s in METRIC_SPECS]
    assert len(ids) == len(set(ids))


def test_no_deferred_metric_is_ever_a_live_evidence_spec():
    """deferred evidence must reach the agent only through unavailable_evidence,
    never as a value-bearing item it could cite as support."""
    assert all(s.validation_state != "deferred" for s in METRIC_SPECS)


def test_unavailable_list_covers_the_known_gaps():
    labels = " ".join(u.label.lower() + " " + u.reason.lower() for u in UNAVAILABLE)
    for expected in ("shot-zone", "half-court", "player-level", "video", "scheme"):
        assert expected in labels


def test_every_limitation_code_used_by_a_spec_has_legend_text():
    used = {c for spec in METRIC_SPECS for c in spec.limitation_codes}
    assert used <= set(LIMITATION_LEGEND), f"undocumented limitation codes: {used - set(LIMITATION_LEGEND)}"


def test_neutral_direction_metrics_carry_the_neutrality_limitation():
    """league_context.py warns that a neutral metric's percentile carries no
    good/bad meaning; the pack must pass that warning along."""
    for spec in METRIC_SPECS:
        if spec.direction == "neutral":
            assert "neutral_direction" in spec.limitation_codes, spec.metric_name


def test_win_loss_thresholds_match_the_stats_layer_gate():
    from basketball_scout.stats.winloss import AGENT_RANKABLE_MIN_LOSSES, AGENT_RANKABLE_MIN_WINS

    assert MIN_WINS_FOR_WL == AGENT_RANKABLE_MIN_WINS
    assert MIN_LOSSES_FOR_WL == AGENT_RANKABLE_MIN_LOSSES


# ---- screening --------------------------------------------------------------


def test_screening_is_deterministic_across_calls():
    items = [make_item(f"EV.season.m{i}", effect_size=1.0 - i * 0.1) for i in range(10)]
    assert build_screening(items).candidate_ids == build_screening(items).candidate_ids


def test_screening_orders_by_effect_size_magnitude_not_sign():
    """A large negative effect is as informative as a large positive one."""
    items = [
        make_item("EV.season.a", effect_size=0.2),
        make_item("EV.season.b", effect_size=-2.5),
        make_item("EV.season.c", effect_size=1.0),
    ]
    assert build_screening(items).top_win_loss_effects[0] == "EV.season.b"


def test_screening_excludes_masked_effects_from_the_effect_ranking():
    items = [
        make_item("EV.season.masked", agent_rankable=False, effect_size=None),
        make_item("EV.season.real", effect_size=0.9),
    ]
    assert build_screening(items).top_win_loss_effects == ["EV.season.real"]


def test_screening_caps_the_candidate_pool():
    items = [make_item(f"EV.season.m{i}", effect_size=1.0) for i in range(40)]
    assert len(build_screening(items, top_n=20).candidate_ids) == 20


def test_screening_still_fills_candidates_when_no_flags_fire():
    """A team with no extremes and no rankable W/L must still get a usable pool,
    otherwise the triage agent has nothing to choose from."""
    items = [
        make_item(f"EV.season.m{i}", agent_rankable=False, effect_size=None, league_extreme=False)
        for i in range(12)
    ]
    assert len(build_screening(items).candidate_ids) == 12
