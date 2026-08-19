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
    """One row per team at season scope, with the defensive four factors joined.

    Opponent metrics are derived per team from its own game rows and ranked
    across the league here rather than living in the artifact's cells: they are
    a property of the season aggregate, not of a segment.
    """
    opponent_values: dict[str, dict[str, float]] = {
        tid: opponent_factors(team.games) for tid, team in teams.items()
    }

    opponent_ranks: dict[str, dict[str, tuple[int, float]]] = {tid: {} for tid in teams}
    for key in LEAGUE_OPPONENT_COLUMNS:
        meta = OPPONENT_META[key]
        pairs = [(tid, v[key]) for tid, v in opponent_values.items() if key in v]
        if len(pairs) < 2:
            continue
        pairs.sort(key=lambda kv: kv[1], reverse=meta.direction != "lower_is_better")
        n = len(pairs)
        for position, (tid, _value) in enumerate(pairs, start=1):
            opponent_ranks[tid][key] = (position, round(100.0 * (n - position) / (n - 1), 1))

    rows: list[LeagueRow] = []
    for tid, team in teams.items():
        cell = team.cell("full", "all")
        metrics: dict[str, MetricCell] = {}
        if cell is not None:
            for key in LEAGUE_COLUMNS:
                built = metric_cell(key, cell)
                if built:
                    metrics[key] = built
        for key in LEAGUE_OPPONENT_COLUMNS:
            value = opponent_values[tid].get(key)
            if value is None:
                continue
            meta = OPPONENT_META[key]
            rank, percentile = opponent_ranks[tid].get(key, (None, None))
            metrics[key] = MetricCell(
                key=key, label=meta.label, short=meta.short, value=value,
                display=format_value(meta, value), direction=meta.direction,
                rank=rank, percentile=percentile, eligible_teams=len(opponent_values),
            )
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
