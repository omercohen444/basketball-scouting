"""Ground-truth fixture for Gate 1 (model feasibility).

One CSV, one row per manually labelled shot event. Human labels and model
labels sit side by side so agreement can be recomputed at any time without
re-running the model.

Columns are generated from the metric registry, so replacing a provisional
metric regenerates the template rather than requiring a hand edit.

This measures feasibility, not benchmark performance. ~20 events cannot
establish accuracy to a decimal place; it can establish whether the approach is
obviously workable, obviously broken, or unclear.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .events import (
    DEFAULT_POST_ROLL_SECONDS,
    DEFAULT_PRE_ROLL_SECONDS,
    ShotEvent,
    VideoWindow,
    parse_timecode,
    window_around,
)
from .metrics import DEFAULT_METRICS, UNCERTAIN, MetricDefinition

HUMAN_PREFIX = "human_"
MODEL_PREFIX = "model_"
MATCH_PREFIX = "match_"

BASE_COLUMNS = (
    "event_id",
    "game_id",
    "video_url",
    "event_seconds",
    "start_seconds",
    "end_seconds",
    "period",
    "game_clock",
    "team",
    "pbp_description",
)
TRAILING_COLUMNS = ("notes",)


class GroundTruthError(ValueError):
    """Raised for a malformed ground-truth fixture."""


def columns(metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS) -> list[str]:
    """Full column list: base fields, then human/model/match per metric, then notes."""
    cols = list(BASE_COLUMNS)
    for metric in metrics:
        cols += [
            f"{HUMAN_PREFIX}{metric.key}",
            f"{MODEL_PREFIX}{metric.key}",
            f"{MATCH_PREFIX}{metric.key}",
        ]
    return cols + list(TRAILING_COLUMNS)


def write_template(
    path: Path, metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS
) -> Path:
    """Write an EMPTY header-only fixture.

    Never writes example rows: a fabricated label that leaks into a real
    labelling session would silently corrupt the Gate 1 result.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(columns(metrics))
    return path


@dataclass
class GroundTruthRow:
    """One labelled event. Blank labels mean "not labelled yet", not "uncertain"."""

    event_id: str
    video_url: str = ""
    game_id: str = ""
    event_seconds: float | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    period: int | None = None
    game_clock: str = ""
    team: str = ""
    pbp_description: str = ""
    human: dict[str, str] = field(default_factory=dict)
    model: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def window(
        self,
        *,
        pre_roll: float = DEFAULT_PRE_ROLL_SECONDS,
        post_roll: float = DEFAULT_POST_ROLL_SECONDS,
    ) -> VideoWindow:
        """Explicit start/end wins; otherwise derive a window from event_seconds."""
        if self.start_seconds is not None and self.end_seconds is not None:
            return VideoWindow(
                video_url=self.video_url,
                start_seconds=self.start_seconds,
                end_seconds=self.end_seconds,
            )
        if self.event_seconds is None:
            raise GroundTruthError(
                f"row {self.event_id!r} needs either event_seconds or both "
                "start_seconds and end_seconds"
            )
        return window_around(
            self.video_url, self.event_seconds, pre_roll=pre_roll, post_roll=post_roll
        )

    def to_shot_event(self, **window_kwargs: float) -> ShotEvent:
        return ShotEvent(
            event_id=self.event_id,
            window=self.window(**window_kwargs),
            game_id=self.game_id,
            team=self.team,
            period=self.period,
            game_clock=self.game_clock,
            description=self.pbp_description,
        )

    def match(self, metric_key: str) -> bool | None:
        """True/False if both labels present, else None (nothing to compare)."""
        human = self.human.get(metric_key, "")
        model = self.model.get(metric_key, "")
        if not human or not model:
            return None
        return human == model

    def to_csv_dict(self, metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS) -> dict[str, Any]:
        row: dict[str, Any] = {
            "event_id": self.event_id,
            "game_id": self.game_id,
            "video_url": self.video_url,
            "event_seconds": _num(self.event_seconds),
            "start_seconds": _num(self.start_seconds),
            "end_seconds": _num(self.end_seconds),
            "period": "" if self.period is None else self.period,
            "game_clock": self.game_clock,
            "team": self.team,
            "pbp_description": self.pbp_description,
            "notes": self.notes,
        }
        for metric in metrics:
            match = self.match(metric.key)
            row[f"{HUMAN_PREFIX}{metric.key}"] = self.human.get(metric.key, "")
            row[f"{MODEL_PREFIX}{metric.key}"] = self.model.get(metric.key, "")
            row[f"{MATCH_PREFIX}{metric.key}"] = "" if match is None else str(match).lower()
        return row


def _num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _opt_float(raw: str, column: str, event_id: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_timecode(raw)
    except Exception as exc:
        raise GroundTruthError(f"row {event_id!r}: bad {column}={raw!r} ({exc})") from exc


def load_rows(
    path: Path, metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS
) -> list[GroundTruthRow]:
    """Read and validate a fixture. Rejects labels outside a metric's label set."""
    if not path.is_file():
        raise GroundTruthError(f"ground-truth fixture not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(BASE_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise GroundTruthError(f"{path.name} is missing columns: {sorted(missing)}")

        rows: list[GroundTruthRow] = []
        seen: set[str] = set()
        for line_no, raw in enumerate(reader, start=2):
            event_id = (raw.get("event_id") or "").strip()
            if not event_id:
                continue  # tolerate trailing blank lines from spreadsheet editors
            if event_id in seen:
                raise GroundTruthError(f"{path.name} line {line_no}: duplicate event_id {event_id!r}")
            seen.add(event_id)

            period_raw = (raw.get("period") or "").strip()
            rows.append(
                GroundTruthRow(
                    event_id=event_id,
                    game_id=(raw.get("game_id") or "").strip(),
                    video_url=(raw.get("video_url") or "").strip(),
                    event_seconds=_opt_float(raw.get("event_seconds", ""), "event_seconds", event_id),
                    start_seconds=_opt_float(raw.get("start_seconds", ""), "start_seconds", event_id),
                    end_seconds=_opt_float(raw.get("end_seconds", ""), "end_seconds", event_id),
                    period=int(period_raw) if period_raw else None,
                    game_clock=(raw.get("game_clock") or "").strip(),
                    team=(raw.get("team") or "").strip(),
                    pbp_description=(raw.get("pbp_description") or "").strip(),
                    human=_labels(raw, HUMAN_PREFIX, metrics, event_id, path.name),
                    model=_labels(raw, MODEL_PREFIX, metrics, event_id, path.name),
                    notes=(raw.get("notes") or "").strip(),
                )
            )
    return rows


def _labels(
    raw: dict[str, str],
    prefix: str,
    metrics: tuple[MetricDefinition, ...],
    event_id: str,
    filename: str,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for metric in metrics:
        value = (raw.get(f"{prefix}{metric.key}") or "").strip().lower()
        if not value:
            continue
        if not metric.is_valid_label(value):
            raise GroundTruthError(
                f"{filename}: event {event_id!r} has invalid {prefix}{metric.key}={value!r}. "
                f"Allowed: {list(metric.all_labels)}"
            )
        labels[metric.key] = value
    return labels


@dataclass(frozen=True)
class MetricAgreement:
    """Gate 1 result for one metric."""

    key: str
    compared: int
    agreed: int
    human_uncertain: int
    model_uncertain: int
    decisive_compared: int
    decisive_agreed: int

    @property
    def agreement_rate(self) -> float | None:
        return self.agreed / self.compared if self.compared else None

    @property
    def decisive_agreement_rate(self) -> float | None:
        """Agreement excluding any pair where either side said ``uncertain``.

        Reported separately because a model that answers ``uncertain`` for
        everything would otherwise look either good or bad for the wrong reason.
        """
        return self.decisive_agreed / self.decisive_compared if self.decisive_compared else None

    def summary_line(self) -> str:
        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.0%}"

        return (
            f"{self.key:<18} compared={self.compared:<3} "
            f"agree={pct(self.agreement_rate):<5} "
            f"decisive={self.decisive_agreed}/{self.decisive_compared} "
            f"({pct(self.decisive_agreement_rate)})  "
            f"uncertain: human={self.human_uncertain} model={self.model_uncertain}"
        )


def agreement(
    rows: Iterable[GroundTruthRow], metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS
) -> dict[str, MetricAgreement]:
    """Compute per-metric agreement over rows that have both labels."""
    rows = list(rows)
    results: dict[str, MetricAgreement] = {}
    for metric in metrics:
        compared = agreed = human_unc = model_unc = decisive = decisive_agreed = 0
        for row in rows:
            human = row.human.get(metric.key, "")
            model = row.model.get(metric.key, "")
            if human == UNCERTAIN:
                human_unc += 1
            if model == UNCERTAIN:
                model_unc += 1
            if not human or not model:
                continue
            compared += 1
            hit = human == model
            agreed += hit
            if human != UNCERTAIN and model != UNCERTAIN:
                decisive += 1
                decisive_agreed += hit
        results[metric.key] = MetricAgreement(
            key=metric.key,
            compared=compared,
            agreed=agreed,
            human_uncertain=human_unc,
            model_uncertain=model_unc,
            decisive_compared=decisive,
            decisive_agreed=decisive_agreed,
        )
    return results


def write_rows(
    path: Path,
    rows: Iterable[GroundTruthRow],
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> Path:
    """Write rows back out, preserving the canonical column order."""
    cols = columns(metrics)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict(metrics))
    return path
