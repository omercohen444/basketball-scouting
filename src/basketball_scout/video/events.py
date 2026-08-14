"""Shot events and the video windows they are localized to.

The pipeline being tested is::

    PBP event -> video time window -> multimodal classification -> aggregation

This module owns the middle-left arrow: turning "a shot happened at this point
in the game" into "look at these seconds of this video". Nothing here talks to
an API or to a data source, so it stays usable when the PBP source or the video
provider changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Broadcast footage carries a lag between a PBP timestamp and what is on screen,
# so windows are padded generously on both sides by default.
#
# Derived from an explicit error budget, not inherited (docs/VIDEO_STAGE_PLAN.md
# §8.3): operator entry lag (0-6s) + calibration residual (+-5s) + possession
# lead-in (8s) needed before the release + 3s to confirm the outcome after it.
# Tighten only on CP1-D evidence.
DEFAULT_PRE_ROLL_SECONDS = 20.0
DEFAULT_POST_ROLL_SECONDS = 8.0

# A guard against accidentally submitting a whole half. Not an API limit — a
# cost and attention limit for the risk stage.
MAX_WINDOW_SECONDS = 120.0

_TIMECODE_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


class TimecodeError(ValueError):
    """Raised for an unparseable timecode."""


def parse_timecode(value: str | int | float) -> float:
    """Parse ``HH:MM:SS``, ``MM:SS``, ``123``, ``123s`` or a number into seconds.

    >>> parse_timecode("1:02:03")
    3723.0
    >>> parse_timecode("12:30")
    750.0
    >>> parse_timecode("754s")
    754.0
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if not text:
            raise TimecodeError("empty timecode")
        if text.endswith("s") and ":" not in text:
            text = text[:-1]
        match = _TIMECODE_RE.match(text)
        if match:
            hours, minutes, secs = match.groups()
            seconds = float(hours or 0) * 3600 + float(minutes) * 60 + float(secs)
        else:
            try:
                seconds = float(text)
            except ValueError as exc:
                raise TimecodeError(f"cannot parse timecode {value!r}") from exc

    if seconds < 0:
        raise TimecodeError(f"timecode cannot be negative: {value!r}")
    return seconds


def to_offset_string(seconds: float) -> str:
    """Format seconds as the protobuf duration string the Gemini API expects.

    Verified against google-genai 2.18.1: ``types.VideoMetadata.start_offset`` /
    ``end_offset`` are ``Optional[str]`` and serialize to ``startOffset`` /
    ``endOffset``.

    >>> to_offset_string(754)
    '754s'
    >>> to_offset_string(12.25)
    '12.25s'
    """
    if seconds < 0:
        raise TimecodeError(f"offset cannot be negative: {seconds}")
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{round(float(seconds), 3)}s"


def format_timecode(seconds: float) -> str:
    """Human-readable ``H:MM:SS`` for logs and debug artifacts."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class VideoWindow:
    """A bounded slice of one video, in video-clock seconds."""

    video_url: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if not self.video_url or not self.video_url.strip():
            raise ValueError("video_url is required")
        if self.start_seconds < 0:
            raise ValueError(f"start_seconds must be >= 0, got {self.start_seconds}")
        if self.end_seconds <= self.start_seconds:
            raise ValueError(
                f"end_seconds ({self.end_seconds}) must be after "
                f"start_seconds ({self.start_seconds})"
            )
        if self.duration_seconds > MAX_WINDOW_SECONDS:
            raise ValueError(
                f"window of {self.duration_seconds:.0f}s exceeds the "
                f"{MAX_WINDOW_SECONDS:.0f}s guard. Localize the event more tightly, "
                "or raise MAX_WINDOW_SECONDS deliberately."
            )

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def start_offset(self) -> str:
        return to_offset_string(self.start_seconds)

    @property
    def end_offset(self) -> str:
        return to_offset_string(self.end_seconds)

    def label(self) -> str:
        return (
            f"{format_timecode(self.start_seconds)}–{format_timecode(self.end_seconds)}"
            f" ({self.duration_seconds:.0f}s)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_url": self.video_url,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "duration_seconds": self.duration_seconds,
        }


def window_around(
    video_url: str,
    event_seconds: float | str,
    *,
    pre_roll: float = DEFAULT_PRE_ROLL_SECONDS,
    post_roll: float = DEFAULT_POST_ROLL_SECONDS,
) -> VideoWindow:
    """Build a window centred on a localized event, clamped at zero.

    ``pre_roll`` is the larger side on purpose: transition-vs-half-court and
    catch-and-shoot-vs-off-dribble are both decided by what happened *before*
    the release.
    """
    centre = parse_timecode(event_seconds)
    start = max(0.0, centre - pre_roll)
    return VideoWindow(video_url=video_url, start_seconds=start, end_seconds=centre + post_roll)


@dataclass(frozen=True)
class ShotEvent:
    """A PBP shot event localized to a video window.

    ``pbp`` deliberately holds the raw source row so the spike can carry
    whatever the PBP provider gave us without this module needing to know that
    provider's schema.
    """

    event_id: str
    window: VideoWindow
    game_id: str = ""
    team: str = ""
    period: int | None = None
    game_clock: str = ""
    description: str = ""
    # Disambiguation fields (docs/VIDEO_STAGE_PLAN.md §8.4): identify WHICH
    # shot in a widened window without leaking the labels being measured.
    # Deliberately excluded here: outcome/made, fastBreak, assisted, coords.
    jersey: str = ""
    shot_type: str = ""
    points: int | None = None
    pbp: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not str(self.event_id).strip():
            raise ValueError("event_id is required")

    @property
    def video_url(self) -> str:
        return self.window.video_url

    def context_line(self) -> str:
        """One-line game context handed to the model alongside the video.

        Kept minimal and factual. It tells the model *which* action to look at;
        it must not tell the model what to conclude about that action.
        """
        bits: list[str] = []
        if self.period is not None:
            bits.append(f"Q{self.period}")
        if self.game_clock:
            bits.append(f"game clock {self.game_clock}")
        if self.team:
            bits.append(f"shooting team: {self.team}")
        if self.jersey:
            bits.append(f"shooter jersey #{self.jersey}")
        if self.shot_type:
            bits.append(f"attempt type: {self.shot_type}")
        if self.points:
            bits.append(f"{self.points}pt attempt")
        if self.description:
            bits.append(f"play-by-play: {self.description}")
        return "; ".join(bits) if bits else "no play-by-play context available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "game_id": self.game_id,
            "team": self.team,
            "period": self.period,
            "game_clock": self.game_clock,
            "description": self.description,
            "window": self.window.to_dict(),
            "pbp": self.pbp,
        }
