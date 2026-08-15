"""Provider-independent team-game statistical data model.

Two layers deliberately kept separate:

* :class:`TeamGameComponents` — raw counting stats, exactly what a source
  provider's box score gives us. No formulas, no derived ratios.
* :class:`DerivedMetrics` — the ten core metrics from PROJECT_SPEC.md,
  computed by ``formulas.py`` and carried here only as a value holder.

:class:`TeamGameStats` is one team's complete record for one game — both
sides (``components_for``/``components_against``) are stored so downstream
code never needs to re-look-up the opponent's row to compute a differential
metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TeamGameComponents:
    """Raw, source-derived counting stats for one team in one game.

    Every field is an exact count aggregated from PBP actions — never an
    estimate. ``fg3m``/``fg3a`` are a subset of ``fgm``/``fga`` (2PT makes are
    ``fgm - fg3m``, not a separate field), matching standard box-score layout.
    """

    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    orb: int
    drb: int
    ast: int
    tov: int
    pf: int
    points: int

    @property
    def fg2m(self) -> int:
        return self.fgm - self.fg3m

    @property
    def fg2a(self) -> int:
        return self.fga - self.fg3a

    @property
    def trb(self) -> int:
        return self.orb + self.drb

    def to_dict(self) -> dict[str, Any]:
        return {
            "fgm": self.fgm, "fga": self.fga,
            "fg3m": self.fg3m, "fg3a": self.fg3a,
            "ftm": self.ftm, "fta": self.fta,
            "orb": self.orb, "drb": self.drb,
            "ast": self.ast, "tov": self.tov,
            "pf": self.pf, "points": self.points,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TeamGameComponents":
        return TeamGameComponents(
            fgm=d["fgm"], fga=d["fga"], fg3m=d["fg3m"], fg3a=d["fg3a"],
            ftm=d["ftm"], fta=d["fta"], orb=d["orb"], drb=d["drb"],
            ast=d["ast"], tov=d["tov"], pf=d["pf"], points=d["points"],
        )


@dataclass(frozen=True)
class DerivedMetrics:
    """The ten core team-game metrics from PROJECT_SPEC.md §2.

    Every field is ``float | None``. ``None`` means "undefined for this game"
    (a zero denominator), never a silent zero — see ``formulas.py`` for the
    exact formula and denominator convention behind each field.
    """

    offensive_rating: float | None
    defensive_rating: float | None
    net_rating: float | None
    pace: float | None
    efg_pct: float | None
    tov_pct: float | None
    orb_pct: float | None
    ft_rate: float | None
    fg3a_rate: float | None
    ast_to_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "offensive_rating": self.offensive_rating,
            "defensive_rating": self.defensive_rating,
            "net_rating": self.net_rating,
            "pace": self.pace,
            "efg_pct": self.efg_pct,
            "tov_pct": self.tov_pct,
            "orb_pct": self.orb_pct,
            "ft_rate": self.ft_rate,
            "fg3a_rate": self.fg3a_rate,
            "ast_to_ratio": self.ast_to_ratio,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DerivedMetrics":
        return DerivedMetrics(**d)


@dataclass(frozen=True)
class TeamGameStats:
    """One team's complete statistical record for one game.

    ``team_id``/``opponent_id`` are provider-qualified (``"segev:2"``), not a
    display name — team names vary in casing/formatting across a source and
    must never be used as the grouping key for historical analysis.
    """

    internal_game_id: str
    source_provider: str
    source_game_id: str
    season: str
    game_date: str
    team_id: str
    team_name: str
    opponent_id: str
    opponent_name: str
    is_home: bool
    final_score_for: int
    final_score_against: int
    win: bool
    regulation_periods: int
    ot_periods: int
    game_minutes: float
    possessions_for: float
    possessions_against: float
    components_for: TeamGameComponents
    components_against: TeamGameComponents
    metrics: DerivedMetrics
    # Provenance: raw action-type counts seen in the source for this game
    # (e.g. {"shot": 140, "rebound": 82, ...}), shared by both teams' records.
    action_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "internal_game_id": self.internal_game_id,
            "source_provider": self.source_provider,
            "source_game_id": self.source_game_id,
            "season": self.season,
            "game_date": self.game_date,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "opponent_id": self.opponent_id,
            "opponent_name": self.opponent_name,
            "is_home": self.is_home,
            "final_score_for": self.final_score_for,
            "final_score_against": self.final_score_against,
            "win": self.win,
            "regulation_periods": self.regulation_periods,
            "ot_periods": self.ot_periods,
            "game_minutes": self.game_minutes,
            "possessions_for": self.possessions_for,
            "possessions_against": self.possessions_against,
            "components_for": self.components_for.to_dict(),
            "components_against": self.components_against.to_dict(),
            "metrics": self.metrics.to_dict(),
            "action_counts": dict(self.action_counts),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TeamGameStats":
        return TeamGameStats(
            internal_game_id=d["internal_game_id"],
            source_provider=d["source_provider"],
            source_game_id=d["source_game_id"],
            season=d["season"],
            game_date=d["game_date"],
            team_id=d["team_id"],
            team_name=d["team_name"],
            opponent_id=d["opponent_id"],
            opponent_name=d["opponent_name"],
            is_home=d["is_home"],
            final_score_for=d["final_score_for"],
            final_score_against=d["final_score_against"],
            win=d["win"],
            regulation_periods=d["regulation_periods"],
            ot_periods=d["ot_periods"],
            game_minutes=d["game_minutes"],
            possessions_for=d["possessions_for"],
            possessions_against=d["possessions_against"],
            components_for=TeamGameComponents.from_dict(d["components_for"]),
            components_against=TeamGameComponents.from_dict(d["components_against"]),
            metrics=DerivedMetrics.from_dict(d["metrics"]),
            action_counts=dict(d.get("action_counts") or {}),
        )
