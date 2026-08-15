"""Scoring runs and scoring/FG droughts — deterministic, from the scoring timeline.

**Runs** (enrichment brief §13): a scoring run is consecutive points by one
team without the opponent scoring. Non-scoring events (turnovers, misses,
fouls) never end a run — only the *opponent scoring* does. Period
boundaries do not end a run either (a run can span a quarter break).

**Droughts** (§14) are an explicit **custom project metric, not an official
FIBA category** — continuous *playing time* (quarter clock, never wall
time, never bridging a quarter boundary) without a score:

* scoring drought: no point by either the profiled team specifically —
  i.e. this is the team's own *offensive* drought (the team not scoring),
  not "neither team scored"; free throws DO end a scoring drought.
* FG drought: no *made field goal* by the team — free throws do **not**
  end this one, only a made 2PT/3PT shot does.

Threshold for both: **>= 3:00 (180s)** of elapsed clock time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .scoring_timeline import ScoringPlay

DROUGHT_THRESHOLD_SECONDS = 180.0


# ---- Runs -------------------------------------------------------------

@dataclass(frozen=True)
class RunsProfile:
    largest_scoring_run_for: int
    largest_scoring_run_against: int
    runs_8_plus_for: int
    runs_8_plus_against: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _runs_by_team(plays: list[ScoringPlay]) -> dict[str, list[int]]:
    """All completed run sizes per team, from a single game's timeline.

    A run accumulates while the same team keeps scoring; it ends (and is
    recorded) the instant the *other* team scores, or at the end of the
    play list. One continuous 12-0 run is one run of 12, never split.
    """
    runs: dict[str, list[int]] = {"home": [], "away": []}
    current_team: str | None = None
    current_total = 0
    for play in plays:
        if play.team == current_team:
            current_total += play.points
        else:
            if current_team is not None and current_total > 0:
                runs[current_team].append(current_total)
            current_team = play.team
            current_total = play.points
    if current_team is not None and current_total > 0:
        runs[current_team].append(current_total)
    return runs


def build_runs_profile(plays: list[ScoringPlay], *, team_side: str) -> RunsProfile:
    """``team_side`` is ``"home"`` or ``"away"`` — the team this profile is FOR."""
    other = "away" if team_side == "home" else "home"
    runs = _runs_by_team(plays)
    for_runs = runs[team_side]
    against_runs = runs[other]
    return RunsProfile(
        largest_scoring_run_for=max(for_runs, default=0),
        largest_scoring_run_against=max(against_runs, default=0),
        runs_8_plus_for=sum(1 for r in for_runs if r >= 8),
        runs_8_plus_against=sum(1 for r in against_runs if r >= 8),
    )


# ---- Droughts -----------------------------------------------------------

@dataclass(frozen=True)
class DroughtsProfile:
    drought_count_3m_plus: int
    longest_scoring_drought_seconds: float
    fg_drought_count_3m_plus: int
    longest_fg_drought_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _quarter_length(quarter: int, regulation_periods: int) -> float:
    return 600.0 if quarter <= regulation_periods else 300.0


def _drought_gaps(
    team_plays: list[ScoringPlay], regulation_periods: int, *, field_goals_only: bool
) -> list[float]:
    """Elapsed-clock gaps (seconds) between this team's own qualifying
    scores, within a period only — never bridged across a quarter boundary.
    Includes the gap from period start to the first score, and from the
    last score to period end, since those are real scoreless stretches too.
    """
    if field_goals_only:
        team_plays = [p for p in team_plays if p.is_field_goal]

    by_quarter: dict[int, list[ScoringPlay]] = {}
    for p in team_plays:
        by_quarter.setdefault(p.quarter, []).append(p)

    gaps: list[float] = []
    for quarter, plays in by_quarter.items():
        length = _quarter_length(quarter, regulation_periods)
        # plays are already in chronological (quarter,id) order upstream;
        # clock_s is a countdown, so chronological order is descending clock.
        clocks = [p.clock_s for p in plays if p.clock_s is not None]
        if not clocks:
            continue
        gaps.append(length - clocks[0])  # period start -> first score
        for a, b in zip(clocks, clocks[1:]):
            gaps.append(a - b)
        gaps.append(clocks[-1])  # last score -> period end
    return [g for g in gaps if g >= 0]


def build_droughts_profile(
    all_plays: list[ScoringPlay], *, team_side: str, regulation_periods: int
) -> DroughtsProfile:
    team_plays = [p for p in all_plays if p.team == team_side]

    scoring_gaps = _drought_gaps(team_plays, regulation_periods, field_goals_only=False)
    fg_gaps = _drought_gaps(team_plays, regulation_periods, field_goals_only=True)

    return DroughtsProfile(
        drought_count_3m_plus=sum(1 for g in scoring_gaps if g >= DROUGHT_THRESHOLD_SECONDS),
        longest_scoring_drought_seconds=max(scoring_gaps, default=0.0),
        fg_drought_count_3m_plus=sum(1 for g in fg_gaps if g >= DROUGHT_THRESHOLD_SECONDS),
        longest_fg_drought_seconds=max(fg_gaps, default=0.0),
    )
