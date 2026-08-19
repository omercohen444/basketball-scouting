"""Deterministic shot-coordinate geometry — CP2.4A (2026-08-16).

Turns Segev's raw ``coordX``/``coordY`` shot-action fields into a coarse,
deterministic shot zone and an approximate shot distance, entirely from PBP
data — no video, no model calls. Independent of ``pbp/canonical.py`` (the
video pipeline's shot-only contract, out of bounds for this track) and of
``stats/possession.py`` (which aggregates FGA/FGM counts, not individual
shot coordinates) — this module works directly from raw Segev action
dicts, one shot at a time.

Coordinate convention (empirically validated this session against 1,104
real shots across 8 cached 2025-26 games — see
``artifacts/cp2/coords/coordinate_validation_report.md`` for the full
evidence)::

    x_m = coordX / 100
    y_m = coordY / 100

with the attacking basket at ``(7.5, 1.575)`` on a court modeled as 15 m
wide. Coordinates are recorded **relative to the attacking basket for
every shot**, regardless of team or quarter — verified: home/away and
Q1-Q4 layup-distance medians are statistically indistinguishable
(1.69-1.89 m), so no team/quarter orientation flip is needed. This was
verified empirically, not assumed from the brief's starting hypothesis —
see the validation report for the full reasoning and the one alternative
convention that was ruled out.

FIBA reference geometry used for zone boundaries (approximate, not
authoritative court markings — see ``ZoneThresholds`` for the exact
constants and where each one is empirically justified):

* lane (key) width 4.9 m, depth 5.8 m from the baseline;
* 3PT arc radius 6.75 m from the basket;
* corner-3 straight segment: FIBA's actual boundary is a straight line
  ~6.6 m from the basket near the sideline, not the 6.75 m arc — this
  module does **not** model that line precisely (a coarse MVP, per the
  brief's explicit instruction not to build a fine hairline classifier);
  instead it uses the *official* 2PT/3PT point value (always authoritative)
  plus a simple, empirically-validated "near the sideline and near the
  baseline" test to label a 3PT attempt as ``corner_3`` vs ``atb_3``.

**Official 2PT/3PT point value is always authoritative and is never
overridden by geometry** — coordinate geometry only helps sub-classify
*within* whichever family the box score already recorded, and (separately,
diagnostically) is compared against official scoring to validate the
coordinate model itself. See ``coordinate_implied_is_three`` /
``classify_coarse_zone``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# ---- Coordinate convention (see module docstring for the validation) ------
COORD_SCALE = 100.0  # raw units per meter
COURT_WIDTH_M = 15.0
COURT_LENGTH_M = 28.0  # not used directly (shots are recorded in the attacking half only)
BASKET_X_M = 7.5
BASKET_Y_M = 1.575

# ---- FIBA-derived zone thresholds -----------------------------------------
ARC_RADIUS_M = 6.75
ARC_AMBIGUITY_BAND_M = 0.30  # +/- around the arc for the diagnostic-only check
LANE_HALF_WIDTH_M = 2.45  # 4.9 m lane width / 2
LANE_DEPTH_M = 5.8
# CP2.4 hardening (2026-08-16): Segev shot coordinates are not freehand —
# empirically, across all 8 validation games, y_m values fall on a strict
# 0.28 m grid (x_m on a 0.15 m grid). The FIBA free-throw line (5.80 m) sits
# between grid rows 5.60 and 5.88, closer to 5.88 — so a real paint attempt
# charted at the free-throw line can snap to the row just beyond it purely
# from grid quantization, not from the shot actually being a mid-range
# attempt. Tolerance = half the measured y-grid pitch (0.28 / 2), the
# standard "nearest grid line" allowance — derived from the provider's own
# data granularity, not tuned to any single event.
LANE_DEPTH_BOUNDARY_TOLERANCE_M = 0.14
# Empirically validated (this session, 83 real 3PT-vs-arc "disagreements",
# every single one within these bounds): a made/attempted 3PT shot is
# treated as a CORNER three when it is taken close to the sideline and
# close to the baseline — matching FIBA's straight corner segment, which
# runs from the baseline to 2.99 m of it. Coarse thresholds, not the exact
# FIBA line (deliberately — see module docstring).
CORNER_SIDELINE_DISTANCE_MAX_M = 2.2
CORNER_BASELINE_Y_MAX_M = 4.0

# ---- Distance-eligibility bands (see brief §9 — no hairline 3.05 m cutoff) -
DISTANCE_UNCERTAINTY_M = 1.0
TEN_FT_ELIGIBLE_MIN_M = 3.5
UNDER_TEN_FT_MAX_M = 2.5

CoarseZone = Literal["lane_2pt", "midrange_2pt", "corner_3", "atb_3"]
DistanceEligibility = Literal["over_10ft", "under_10ft", "uncertain"]

# `allyhoop` was added 2026-08-19 on measured evidence, not on the name. Across
# all 24,432 shots in the 182-game season there are 27 of them, and every
# property matches a rim finish rather than a jump shot: median distance 0.45 m
# (identical to a dunk; a lay-up is 1.77 m), maximum 2.43 m, all 27 classify
# geometrically as `lane_2pt`, all 27 are 2-point attempts, and the 85.2% make
# rate sits between dunk (91.7%) and lay-up (56.2%) — exactly where a
# catch-and-finish belongs. 16 of the 27 belong to one team, which is a real
# basketball fact rather than an artifact.
_RIM_SHOT_TYPES = {"dunk", "lay-up", "allyhoop"}


class GeometryError(ValueError):
    """Raised only for structurally malformed input, never for out-of-range
    coordinates (those are handled by returning ``None``, not raising)."""


@dataclass(frozen=True)
class NormalizedCoordinate:
    x_m: float
    y_m: float

    def to_dict(self) -> dict[str, Any]:
        return {"x_m": self.x_m, "y_m": self.y_m}


def normalize_coordinate(raw_x: float | None, raw_y: float | None) -> NormalizedCoordinate | None:
    """Raw Segev ``coordX``/``coordY`` -> meters, or ``None`` if either is missing."""
    if raw_x is None or raw_y is None:
        return None
    return NormalizedCoordinate(x_m=raw_x / COORD_SCALE, y_m=raw_y / COORD_SCALE)


def basket_distance_m(coord: NormalizedCoordinate) -> float:
    """Euclidean distance from the attacking basket, in meters."""
    return ((coord.x_m - BASKET_X_M) ** 2 + (coord.y_m - BASKET_Y_M) ** 2) ** 0.5


def coordinate_implied_is_three(distance_m: float) -> bool | None:
    """Diagnostic-only radial-distance-vs-arc check — ``None`` inside the
    ambiguity band (too close to call). **Never used to override official
    scoring** — see module docstring. Used only to validate the coordinate
    model itself (``coordinate_validation_report.md``) and, at classification
    time, is explicitly NOT consulted — ``classify_coarse_zone`` always takes
    official points as ground truth for the 2PT/3PT family.
    """
    if abs(distance_m - ARC_RADIUS_M) <= ARC_AMBIGUITY_BAND_M:
        return None
    return distance_m > ARC_RADIUS_M


def is_rim_attempt(shot_type: str | None) -> bool:
    """Trusted-PBP-field rim indicator — dunk/lay-up, independent of
    coordinate precision (brief §7: "may use trusted PBP shot_type
    information"). Not mutually exclusive with the coarse zone below; a rim
    attempt is almost always also geometrically ``lane_2pt``, but this flag
    does not depend on that agreement holding."""
    return (shot_type or "").lower() in _RIM_SHOT_TYPES


def _is_within_lane(coord: NormalizedCoordinate) -> bool:
    return (
        abs(coord.x_m - BASKET_X_M) <= LANE_HALF_WIDTH_M
        and coord.y_m <= LANE_DEPTH_M + LANE_DEPTH_BOUNDARY_TOLERANCE_M
    )


def _is_corner(coord: NormalizedCoordinate) -> bool:
    dist_from_sideline = min(coord.x_m, COURT_WIDTH_M - coord.x_m)
    return dist_from_sideline <= CORNER_SIDELINE_DISTANCE_MAX_M and coord.y_m <= CORNER_BASELINE_Y_MAX_M


def classify_coarse_zone(coord: NormalizedCoordinate, official_points: int) -> CoarseZone | None:
    """Coarse MVP zone, per the brief's four-zone taxonomy.

    ``official_points`` (2 or 3) is authoritative for the 2PT/3PT family —
    geometry only decides *which* zone within that family. Returns ``None``
    for a malformed ``official_points`` (neither 2 nor 3) rather than
    guessing.
    """
    if official_points == 3:
        return "corner_3" if _is_corner(coord) else "atb_3"
    if official_points == 2:
        return "lane_2pt" if _is_within_lane(coord) else "midrange_2pt"
    return None


def distance_eligibility(official_points: int, distance_m: float | None) -> DistanceEligibility:
    """Deterministic ``>=10ft`` eligibility (brief §9) — never a hairline
    3.05 m classifier. Official 3PT is always ``over_10ft`` (a 3PT shot is
    definitionally beyond the arc, itself beyond 10ft, regardless of any
    coordinate precision question). For a 2PT attempt: ``over_10ft`` only
    if the (uncertain-by-``DISTANCE_UNCERTAINTY_M``) distance is
    confidently beyond ``TEN_FT_ELIGIBLE_MIN_M``; ``under_10ft`` only if
    confidently under ``UNDER_TEN_FT_MAX_M``; anything in between —
    including a missing distance — is ``uncertain``, excluded from the
    eligibility denominator by callers rather than guessed.
    """
    if official_points == 3:
        return "over_10ft"
    if distance_m is None:
        return "uncertain"
    if distance_m >= TEN_FT_ELIGIBLE_MIN_M:
        return "over_10ft"
    if distance_m < UNDER_TEN_FT_MAX_M:
        return "under_10ft"
    return "uncertain"


@dataclass(frozen=True)
class ShotGeometry:
    """The full deterministic geometry bundle for one shot action."""

    coord: NormalizedCoordinate | None
    distance_m: float | None
    distance_uncertainty_m: float
    coarse_zone: CoarseZone | None
    is_rim_attempt: bool
    distance_eligibility: DistanceEligibility

    def to_dict(self) -> dict[str, Any]:
        return {
            "coord": self.coord.to_dict() if self.coord else None,
            "distance_m": self.distance_m,
            "distance_uncertainty_m": self.distance_uncertainty_m,
            "coarse_zone": self.coarse_zone,
            "is_rim_attempt": self.is_rim_attempt,
            "distance_eligibility": self.distance_eligibility,
        }


def build_shot_geometry(
    raw_x: float | None, raw_y: float | None, *, official_points: int, shot_type: str | None
) -> ShotGeometry:
    """One-call entry point: raw Segev shot fields -> the full geometry bundle.

    Never raises for missing/out-of-range coordinates — a shot with no
    ``coordX``/``coordY`` (or an unrecognized ``official_points``) still
    returns a valid ``ShotGeometry`` with the geometry-dependent fields set
    to ``None``, so a caller can always attach provenance rather than crash.
    """
    coord = normalize_coordinate(raw_x, raw_y)
    distance = basket_distance_m(coord) if coord is not None else None
    zone = classify_coarse_zone(coord, official_points) if coord is not None else None
    return ShotGeometry(
        coord=coord,
        distance_m=distance,
        distance_uncertainty_m=DISTANCE_UNCERTAINTY_M,
        coarse_zone=zone,
        is_rim_attempt=is_rim_attempt(shot_type),
        distance_eligibility=distance_eligibility(official_points, distance),
    )
