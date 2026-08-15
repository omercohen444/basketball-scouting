"""Shot-coordinate geometry: normalization, distance, zones, official-scoring
authority, ambiguity band, eligibility, orientation, boundary/missing data."""

from __future__ import annotations

import pytest

from basketball_scout.pbp.geometry import (
    ARC_RADIUS_M,
    BASKET_X_M,
    BASKET_Y_M,
    LANE_DEPTH_M,
    LANE_DEPTH_BOUNDARY_TOLERANCE_M,
    NormalizedCoordinate,
    basket_distance_m,
    build_shot_geometry,
    classify_coarse_zone,
    coordinate_implied_is_three,
    distance_eligibility,
    is_rim_attempt,
    normalize_coordinate,
)


def test_normalize_coordinate_scales_by_100():
    c = normalize_coordinate(750.0, 157.5)
    assert c.x_m == pytest.approx(7.5)
    assert c.y_m == pytest.approx(1.575)


def test_normalize_coordinate_none_for_missing_x_or_y():
    assert normalize_coordinate(None, 100.0) is None
    assert normalize_coordinate(100.0, None) is None


def test_distance_at_the_basket_is_zero():
    c = NormalizedCoordinate(x_m=BASKET_X_M, y_m=BASKET_Y_M)
    assert basket_distance_m(c) == pytest.approx(0.0)


def test_distance_pythagorean():
    c = NormalizedCoordinate(x_m=BASKET_X_M + 3.0, y_m=BASKET_Y_M + 4.0)
    assert basket_distance_m(c) == pytest.approx(5.0)


# ---- Coarse zones -----------------------------------------------------

def test_lane_2pt_close_to_basket():
    c = NormalizedCoordinate(x_m=BASKET_X_M + 0.5, y_m=2.0)
    assert classify_coarse_zone(c, official_points=2) == "lane_2pt"


def test_midrange_2pt_outside_lane_but_2pt():
    c = NormalizedCoordinate(x_m=BASKET_X_M + 4.0, y_m=4.0)  # outside lane half-width (2.45m)
    assert classify_coarse_zone(c, official_points=2) == "midrange_2pt"


def test_corner_3_near_sideline_near_baseline():
    c = NormalizedCoordinate(x_m=1.5, y_m=2.0)  # near sideline, near baseline
    assert classify_coarse_zone(c, official_points=3) == "corner_3"


def test_atb_3_far_from_sideline():
    c = NormalizedCoordinate(x_m=7.5, y_m=8.0)  # top of the arc, not near either sideline
    assert classify_coarse_zone(c, official_points=3) == "atb_3"


def test_zone_none_for_invalid_official_points():
    c = NormalizedCoordinate(x_m=7.5, y_m=2.0)
    assert classify_coarse_zone(c, official_points=1) is None
    assert classify_coarse_zone(c, official_points=0) is None


# ---- Lane-depth boundary tolerance (CP2.4 hardening, 2026-08-16) ---------
#
# Segev shot coordinates are recorded on a discrete provider-side grid
# rather than freehand; the FIBA free-throw line sits between two grid
# rows rather than exactly on one. A shot genuinely taken at the
# free-throw line can therefore be charted a small, principled distance
# beyond the nominal 5.8m lane depth. LANE_DEPTH_BOUNDARY_TOLERANCE_M
# absorbs that grid-quantization slop; it does not extend the lane by an
# arbitrary amount, and it must not swallow a real mid-range shot clearly
# beyond the free-throw line.

def test_lane_depth_within_tolerance_still_counts_as_lane():
    c = NormalizedCoordinate(x_m=BASKET_X_M, y_m=LANE_DEPTH_M + LANE_DEPTH_BOUNDARY_TOLERANCE_M)
    assert classify_coarse_zone(c, official_points=2) == "lane_2pt"


def test_lane_depth_clearly_beyond_tolerance_is_midrange():
    c = NormalizedCoordinate(x_m=BASKET_X_M, y_m=LANE_DEPTH_M + LANE_DEPTH_BOUNDARY_TOLERANCE_M + 0.5)
    assert classify_coarse_zone(c, official_points=2) == "midrange_2pt"


def test_lane_depth_tolerance_does_not_affect_lane_x_boundary():
    """The tolerance is specific to lane depth (y); it must not loosen the
    lane half-width (x) boundary, which has no evidenced grid-alignment
    issue and was not touched."""
    c = NormalizedCoordinate(x_m=BASKET_X_M + 2.45 + LANE_DEPTH_BOUNDARY_TOLERANCE_M, y_m=2.0)
    assert classify_coarse_zone(c, official_points=2) == "midrange_2pt"


# ---- Official 2PT/3PT authority + ambiguity band ---------------------

def test_official_points_always_wins_even_if_geometry_disagrees():
    """A shot geometrically far beyond the arc but officially scored 2PT
    must still classify within the 2PT family — geometry never overrides
    the box score."""
    far_coord = NormalizedCoordinate(x_m=BASKET_X_M, y_m=BASKET_Y_M + 10.0)
    zone = classify_coarse_zone(far_coord, official_points=2)
    assert zone in ("lane_2pt", "midrange_2pt")  # never corner_3/atb_3


def test_coordinate_implied_is_three_beyond_arc():
    assert coordinate_implied_is_three(ARC_RADIUS_M + 1.0) is True


def test_coordinate_implied_is_three_inside_arc():
    assert coordinate_implied_is_three(ARC_RADIUS_M - 1.0) is False


def test_coordinate_implied_is_three_none_inside_ambiguity_band():
    assert coordinate_implied_is_three(ARC_RADIUS_M) is None
    assert coordinate_implied_is_three(ARC_RADIUS_M + 0.1) is None
    assert coordinate_implied_is_three(ARC_RADIUS_M - 0.1) is None


def test_coordinate_implied_is_three_just_outside_band():
    assert coordinate_implied_is_three(ARC_RADIUS_M + 0.31) is True
    assert coordinate_implied_is_three(ARC_RADIUS_M - 0.31) is False


# ---- Rim attempt (trusted shot_type) -----------------------------------

def test_is_rim_attempt_dunk_and_layup():
    assert is_rim_attempt("dunk") is True
    assert is_rim_attempt("lay-up") is True


def test_is_rim_attempt_false_for_jump_shot():
    assert is_rim_attempt("jump-shot") is False


def test_is_rim_attempt_false_for_missing_type():
    assert is_rim_attempt(None) is False


# ---- Distance eligibility (no hairline 3.05m classifier) ---------------

def test_official_3pt_is_always_over_10ft():
    assert distance_eligibility(3, distance_m=1.0) == "over_10ft"  # even if geometry looks close


def test_2pt_over_10ft_when_confidently_far():
    assert distance_eligibility(2, distance_m=4.0) == "over_10ft"


def test_2pt_under_10ft_when_confidently_close():
    assert distance_eligibility(2, distance_m=1.0) == "under_10ft"


def test_2pt_uncertain_in_the_middle_band():
    assert distance_eligibility(2, distance_m=3.0) == "uncertain"


def test_2pt_uncertain_when_distance_missing():
    assert distance_eligibility(2, distance_m=None) == "uncertain"


def test_eligibility_boundary_values():
    assert distance_eligibility(2, distance_m=3.5) == "over_10ft"  # >= threshold
    assert distance_eligibility(2, distance_m=2.5) == "uncertain"  # not < threshold, falls in band
    assert distance_eligibility(2, distance_m=2.4999) == "under_10ft"


# ---- Orientation (documented, empirically validated convention) --------

def test_basket_position_matches_validated_convention():
    assert BASKET_X_M == 7.5
    assert BASKET_Y_M == 1.575


# ---- Full pipeline: missing/bad coordinates ----------------------------

def test_build_shot_geometry_handles_missing_coordinates_gracefully():
    geo = build_shot_geometry(None, None, official_points=2, shot_type="jump-shot")
    assert geo.coord is None
    assert geo.distance_m is None
    assert geo.coarse_zone is None
    assert geo.distance_eligibility == "uncertain"
    assert geo.is_rim_attempt is False  # shot_type doesn't match rim types


def test_build_shot_geometry_full_pipeline_dunk():
    geo = build_shot_geometry(750.0, 175.0, official_points=2, shot_type="dunk")
    assert geo.coord is not None
    assert geo.distance_m < 1.0
    assert geo.coarse_zone == "lane_2pt"
    assert geo.is_rim_attempt is True
    assert geo.distance_eligibility == "under_10ft"


def test_build_shot_geometry_full_pipeline_three():
    geo = build_shot_geometry(750.0, 950.0, official_points=3, shot_type="jump-shot")
    assert geo.coord is not None
    assert geo.coarse_zone == "atb_3"
    assert geo.distance_eligibility == "over_10ft"
    assert geo.distance_uncertainty_m == 1.0


def test_build_shot_geometry_never_raises_for_invalid_points():
    geo = build_shot_geometry(750.0, 175.0, official_points=99, shot_type="jump-shot")
    assert geo.coarse_zone is None  # no exception, just an honest None
