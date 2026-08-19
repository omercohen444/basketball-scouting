"""The on-disk analytics contract.

Two artifacts, one file per team plus an index, mirroring
``agents/pack_store.py``'s envelope so the loader, hashing and failure modes
are already-proven patterns rather than new ones.

**A — game rows.** 26 per team, both sides' box components. Everything the
Games log, the season aggregates and the *defensive* four factors need.

**B — the segment grid.** 11 segments x 3 outcomes per team. Outcome is a
per-game attribute and a segment is a possession filter inside a game, so the
grid is just "filter the games, then aggregate that subset's possessions" —
which is why it precomputes cheaply and why free-form filter composition
(which would need possessions at request time) is deliberately not offered.

Values are volume-weighted: components are summed across games first, then the
formulas run once. That is the convention the season metrics already use. The
unweighted per-game mean is carried alongside for the handful of places that
need to reconcile against a saved scouting report, which was generated under
the older convention — see ``SegmentCell.unweighted``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ANALYTICS_ARTIFACT_VERSION = "analytics-v1"
INDEX_FILENAME = "index.json"

# UI-facing segment keys. `full` is the whole game; the rest are possession
# filters. Deliberately NOT offered: OT (16.7 possessions per team-season),
# narrower clutch windows (~20), and any composition of two segments.
SEGMENTS: tuple[str, ...] = (
    "full", "q1", "q2", "q3", "q4", "h1", "h2",
    "close", "leading", "trailing", "clutch",
)

OUTCOMES: tuple[str, ...] = ("all", "wins", "losses")

# The ten core metrics, in display order.
METRICS: tuple[str, ...] = (
    "offensive_rating", "defensive_rating", "net_rating", "pace",
    "efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio",
)

# Sample thresholds, reusing the project's committed constants rather than
# inventing new ones (agents/evidence_pack.py, stats/winloss.py).
MIN_POSSESSIONS = 50
MIN_GAMES = 3
LOW_POSSESSIONS = 100
LOW_GAMES = 5

SampleState = Literal["sufficient", "limited", "insufficient"]


def classify_sample(possessions: int, games: int) -> SampleState:
    """Where a cell sits against the project's own sample thresholds.

    ``insufficient`` means the numbers exist but must not be shown as a
    result — the caller renders a state, not a value. ``limited`` means show
    them with a visible warning. This is computed once at build time and
    stamped onto the cell so the gate cannot drift from the data it describes.
    """
    if possessions < MIN_POSSESSIONS or games < MIN_GAMES:
        return "insufficient"
    if possessions < LOW_POSSESSIONS or games < LOW_GAMES:
        return "limited"
    return "sufficient"


class BoxComponents(BaseModel):
    """One side's raw box-score counts over some set of possessions."""

    model_config = ConfigDict(extra="forbid")

    fgm: int = 0
    fga: int = 0
    fg3m: int = 0
    fg3a: int = 0
    ftm: int = 0
    fta: int = 0
    orb: int = 0
    drb: int = 0
    ast: int = 0
    tov: int = 0
    pf: int = 0
    points: int = 0


class GameRow(BaseModel):
    """One team-game. 26 per team, 364 in the league.

    Carries both sides' components because the *defensive* four factors are a
    pure derivation from ``components_against`` — opponent eFG%, opponent TOV%,
    defensive rebound %, opponent FT rate — with no new analytics and no raw
    play-by-play.
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    game_date: str
    team_id: str
    opponent_id: str
    opponent_name: str
    is_home: bool
    win: bool
    score_for: int
    score_against: int
    possessions_for: float
    possessions_against: float
    components_for: BoxComponents
    components_against: BoxComponents
    metrics: dict[str, float | None] = Field(default_factory=dict)


class SegmentCell(BaseModel):
    """One (segment, outcome) cell for one team.

    ``metrics`` omits a metric entirely rather than carrying a null where the
    value would be meaningless — Pace on segments with no defined elapsed time,
    for instance. A template cannot render what is not there, which is the
    point: correctness is enforced below presentation, not in the markup.
    """

    model_config = ConfigDict(extra="forbid")

    segment: str
    outcome: str
    games: int = 0
    possessions: int = 0
    sample_state: SampleState = "insufficient"
    metrics: dict[str, float] = Field(default_factory=dict)
    # League rank / percentile per metric, over the teams with a usable sample
    # in this same cell. Absent for a metric the team has no value for.
    ranks: dict[str, int] = Field(default_factory=dict)
    percentiles: dict[str, float] = Field(default_factory=dict)
    eligible_teams: int = 0
    # The legacy unweighted per-game mean, kept only so a value can be
    # reconciled against a saved scouting report generated under that
    # convention. Never the headline number — see the module docstring.
    unweighted: dict[str, float] = Field(default_factory=dict)


class TeamAnalytics(BaseModel):
    """Everything the website needs about one team."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    season: str
    wins: int
    losses: int
    games_n: int
    date_range: str
    games: list[GameRow] = Field(default_factory=list)
    # Keyed "{segment}:{outcome}", e.g. "q4:losses".
    cells: dict[str, SegmentCell] = Field(default_factory=dict)

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}"

    def cell(self, segment: str, outcome: str = "all") -> SegmentCell | None:
        return self.cells.get(f"{segment}:{outcome}")


class AnalyticsArtifact(BaseModel):
    """One team's file, with the hash envelope."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: str = ANALYTICS_ARTIFACT_VERSION
    content_hash: str = ""
    generated_at: str = ""
    team: TeamAnalytics


class AnalyticsIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    file: str
    content_hash: str
    games_n: int
    wins: int
    losses: int


class AnalyticsIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: str = ANALYTICS_ARTIFACT_VERSION
    season: str
    generated_at: str
    teams: list[AnalyticsIndexEntry] = Field(default_factory=list)
