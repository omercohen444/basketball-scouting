"""Scoring-source statistics: points off turnovers, second chance, fast break,
assisted/unassisted profile, and shot/scoring mix — all derived purely from
:class:`Possession` records (enrichment brief §10-12).

Every function here takes ``team_possessions`` (the team's own offensive
possessions for the scope in question — a game, a window, a segment) and,
where an AGAINST value is requested, the caller passes the opponent's own
offensive possessions for the identical scope. There is one normalized
computation, queried from both perspectives — no separate "against" formula
exists anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .possession import Possession


def _sum_points(possessions: list[Possession]) -> int:
    return sum(p.points for p in possessions)


# ---- 10A. Points off turnovers --------------------------------------------

def points_off_turnovers(team_possessions: list[Possession]) -> int:
    """FIBA definition: points scored during the possession immediately
    following an opposition turnover, from FGM and/or valid FTM in that
    possession — never extended into a later possession."""
    return sum(p.points for p in team_possessions if p.followed_opponent_turnover)


def opponent_turnovers(opponent_possessions: list[Possession]) -> int:
    return sum(1 for p in opponent_possessions if p.turnover)


@dataclass(frozen=True)
class PointsOffTurnoversProfile:
    points_off_turnovers: int
    points_off_turnovers_per_game: float | None
    points_off_turnovers_share_of_points: float | None
    opponent_turnovers: int
    points_per_opponent_turnover: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_points_off_turnovers_profile(
    team_possessions: list[Possession], opponent_possessions: list[Possession], *, games_n: int
) -> PointsOffTurnoversProfile:
    pot = points_off_turnovers(team_possessions)
    total_points = _sum_points(team_possessions)
    opp_tov = opponent_turnovers(opponent_possessions)
    return PointsOffTurnoversProfile(
        points_off_turnovers=pot,
        points_off_turnovers_per_game=(pot / games_n) if games_n > 0 else None,
        points_off_turnovers_share_of_points=(pot / total_points) if total_points > 0 else None,
        opponent_turnovers=opp_tov,
        points_per_opponent_turnover=(pot / opp_tov) if opp_tov > 0 else None,
    )


# ---- 10B. Second-chance points --------------------------------------------

@dataclass(frozen=True)
class SecondChanceProfile:
    """Offensive-rebound consequences (§9, 2026-08-15 v2). All fields below
    ``second_chance_scoring_conversion`` are v2 additions built directly on
    top of the same OREB-possession subset — no double counting against
    ``second_chance_points``: each is a different view of the identical
    "after the first OREB, within the same possession" window.
    """

    offensive_rebound_possessions: int
    second_chance_points: int
    second_chance_points_per_game: float | None
    second_chance_points_share_of_points: float | None
    points_per_second_chance_possession: float | None
    second_chance_scoring_conversion: float | None  # % of OREB possessions scoring >=1 pt after the OREB
    zero_point_rate_after_oreb: float | None  # 1 - second_chance_scoring_conversion, kept explicit for readability
    scoring_oreb_possessions: int  # numerator behind second_chance_scoring_conversion — exposed for season recompute
    fga_after_oreb: int  # shot attempts taken after the first OREB, same possession
    fga_after_oreb_per_oreb_possession: float | None
    multi_oreb_possessions: int  # possessions with >=2 offensive rebounds (extra second chances)
    additional_orebs: int  # orb count beyond the first, summed across OREB possessions

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_second_chance_profile(team_possessions: list[Possession], *, games_n: int) -> SecondChanceProfile:
    oreb_possessions = [p for p in team_possessions if p.had_offensive_rebound]
    scp = sum(p.points_after_first_oreb for p in oreb_possessions)
    total_points = _sum_points(team_possessions)
    scoring_oreb_possessions = sum(1 for p in oreb_possessions if p.points_after_first_oreb > 0)
    conversion = (scoring_oreb_possessions / len(oreb_possessions)) if oreb_possessions else None
    fga_after = sum(p.fga_after_first_oreb for p in oreb_possessions)
    multi_oreb = sum(1 for p in oreb_possessions if p.orb >= 2)
    additional_orebs = sum(p.orb - 1 for p in oreb_possessions)  # orb>=1 guaranteed by had_offensive_rebound

    return SecondChanceProfile(
        offensive_rebound_possessions=len(oreb_possessions),
        second_chance_points=scp,
        second_chance_points_per_game=(scp / games_n) if games_n > 0 else None,
        second_chance_points_share_of_points=(scp / total_points) if total_points > 0 else None,
        points_per_second_chance_possession=(scp / len(oreb_possessions)) if oreb_possessions else None,
        second_chance_scoring_conversion=conversion,
        zero_point_rate_after_oreb=(1.0 - conversion) if conversion is not None else None,
        scoring_oreb_possessions=scoring_oreb_possessions,
        fga_after_oreb=fga_after,
        fga_after_oreb_per_oreb_possession=(fga_after / len(oreb_possessions)) if oreb_possessions else None,
        multi_oreb_possessions=multi_oreb,
        additional_orebs=additional_orebs,
    )


# ---- 10C. Provider-defined fast-break points -------------------------------

@dataclass(frozen=True)
class FastBreakProfile:
    """Explicitly provider-defined — Segev's own ``fastBreak`` flag on shot/FT
    actions, never reinterpreted as the video pipeline's ``possession_type``
    metric (a completely separate, human/model-judged concept). This is
    marked directly in ``source``.
    """

    provider_fast_break_points: int
    provider_fast_break_points_per_game: float | None
    provider_fast_break_share_of_points: float | None
    source: str = "segev_provider_flag"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_fast_break_profile(team_possessions: list[Possession], *, games_n: int) -> FastBreakProfile:
    fbp = sum(p.fast_break_points for p in team_possessions)
    total_points = _sum_points(team_possessions)
    return FastBreakProfile(
        provider_fast_break_points=fbp,
        provider_fast_break_points_per_game=(fbp / games_n) if games_n > 0 else None,
        provider_fast_break_share_of_points=(fbp / total_points) if total_points > 0 else None,
    )


# ---- 11. Assisted / unassisted scoring profile -----------------------------

@dataclass(frozen=True)
class AssistedProfile:
    """Shot-level assisted/unassisted attribution — deliberately a separate,
    stricter concept from the raw AST/TO assist count (``TeamGameComponents.ast``
    / ``Possession.raw_assist_count``, see ``segment_metrics.py``). A shot's
    attribution here can only be "resolved assisted" or "resolved
    unassisted" (mutually exclusive, always summing to FGM) — but some
    provider assist actions cannot be linked to any specific shot at all
    (2026-08-15 audit: ~709/732 season-wide have no plausible preceding made
    shot; the remaining ~23 are team-mismatched — confirmed genuinely
    unresolvable, not merely unattempted). Those are counted here as
    ``unresolved_assist_count``/``unresolved_assist_rate`` so a downstream
    agent can never mistake ``assisted_fgm_pct`` for 100%-coverage ground
    truth — it is a resolved-only figure with a known, quantified gap.
    """

    assisted_fgm: int
    unassisted_fgm: int
    assisted_fgm_pct: float | None
    unassisted_fgm_pct: float | None
    assisted_2pm: int
    unassisted_2pm: int
    assisted_2pm_pct: float | None
    unassisted_2pm_pct: float | None
    assisted_3pm: int
    unassisted_3pm: int
    assisted_3pm_pct: float | None
    unassisted_3pm_pct: float | None
    total_provider_assists: int  # all raw assist actions: resolved + unresolved
    resolved_shot_attributed_assists: int  # == assisted_fgm; named for provenance clarity
    unresolved_assist_count: int  # not attributed to any specific shot — see class docstring
    unresolved_assist_rate: float | None  # unresolved / total_provider_assists

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_assisted_profile(team_possessions: list[Possession], *, unresolved_assist_count: int = 0) -> AssistedProfile:
    assisted = sum(p.assisted_fgm for p in team_possessions)
    unassisted = sum(p.unassisted_fgm for p in team_possessions)
    total = assisted + unassisted
    assisted_2 = sum(p.fg2m_assisted for p in team_possessions)
    unassisted_2 = sum(p.fg2m_unassisted for p in team_possessions)
    total_2 = assisted_2 + unassisted_2
    assisted_3 = sum(p.fg3m_assisted for p in team_possessions)
    unassisted_3 = sum(p.fg3m_unassisted for p in team_possessions)
    total_3 = assisted_3 + unassisted_3
    total_provider_assists = assisted + unresolved_assist_count

    def pct(n: int, d: int) -> float | None:
        return (n / d) if d > 0 else None

    return AssistedProfile(
        assisted_fgm=assisted, unassisted_fgm=unassisted,
        assisted_fgm_pct=pct(assisted, total), unassisted_fgm_pct=pct(unassisted, total),
        assisted_2pm=assisted_2, unassisted_2pm=unassisted_2,
        assisted_2pm_pct=pct(assisted_2, total_2), unassisted_2pm_pct=pct(unassisted_2, total_2),
        assisted_3pm=assisted_3, unassisted_3pm=unassisted_3,
        assisted_3pm_pct=pct(assisted_3, total_3), unassisted_3pm_pct=pct(unassisted_3, total_3),
        total_provider_assists=total_provider_assists,
        resolved_shot_attributed_assists=assisted,
        unresolved_assist_count=unresolved_assist_count,
        unresolved_assist_rate=pct(unresolved_assist_count, total_provider_assists),
    )


# ---- 12. Shot-mix / scoring-mix --------------------------------------------

@dataclass(frozen=True)
class ShotScoringMix:
    fg2a_share: float | None  # 2PA / FGA
    fg3a_share: float | None  # 3PA / FGA
    scoring_share_2pt: float | None  # points from 2PM / total points
    scoring_share_3pt: float | None  # points from 3PM / total points
    scoring_share_ft: float | None  # FTM / total points
    scoring_share_reconciles: bool | None  # sum of the three shares ~= 1.0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_shot_scoring_mix(team_possessions: list[Possession]) -> ShotScoringMix:
    fga = sum(p.fga for p in team_possessions)
    fg3a = sum(p.fg3a for p in team_possessions)
    fg2a = fga - fg3a
    fg3m = sum(p.fg3m for p in team_possessions)
    fg2m = sum(p.fgm for p in team_possessions) - fg3m
    ftm = sum(p.ftm for p in team_possessions)
    total_points = _sum_points(team_possessions)

    fg2a_share = (fg2a / fga) if fga > 0 else None
    fg3a_share = (fg3a / fga) if fga > 0 else None
    share_2pt = (2 * fg2m / total_points) if total_points > 0 else None
    share_3pt = (3 * fg3m / total_points) if total_points > 0 else None
    share_ft = (ftm / total_points) if total_points > 0 else None

    reconciles = None
    if total_points > 0:
        reconciles = abs((share_2pt or 0) + (share_3pt or 0) + (share_ft or 0) - 1.0) < 1e-9

    return ShotScoringMix(
        fg2a_share=fg2a_share, fg3a_share=fg3a_share,
        scoring_share_2pt=share_2pt, scoring_share_3pt=share_3pt, scoring_share_ft=share_ft,
        scoring_share_reconciles=reconciles,
    )
