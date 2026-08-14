"""The structured contract: valid classifications in, invalid ones rejected."""

from __future__ import annotations

import pytest

from basketball_scout.video.metrics import DEFAULT_METRICS, MetricDefinition, select_metrics
from basketball_scout.video.schema import (
    ClassificationSchemaError,
    ClassifiedEvent,
    MetricOutcome,
    build_classification_model,
    classification_model_for,
    outcomes_from_response,
    parse_json_payload,
)


def valid_payload() -> dict:
    return {
        "shot_contest": "contested",
        "shot_contest_confidence": 0.82,
        "shot_contest_evidence": "Defender closing out with a hand up at release.",
        "possession_type": "half_court",
        "possession_type_confidence": 0.9,
        "possession_type_evidence": "Defence was set before the entry pass.",
        "shot_creation": "uncertain",
        "shot_creation_confidence": 0.1,
        "shot_creation_evidence": "The catch happened off-camera.",
    }


def test_model_has_label_confidence_and_evidence_for_each_metric():
    fields = list(classification_model_for(DEFAULT_METRICS).model_fields)
    for metric in DEFAULT_METRICS:
        assert metric.key in fields
        assert f"{metric.key}_confidence" in fields
        assert f"{metric.key}_evidence" in fields
    assert len(fields) == 3 * len(DEFAULT_METRICS)


def test_valid_payload_flattens_into_outcomes():
    outcomes = outcomes_from_response(valid_payload())
    assert outcomes["shot_contest"].label == "contested"
    assert outcomes["shot_contest"].confidence == pytest.approx(0.82)
    assert outcomes["possession_type"].label == "half_court"
    assert outcomes["shot_creation"].is_uncertain


def test_uncertain_is_accepted_for_every_metric():
    payload = {}
    for metric in DEFAULT_METRICS:
        payload[metric.key] = "uncertain"
        payload[f"{metric.key}_confidence"] = 0.0
        payload[f"{metric.key}_evidence"] = "Not visible."
    outcomes = outcomes_from_response(payload)
    assert all(o.is_uncertain for o in outcomes.values())


def test_label_outside_the_metric_label_set_is_rejected():
    payload = valid_payload()
    payload["shot_contest"] = "somewhat_contested"
    with pytest.raises(ClassificationSchemaError):
        outcomes_from_response(payload)


def test_a_label_belonging_to_a_different_metric_is_rejected():
    """Labels must not be interchangeable across metrics."""
    payload = valid_payload()
    payload["shot_contest"] = "transition"
    with pytest.raises(ClassificationSchemaError):
        outcomes_from_response(payload)


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.5, "high"])
def test_out_of_range_confidence_is_rejected(bad_confidence):
    payload = valid_payload()
    payload["shot_contest_confidence"] = bad_confidence
    with pytest.raises(ClassificationSchemaError):
        outcomes_from_response(payload)


def test_missing_field_is_rejected():
    payload = valid_payload()
    del payload["possession_type"]
    with pytest.raises(ClassificationSchemaError):
        outcomes_from_response(payload)


def test_unexpected_extra_field_is_ignored_not_fatal():
    """A stray key must not throw away an otherwise valid classification."""
    payload = valid_payload() | {"model_commentary": "nice shot"}
    assert outcomes_from_response(payload)["shot_contest"].label == "contested"


def test_schema_follows_a_replaced_metric():
    """Replacing a provisional metric must not require editing the schema."""
    replacement = MetricDefinition(
        key="shot_zone",
        title="Rim vs Perimeter",
        question="Was the shot at the rim or from the perimeter?",
        labels=("rim", "perimeter"),
    )
    model = build_classification_model((replacement,))
    assert set(model.model_fields) == {"shot_zone", "shot_zone_confidence", "shot_zone_evidence"}
    outcomes = outcomes_from_response(
        {"shot_zone": "rim", "shot_zone_confidence": 0.7, "shot_zone_evidence": "Layup."},
        (replacement,),
    )
    assert outcomes["shot_zone"].label == "rim"


def test_a_metric_subset_produces_a_smaller_schema():
    metrics = select_metrics(["shot_contest"])
    assert set(classification_model_for(metrics).model_fields) == {
        "shot_contest",
        "shot_contest_confidence",
        "shot_contest_evidence",
    }


def test_empty_metric_set_is_rejected():
    with pytest.raises(ClassificationSchemaError):
        build_classification_model(())


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go:\n{"a": 1}\nHope that helps.'],
)
def test_json_is_recovered_from_a_fenced_or_chatty_response(text):
    assert parse_json_payload(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "   ", "no json here", "[1, 2, 3]"])
def test_unusable_responses_raise_clearly(text):
    with pytest.raises(ClassificationSchemaError):
        parse_json_payload(text)


def test_classified_event_reports_success_and_failure():
    ok = ClassifiedEvent(
        event_id="E1",
        outcomes={"shot_contest": MetricOutcome(key="shot_contest", label="open", confidence=0.5)},
    )
    assert ok.ok and ok.label("shot_contest") == "open"
    assert ok.label("missing_metric") is None

    failed = ClassifiedEvent(event_id="E2", error="429 rate limited")
    assert not failed.ok
    assert "429" in failed.summary_line()
