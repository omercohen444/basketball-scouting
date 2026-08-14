"""Video analytics: localize a PBP event in a broadcast, classify it, aggregate.

Layering (keep it this way — it is what makes the provider replaceable):

    metrics.py       what we ask                (no provider, no schema)
    events.py        which seconds to look at   (no provider)
    schema.py        what a valid answer is     (no provider)
    prompts.py       how we ask                 (no provider)
    gemini_client.py how we call Gemini         (the ONLY provider-aware module)
"""

from .events import ShotEvent, VideoWindow, parse_timecode, window_around
from .metrics import DEFAULT_METRICS, UNCERTAIN, MetricDefinition, select_metrics
from .schema import (
    ClassifiedEvent,
    MetricOutcome,
    build_classification_model,
    classification_model_for,
    outcomes_from_response,
)

__all__ = [
    "DEFAULT_METRICS",
    "UNCERTAIN",
    "ClassifiedEvent",
    "MetricDefinition",
    "MetricOutcome",
    "ShotEvent",
    "VideoWindow",
    "build_classification_model",
    "classification_model_for",
    "outcomes_from_response",
    "parse_timecode",
    "select_metrics",
    "window_around",
]
