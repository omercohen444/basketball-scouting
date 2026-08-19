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
from .schema import (
    LOW_GAMES,
    LOW_POSSESSIONS,
    METRICS,
    MIN_GAMES,
    MIN_POSSESSIONS,
    GameRow,
    SegmentCell,
    TeamAnalytics,
)

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

# Every key a segment cell can carry. The builder stores the offensive ten and
# the defensive four in one `metrics` dict, so one lookup resolves both.
CELL_META: dict[str, MetricMeta] = {**METRIC_META, **OPPONENT_META}

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
    meta = meta or CELL_META.get(key)
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
    def _limiter(self) -> str:
        """Whichever count actually put this cell below the line.

        A segment cell can be thin on possessions while spanning plenty of
        games — clutch is the obvious case — so naming games there would
        state a reason that is not the reason.
        """
        if self.state == "insufficient":
            possession_floor, game_floor = MIN_POSSESSIONS, MIN_GAMES
        else:
            possession_floor, game_floor = LOW_POSSESSIONS, LOW_GAMES
        if self.games < game_floor:
            return self._counted
        if self.possessions < possession_floor:
            return f"{self.possessions} possessions"
        return self._counted

    @property
    def badge(self) -> str | None:
        """Never a footnote. ``None`` only when the sample is genuinely fine."""
        if self.state == "sufficient":
            return None
        if self.state == "insufficient":
            return f"Insufficient sample — {self._limiter}"
        return f"Limited sample — {self._limiter}"

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


# The defensive four factors used to be derived here from the game rows. They
# now arrive on every segment cell, computed by the builder through the same
# four functions in `formulas.py` the offensive half uses — see
# `analytics.build.opponent_metrics`. Deriving them twice was how the opponent
# turnover rate ended up on a possession denominator while the team's own sat
# on a plays denominator, two to three points apart in the same table.


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


# ---- league -----------------------------------------------------------------


LEAGUE_COLUMNS: tuple[str, ...] = (
    "offensive_rating", "defensive_rating", "net_rating", "pace",
    "efg_pct", "tov_pct", "orb_pct", "ft_rate",
)
LEAGUE_OPPONENT_COLUMNS: tuple[str, ...] = ("opp_efg_pct", "opp_tov_pct", "drb_pct")

SORTABLE: dict[str, MetricMeta] = {**METRIC_META, **OPPONENT_META}


@dataclass(frozen=True)
class LeagueRow:
    team_id: str
    team_name: str
    record: str
    wins: int
    losses: int
    metrics: dict[str, MetricCell]

    def get(self, key: str) -> MetricCell | None:
        return self.metrics.get(key)

    def sort_value(self, key: str) -> float:
        """Missing values sort to the bottom whichever way the column runs, so
        an absent metric never masquerades as a good one."""
        cell = self.metrics.get(key)
        return cell.value if cell and cell.value is not None else float("-inf")


def league_rows(teams: dict[str, TeamAnalytics], *, sort: str = "net_rating") -> list[LeagueRow]:
    """One row per team at season scope, both halves out of the same cell.

    The defensive four used to be recomputed here from the game rows, with a
    possession denominator for the opponent turnover rate while the team's own
    turnover rate came from the cell with a plays denominator — a two to three
    point gap on every team, in adjacent columns of one table. Both now come
    from the artifact, where the builder ran all fourteen metrics through the
    same functions, and the ranks come with them.
    """
    rows: list[LeagueRow] = []
    for tid, team in teams.items():
        cell = team.cell("full", "all")
        metrics: dict[str, MetricCell] = {}
        if cell is not None:
            for key in (*LEAGUE_COLUMNS, *LEAGUE_OPPONENT_COLUMNS):
                built = metric_cell(key, cell)
                if built:
                    metrics[key] = built
        rows.append(
            LeagueRow(team_id=tid, team_name=team.team_name, record=team.record,
                      wins=team.wins, losses=team.losses, metrics=metrics)
        )

    meta = SORTABLE.get(sort) or METRIC_META["net_rating"]
    rows.sort(key=lambda r: r.sort_value(meta.key), reverse=meta.direction != "lower_is_better")
    return rows


@dataclass(frozen=True)
class LeagueLeader:
    label: str
    team_id: str
    team_name: str
    display: str


def league_leaders(rows: list[LeagueRow]) -> list[LeagueLeader]:
    """The four headline facts.

    Pace is here as a fact — *fastest*, not *best*. A fast team is not thereby
    a good one, and the label has to keep saying so.
    """
    wanted = (
        ("Best offense", "offensive_rating"),
        ("Best defense", "defensive_rating"),
        ("Best net rating", "net_rating"),
        ("Fastest pace", "pace"),
    )
    out: list[LeagueLeader] = []
    for label, key in wanted:
        meta = METRIC_META[key]
        candidates = [r for r in rows if r.get(key) and r.get(key).value is not None]
        if not candidates:
            continue
        picker = min if meta.direction == "lower_is_better" else max
        best = picker(candidates, key=lambda r: r.get(key).value)
        out.append(LeagueLeader(label=label, team_id=best.team_id,
                                team_name=best.team_name, display=best.get(key).display))
    return out


def scatter_points(rows: list[LeagueRow]) -> list[dict[str, object]]:
    """Offense against defense, normalised to a 0-100 box.

    The defensive axis is inverted so up-and-right is unambiguously good.
    Plotted raw, the best defences sit at the bottom and the chart reads
    backwards to anyone glancing at it.
    """
    pts = [
        r for r in rows
        if r.get("offensive_rating") and r.get("defensive_rating")
        and r.get("offensive_rating").value is not None
        and r.get("defensive_rating").value is not None
    ]
    if not pts:
        return []
    ortgs = [r.get("offensive_rating").value for r in pts]
    drtgs = [r.get("defensive_rating").value for r in pts]
    o_lo, o_hi = min(ortgs), max(ortgs)
    d_lo, d_hi = min(drtgs), max(drtgs)
    o_pad = (o_hi - o_lo) * 0.14 or 1.0
    d_pad = (d_hi - d_lo) * 0.14 or 1.0
    o_lo, o_hi = o_lo - o_pad, o_hi + o_pad
    d_lo, d_hi = d_lo - d_pad, d_hi + d_pad

    labels = disambiguate([r.team_name for r in pts])
    out: list[dict[str, object]] = []
    for r in pts:
        ortg = r.get("offensive_rating").value
        drtg = r.get("defensive_rating").value
        out.append({
            "team_id": r.team_id,
            "team_name": r.team_name,
            "abbr": labels[r.team_name],
            "x": round((ortg - o_lo) / (o_hi - o_lo) * 100, 2),
            # inverted: a LOW defensive rating is good, so it belongs high up
            "y": round((d_hi - drtg) / (d_hi - d_lo) * 100, 2),
            "ortg": r.get("offensive_rating").display,
            "drtg": r.get("defensive_rating").display,
            "net": r.get("net_rating").display if r.get("net_rating") else "",
        })
    return out


CLUB_PREFIXES = {"hapoel", "maccabi", "ironi", "bnei", "elitzur", "elizur"}


def abbreviate(team_name: str) -> str:
    """A short chart label, keeping the town rather than the club — three sides
    are called Maccabi and four Hapoel, so the club is the less useful half."""
    words = [w for w in team_name.split() if w]
    significant = [w for w in words if w.lower() not in CLUB_PREFIXES] or words
    if len(significant) == 1:
        return significant[0][:3].upper()
    return "".join(w[0] for w in significant[:3]).upper()


def disambiguate(names: list[str]) -> dict[str, str]:
    """Abbreviations that are unique across the given names.

    Dropping the club works until two clubs share a town: Maccabi and Hapoel
    Tel Aviv both reduce to "TA", which on a scatter plot is two unlabelled
    dots. Where that happens the club initial goes back on the front.
    """
    short = {name: abbreviate(name) for name in names}
    counts: dict[str, int] = {}
    for value in short.values():
        counts[value] = counts.get(value, 0) + 1

    for name, value in list(short.items()):
        if counts[value] < 2:
            continue
        first = name.split()[0]
        if first.lower() in CLUB_PREFIXES:
            short[name] = f"{first[0].upper()}{value}"
    return short


def league_mean(rows: list[LeagueRow], key: str) -> float | None:
    vals = [r.get(key).value for r in rows if r.get(key) and r.get(key).value is not None]
    return sum(vals) / len(vals) if vals else None


def scatter_mean_position(rows: list[LeagueRow], points: list[dict[str, object]]) -> dict[str, float]:
    """Where the league average sits inside the same normalised box, for the
    crosshair. Derived from the points so it cannot drift from them."""
    if not points:
        return {}
    return {
        "x": sum(float(p["x"]) for p in points) / len(points),
        "y": sum(float(p["y"]) for p in points) / len(points),
    }


FACTOR_LEADER_KEYS: tuple[str, ...] = (
    "efg_pct", "tov_pct", "orb_pct", "ft_rate",
    "opp_efg_pct", "opp_tov_pct", "drb_pct",
)


def factor_leaders(rows: list[LeagueRow]) -> list[LeagueLeader]:
    """Who leads each of the four factors, offense then defense.

    Free-throw rate is included because it is one of the four, but it is a
    style metric — so the label says "highest", not "best". Everything else
    resolves by its own direction, which is why the turnover leader is the team
    that commits fewest and the opponent-turnover leader is the one that forces
    most.
    """
    out: list[LeagueLeader] = []
    for key in FACTOR_LEADER_KEYS:
        meta = SORTABLE[key]
        candidates = [r for r in rows if r.get(key) and r.get(key).value is not None]
        if not candidates:
            continue
        picker = min if meta.direction == "lower_is_better" else max
        best = picker(candidates, key=lambda r: r.get(key).value)
        label = f"Highest {meta.short}" if meta.direction == "neutral" else meta.short
        out.append(LeagueLeader(label=label, team_id=best.team_id,
                                team_name=best.team_name, display=best.get(key).display))
    return out


# ---- team page --------------------------------------------------------------


TEAM_TABS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("splits", "Splits"),
    ("quarters", "Quarters"),
    ("situations", "Situations"),
    ("games", "Games"),
)

HEADLINE_KEYS: tuple[str, ...] = ("offensive_rating", "defensive_rating", "net_rating", "pace")
OFFENSIVE_FACTORS: tuple[str, ...] = ("efg_pct", "tov_pct", "orb_pct", "ft_rate")
DEFENSIVE_FACTORS: tuple[str, ...] = ("opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate")

QUARTER_SEGMENTS: tuple[str, ...] = ("q1", "q2", "q3", "q4")
SITUATION_SEGMENTS: tuple[str, ...] = ("close", "leading", "trailing", "clutch")
SEGMENT_TABLE_METRICS: tuple[str, ...] = (
    "offensive_rating", "defensive_rating", "net_rating",
    "efg_pct", "tov_pct", "orb_pct",
)


@dataclass(frozen=True)
class SegmentRow:
    """One segment as a table row: its label, its metrics, and its sample."""

    segment: str
    label: str
    definition: str
    cells: dict[str, MetricCell]
    sample: SampleView

    def get(self, key: str) -> MetricCell | None:
        return self.cells.get(key)


def segment_rows(
    team: TeamAnalytics,
    segments: tuple[str, ...],
    outcome: str = "all",
    metrics: tuple[str, ...] = SEGMENT_TABLE_METRICS,
) -> list[SegmentRow]:
    """One row per segment. A cell below the sample floor still appears — with
    its state — because "we have too little of this" is itself information."""
    rows: list[SegmentRow] = []
    for segment in segments:
        cell = team.cell(segment, outcome)
        if cell is None:
            continue
        rows.append(
            SegmentRow(
                segment=segment,
                label=SEGMENT_LABELS.get(segment, segment),
                definition=SEGMENT_DEFINITIONS.get(segment, ""),
                cells={k: c for k in metrics if (c := metric_cell(k, cell))},
                sample=sample_view(cell),
            )
        )
    return rows


def quarter_bars(team: TeamAnalytics, outcome: str = "all") -> list[tuple[str, float, str]]:
    """Net rating by quarter, ready for the bar macro."""
    meta = METRIC_META["net_rating"]
    out: list[tuple[str, float, str]] = []
    for segment in QUARTER_SEGMENTS:
        cell = team.cell(segment, outcome)
        if cell is None or "net_rating" not in cell.metrics:
            continue
        value = cell.metrics["net_rating"]
        out.append((SEGMENT_LABELS[segment], value, format_value(meta, value)))
    return out


def headline_metrics(team: TeamAnalytics) -> list[MetricCell]:
    cell = team.cell("full", "all")
    if cell is None:
        return []
    return [c for key in HEADLINE_KEYS if (c := metric_cell(key, cell))]


def team_four_factors(team: TeamAnalytics, segment: str = "full") -> FourFactors:
    """Both halves out of the same cell, so they are the same statistic.

    The defensive four used to be recomputed here from the game rows with a
    possession denominator, while the offensive turnover rate came from the
    cell with a plays denominator — a two to three point gap on every team, in
    the same table, on what reads as one measure. Both sides now come from the
    cell, where the builder ran all eight through the same functions in
    ``formulas.py``. Ranks arrive with them.
    """
    cell = team.cell(segment, "all")
    if cell is None:
        return FourFactors()
    return FourFactors(
        offense=[c for key in OFFENSIVE_FACTORS if (c := metric_cell(key, cell))],
        defense=[c for key in DEFENSIVE_FACTORS if (c := metric_cell(key, cell))],
    )


@dataclass(frozen=True)
class GameLogRow:
    game: GameRow
    cells: dict[str, MetricCell]

    @property
    def date(self) -> str:
        return self.game.game_date[:10]

    @property
    def result(self) -> str:
        return "W" if self.game.win else "L"

    @property
    def score(self) -> str:
        return f"{self.game.score_for}-{self.game.score_against}"

    @property
    def venue(self) -> str:
        return "vs" if self.game.is_home else "at"

    def get(self, key: str) -> MetricCell | None:
        return self.cells.get(key)


GAME_LOG_METRICS: tuple[str, ...] = (
    "offensive_rating", "defensive_rating", "net_rating", "pace",
    "efg_pct", "tov_pct", "orb_pct", "ft_rate",
)


def game_log(team: TeamAnalytics, *, newest_first: bool = True) -> list[GameLogRow]:
    """One row per game. No league ranks — a single game is not ranked against
    a season, and pretending otherwise would be the sort of number that looks
    authoritative and means nothing."""
    rows = [
        GameLogRow(
            game=g,
            cells={
                key: MetricCell(
                    key=key, label=METRIC_META[key].label, short=METRIC_META[key].short,
                    value=g.metrics[key], display=format_value(METRIC_META[key], g.metrics[key]),
                    direction=METRIC_META[key].direction,
                )
                for key in GAME_LOG_METRICS
                if key in g.metrics
            },
        )
        for g in team.games
    ]
    rows.sort(key=lambda r: r.game.game_date, reverse=newest_first)
    return rows


def dumbbell_bounds(rows: list[SplitRow]) -> dict[str, tuple[float, float]]:
    """Per-metric axis bounds for the wins/losses dumbbells.

    Each metric gets its own scale — a shared one would squash turnover rate
    into invisibility beside offensive rating.
    """
    bounds: dict[str, tuple[float, float]] = {}
    for row in rows:
        values = [v for v in (row.wins, row.losses, row.overall) if v is not None]
        if len(values) < 2:
            continue
        lo, hi = min(values), max(values)
        pad = (hi - lo) * 0.35 or abs(hi) * 0.05 or 1.0
        bounds[row.meta.key] = (lo - pad, hi + pad)
    return bounds


# ---- explorer ---------------------------------------------------------------


METRIC_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "efficiency": ("Efficiency", ("offensive_rating", "defensive_rating", "net_rating", "pace")),
    "four_factors": ("Four factors", ("efg_pct", "tov_pct", "orb_pct", "ft_rate")),
    "shooting": ("Shooting", ("efg_pct", "fg3a_rate", "ast_to_ratio")),
}

# The metric each family is ranked by, and which is used for the "vs season"
# delta. Chosen as the one a reader is actually comparing teams on.
FAMILY_PRIMARY: dict[str, str] = {
    "efficiency": "net_rating",
    "four_factors": "efg_pct",
    "shooting": "efg_pct",
}


@dataclass(frozen=True)
class ExplorerRow:
    rank: int
    team_id: str
    team_name: str
    cells: dict[str, MetricCell]
    sample: SampleView
    vs_season: float | None
    vs_season_display: str
    primary_key: str

    def get(self, key: str) -> MetricCell | None:
        return self.cells.get(key)

    @property
    def vs_season_direction(self) -> int:
        """+1 better than their own baseline, -1 worse, 0 neither or unknown.

        Read against the metric's own direction, so a *fall* in turnover rate
        counts as better rather than worse.
        """
        if self.vs_season is None:
            return 0
        meta = METRIC_META.get(self.primary_key)
        if meta is None or meta.direction == "neutral":
            return 0
        better = self.vs_season > 0 if meta.direction == "higher_is_better" else self.vs_season < 0
        if abs(self.vs_season) < 1e-9:
            return 0
        return 1 if better else -1


def explorer_rows(
    teams: dict[str, TeamAnalytics],
    *,
    segment: str = "full",
    outcome: str = "all",
    family: str = "efficiency",
) -> list[ExplorerRow]:
    """All fourteen teams under one Outcome x Segment, ranked.

    The ``vs season`` column compares each team's segment value against its own
    full-game value **under the same outcome**. That baseline matters: in
    "Losses / Q4", comparing against the team's overall season would conflate
    "worse in losses" with "worse in the fourth", and the column would stop
    meaning anything.

    Teams whose cell is below the sample floor are still returned — with their
    state, and unranked — because their absence is information too.
    """
    _label, keys = METRIC_FAMILIES.get(family, METRIC_FAMILIES["efficiency"])
    primary = FAMILY_PRIMARY.get(family, "net_rating")
    meta = METRIC_META[primary]

    built: list[tuple[float | None, ExplorerRow]] = []
    for tid, team in teams.items():
        cell = team.cell(segment, outcome)
        if cell is None:
            continue
        cells = {k: c for k in keys if (c := metric_cell(k, cell))}

        baseline_cell = team.cell("full", outcome)
        value = cell.metrics.get(primary)
        baseline = baseline_cell.metrics.get(primary) if baseline_cell else None
        delta = None
        if value is not None and baseline is not None and segment != "full":
            delta = value - baseline

        if delta is None:
            delta_display = "—"
        elif meta.unit == "pct":
            delta_display = f"{delta * 100:+.1f}"
        else:
            delta_display = f"{delta:+.1f}"

        built.append((
            value if cell.sample_state != "insufficient" else None,
            ExplorerRow(rank=0, team_id=tid, team_name=team.team_name, cells=cells,
                        sample=sample_view(cell), vs_season=delta,
                        vs_season_display=delta_display, primary_key=primary),
        ))

    ranked = [(v, r) for v, r in built if v is not None]
    unranked = [r for v, r in built if v is None]
    ranked.sort(key=lambda vr: vr[0], reverse=meta.direction != "lower_is_better")

    rows = [
        ExplorerRow(rank=i, team_id=r.team_id, team_name=r.team_name, cells=r.cells,
                    sample=r.sample, vs_season=r.vs_season,
                    vs_season_display=r.vs_season_display, primary_key=r.primary_key)
        for i, (_v, r) in enumerate(ranked, start=1)
    ]
    rows.extend(sorted(unranked, key=lambda r: r.team_name))
    return rows


def explorer_columns(family: str) -> list[tuple[str, str]]:
    _label, keys = METRIC_FAMILIES.get(family, METRIC_FAMILIES["efficiency"])
    return [(k, METRIC_META[k].short) for k in keys]


# ---- season identity profile ------------------------------------------------
#
# The artifact stores counts. Everything below turns them into rates once, in
# one place, and attaches a league rank where a rank is meaningful. Two rules
# are enforced structurally rather than by convention:
#
#   * the experimental shot metrics are forced `neutral` and never ranked, so
#     `MetricCell.tint` is zero and no template can shade them;
#   * a scoring source declares whether it PARTITIONS the total or merely
#     describes part of it, and only a partition may be drawn as one.


PROFILE_META: dict[str, MetricMeta] = {
    # transition — running more is style, conceding transition is not
    "fb_rate": MetricMeta("fb_rate", "Fast-Break Attempt Rate", "FB rate", "neutral", "pct"),
    "fb_fg_pct": MetricMeta("fb_fg_pct", "Fast-Break FG%", "FB FG%", "higher_is_better", "pct"),
    "fb_points_pg": MetricMeta("fb_points_pg", "Fast-Break Points / game", "FB pts", "higher_is_better", "count"),
    "fb_rate_allowed": MetricMeta("fb_rate_allowed", "Fast-Break Rate Allowed", "FB allowed", "lower_is_better", "pct"),
    "fb_fg_pct_allowed": MetricMeta("fb_fg_pct_allowed", "Opponent Fast-Break FG%", "oFB FG%", "lower_is_better", "pct"),
    # scoring sources — the partition is style, the contextual ones have a direction
    "share_2pt": MetricMeta("share_2pt", "Share of Points from 2PT", "2PT", "neutral", "pct"),
    "share_3pt": MetricMeta("share_3pt", "Share of Points from 3PT", "3PT", "neutral", "pct"),
    "share_ft": MetricMeta("share_ft", "Share of Points from FT", "FT", "neutral", "pct"),
    "pot_pg": MetricMeta("pot_pg", "Points Off Turnovers / game", "PoT", "higher_is_better", "count"),
    "points_per_opp_tov": MetricMeta("points_per_opp_tov", "Points per Opponent Turnover", "pts/TO", "higher_is_better", "ratio", 2),
    "second_chance_pg": MetricMeta("second_chance_pg", "Second-Chance Points / game", "2nd ch.", "higher_is_better", "count"),
    "second_chance_conversion": MetricMeta("second_chance_conversion", "Second-Chance Conversion", "2nd conv.", "higher_is_better", "pct"),
    "assisted_share": MetricMeta("assisted_share", "Assisted Share of Made FG", "AST%", "neutral", "pct"),
    "assisted_3pm_share": MetricMeta("assisted_3pm_share", "Assisted Share of Made 3PT", "AST% 3PT", "neutral", "pct"),
    # scoring rhythm
    "runs_8_for_pg": MetricMeta("runs_8_for_pg", "8+ Point Runs Made / game", "8+ made", "higher_is_better", "ratio", 2),
    "runs_8_against_pg": MetricMeta("runs_8_against_pg", "8+ Point Runs Conceded / game", "8+ conceded", "lower_is_better", "ratio", 2),
    "largest_run_for_pg": MetricMeta("largest_run_for_pg", "Largest Run Made / game", "Best run", "higher_is_better", "count"),
    "largest_run_against_pg": MetricMeta("largest_run_against_pg", "Largest Run Conceded / game", "Worst run", "lower_is_better", "count"),
    "scoring_droughts_pg": MetricMeta("scoring_droughts_pg", "Scoring Droughts 3m+ / game", "Droughts", "lower_is_better", "ratio", 2),
    "fg_droughts_pg": MetricMeta("fg_droughts_pg", "Field-Goal Droughts 3m+ / game", "FG droughts", "lower_is_better", "ratio", 2),
    "longest_fg_drought_s": MetricMeta("longest_fg_drought_s", "Longest Field-Goal Drought", "Longest FG drought", "lower_is_better", "seconds"),
}

# Shot location is style, exactly like Pace: a team that shoots more threes is
# not a better team. Held apart from PROFILE_META because these are never
# ranked either — the geometry is provisional and a league position would imply
# a confidence the validation does not support.
SHOT_META: dict[str, MetricMeta] = {
    "zone_share_lane_2pt": MetricMeta("zone_share_lane_2pt", "Lane share", "Lane", "neutral", "pct"),
    "zone_share_midrange_2pt": MetricMeta("zone_share_midrange_2pt", "Mid-range share", "Mid", "neutral", "pct"),
    "zone_share_corner_3": MetricMeta("zone_share_corner_3", "Corner three share", "Corner 3", "neutral", "pct"),
    "zone_share_atb_3": MetricMeta("zone_share_atb_3", "Above-the-break three share", "ATB 3", "neutral", "pct"),
    "rim_share": MetricMeta("rim_share", "Rim attempt share", "Rim", "neutral", "pct"),
}

TRANSITION_KEYS: tuple[str, ...] = (
    "fb_rate", "fb_fg_pct", "fb_points_pg", "fb_rate_allowed", "fb_fg_pct_allowed",
)
SCORING_CONTEXT_KEYS: tuple[str, ...] = (
    "pot_pg", "points_per_opp_tov", "second_chance_pg", "second_chance_conversion",
    "fb_points_pg", "assisted_share", "assisted_3pm_share",
)
SCORING_PARTITION_KEYS: tuple[str, ...] = ("share_2pt", "share_3pt", "share_ft")
RUNS_KEYS: tuple[str, ...] = (
    "runs_8_for_pg", "runs_8_against_pg", "largest_run_for_pg", "largest_run_against_pg",
    "scoring_droughts_pg", "fg_droughts_pg", "longest_fg_drought_s",
)

# Ten provider turnover categories collapse to four for display. The three
# named ones carry most of the volume and mean something distinct to a coach;
# the seven violations are each under three per cent and are more legible
# pooled. The raw ten are still rendered underneath, untouched.
TURNOVER_BUCKETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bad_pass", "Bad pass", ("bad-pass",)),
    ("ball_handling", "Ball handling", ("ball-handling",)),
    ("travelling", "Travelling", ("travelling",)),
    ("violations", "Violations & other", (
        "other", "24-seconds-violation", "out-of-bounds", "8-seconds-violation",
        "5-seconds-violation", "backcourt-violation", "3-seconds-violation",
    )),
)


def _rate(numerator: float | int | None, denominator: float | int | None) -> float | None:
    """A rate, or nothing. Never a zero standing in for an undefined value."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def format_profile_value(meta: MetricMeta, value: float | None) -> str:
    if value is None:
        return "—"
    if meta.unit == "seconds":
        minutes, seconds = divmod(int(round(value)), 60)
        return f"{minutes}:{seconds:02d}"
    if meta.unit == "pct":
        return f"{value * 100:.{meta.decimals}f}%"
    return f"{value:.{meta.decimals}f}"


def profile_values(team: TeamAnalytics) -> dict[str, float | None]:
    """Every derived season rate for one team, from the artifact's counts.

    One function so a value is computed once and the league ranking below reads
    exactly what the page renders.
    """
    p = team.profile
    t, s, r = p.transition, p.scoring, p.runs
    games = r.games or team.games_n or 1

    return {
        "fb_rate": _rate(t.fb_fga, t.fga),
        "fb_fg_pct": _rate(t.fb_fgm, t.fb_fga),
        "fb_points_pg": _rate(t.fb_points, games),
        "fb_rate_allowed": _rate(t.fb_fga_allowed, t.opp_fga),
        "fb_fg_pct_allowed": _rate(t.fb_fgm_allowed, t.fb_fga_allowed),

        "share_2pt": _rate(s.points_2pt, s.points),
        "share_3pt": _rate(s.points_3pt, s.points),
        "share_ft": _rate(s.points_ft, s.points),
        "pot_pg": _rate(s.points_off_turnovers, games),
        "points_per_opp_tov": _rate(s.points_off_turnovers, s.opponent_turnovers),
        "second_chance_pg": _rate(s.second_chance_points, games),
        "second_chance_conversion": _rate(s.scoring_oreb_possessions, s.oreb_possessions),
        "assisted_share": _rate(s.assisted_fgm, s.assisted_fgm + s.unassisted_fgm),
        "assisted_3pm_share": _rate(s.assisted_3pm, s.assisted_3pm + s.unassisted_3pm),

        "runs_8_for_pg": _rate(r.runs_8_plus_for, games),
        "runs_8_against_pg": _rate(r.runs_8_plus_against, games),
        "largest_run_for_pg": _rate(r.largest_run_for_sum, games),
        "largest_run_against_pg": _rate(r.largest_run_against_sum, games),
        "scoring_droughts_pg": _rate(r.scoring_droughts_3m, games),
        "fg_droughts_pg": _rate(r.fg_droughts_3m, games),
        "longest_fg_drought_s": r.longest_fg_drought_s or None,
    }


def profile_ranks(teams: dict[str, TeamAnalytics]) -> dict[str, dict[str, tuple[int, int]]]:
    """``{team_id: {metric: (rank, eligible)}}`` for the profile metrics.

    Computed here rather than stamped at build time because these are season
    figures over a fixed fourteen-team league — the whole ranking costs one
    pass over fourteen small objects, and keeping it in the view layer means
    the rank can never disagree with the value printed beside it.
    """
    values = {tid: profile_values(team) for tid, team in teams.items()}
    out: dict[str, dict[str, tuple[int, int]]] = {tid: {} for tid in teams}

    for key, meta in PROFILE_META.items():
        present = {tid: v[key] for tid, v in values.items() if v.get(key) is not None}
        if len(present) < 2:
            continue
        order = sorted(present.items(), key=lambda kv: kv[1],
                       reverse=meta.direction != "lower_is_better")
        n = len(order)
        for position, (tid, _value) in enumerate(order, start=1):
            out[tid][key] = (position, n)
    return out


def profile_cell(
    key: str,
    values: dict[str, float | None],
    ranks: dict[str, tuple[int, int]] | None = None,
) -> MetricCell | None:
    """One profile metric ready to render, with its rank where one exists."""
    meta = PROFILE_META.get(key)
    if meta is None:
        return None
    value = values.get(key)
    if value is None:
        return None

    rank = eligible = None
    percentile = None
    if ranks and key in ranks:
        rank, eligible = ranks[key]
        if eligible > 1:
            percentile = round(100.0 * (eligible - rank) / (eligible - 1), 1)

    return MetricCell(
        key=key, label=meta.label, short=meta.short, value=value,
        display=format_profile_value(meta, value), direction=meta.direction,
        rank=rank, percentile=percentile, eligible_teams=eligible or 0,
    )


def transition_view(
    team: TeamAnalytics, ranks: dict[str, tuple[int, int]] | None = None
) -> list[MetricCell]:
    """Five numbers. The attempt rate is style — a team that runs more is not a
    better team — while conceding transition has a clear better end.

    Nothing here has a half-court counterpart, and none is offered: a false
    provider flag means only that the provider did not call the play a fast
    break, and 5.7% of provider-negatives happen inside four seconds of a
    change of possession.
    """
    values = profile_values(team)
    return [c for key in TRANSITION_KEYS if (c := profile_cell(key, values, ranks))]


@dataclass(frozen=True)
class ScoringSources:
    """Two different kinds of thing, kept apart in the type rather than in a
    template's discipline."""

    partition: list[MetricCell] = field(default_factory=list)
    context: list[MetricCell] = field(default_factory=list)

    @property
    def partition_total(self) -> float:
        return sum(c.value or 0.0 for c in self.partition)

    @property
    def partition_reconciles(self) -> bool:
        """The three shares are the whole of scoring, so they must sum to one.
        A template may only draw a part-to-whole when this is true."""
        return abs(self.partition_total - 1.0) < 1e-6


def scoring_sources_view(
    team: TeamAnalytics, ranks: dict[str, tuple[int, int]] | None = None
) -> ScoringSources:
    """2PT/3PT/FT partition the points exactly. Everything else overlaps —
    a fast-break layup off a steal is points off turnovers and fast-break
    points and two-point scoring at the same time — so the contextual sources
    are returned separately and never sum to anything."""
    values = profile_values(team)
    return ScoringSources(
        partition=[c for key in SCORING_PARTITION_KEYS if (c := profile_cell(key, values, None))],
        context=[c for key in SCORING_CONTEXT_KEYS if (c := profile_cell(key, values, ranks))],
    )


def runs_view(
    team: TeamAnalytics, ranks: dict[str, tuple[int, int]] | None = None
) -> list[MetricCell]:
    """Scoring rhythm: runs made and conceded, and how often the team goes
    quiet. Descriptive patterns — nothing here claims a cause."""
    values = profile_values(team)
    return [c for key in RUNS_KEYS if (c := profile_cell(key, values, ranks))]


@dataclass(frozen=True)
class ShotZoneRow:
    key: str
    label: str
    share: MetricCell
    attempts: int
    efg: str
    efg_value: float | None


@dataclass(frozen=True)
class ShotProfile:
    """EXPERIMENTAL. Complete data, provisional validation.

    Coordinate coverage is total — 24,432 of 24,432 shots, none left without a
    zone — but the human check is twenty labelled shots from one game in one
    arena, and the same twenty both diagnosed and confirmed the one rule change
    made since. That is enough to describe where a team shoots from. It is not
    enough to plot a shot, quote a distance, or rank a team on efficiency
    inside a zone.
    """

    zones: list[ShotZoneRow] = field(default_factory=list)
    rim: MetricCell | None = None
    attempts: int = 0
    unclassified: int = 0
    validation_state: str = "provisional_deterministic"

    @property
    def is_experimental(self) -> bool:
        return self.validation_state != "validated_deterministic"


_ZONE_LABELS: tuple[tuple[str, str], ...] = (
    ("lane_2pt", "Lane"),
    ("midrange_2pt", "Mid-range"),
    ("corner_3", "Corner 3"),
    ("atb_3", "Above the break 3"),
)


def shot_profile_view(team: TeamAnalytics) -> ShotProfile:
    """Attempt shares and per-zone eFG%, with the attempt count beside every
    efficiency figure and no rank on any of them.

    eFG% is ``points / 2 / attempts``: inside a two-point zone every make is
    worth two, inside a three-point zone every make is worth three, and
    ``(FGM + 0.5*3PM)/FGA`` reduces to the same thing in both cases.
    """
    s = team.profile.shots
    if not s.fga:
        return ShotProfile(validation_state=s.validation_state)

    rows: list[ShotZoneRow] = []
    for zone, label in _ZONE_LABELS:
        attempts = s.zone_attempts.get(zone, 0)
        share_key = f"zone_share_{zone}"
        meta = SHOT_META[share_key]
        share_value = attempts / s.fga
        efg_value = _rate(s.zone_points.get(zone, 0) / 2.0, attempts)
        rows.append(ShotZoneRow(
            key=zone, label=label,
            share=MetricCell(
                key=share_key, label=meta.label, short=meta.short, value=share_value,
                display=format_profile_value(meta, share_value), direction="neutral",
            ),
            attempts=attempts,
            efg=f"{efg_value * 100:.1f}%" if efg_value is not None else "—",
            efg_value=efg_value,
        ))

    rim_meta = SHOT_META["rim_share"]
    rim_value = s.rim_attempts / s.fga
    return ShotProfile(
        zones=rows,
        rim=MetricCell(
            key="rim_share", label=rim_meta.label, short=rim_meta.short, value=rim_value,
            display=format_profile_value(rim_meta, rim_value), direction="neutral",
        ),
        attempts=s.fga,
        unclassified=s.unclassified,
        validation_state=s.validation_state,
    )


@dataclass(frozen=True)
class TurnoverBucket:
    key: str
    label: str
    count: int
    share: float
    display: str


@dataclass(frozen=True)
class TurnoverView:
    """The provider's own categories, grouped for reading and listed in full
    underneath. These four are exhaustive over the ten, so they genuinely do
    partition the total and may be drawn as one."""

    total: int
    buckets: list[TurnoverBucket] = field(default_factory=list)
    detail: list[tuple[str, int, float]] = field(default_factory=list)
    forced_total: int = 0
    forced_buckets: list[TurnoverBucket] = field(default_factory=list)


def _buckets(by_type: dict[str, int], total: int) -> list[TurnoverBucket]:
    out: list[TurnoverBucket] = []
    for key, label, members in TURNOVER_BUCKETS:
        count = sum(by_type.get(m, 0) for m in members)
        share = (count / total) if total else 0.0
        out.append(TurnoverBucket(
            key=key, label=label, count=count, share=share, display=f"{share * 100:.1f}%"
        ))
    return out


def turnover_view(team: TeamAnalytics) -> TurnoverView:
    """Turnover rate says how often; this says what kind.

    The provider's ten categories are preserved verbatim rather than mapped
    onto an invented scouting taxonomy — the source field is clean, with no
    nulls anywhere in the season, so it is already the trusted representation.
    """
    tv = team.profile.turnovers
    detail = [
        (name, count, (count / tv.total) if tv.total else 0.0)
        for name, count in sorted(tv.by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return TurnoverView(
        total=tv.total,
        buckets=_buckets(tv.by_type, tv.total),
        detail=detail,
        forced_total=tv.forced_total,
        forced_buckets=_buckets(tv.forced_by_type, tv.forced_total),
    )


@dataclass(frozen=True)
class ConsistencyCell:
    """A three-level label, or nothing at all.

    Nothing is the important case: the coefficient of variation is std over
    |mean|, so for a metric that sits near zero it inflates without bound.
    Where ``cv_applicable`` is false there is no consistency claim to make and
    none is made.
    """

    key: str
    label: str
    short: str
    level: str | None
    cv: float | None
    spread: str


CONSISTENCY_LEVELS: tuple[tuple[float, str], ...] = ((0.10, "Steady"), (0.25, "Typical"))


def consistency_view(
    team: TeamAnalytics, keys: tuple[str, ...] = HEADLINE_KEYS
) -> list[ConsistencyCell]:
    """How steady each headline metric was, game to game.

    Deliberately the unweighted per-game distribution rather than the season
    value: a ten-shot night and a ninety-shot night genuinely do count equally
    when the question is "what does a typical game look like".
    """
    out: list[ConsistencyCell] = []
    for key in keys:
        entry = team.profile.stability.get(key)
        meta = METRIC_META.get(key)
        if entry is None or meta is None:
            continue
        level = None
        if entry.cv_applicable and entry.cv is not None:
            level = "Variable"
            for threshold, name in CONSISTENCY_LEVELS:
                if abs(entry.cv) <= threshold:
                    level = name
                    break
        spread = "—"
        if entry.min is not None and entry.max is not None:
            spread = f"{format_value(meta, entry.min)} to {format_value(meta, entry.max)}"
        out.append(ConsistencyCell(
            key=key, label=meta.label, short=meta.short,
            level=level, cv=entry.cv if entry.cv_applicable else None, spread=spread,
        ))
    return out


@dataclass(frozen=True)
class ComebackView:
    """Counts first. The denominators run from five to twenty-two across the
    league, so a bare percentage would put a four-from-five and a
    nine-from-twenty side by side as if they were the same claim."""

    trailing_games: int
    comeback_wins: int
    leading_games: int
    blown_leads: int

    @property
    def comeback_display(self) -> str:
        return f"{self.comeback_wins} of {self.trailing_games}"

    @property
    def blown_display(self) -> str:
        return f"{self.blown_leads} of {self.leading_games}"

    @property
    def has_comeback_sample(self) -> bool:
        return self.trailing_games > 0

    @property
    def has_lead_sample(self) -> bool:
        return self.leading_games > 0


def comeback_view(team: TeamAnalytics) -> ComebackView:
    c = team.profile.comeback
    return ComebackView(
        trailing_games=c.games_trailing_10_plus, comeback_wins=c.comeback_wins,
        leading_games=c.games_leading_10_plus, blown_leads=c.blown_leads,
    )
