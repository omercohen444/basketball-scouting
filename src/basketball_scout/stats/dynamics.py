"""Score dynamics: ties, lead changes, largest lead/deficit, comeback and
blown-lead indicators (enrichment brief §15) — derived from the chronological
scoring timeline (running score after every scoring play).

Two kinds of output, kept explicitly separate:

* **Standard factual game state** — ``times_tied`` (excluding the initial
  0-0), ``lead_changes``, ``largest_lead``, ``largest_deficit`` — read
  directly off the score sequence.
* **Transparent project-derived indicators** — ``trailed_by_10_plus`` /
  ``led_by_10_plus`` and the win/loss-conditioned comeback/blown-lead flags.
  These are still pure facts (a trailed-by-10-plus flag plus the game's
  actual result), never a narrative judgment.

Comeback/blown-lead season rates use the correct denominator throughout —
the count of games where the *opportunity* occurred, never every game
played (enrichment brief's explicit instruction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .scoring_timeline import ScoringPlay

BIG_MARGIN_THRESHOLD = 10


@dataclass(frozen=True)
class GameDynamics:
    """One game's factual score-dynamics record, for one team's perspective."""

    times_tied: int
    lead_changes: int
    largest_lead: int  # this team's largest lead (points), 0 if never ahead
    largest_deficit: int  # this team's largest deficit (points), 0 if never behind
    trailed_by_10_plus: bool
    led_by_10_plus: bool
    team_won: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_game_dynamics(plays: list[ScoringPlay], *, team_side: str, team_won: bool) -> GameDynamics:
    # `last_leader` tracks the most recent NON-tied leader ("team"/"opp"),
    # deliberately not reset by a tie — a sequence team-leads -> tied ->
    # opp-leads is one real lead change (the lead changed hands), so a tie
    # in between must not suppress it. A margin of exactly 0 can never be
    # the very first play's result (the first score always breaks 0-0), so
    # the brief's "excluding the initial 0-0" is satisfied automatically —
    # no separate guard needed.
    last_leader: str | None = None
    times_tied = 0
    lead_changes = 0
    largest_lead = 0
    largest_deficit = 0
    trailed_10 = False
    led_10 = False

    for play in plays:
        team_score = play.home_score_after if team_side == "home" else play.away_score_after
        opp_score = play.away_score_after if team_side == "home" else play.home_score_after
        margin = team_score - opp_score

        if margin > 0:
            largest_lead = max(largest_lead, margin)
            if margin >= BIG_MARGIN_THRESHOLD:
                led_10 = True
            if last_leader is not None and last_leader != "team":
                lead_changes += 1
            last_leader = "team"
        elif margin < 0:
            largest_deficit = max(largest_deficit, -margin)
            if -margin >= BIG_MARGIN_THRESHOLD:
                trailed_10 = True
            if last_leader is not None and last_leader != "opp":
                lead_changes += 1
            last_leader = "opp"
        else:
            times_tied += 1
            # last_leader intentionally left unchanged on a tie.

    return GameDynamics(
        times_tied=times_tied,
        lead_changes=lead_changes,
        largest_lead=largest_lead,
        largest_deficit=largest_deficit,
        trailed_by_10_plus=trailed_10,
        led_by_10_plus=led_10,
        team_won=team_won,
    )


@dataclass(frozen=True)
class ComebackProfile:
    games_trailing_10_plus: int
    comeback_wins_from_10_plus: int
    comeback_conversion_rate: float | None  # comeback_wins / games_trailing_10_plus
    games_leading_10_plus: int
    losses_after_leading_10_plus: int
    blown_10_plus_lead_rate: float | None  # losses_after_leading / games_leading_10_plus

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_comeback_profile(game_dynamics_list: list[GameDynamics]) -> ComebackProfile:
    trailing_games = [g for g in game_dynamics_list if g.trailed_by_10_plus]
    leading_games = [g for g in game_dynamics_list if g.led_by_10_plus]
    comeback_wins = sum(1 for g in trailing_games if g.team_won)
    blown_losses = sum(1 for g in leading_games if not g.team_won)

    return ComebackProfile(
        games_trailing_10_plus=len(trailing_games),
        comeback_wins_from_10_plus=comeback_wins,
        comeback_conversion_rate=(comeback_wins / len(trailing_games)) if trailing_games else None,
        games_leading_10_plus=len(leading_games),
        losses_after_leading_10_plus=blown_losses,
        blown_10_plus_lead_rate=(blown_losses / len(leading_games)) if leading_games else None,
    )
