"""View models the templates consume.

This is the boundary. Everything a template is allowed to render passes through
here first, and anything unsafe is *absent from the object* rather than present
and hidden by markup — a template cannot leak a field it was never handed.

Three rules live here rather than in a template:

* **Direction.** Pace, free-throw rate, three-point-attempt rate and the
  scoring shares describe style, not quality. They carry ``neutral`` and are
  structurally incapable of being tinted good or bad.
* **Sample.** Every situational number arrives with the state its cell was
  stamped with at build time, so a 44-possession clutch cell cannot render as
  an ordinary number beside a 500-possession quarter.
* **Labels.** One shipped evidence id is mislabelled at source; the correction
  is applied on the way out — see ``DISPLAY_LABEL_OVERRIDES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..stats.winloss import OUTCOME_CONTEXT, category_for
from .schema import METRICS, GameRow, SegmentCell, TeamAnalytics

# ---- metric presentation ----------------------------------------------------

Direction = str  # "higher_is_better" | "lower_is_better" | "neutral"


@dataclass(frozen=True)
class MetricMeta:
    key: str
    label: str
    short: str
    direction: Direction
    unit: str  # "per100" | "pct" | "ratio" | "count"
    decimals: int = 1


METRIC_META: dict[str, MetricMeta] = {
    "offensive_rating": MetricMeta("offensive_rating", "Offensive Rating", "ORtg", "higher_is_better", "per100"),
    "defensive_rating": MetricMeta("defensive_rating", "Defensive Rating", "DRtg", "lower_is_better", "per100"),
    "net_rating": MetricMeta("net_rating", "Net Rating", "Net", "higher_is_better", "per100"),
    # Style, not quality: a fast team is not a good team. Never tinted.
    "pace": MetricMeta("pace", "Pace", "Pace", "neutral", "per100"),
    "efg_pct": MetricMeta("efg_pct", "Effective FG%", "eFG%", "higher_is_better", "pct"),
    "tov_pct": MetricMeta("tov_pct", "Turnover Rate", "TOV%", "lower_is_better", "pct"),
    "orb_pct": MetricMeta("orb_pct", "Offensive Rebound %", "ORB%", "higher_is_better", "pct"),
    "ft_rate": MetricMeta("ft_rate", "Free Throw Rate", "FTr", "neutral", "ratio", 2),
    "fg3a_rate": MetricMeta("fg3a_rate", "3PA Rate", "3PAr", "neutral", "ratio", 2),
    "ast_to_ratio": MetricMeta("ast_to_ratio", "Assist/Turnover", "AST/TO", "higher_is_better", "ratio", 2),
}

# Opponent-side four factors, derived from `components_against`.
OPPONENT_META: dict[str, MetricMeta] = {
    "opp_efg_pct": MetricMeta("opp_efg_pct", "Opponent eFG%", "oeFG%", "lower_is_better", "pct"),
    "opp_tov_pct": MetricMeta("opp_tov_pct", "Opponent Turnover Rate", "oTOV%", "higher_is_better", "pct"),
    "drb_pct": MetricMeta("drb_pct", "Defensive Rebound %", "DRB%", "higher_is_better", "pct"),
    "opp_ft_rate": MetricMeta("opp_ft_rate", "Opponent Free Throw Rate", "oFTr", "lower_is_better", "ratio", 2),
}

# One shipped evidence id is mislabelled at source. The score-state bin it uses
# starts at a margin of -5, not -6, so "Trailing 6+" overstates it. Correcting
# the *bin* would move the value by up to eight points and invalidate every
# stored scouting report, so the data stays and the label is corrected on the
# way out — here, and in the report renderer.
DISPLAY_LABEL_OVERRIDES: dict[str, str] = {
    "EV.behind_6_plus.efg_pct": "Effective FG% When Trailing 5+",
}


def display_label(evidence_id: str, fallback: str) -> str:
    """The label a user should see for an evidence id."""
    return DISPLAY_LABEL_OVERRIDES.get(evidence_id, fallback)


SEGMENT_LABELS: dict[str, str] = {
    "full": "Full Game", "q1": "Q1", "q2": "Q2", "q3": "Q3", "q4": "Q4",
    "h1": "1st Half", "h2": "2nd Half",
    "close": "Close", "leading": "Leading", "trailing": "Trailing", "clutch": "Clutch",
}

SEGMENT_DEFINITIONS: dict[str, str] = {
    "full": "Every possession.",
    "q1": "First-quarter possessions.",
    "q2": "Second-quarter possessions.",
    "q3": "Third-quarter possessions.",
    "q4": "Fourth-quarter possessions. Overtime is excluded.",
    "h1": "First-half possessions.",
    "h2": "Second-half possessions. Overtime is excluded.",
    "close": "Possessions starting within 5 points, at any point in the game.",
    "leading": "Possessions starting with the lead.",
    "trailing": "Possessions starting from behind.",
    "clutch": "Final 5 minutes of the 4th quarter or overtime, within 5 points.",
}

OUTCOME_LABELS: dict[str, str] = {"all": "All games", "wins": "Wins", "losses": "Losses"}


def format_value(meta: MetricMeta, value: float | None) -> str:
    if value is None:
        return "—"
    if meta.unit == "pct":
        return f"{value * 100:.{meta.decimals}f}%"
    if meta.unit == "per100" and meta.key == "net_rating":
        return f"{value:+.{meta.decimals}f}"
    return f"{value:.{meta.decimals}f}"


@dataclass(frozen=True)
class MetricCell:
    """One number, ready to render."""

    key: str
    label: str
    short: str
    value: float | None
    display: str
    direction: Direction
    rank: int | None = None
    percentile: float | None = None
    eligible_teams: int = 0

    @property
    def is_style(self) -> bool:
        """Style metrics carry no good/bad direction and must never be tinted."""
        return self.direction == "neutral"

    @property
    def tint(self) -> int:
        """0-3 shading step from the percentile. Always 0 for style metrics, so
        a template cannot colour Pace by accident."""
        if self.is_style or self.percentile is None:
            return 0
        if self.percentile >= 85:
            return 3
        if self.percentile >= 65:
            return 2
        if self.percentile >= 50:
            return 1
        return 0

    @property
    def rank_display(self) -> str:
        return f"{self.rank} of {self.eligible_teams}" if self.rank else ""


def metric_cell(key: str, cell: SegmentCell, meta: MetricMeta | None = None) -> MetricCell | None:
    """One metric out of a segment cell, or ``None`` when the cell does not
    carry it — an absent metric is never rendered as a zero or a dash-with-tint."""
    meta = meta or METRIC_META.get(key)
    if meta is None or key not in cell.metrics:
        return None
    return MetricCell(
        key=key, label=meta.label, short=meta.short,
        value=cell.metrics[key], display=format_value(meta, cell.metrics[key]),
        direction=meta.direction,
        rank=cell.ranks.get(key), percentile=cell.percentiles.get(key),
        eligible_teams=cell.eligible_teams,
    )


# ---- sample state -----------------------------------------------------------


@dataclass(frozen=True)
class SampleView:
    """How a filtered number should be qualified, if at all."""

    state: str
    games: int
    possessions: int
    outcome: str = "all"

    @property
    def is_usable(self) -> bool:
        return self.state != "insufficient"

    @property
    def _counted(self) -> str:
        """"4 losses" when the filter is an outcome, "4 games" otherwise."""
        plural = {"wins": ("win", "wins"), "losses": ("loss", "losses")}.get(
            self.outcome, ("game", "games")
        )
        return f"{self.games} {plural[0] if self.games == 1 else plural[1]}"

    @property
    def badge(self) -> str | None:
        """Never a footnote. ``None`` only when the sample is genuinely fine."""
        if self.state == "sufficient":
            return None
        if self.state == "insufficient":
            return f"Insufficient sample — {self._counted}"
        return f"Limited sample — {self._counted}"

    @property
    def detail(self) -> str:
        parts = [f"{self.games} game{'s' if self.games != 1 else ''}"]
        if self.possessions:
            parts.append(f"{self.possessions} possessions")
        return " · ".join(parts)


def sample_view(cell: SegmentCell) -> SampleView:
    return SampleView(state=cell.sample_state, games=cell.games,
                      possessions=cell.possessions, outcome=cell.outcome)


# ---- defensive four factors -------------------------------------------------


@dataclass(frozen=True)
class FourFactors:
    offense: list[MetricCell] = field(default_factory=list)
    defense: list[MetricCell] = field(default_factory=list)


def opponent_factors(games: list[GameRow]) -> dict[str, float]:
    """The defensive four factors, summed across games then divided once.

    A pure derivation from ``components_against`` — the opponent's own box
    score is already in every game row, so this needs no new analytics and no
    play-by-play. Volume-weighted, matching every other aggregate here.
    """
    if not games:
        return {}
    fgm = sum(g.components_against.fgm for g in games)
    fga = sum(g.components_against.fga for g in games)
    fg3m = sum(g.components_against.fg3m for g in games)
    fta = sum(g.components_against.fta for g in games)
    tov = sum(g.components_against.tov for g in games)
    opp_orb = sum(g.components_against.orb for g in games)
    own_drb = sum(g.components_for.drb for g in games)
    opp_poss = sum(g.possessions_against for g in games)

    out: dict[str, float] = {}
    if fga:
        out["opp_efg_pct"] = (fgm + 0.5 * fg3m) / fga
        out["opp_ft_rate"] = fta / fga
    if opp_poss:
        out["opp_tov_pct"] = tov / opp_poss
    if own_drb + opp_orb:
        out["drb_pct"] = own_drb / (own_drb + opp_orb)
    return out


# ---- win / loss -------------------------------------------------------------


@dataclass(frozen=True)
class SplitRow:
    """One metric compared between wins and losses."""

    meta: MetricMeta
    overall: float | None
    wins: float | None
    losses: float | None
    is_outcome_context: bool

    @property
    def delta(self) -> float | None:
        if self.wins is None or self.losses is None:
            return None
        return self.wins - self.losses

    @property
    def delta_display(self) -> str:
        d = self.delta
        if d is None:
            return "—"
        scaled = d * 100 if self.meta.unit == "pct" else d
        return f"{scaled:+.{self.meta.decimals}f}"

    @property
    def favours_wins(self) -> bool | None:
        """Whether the wins side is the better side. ``None`` for style metrics,
        where neither direction is better and colouring would be a claim."""
        d = self.delta
        if d is None or self.meta.direction == "neutral":
            return None
        return d > 0 if self.meta.direction == "higher_is_better" else d < 0

    def display(self, value: float | None) -> str:
        return format_value(self.meta, value)


def split_rows(team: TeamAnalytics, segment: str = "full") -> list[SplitRow]:
    """Wins vs losses for one segment.

    Ordered actionable-first. Outcome-context metrics (ORtg, DRtg, Net, Pace)
    are kept but flagged, because they are near-tautological in a win/loss
    comparison — a team does outscore its opponents in the games it wins — and
    must never be presented as *the reason* a team wins.
    """
    overall = team.cell(segment, "all")
    wins = team.cell(segment, "wins")
    losses = team.cell(segment, "losses")
    if overall is None:
        return []

    rows: list[SplitRow] = []
    for key in METRICS:
        meta = METRIC_META.get(key)
        if meta is None or key not in overall.metrics:
            continue
        rows.append(
            SplitRow(
                meta=meta,
                overall=overall.metrics.get(key),
                wins=wins.metrics.get(key) if wins and wins.sample_state != "insufficient" else None,
                losses=losses.metrics.get(key) if losses and losses.sample_state != "insufficient" else None,
                is_outcome_context=category_for(key) == OUTCOME_CONTEXT,
            )
        )
    rows.sort(key=lambda r: (r.is_outcome_context, METRICS.index(r.meta.key)))
    return rows


def largest_differences(rows: list[SplitRow], limit: int = 3) -> list[SplitRow]:
    """The biggest actionable win/loss gaps.

    Outcome-context metrics are excluded outright. Including them would put Net
    Rating first for nearly every team — true, and useless: it says a team wins
    when it outscores its opponent. The interesting rows are the descriptive
    factors underneath that.
    """
    scored = [
        r for r in rows
        if not r.is_outcome_context and r.delta is not None and r.meta.direction != "neutral"
    ]
    scored.sort(key=lambda r: abs(r.delta or 0), reverse=True)
    return scored[:limit]
