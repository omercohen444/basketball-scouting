"""The metric registry is the thing everything else derives from."""

from __future__ import annotations

import pytest

from basketball_scout.video.metrics import (
    DEFAULT_METRICS,
    UNCERTAIN,
    MetricDefinition,
    MetricError,
    select_metrics,
)


def test_default_registry_matches_the_agreed_candidate_metrics():
    assert [m.key for m in DEFAULT_METRICS] == [
        "shot_contest",
        "possession_type",
        "shot_creation",
    ]
    assert [m.labels for m in DEFAULT_METRICS] == [
        ("open", "contested"),
        ("transition", "half_court"),
        ("catch_and_shoot", "off_dribble"),
    ]


def test_every_metric_offers_uncertain():
    """A model that cannot see enough must always be able to say so."""
    for metric in DEFAULT_METRICS:
        assert metric.all_labels[-1] == UNCERTAIN
        assert metric.is_valid_label(UNCERTAIN)
        assert not metric.is_valid_label("definitely_not_a_label")


def test_metric_definitions_are_internally_consistent():
    for metric in DEFAULT_METRICS:
        assert metric.question.strip()
        assert metric.uncertain_when.strip(), f"{metric.key} must say when to answer uncertain"
        assert set(metric.guidance) == set(metric.labels)


def test_uncertain_may_not_be_declared_explicitly():
    with pytest.raises(MetricError, match="must not list"):
        MetricDefinition(
            key="k", title="T", question="Q?", labels=("a", UNCERTAIN)
        )


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"key": "bad key!", "labels": ("a", "b")}, "snake_case"),
        ({"key": "k", "labels": ("only",)}, "at least 2"),
        ({"key": "k", "labels": ("a", "a")}, "duplicate"),
    ],
)
def test_malformed_metric_definitions_are_rejected(kwargs, match):
    with pytest.raises(MetricError, match=match):
        MetricDefinition(title="T", question="Q?", **kwargs)


def test_guidance_for_an_unknown_label_is_rejected():
    with pytest.raises(MetricError, match="unknown labels"):
        MetricDefinition(
            key="k", title="T", question="Q?", labels=("a", "b"), guidance={"c": "?"}
        )


def test_select_metrics_returns_requested_order_and_rejects_unknown():
    picked = select_metrics(["shot_creation", "shot_contest"])
    assert [m.key for m in picked] == ["shot_creation", "shot_contest"]
    assert select_metrics(None) == DEFAULT_METRICS
    assert select_metrics([]) == DEFAULT_METRICS

    with pytest.raises(MetricError, match="unknown metric"):
        select_metrics(["not_a_metric"])


def test_metrics_are_hashable_so_the_schema_cache_works():
    """The guidance dict must not make a definition unhashable."""
    assert len({*DEFAULT_METRICS}) == len(DEFAULT_METRICS)
