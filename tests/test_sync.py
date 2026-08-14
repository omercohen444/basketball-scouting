"""PBP <-> video synchronization: mapping, residuals, quality, drift.

Highest-value tests in the stage (docs/VIDEO_STAGE_PLAN.md §20): a sync bug
produces confidently-wrong localization that looks plausible and is the one
failure mode a quick human review won't reliably catch.
"""

from __future__ import annotations

import pytest

from basketball_scout.video.sync import (
    GameSync,
    QuarterAnchor,
    ResidualCheck,
    SyncError,
    fit_slope,
)


def make_anchor(quarter: int, pbp_t: float, video_t: float, action_id: int = 1) -> QuarterAnchor:
    return QuarterAnchor(
        quarter=quarter, source_action_id=action_id, pbp_user_time_s=pbp_t, video_time_s=video_t
    )


def test_mapping_applies_the_quarter_offset():
    sync = GameSync(video_duration_s=6000, anchors=[make_anchor(1, pbp_t=1000, video_t=100)])
    # An event 50s later in real time must land 50s later in video time.
    assert sync.map_to_video(1, 1050) == pytest.approx(150.0)


def test_mapping_returns_none_when_quarter_has_no_anchor():
    sync = GameSync(video_duration_s=6000, anchors=[make_anchor(1, 1000, 100)])
    assert sync.map_to_video(2, 1500) is None


def test_each_quarter_uses_its_own_anchor_independently():
    """The whole point of per-quarter anchoring: quarter 2's offset must not
    leak from quarter 1's, even if the anchors were entered out of order."""
    sync = GameSync(
        video_duration_s=6000,
        anchors=[make_anchor(2, pbp_t=5000, video_t=3000), make_anchor(1, pbp_t=1000, video_t=100)],
    )
    assert sync.map_to_video(1, 1000) == pytest.approx(100.0)
    assert sync.map_to_video(2, 5000) == pytest.approx(3000.0)


def test_mapped_time_outside_video_bounds_is_rejected_not_clamped():
    """A silent clamp would misattribute the event to the wrong frame instead
    of correctly excluding it — clamping is explicitly the wrong behaviour."""
    sync = GameSync(video_duration_s=100, anchors=[make_anchor(1, pbp_t=1000, video_t=50)])
    assert sync.map_to_video(1, 1000 - 200) is None  # would be -150s
    assert sync.map_to_video(1, 1000 + 200) is None  # would be 250s, past duration


def test_slope_other_than_one_scales_the_mapping():
    sync = GameSync(video_duration_s=6000, anchors=[make_anchor(1, 1000, 100)], slope=2.0)
    assert sync.map_to_video(1, 1050) == pytest.approx(200.0)  # 50s real -> 100s video


def test_when_multiple_anchors_exist_for_a_quarter_the_latest_wins():
    """Re-running --set-anchor for the same quarter must not silently keep
    the stale first anchor."""
    sync = GameSync(
        video_duration_s=6000,
        anchors=[make_anchor(1, 1000, 100, action_id=1), make_anchor(1, 1000, 999, action_id=2)],
    )
    assert sync.map_to_video(1, 1000) == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# Residual checks / quality classification (docs §7.5 drift table)
# ---------------------------------------------------------------------------


def test_residual_within_5s_is_ok():
    check = ResidualCheck(quarter=1, source_action_id=2, predicted_video_s=100.0, observed_video_s=103.0)
    assert check.status == "ok"
    assert check.residual_s == pytest.approx(3.0)


def test_residual_between_5_and_15s_is_drift():
    check = ResidualCheck(quarter=1, source_action_id=2, predicted_video_s=100.0, observed_video_s=112.0)
    assert check.status == "drift"


def test_residual_over_15s_is_a_cut():
    check = ResidualCheck(quarter=1, source_action_id=2, predicted_video_s=100.0, observed_video_s=130.0)
    assert check.status == "cut"


def test_negative_residual_is_evaluated_by_magnitude():
    check = ResidualCheck(quarter=1, source_action_id=2, predicted_video_s=100.0, observed_video_s=80.0)
    assert check.status == "cut"  # |-20| > 15
    assert check.residual_s == pytest.approx(-20.0)


def test_quality_is_ok_with_only_good_residuals():
    sync = GameSync(
        video_duration_s=6000,
        anchors=[make_anchor(1, 1000, 100)],
        checks=[ResidualCheck(1, 2, predicted_video_s=200, observed_video_s=202)],
    )
    assert sync.quality() == "ok"


def test_quality_degrades_on_a_drift_residual():
    sync = GameSync(
        video_duration_s=6000,
        anchors=[make_anchor(1, 1000, 100)],
        checks=[ResidualCheck(1, 2, predicted_video_s=200, observed_video_s=212)],
    )
    assert sync.quality() == "degraded"


def test_quality_fails_on_a_cut_residual_in_any_quarter():
    """A cut in ANY quarter fails the whole game — a degraded quarter elsewhere
    must not mask it."""
    sync = GameSync(
        video_duration_s=6000,
        anchors=[make_anchor(1, 1000, 100), make_anchor(2, 5000, 3000)],
        checks=[
            ResidualCheck(1, 2, predicted_video_s=200, observed_video_s=202),  # ok
            ResidualCheck(2, 3, predicted_video_s=3200, observed_video_s=3250),  # cut
        ],
    )
    assert sync.quality() == "failed"


def test_quality_is_failed_when_no_anchors_exist_at_all():
    sync = GameSync(video_duration_s=6000)
    assert sync.quality() == "failed"


def test_quality_ok_with_anchors_but_no_checks_yet():
    """An anchor with no residual check yet is not a failure — just unverified."""
    sync = GameSync(video_duration_s=6000, anchors=[make_anchor(1, 1000, 100)])
    assert sync.quality() == "ok"


# ---------------------------------------------------------------------------
# Slope fitting (docs §7.5: only used for the 5-15s drift case)
# ---------------------------------------------------------------------------


def test_fit_slope_recovers_a_known_rate():
    a = make_anchor(1, pbp_t=1000, video_t=100, action_id=1)
    b = make_anchor(1, pbp_t=1100, video_t=100 + 105, action_id=2)  # 100 real -> 105 video
    assert fit_slope(a, b) == pytest.approx(1.05)


def test_fit_slope_rejects_anchors_from_different_quarters():
    a = make_anchor(1, 1000, 100)
    b = make_anchor(2, 1100, 200)
    with pytest.raises(SyncError, match="same quarter"):
        fit_slope(a, b)


def test_fit_slope_rejects_identical_pbp_times():
    a = make_anchor(1, 1000, 100, action_id=1)
    b = make_anchor(1, 1000, 200, action_id=2)
    with pytest.raises(SyncError, match="identical"):
        fit_slope(a, b)


# ---------------------------------------------------------------------------
# Serialization round-trip (the manifest persists this to disk)
# ---------------------------------------------------------------------------


def test_round_trips_through_dict():
    sync = GameSync(
        video_duration_s=5222.0,
        anchors=[make_anchor(1, 1000, 100, action_id=42)],
        checks=[ResidualCheck(1, 43, predicted_video_s=200, observed_video_s=201)],
        operator_lag_estimate_s=3.0,
        operator_lag_std_s=1.2,
        calibrated_by="manual",
        calibrated_at="2026-08-15T10:00:00Z",
    )
    restored = GameSync.from_dict(sync.to_dict())
    assert restored.video_duration_s == sync.video_duration_s
    assert restored.anchors[0].source_action_id == 42
    assert restored.checks[0].residual_s == pytest.approx(1.0)
    assert restored.quality() == sync.quality()
    assert restored.operator_lag_estimate_s == 3.0
