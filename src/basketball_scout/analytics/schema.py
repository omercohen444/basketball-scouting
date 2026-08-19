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

ANALYTICS_ARTIFACT_VERSION = "analytics-v2"
INDEX_FILENAME = "index.json"

# Versions this loader understands. An older artifact is refused outright rather
# than half-loaded — a v1 file has no `profile` block, and silently rendering a
# team page with the identity half missing would look like a data problem in the
# league rather than a stale build.
SUPPORTED_ARTIFACT_VERSIONS: frozenset[str] = frozenset({"analytics-v2"})

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

# The four defensive factors, computed from the same `components_against` the
# segment builder already produces for the ratings and then discarded. They live
# in the same `metrics` dict as the ten above so ranking, percentiles and the
# unweighted reconciliation all work without a second code path.
OPPONENT_METRICS: tuple[str, ...] = ("opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate")

CELL_METRICS: tuple[str, ...] = METRICS + OPPONENT_METRICS

# Ranking direction for the four. Forcing more opponent turnovers and keeping
# more defensive boards is good; letting opponents shoot well or reach the line
# is not. The ten core directions already live in stats.league_context.
OPPONENT_DIRECTIONS: dict[str, str] = {
    "opp_efg_pct": "lower_is_better",
    "opp_tov_pct": "higher_is_better",
    "drb_pct": "higher_is_better",
    "opp_ft_rate": "lower_is_better",
}

# Coefficient of variation is std/|mean|, so it is only interpretable on a
# strictly positive scale. A difference metric sits near zero and inflates CV
# without bound. Mirrors agents/evidence_pack.py's per-metric `cv_applicable`
# declaration so the site and the packs agree on which metrics have a
# meaningful consistency figure.
CV_APPLICABLE: dict[str, bool] = {m: m != "net_rating" for m in CELL_METRICS}

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
    # How the game actually went, from this team's side. Facts read off the
    # running score, not a judgement about it.
    times_tied: int = 0
    lead_changes: int = 0
    largest_lead: int = 0
    largest_deficit: int = 0


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


# ---- season-scope identity profile ------------------------------------------
#
# Everything below stores COUNTS AND SUMS ONLY. Every rate is derived in
# analytics/views.py. Three reasons: the artifact stays auditable (that
# `unclassified` is zero is visible rather than implied by a share of 1.0),
# rounding happens in exactly one place, and a reader can recompute any
# displayed figure from the file.


class ShotZoneProfile(BaseModel):
    """Coarse shot location. EXPERIMENTAL — see `validation_state`.

    The taxonomy is the four zones `pbp/geometry.py` actually implements. There
    is no restricted-area split and no left/right corner split; both are
    deliberately deferred. `rim_attempts` comes from the trusted PBP shot type,
    not from coordinates, and so is the sturdiest number here.

    `zone_points` divided by two and by `zone_attempts` is eFG% for that zone:
    within a two-point zone every make is worth 2, within a three-point zone
    every make is worth 3, and (fgm + 0.5*fg3m)/fga reduces to points/2/fga in
    both cases.
    """

    model_config = ConfigDict(extra="forbid")

    fga: int = 0
    lane_2pt: int = 0
    midrange_2pt: int = 0
    corner_3: int = 0
    atb_3: int = 0
    unclassified: int = 0
    rim_attempts: int = 0
    zone_attempts: dict[str, int] = Field(default_factory=dict)
    zone_points: dict[str, int] = Field(default_factory=dict)
    validation_state: str = "provisional_deterministic"


class TransitionProfile(BaseModel):
    """Provider fast-break flag, both directions.

    The allowed side is the same events grouped by `opponent_id`, so it needs no
    new analytics. A false flag means only that the provider did not call this a
    fast break — never half-court, never set defence.
    """

    model_config = ConfigDict(extra="forbid")

    fga: int = 0
    opp_fga: int = 0
    fb_fga: int = 0
    fb_fgm: int = 0
    fb_points: int = 0
    fb_fga_allowed: int = 0
    fb_fgm_allowed: int = 0
    fb_points_allowed: int = 0


class TurnoverProfile(BaseModel):
    """The provider's own ten turnover categories, verbatim.

    No scouting taxonomy is invented on top: the source field is clean (zero
    nulls across 5,205 events) so the provider's strings are the trusted
    representation. `forced_*` is the opponent's own taxonomy in the same games.
    """

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    forced_total: int = 0
    forced_by_type: dict[str, int] = Field(default_factory=dict)


class ScoringSourceProfile(BaseModel):
    """Where the points came from.

    Two different kinds of thing live here and must not be mixed in one chart.
    `points_2pt`/`points_3pt`/`points_ft` PARTITION the total. The rest are
    CONTEXTUAL and overlap each other — a fast-break layup off a steal is points
    off turnovers and fast-break points and two-point scoring at once.
    """

    model_config = ConfigDict(extra="forbid")

    points: int = 0
    points_2pt: int = 0
    points_3pt: int = 0
    points_ft: int = 0
    points_off_turnovers: int = 0
    opponent_turnovers: int = 0
    second_chance_points: int = 0
    oreb_possessions: int = 0
    scoring_oreb_possessions: int = 0
    fast_break_points: int = 0
    assisted_fgm: int = 0
    unassisted_fgm: int = 0
    assisted_3pm: int = 0
    unassisted_3pm: int = 0


class RunsDroughtsProfile(BaseModel):
    """Scoring rhythm. Descriptive patterns, never a claim about momentum.

    A scoring drought is 180+ seconds of quarter clock with no point of any
    kind; a field-goal drought is 180+ seconds with no made field goal, so free
    throws end the first and not the second. Neither bridges a quarter break.
    """

    model_config = ConfigDict(extra="forbid")

    games: int = 0
    largest_run_for_sum: int = 0
    largest_run_against_sum: int = 0
    largest_run_for_max: int = 0
    largest_run_against_max: int = 0
    runs_8_plus_for: int = 0
    runs_8_plus_against: int = 0
    scoring_droughts_3m: int = 0
    fg_droughts_3m: int = 0
    longest_scoring_drought_s: float = 0.0
    longest_fg_drought_s: float = 0.0


class ComebackBlock(BaseModel):
    """Opportunity-based, never games-played-based. The counts are the point:
    the denominators run from five to twenty-two across the league, so these are
    reported as "n of m" and never ranked."""

    model_config = ConfigDict(extra="forbid")

    games_trailing_10_plus: int = 0
    comeback_wins: int = 0
    games_leading_10_plus: int = 0
    blown_leads: int = 0


class StabilityEntry(BaseModel):
    """How steady one metric was, game to game. `cv_applicable` is false where
    the metric crosses zero and the coefficient of variation would be
    meaningless — see CV_APPLICABLE."""

    model_config = ConfigDict(extra="forbid")

    games: int = 0
    mean: float | None = None
    std: float | None = None
    cv: float | None = None
    cv_applicable: bool = True
    min: float | None = None
    max: float | None = None


class TeamProfile(BaseModel):
    """One team's season identity — the half of the deterministic layer the
    segment grid does not describe."""

    model_config = ConfigDict(extra="forbid")

    shots: ShotZoneProfile = Field(default_factory=ShotZoneProfile)
    transition: TransitionProfile = Field(default_factory=TransitionProfile)
    turnovers: TurnoverProfile = Field(default_factory=TurnoverProfile)
    scoring: ScoringSourceProfile = Field(default_factory=ScoringSourceProfile)
    runs: RunsDroughtsProfile = Field(default_factory=RunsDroughtsProfile)
    comeback: ComebackBlock = Field(default_factory=ComebackBlock)
    stability: dict[str, StabilityEntry] = Field(default_factory=dict)


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
    profile: TeamProfile = Field(default_factory=TeamProfile)

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
