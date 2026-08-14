"""The Gate 1 fixture: format, validation and agreement arithmetic."""

from __future__ import annotations

from pathlib import Path

import pytest

from basketball_scout.config import REPO_ROOT
from basketball_scout.video.ground_truth import (
    BASE_COLUMNS,
    GroundTruthError,
    GroundTruthRow,
    agreement,
    columns,
    load_rows,
    write_rows,
    write_template,
)
from basketball_scout.video.metrics import DEFAULT_METRICS

TRACKED_FIXTURE = REPO_ROOT / "data" / "validation" / "video_events_ground_truth.csv"
URL = "https://www.youtube.com/watch?v=EXAMPLE"


def make_row(event_id: str, human: dict, model: dict) -> GroundTruthRow:
    return GroundTruthRow(
        event_id=event_id, video_url=URL, event_seconds=600, human=human, model=model
    )


def test_columns_cover_base_fields_and_every_metric():
    cols = columns()
    assert cols[: len(BASE_COLUMNS)] == list(BASE_COLUMNS)
    assert cols[-1] == "notes"
    for metric in DEFAULT_METRICS:
        assert {f"human_{metric.key}", f"model_{metric.key}", f"match_{metric.key}"} <= set(cols)


def test_tracked_fixture_exists_and_is_empty_of_labels():
    """It must ship header-only: a fabricated label would corrupt Gate 1."""
    assert TRACKED_FIXTURE.is_file(), "the Gate 1 fixture must be committed"
    assert load_rows(TRACKED_FIXTURE) == []


def test_tracked_fixture_header_matches_the_current_registry():
    header = TRACKED_FIXTURE.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.strip().split(",") == columns()


def test_template_write_and_roundtrip(tmp_path: Path):
    path = write_template(tmp_path / "gt.csv")
    assert load_rows(path) == []

    rows = [
        make_row("E1", {"shot_contest": "open"}, {"shot_contest": "open"}),
        make_row("E2", {"shot_contest": "contested"}, {}),
    ]
    write_rows(path, rows)
    loaded = load_rows(path)
    assert [r.event_id for r in loaded] == ["E1", "E2"]
    assert loaded[0].human["shot_contest"] == "open"
    assert loaded[0].match("shot_contest") is True
    assert loaded[1].match("shot_contest") is None  # no model label yet


def test_labels_are_case_insensitive_but_must_be_valid(tmp_path: Path):
    path = write_template(tmp_path / "gt.csv")
    write_rows(path, [make_row("E1", {"shot_contest": "OPEN"}, {})])
    assert load_rows(path)[0].human["shot_contest"] == "open"


def test_invalid_label_is_rejected_loudly(tmp_path: Path):
    path = tmp_path / "gt.csv"
    write_template(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("E1,G1," + URL + ",600,,,,,,,definitely_open,,,,,,,,,\n")
    with pytest.raises(GroundTruthError, match="invalid human_shot_contest"):
        load_rows(path)


def test_duplicate_event_ids_are_rejected(tmp_path: Path):
    path = write_template(tmp_path / "gt.csv")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"E1,,{URL},600" + "," * 16 + "\n")
        handle.write(f"E1,,{URL},700" + "," * 16 + "\n")
    with pytest.raises(GroundTruthError, match="duplicate event_id"):
        load_rows(path)


def test_missing_columns_are_reported(tmp_path: Path):
    path = tmp_path / "broken.csv"
    path.write_text("event_id,notes\nE1,hi\n", encoding="utf-8")
    with pytest.raises(GroundTruthError, match="missing columns"):
        load_rows(path)


def test_missing_file_is_reported(tmp_path: Path):
    with pytest.raises(GroundTruthError, match="not found"):
        load_rows(tmp_path / "nope.csv")


def test_row_derives_a_window_from_event_seconds():
    row = make_row("E1", {}, {})
    window = row.window()
    assert (window.start_seconds, window.end_seconds) == (580, 608)


def test_explicit_window_overrides_event_seconds():
    row = GroundTruthRow(event_id="E1", video_url=URL, event_seconds=600, start_seconds=10, end_seconds=25)
    assert (row.window().start_seconds, row.window().end_seconds) == (10, 25)


def test_row_without_any_timing_cannot_produce_a_window():
    with pytest.raises(GroundTruthError, match="event_seconds"):
        GroundTruthRow(event_id="E1", video_url=URL).window()


def test_row_converts_to_a_shot_event():
    row = GroundTruthRow(
        event_id="E1", video_url=URL, event_seconds=600, period=2, team="Team A",
        pbp_description="2PT shot missed",
    )
    event = row.to_shot_event()
    assert event.event_id == "E1" and event.period == 2
    assert "Team A" in event.context_line()


def test_agreement_counts_only_comparable_pairs():
    rows = [
        make_row("E1", {"shot_contest": "open"}, {"shot_contest": "open"}),
        make_row("E2", {"shot_contest": "open"}, {"shot_contest": "contested"}),
        make_row("E3", {"shot_contest": "contested"}, {}),          # not classified yet
        make_row("E4", {}, {"shot_contest": "open"}),               # not labelled yet
    ]
    result = agreement(rows)["shot_contest"]
    assert result.compared == 2
    assert result.agreed == 1
    assert result.agreement_rate == pytest.approx(0.5)


def test_decisive_agreement_excludes_uncertain_on_either_side():
    """A model answering 'uncertain' everywhere must not read as agreement."""
    rows = [
        make_row("E1", {"shot_contest": "open"}, {"shot_contest": "open"}),
        make_row("E2", {"shot_contest": "uncertain"}, {"shot_contest": "uncertain"}),
        make_row("E3", {"shot_contest": "contested"}, {"shot_contest": "uncertain"}),
    ]
    result = agreement(rows)["shot_contest"]
    assert result.compared == 3
    assert result.agreed == 2                     # E1 and E2 both "match"
    assert result.decisive_compared == 1          # only E1 is a real decision
    assert result.decisive_agreed == 1
    assert result.decisive_agreement_rate == pytest.approx(1.0)
    assert result.human_uncertain == 1
    assert result.model_uncertain == 2


def test_writing_with_a_metric_subset_would_drop_other_columns(tmp_path: Path):
    """Documents why --update-fixture must always write the FULL registry.

    Writing a subset silently discards the other metrics' columns — and with
    them, hand-entered human labels. The CLI guards against this; this test
    pins the underlying behaviour so the guard is not removed by accident.
    """
    from basketball_scout.video.metrics import select_metrics

    path = write_template(tmp_path / "gt.csv")
    write_rows(
        path,
        [make_row("E1", {"shot_contest": "open", "shot_creation": "off_dribble"}, {})],
    )
    assert "human_shot_creation" in path.read_text(encoding="utf-8")

    subset = select_metrics(["shot_contest"])
    write_rows(path, load_rows(path, subset), subset)
    assert "human_shot_creation" not in path.read_text(encoding="utf-8")

    # Round-tripping through the full registry preserves everything.
    path2 = write_template(tmp_path / "gt2.csv")
    rows = [make_row("E1", {"shot_contest": "open", "shot_creation": "off_dribble"}, {})]
    write_rows(path2, rows)
    write_rows(path2, load_rows(path2, DEFAULT_METRICS), DEFAULT_METRICS)
    assert load_rows(path2)[0].human["shot_creation"] == "off_dribble"


def test_agreement_on_no_data_is_none_not_zero():
    """An unmeasured metric must not look like a metric that scored 0%."""
    result = agreement([])["shot_contest"]
    assert result.compared == 0
    assert result.agreement_rate is None
    assert result.decisive_agreement_rate is None
    assert "n/a" in result.summary_line()
