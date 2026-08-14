"""Temporal localization: PBP moment -> the seconds of video we send."""

from __future__ import annotations

import pytest

from basketball_scout.video.events import (
    MAX_WINDOW_SECONDS,
    ShotEvent,
    TimecodeError,
    VideoWindow,
    format_timecode,
    parse_timecode,
    to_offset_string,
    window_around,
)

URL = "https://www.youtube.com/watch?v=EXAMPLE"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1:02:03", 3723.0),
        ("12:30", 750.0),
        ("00:45", 45.0),
        ("754", 754.0),
        ("754s", 754.0),
        (754, 754.0),
        (12.5, 12.5),
        ("12:30.5", 750.5),
    ],
)
def test_timecode_formats_a_human_would_type(raw, expected):
    assert parse_timecode(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "-5", "1:2:3:4", None])
def test_unparseable_timecodes_raise(raw):
    with pytest.raises((TimecodeError, TypeError, AttributeError)):
        parse_timecode(raw)


def test_offset_string_matches_the_protobuf_duration_format():
    """Verified against google-genai: VideoMetadata offsets are strings like '754s'."""
    assert to_offset_string(754) == "754s"
    assert to_offset_string(0) == "0s"
    assert to_offset_string(12.25) == "12.25s"
    with pytest.raises(TimecodeError):
        to_offset_string(-1)


def test_format_timecode_is_readable():
    assert format_timecode(3723) == "1:02:03"
    assert format_timecode(750) == "12:30"


def test_window_exposes_offsets_and_duration():
    window = VideoWindow(video_url=URL, start_seconds=100, end_seconds=112)
    assert (window.start_offset, window.end_offset) == ("100s", "112s")
    assert window.duration_seconds == 12
    assert window.to_dict()["video_url"] == URL


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"video_url": "", "start_seconds": 0, "end_seconds": 10}, "video_url"),
        ({"video_url": URL, "start_seconds": -1, "end_seconds": 10}, "start_seconds"),
        ({"video_url": URL, "start_seconds": 10, "end_seconds": 10}, "must be after"),
        ({"video_url": URL, "start_seconds": 10, "end_seconds": 5}, "must be after"),
    ],
)
def test_malformed_windows_are_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        VideoWindow(**kwargs)


def test_an_absurdly_long_window_is_refused():
    """Guards against quietly submitting half a game to the model."""
    with pytest.raises(ValueError, match="guard"):
        VideoWindow(video_url=URL, start_seconds=0, end_seconds=MAX_WINDOW_SECONDS + 1)


def test_window_around_favours_the_lead_up_to_the_shot():
    window = window_around(URL, "1:00:00")
    assert window.start_seconds == 3600 - 20
    assert window.end_seconds == 3600 + 8


def test_window_around_clamps_at_the_start_of_the_video():
    window = window_around(URL, 3, pre_roll=8, post_roll=4)
    assert window.start_seconds == 0
    assert window.end_seconds == 7


def test_shot_event_requires_an_id():
    window = window_around(URL, 100)
    with pytest.raises(ValueError, match="event_id"):
        ShotEvent(event_id="", window=window)


def test_context_line_states_facts_without_hinting_at_an_answer():
    event = ShotEvent(
        event_id="E1",
        window=window_around(URL, 100),
        team="Team A",
        period=3,
        game_clock="04:12",
        description="3PT shot made",
    )
    line = event.context_line()
    assert "Q3" in line and "Team A" in line and "3PT shot made" in line
    # It must not leak the labels the model is being asked to choose between.
    for leak in ("open", "contested", "transition", "half_court", "catch_and_shoot"):
        assert leak not in line


def test_context_line_survives_missing_pbp_context():
    event = ShotEvent(event_id="E1", window=window_around(URL, 100))
    assert "no play-by-play context" in event.context_line()
