"""Deterministic team-game statistical formulas — the ten core metrics.

This module is the single source of truth for how every metric in
PROJECT_SPEC.md's "ten core team metrics" is computed. Rules that apply to
every function here:

* Pure arithmetic over already-extracted :class:`TeamGameComponents` values.
  No I/O, no provider knowledge, no LLM involvement of any kind.
* Every ratio-type metric returns ``None`` — never ``0.0``, never ``inf`` —
  when its denominator is exactly zero. ``None`` means "undefined for this
  game"; callers must treat it as missing data, not as a real zero.
* Where a formula has more than one common convention, the convention chosen
  is written out and the reason given, so a later reviewer can change it in
  one place.

Possessions
-----------

Team possessions are estimated with the standard Dean Oliver /
basketball-reference formula, computed **once per team** using that team's
own box-score components plus only the opponent's defensive-rebound count
(needed for the ORB-weighting term):

    Poss = FGA - 1.07 * (ORB / (ORB + OppDRB)) * (FGA - FGM) + TOV + 0.4 * FTA

This is deliberately **not** averaged into one game-level possession count.
``offensive_rating`` uses a team's own possession estimate; ``defensive_rating``
uses the opponent's own possession estimate (i.e. the opponent's offensive
possessions, which are this team's defensive possessions faced). The two
estimates are close but not forced equal — allowing them to diverge slightly
reflects real estimation noise in the underlying box score rather than
papering over it with an artificial average.

Pace
----

Pace is a **game-level** tempo estimate, not a per-team one, even though
``pace()`` is invoked once per team from ``engine.py``: it averages
``team_possessions`` and ``opponent_possessions`` before dividing, so
``home.metrics.pace == away.metrics.pace`` always, by construction (both
calls compute ``base_minutes * ((home_poss + away_poss) / 2) / minutes`` —
addition is commutative, so the call order never matters). This was
verified against real ingested games this session, including an OT game and
a double-OT game, before the 2026-08-15 management review; both showed
byte-identical home/away pace. See ``formulas.pace()``'s docstring for the
exact contract.

Reported as estimated possessions per team per **40 minutes** (FIBA
regulation length), not the NBA's 48 — this league plays 4x10-minute
quarters. ``minutes_played`` must be the game's *actual* elapsed minutes
(``game_minutes()`` — regulation plus 5 real minutes per FIBA OT period),
never a fixed 40 — a 45-minute single-OT game and a 50-minute double-OT game
both normalize against their own real minutes, so an OT game's pace is not
inflated by treating the extra period as if it were also regulation length.
"""

from __future__ import annotations

from .models import TeamGameComponents

# FIBA rules: 4 regulation quarters of 10 minutes; each overtime period is
# 5 minutes. See engine.py for how ``regulation_periods``/``ot_periods`` are
# derived from the observed PBP quarters.
REGULATION_PERIODS = 4
REGULATION_PERIOD_MINUTES = 10.0
OT_PERIOD_MINUTES = 5.0
PACE_BASE_MINUTES = 40.0


def game_minutes(
    regulation_periods: int,
    ot_periods: int,
    *,
    regulation_period_minutes: float = REGULATION_PERIOD_MINUTES,
    ot_period_minutes: float = OT_PERIOD_MINUTES,
) -> float:
    """Total game length in minutes, regulation + any overtime periods."""
    return regulation_periods * regulation_period_minutes + ot_periods * ot_period_minutes


def estimate_possessions(team: TeamGameComponents, opponent: TeamGameComponents) -> float:
    """Dean Oliver / basketball-reference single-team possession estimate.

    ``opponent`` is only consulted for ``drb`` (the ORB-weighting term).
    If ``team.orb + opponent.drb == 0`` (no rebound recorded in that category
    at all — only reachable with degenerate/synthetic data, never a real
    finished game), the ORB-weighting term is treated as 0 rather than
    raising a ZeroDivisionError.
    """
    reb_denom = team.orb + opponent.drb
    orb_weight = (team.orb / reb_denom) if reb_denom else 0.0
    return (
        team.fga
        - 1.07 * orb_weight * (team.fga - team.fgm)
        + team.tov
        + 0.4 * team.fta
    )


def offensive_rating(points: int, possessions: float) -> float | None:
    """Points scored per 100 of this team's own possessions."""
    if possessions <= 0:
        return None
    return 100.0 * points / possessions


def defensive_rating(opponent_points: int, opponent_possessions: float) -> float | None:
    """Points allowed per 100 of the *opponent's* own possessions.

    Takes the opponent's points and the opponent's own possession estimate
    (not this team's) — see the module docstring for why the two sides are
    not forced to share one averaged possession count.
    """
    if opponent_possessions <= 0:
        return None
    return 100.0 * opponent_points / opponent_possessions


def net_rating(off_rtg: float | None, def_rtg: float | None) -> float | None:
    """Offensive rating minus defensive rating. ``None`` if either side is undefined."""
    if off_rtg is None or def_rtg is None:
        return None
    return off_rtg - def_rtg


def pace(
    team_possessions: float,
    opponent_possessions: float,
    minutes_played: float,
    *,
    base_minutes: float = PACE_BASE_MINUTES,
) -> float | None:
    """Estimated possessions per team per ``base_minutes`` (default: 40, FIBA).

    Symmetric/game-level by construction: called with
    ``(team_possessions, opponent_possessions, ...)`` for one side and
    ``(opponent_possessions, team_possessions, ...)`` for the other, but the
    numerator averages the two, so both calls return the identical value —
    see the module docstring's "Pace" section for the real-data verification.
    """
    if minutes_played <= 0:
        return None
    return base_minutes * ((team_possessions + opponent_possessions) / 2.0) / minutes_played


def effective_fg_pct(fgm: int, fg3m: int, fga: int) -> float | None:
    """eFG% = (FGM + 0.5 * 3PM) / FGA — weights made threes at 1.5x a make."""
    if fga <= 0:
        return None
    return (fgm + 0.5 * fg3m) / fga


def turnover_pct(tov: int, fga: int, fta: int) -> float | None:
    """TOV% = TOV / (FGA + 0.44*FTA + TOV) — turnovers per estimated play.

    ``0.44`` is the standard Oliver "plays" weighting for free throw
    attempts (accounts for and-1s / multi-FT trips not each being a
    separate scoring play).
    """
    plays = fga + 0.44 * fta + tov
    if plays <= 0:
        return None
    return tov / plays


def off_reb_pct(team_orb: int, opponent_drb: int) -> float | None:
    """ORB% = team ORB / (team ORB + opponent DRB) — share of contestable boards kept."""
    denom = team_orb + opponent_drb
    if denom <= 0:
        return None
    return team_orb / denom


def free_throw_rate(fta: int, fga: int) -> float | None:
    """FT Rate = FTA / FGA (attempts, not makes — measures trips to the line)."""
    if fga <= 0:
        return None
    return fta / fga


def three_point_rate(fg3a: int, fga: int) -> float | None:
    """3PA Rate = 3PA / FGA — share of field goal attempts taken from three."""
    if fga <= 0:
        return None
    return fg3a / fga


def ast_to_ratio(ast: int, tov: int) -> float | None:
    """AST/TO = assists / turnovers. ``None`` when TOV is 0 (undefined ratio, not infinity)."""
    if tov <= 0:
        return None
    return ast / tov
