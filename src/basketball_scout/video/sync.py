"""PBP <-> broadcast synchronization.

Design rationale (docs/VIDEO_STAGE_PLAN.md §7): every Segev PBP action carries
``userTime`` — real wall-clock time — not just the game clock. Broadcast video
also advances in real time, and every stoppage (dead balls, timeouts, replays,
free-throw sequences) consumes real time and video time identically. The
mapping is therefore **linear within a quarter**, with slope 1.0, and only the
constant offset is unknown. It only breaks if the upload itself cuts footage
(most likely at halftime) — hence one anchor per quarter rather than one
anchor for the whole game.

This module is pure: no network, no video access. Calibration data (the
anchors) comes from a human watching the real video; this module only does the
arithmetic and the bookkeeping around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Residual thresholds from docs/VIDEO_STAGE_PLAN.md §7.5.
GOOD_RESIDUAL_S = 5.0
DRIFT_RESIDUAL_S = 15.0


class SyncError(ValueError):
    """Raised for malformed calibration data."""


@dataclass(frozen=True)
class QuarterAnchor:
    """One calibration point: a PBP event and its observed video timestamp."""

    quarter: int
    source_action_id: int
    pbp_user_time_s: float
    video_time_s: float
    method: str = "manual"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "quarter": self.quarter,
            "source_action_id": self.source_action_id,
            "pbp_user_time_s": self.pbp_user_time_s,
            "video_time_s": self.video_time_s,
            "method": self.method,
            "note": self.note,
        }


@dataclass(frozen=True)
class ResidualCheck:
    """A second PBP event in the same quarter used to validate the anchor."""

    quarter: int
    source_action_id: int
    predicted_video_s: float
    observed_video_s: float

    @property
    def residual_s(self) -> float:
        return self.observed_video_s - self.predicted_video_s

    @property
    def status(self) -> str:
        r = abs(self.residual_s)
        if r <= GOOD_RESIDUAL_S:
            return "ok"
        if r <= DRIFT_RESIDUAL_S:
            return "drift"
        return "cut"

    def to_dict(self) -> dict[str, Any]:
        return {
            "quarter": self.quarter,
            "source_action_id": self.source_action_id,
            "predicted_video_s": self.predicted_video_s,
            "observed_video_s": self.observed_video_s,
            "residual_s": round(self.residual_s, 2),
            "status": self.status,
        }


@dataclass
class GameSync:
    """Calibration for one game: one offset per quarter, slope, quality."""

    video_duration_s: float
    anchors: list[QuarterAnchor] = field(default_factory=list)
    checks: list[ResidualCheck] = field(default_factory=list)
    slope: float = 1.0
    tolerance_s: float = GOOD_RESIDUAL_S
    operator_lag_estimate_s: float | None = None
    operator_lag_std_s: float | None = None
    calibrated_by: str = "manual"
    calibrated_at: str = ""

    def _anchor_for(self, quarter: int) -> QuarterAnchor | None:
        candidates = [a for a in self.anchors if a.quarter == quarter]
        return candidates[-1] if candidates else None

    def has_anchor(self, quarter: int) -> bool:
        return self._anchor_for(quarter) is not None

    def map_to_video(self, quarter: int, user_time_s: float) -> float | None:
        """Map a PBP ``userTime`` (seconds) to a video timestamp (seconds).

        Returns ``None`` if there is no anchor for this quarter, or if the
        mapped time falls outside the video — both are guard conditions the
        caller must count and exclude, not silently clamp (see
        docs/VIDEO_STAGE_PLAN.md §7.3 / §8.1).
        """
        anchor = self._anchor_for(quarter)
        if anchor is None:
            return None
        video_t = (user_time_s - anchor.pbp_user_time_s) * self.slope + anchor.video_time_s
        if video_t < 0 or video_t > self.video_duration_s:
            return None
        return video_t

    def quality(self) -> str:
        """``ok`` | ``degraded`` | ``failed`` — see docs/VIDEO_STAGE_PLAN.md §7.5/§7.7."""
        quarters_with_anchors = {a.quarter for a in self.anchors}
        if not quarters_with_anchors:
            return "failed"

        worst = "ok"
        by_quarter: dict[int, list[ResidualCheck]] = {}
        for check in self.checks:
            by_quarter.setdefault(check.quarter, []).append(check)

        for quarter, checks in by_quarter.items():
            statuses = {c.status for c in checks}
            if "cut" in statuses:
                worst = "failed"
                break
            if "drift" in statuses and worst == "ok":
                worst = "degraded"
        return worst

    def summary(self) -> dict[str, Any]:
        return {
            "video_duration_s": self.video_duration_s,
            "slope": self.slope,
            "tolerance_s": self.tolerance_s,
            "quarters_anchored": sorted({a.quarter for a in self.anchors}),
            "anchor_count": len(self.anchors),
            "check_count": len(self.checks),
            "worst_residual_s": max((abs(c.residual_s) for c in self.checks), default=None),
            "quality": self.quality(),
            "operator_lag_estimate_s": self.operator_lag_estimate_s,
            "operator_lag_std_s": self.operator_lag_std_s,
            "calibrated_by": self.calibrated_by,
            "calibrated_at": self.calibrated_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_duration_s": self.video_duration_s,
            "slope": self.slope,
            "tolerance_s": self.tolerance_s,
            "anchors": [a.to_dict() for a in self.anchors],
            "checks": [c.to_dict() for c in self.checks],
            "quality": self.quality(),
            "operator_lag_estimate_s": self.operator_lag_estimate_s,
            "operator_lag_std_s": self.operator_lag_std_s,
            "calibrated_by": self.calibrated_by,
            "calibrated_at": self.calibrated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameSync:
        return cls(
            video_duration_s=float(data["video_duration_s"]),
            anchors=[QuarterAnchor(**a) for a in data.get("anchors", [])],
            checks=[
                ResidualCheck(
                    quarter=c["quarter"],
                    source_action_id=c["source_action_id"],
                    predicted_video_s=c["predicted_video_s"],
                    observed_video_s=c["observed_video_s"],
                )
                for c in data.get("checks", [])
            ],
            slope=float(data.get("slope", 1.0)),
            tolerance_s=float(data.get("tolerance_s", GOOD_RESIDUAL_S)),
            operator_lag_estimate_s=data.get("operator_lag_estimate_s"),
            operator_lag_std_s=data.get("operator_lag_std_s"),
            calibrated_by=data.get("calibrated_by", "manual"),
            calibrated_at=data.get("calibrated_at", ""),
        )


def fit_slope(anchor_a: QuarterAnchor, anchor_b: QuarterAnchor) -> float:
    """Fit slope from two anchors in the same quarter (docs §7.5, drift case).

    Used only when a residual shows 5-15s drift — a symptom of the source
    real-time clock and the video not advancing at exactly the same rate.
    """
    if anchor_a.quarter != anchor_b.quarter:
        raise SyncError("anchors must be in the same quarter to fit a slope")
    d_pbp = anchor_b.pbp_user_time_s - anchor_a.pbp_user_time_s
    if d_pbp == 0:
        raise SyncError("anchors have identical pbp_user_time_s; cannot fit a slope")
    d_video = anchor_b.video_time_s - anchor_a.video_time_s
    return d_video / d_pbp
