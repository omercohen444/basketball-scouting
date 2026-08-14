"""Structured contract for video classification results.

The response model is *built from* the metric registry rather than hard-coded,
so replacing a provisional metric is a one-line edit in ``metrics.py``. This is
the "small extensible schema" the spike needs — not the product database.

Two layers:

``build_classification_model()``
    The pydantic model the multimodal provider must return. One label field,
    one confidence field and one evidence field per metric.

``ClassifiedEvent``
    What we persist: the event, the raw + validated model answer, and enough
    provenance to reproduce or audit the call.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .metrics import DEFAULT_METRICS, UNCERTAIN, MetricDefinition

CONFIDENCE_FIELD_SUFFIX = "_confidence"
EVIDENCE_FIELD_SUFFIX = "_evidence"


class ClassificationSchemaError(ValueError):
    """Raised when a provider response cannot be turned into a valid result."""


def _metric_fields(metric: MetricDefinition) -> dict[str, tuple[Any, Any]]:
    label_type = Literal[metric.all_labels]  # type: ignore[valid-type]
    return {
        metric.key: (
            label_type,
            Field(description=f"{metric.question} One of: {', '.join(metric.all_labels)}."),
        ),
        f"{metric.key}{CONFIDENCE_FIELD_SUFFIX}": (
            Annotated[float, Field(ge=0.0, le=1.0)],
            Field(
                description=(
                    "Confidence from 0.0 to 1.0 in the "
                    f"{metric.key} label. Use a low value rather than guessing."
                )
            ),
        ),
        f"{metric.key}{EVIDENCE_FIELD_SUFFIX}": (
            str,
            Field(
                description=(
                    "One short sentence describing what was visible in the clip that "
                    f"justifies the {metric.key} label."
                )
            ),
        ),
    }


def build_classification_model(
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
    *,
    name: str = "VideoEventClassification",
) -> type[BaseModel]:
    """Create the pydantic response model for the given metric set.

    ``extra="ignore"``: during a risk spike, an unexpected extra key from the
    provider should not throw away an otherwise valid classification. Label
    validity is still strict — the ``Literal`` types reject anything outside the
    permitted label set.
    """
    if not metrics:
        raise ClassificationSchemaError("at least one metric is required")

    fields: dict[str, tuple[Any, Any]] = {}
    for metric in metrics:
        for field_name, spec in _metric_fields(metric).items():
            if field_name in fields:
                raise ClassificationSchemaError(f"duplicate field {field_name!r} across metrics")
            fields[field_name] = spec

    return create_model(  # type: ignore[call-overload,no-any-return]
        name,
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )


@lru_cache(maxsize=8)
def _cached_model(metrics: tuple[MetricDefinition, ...]) -> type[BaseModel]:
    return build_classification_model(metrics)


def classification_model_for(
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> type[BaseModel]:
    """Cached :func:`build_classification_model` — cheap to call per event."""
    return _cached_model(tuple(metrics))


class MetricOutcome(BaseModel):
    """A single metric's answer, flattened for storage and comparison."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""

    @property
    def is_uncertain(self) -> bool:
        return self.label == UNCERTAIN


class ClassifiedEvent(BaseModel):
    """One event, classified — the unit written to disk during the risk stage."""

    model_config = ConfigDict(extra="allow")

    event_id: str
    game_id: str = ""
    video_url: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    outcomes: dict[str, MetricOutcome] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    schema_version: str = ""
    media_resolution: str | None = None
    fps: float | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latency_seconds: float | None = None
    raw_text: str | None = None
    error: str | None = None
    finish_reason: str | None = None
    # {"prompt": int, "video": int|None, "output": int, "thoughts": int|None, "total": int}
    # video-token presence/absence is itself CP1-A's evidence — see gemini_client.py
    usage: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.outcomes)

    def label(self, metric_key: str) -> str | None:
        outcome = self.outcomes.get(metric_key)
        return outcome.label if outcome else None

    def summary_line(self) -> str:
        if self.error:
            return f"{self.event_id}: ERROR {self.error}"
        parts = [
            f"{key}={o.label}({o.confidence:.2f})" for key, o in sorted(self.outcomes.items())
        ]
        return f"{self.event_id}: " + "  ".join(parts)


def outcomes_from_response(
    payload: dict[str, Any],
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> dict[str, MetricOutcome]:
    """Validate a provider payload and flatten it into per-metric outcomes.

    Raises :class:`ClassificationSchemaError` if any label is outside the
    permitted set for its metric.
    """
    model_cls = classification_model_for(metrics)
    try:
        validated = model_cls.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError, but keep the boundary simple
        raise ClassificationSchemaError(str(exc)) from exc

    data = validated.model_dump()
    return {
        metric.key: MetricOutcome(
            key=metric.key,
            label=data[metric.key],
            confidence=data[f"{metric.key}{CONFIDENCE_FIELD_SUFFIX}"],
            evidence=data.get(f"{metric.key}{EVIDENCE_FIELD_SUFFIX}", ""),
        )
        for metric in metrics
    }


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_payload(text: str) -> dict[str, Any]:
    """Parse a model's JSON answer, tolerating a stray code fence.

    Structured-output mode should return bare JSON, but a risk spike should not
    fail an otherwise good classification over a markdown fence.
    """
    if not text or not text.strip():
        raise ClassificationSchemaError("provider returned an empty response")

    candidate = text.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(candidate)
        if not match:
            raise ClassificationSchemaError(
                f"response is not JSON and contains no JSON object: {candidate[:200]!r}"
            ) from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ClassificationSchemaError(f"malformed JSON in response: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ClassificationSchemaError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed
