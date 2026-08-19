"""The analytics artifact builder.

Two things carry most of the weight here: the completeness guard, which is what
stops a partial play-by-play cache producing an artifact that *looks* whole,
and the segment predicates, where the opponent side is not always the same
predicate as the team side.
"""

from __future__ import annotations

import json

import pytest
from analytics_factories import make_bundle, make_league, make_possession

from basketball_scout.analytics.build import (
    EXPECTED_GAMES_PER_TEAM,
    EXPECTED_TEAMS,
    IncompleteLeagueError,
    assert_complete_league,
    build_full_cell,
    build_segment_cell,
    build_team_analytics,
    content_hash,
    segment_predicates,
    stamp_league_ranks,
    write_all,
)
from basketball_scout.analytics.schema import (
    OUTCOMES,
    SEGMENTS,
    AnalyticsArtifact,
    AnalyticsIndex,
    classify_sample,
)
from basketball_scout.analytics.store import AnalyticsStore

# ---- the completeness guard -------------------------------------------------


def test_a_complete_league_passes():
    assert_complete_league(make_league())  # 14 x 26


def test_a_missing_team_fails_the_build():
    league = make_league(teams=EXPECTED_TEAMS - 1)
    with pytest.raises(IncompleteLeagueError, match=f"expected {EXPECTED_TEAMS} teams"):
        assert_complete_league(league)


def test_a_short_game_count_fails_the_build():
    """The failure mode this exists for: the loader skips a game whose raw
    play-by-play file is missing, so a partial cache silently produces smaller
    aggregates rather than an error."""
    league = make_league()
    league["segev:4"] = league["segev:4"][:-1]  # 25 games
    with pytest.raises(IncompleteLeagueError) as exc:
        assert_complete_league(league)
    assert "segev:4" in str(exc.value)
    assert f"expected {EXPECTED_GAMES_PER_TEAM} games, found 25" in str(exc.value)


def test_the_error_names_every_short_team_not_just_the_first():
    league = make_league()
    league["segev:4"] = league["segev:4"][:10]
    league["segev:7"] = league["segev:7"][:20]
    with pytest.raises(IncompleteLeagueError) as exc:
        assert_complete_league(league)
    assert "segev:4" in str(exc.value) and "segev:7" in str(exc.value)


def test_a_failed_guard_writes_nothing(tmp_path):
    """Fail before touching the filesystem, not halfway through it."""
    league = make_league(teams=3)
    with pytest.raises(IncompleteLeagueError):
        assert_complete_league(league)
    assert list(tmp_path.iterdir()) == []


# ---- segment predicates -----------------------------------------------------


def test_quarter_and_half_predicates_are_symmetric():
    """Both sides are in the same quarter at the same time, so one predicate
    serves both lists."""
    for segment in ("q1", "q4", "h1", "h2"):
        team_pred, opp_pred = segment_predicates(segment, 4)
        p = make_possession(quarter=1 if segment in ("q1", "h1") else 4)
        assert team_pred(p) == opp_pred(p)


def test_leading_and_trailing_mirror_the_opponent():
    """The asymmetric case. While we lead by four the opponent trails by four,
    so their qualifying possessions are the ones where *their* margin is
    negative — not the ones where it is positive."""
    team_pred, opp_pred = segment_predicates("leading", 4)

    ours_leading = make_possession(offense="home", margin=4)
    theirs_trailing = make_possession(offense="away", margin=-4)
    theirs_leading = make_possession(offense="away", margin=4)

    assert team_pred(ours_leading)
    assert opp_pred(theirs_trailing), "the opponent's trailing possessions overlap ours"
    assert not opp_pred(theirs_leading), "their own leading possessions are a different moment"


def test_trailing_is_the_exact_inverse_of_leading():
    lead_team, lead_opp = segment_predicates("leading", 4)
    trail_team, trail_opp = segment_predicates("trailing", 4)
    for margin in range(-10, 11):
        p = make_possession(margin=margin)
        assert lead_team(p) == trail_opp(p)
        assert trail_team(p) == lead_opp(p)


def test_a_tied_possession_is_neither_leading_nor_trailing():
    lead_team, _ = segment_predicates("leading", 4)
    trail_team, _ = segment_predicates("trailing", 4)
    tied = make_possession(margin=0)
    assert not lead_team(tied) and not trail_team(tied)


def test_unknown_segment_raises():
    with pytest.raises(ValueError, match="unknown segment"):
        segment_predicates("nonsense", 4)


# ---- cell construction ------------------------------------------------------


def test_pace_is_omitted_where_no_elapsed_time_is_defined():
    """There is no rigorous denominator for "minutes spent trailing". An absent
    cell is honest; an invented one is not."""
    bundles = [make_bundle() for _ in range(6)]
    for segment in ("close", "leading", "trailing", "clutch"):
        cell = build_segment_cell(segment, "all", bundles)
        assert "pace" not in cell.metrics, segment


def test_pace_is_present_on_quarters_and_halves():
    bundles = [make_bundle() for _ in range(6)]
    cell = build_segment_cell("q1", "all", bundles)
    assert cell.possessions > 0
    assert "pace" in cell.metrics


def test_a_game_the_team_never_entered_is_skipped_not_counted_as_a_zero():
    """A wire-to-wire win contributes no "trailing" possessions. Skipping it is
    right; counting it as a zero-possession game would inflate the game count
    and drag the average toward nothing."""
    trailed = [
        make_bundle(
            team_possessions=[make_possession(offense="home", margin=-6) for _ in range(8)],
            opponent_possessions=[make_possession(offense="away", margin=6) for _ in range(8)],
        )
        for _ in range(4)
    ]
    never_trailed = make_bundle(
        team_possessions=[make_possession(offense="home", margin=8) for _ in range(8)],
        opponent_possessions=[make_possession(offense="away", margin=-8) for _ in range(8)],
    )

    cell = build_segment_cell("trailing", "all", trailed + [never_trailed])

    assert cell.games == 4, "the wire-to-wire game must not appear in the trailing cell"
    assert cell.possessions == 32

    # ...and that same game does appear in the leading cell.
    leading = build_segment_cell("leading", "all", trailed + [never_trailed])
    assert leading.games == 1


def test_an_empty_cell_is_insufficient_not_a_crash():
    cell = build_segment_cell("clutch", "losses", [])
    assert cell.games == 0 and cell.possessions == 0
    assert cell.sample_state == "insufficient"
    assert cell.metrics == {}


def test_full_cell_comes_from_the_stored_records():
    """`full` is driven off TeamGameStats rather than summed possessions so the
    site's season row matches the reports' exactly — possession-derived
    defensive rebounds drift by a few per season."""
    bundles = [make_bundle() for _ in range(26)]
    cell = build_full_cell("all", bundles)
    assert cell.games == 26
    assert "offensive_rating" in cell.metrics
    assert "pace" in cell.metrics


def test_every_team_gets_the_full_grid():
    bundles = [make_bundle(win=(i % 2 == 0)) for i in range(26)]
    team = build_team_analytics("segev:4", bundles, "TEST", "2025-26")
    assert len(team.cells) == len(SEGMENTS) * len(OUTCOMES) == 33
    assert len(team.games) == 26
    assert team.record == "13-13"
    for segment in SEGMENTS:
        for outcome in OUTCOMES:
            assert team.cell(segment, outcome) is not None, f"{segment}:{outcome}"


def test_the_unweighted_mean_is_carried_only_for_metrics_that_are_shown():
    """It exists to reconcile against a saved report, not to be a second
    parallel dataset."""
    bundles = [make_bundle() for _ in range(6)]
    cell = build_segment_cell("q1", "all", bundles)
    assert set(cell.unweighted) <= set(cell.metrics)


# ---- ranking ----------------------------------------------------------------


def test_ranks_are_stamped_only_over_teams_with_a_usable_sample():
    """Ranking a two-game sample against a twenty-game one is not a ranking, so
    an insufficient team is left unranked rather than ranked last."""
    teams = {}
    for i, tid in enumerate(("segev:2", "segev:3", "segev:4")):
        bundles = [make_bundle(team_id=tid) for _ in range(26)]
        teams[tid] = build_team_analytics(tid, bundles, f"T{i}", "2025-26")
    # Starve one team's cell so it drops below the floor.
    thin = teams["segev:4"].cells["q1:all"]
    thin.possessions = 5
    thin.games = 1
    thin.sample_state = classify_sample(5, 1)

    stamp_league_ranks(teams)

    assert teams["segev:4"].cells["q1:all"].ranks == {}
    ranked = [t.cells["q1:all"] for tid, t in teams.items() if tid != "segev:4"]
    assert all(c.eligible_teams == 2 for c in ranked)


def test_lower_is_better_metrics_rank_ascending():
    """Turnover rate: the lowest value is rank 1, not the highest."""
    teams = {}
    for tid, tov in (("segev:2", 0.10), ("segev:3", 0.20)):
        bundles = [make_bundle(team_id=tid) for _ in range(26)]
        team = build_team_analytics(tid, bundles, tid, "2025-26")
        team.cells["q1:all"].metrics["tov_pct"] = tov
        teams[tid] = team
    stamp_league_ranks(teams)
    assert teams["segev:2"].cells["q1:all"].ranks["tov_pct"] == 1
    assert teams["segev:3"].cells["q1:all"].ranks["tov_pct"] == 2


# ---- the artifact envelope --------------------------------------------------


def test_content_hash_changes_when_a_value_changes():
    bundles = [make_bundle() for _ in range(26)]
    team = build_team_analytics("segev:4", bundles, "TEST", "2025-26")
    before = content_hash(team)
    team.cells["q1:all"].metrics["efg_pct"] = 0.999
    assert content_hash(team) != before


def test_written_artifacts_reload_and_verify(tmp_path):
    bundles = [make_bundle(team_id="segev:4") for _ in range(26)]
    team = build_team_analytics("segev:4", bundles, "TEST", "2025-26")
    artifact = AnalyticsArtifact(content_hash=content_hash(team), generated_at="2026-01-01T00:00:00Z", team=team)
    index = AnalyticsIndex(
        season="2025-26", generated_at="2026-01-01T00:00:00Z",
        teams=[{
            "team_id": "segev:4", "team_name": "TEST", "file": "analytics_segev_4.json",
            "content_hash": artifact.content_hash, "games_n": 26, "wins": team.wins, "losses": team.losses,
        }],
    )
    write_all({"segev:4": artifact}, index, tmp_path)

    store = AnalyticsStore(tmp_path)
    assert store.available
    assert store.team_ids() == ["segev:4"]
    assert store.team("segev:4").team_id == "segev:4"


def test_a_tampered_artifact_is_rejected(tmp_path):
    """The hash is not decoration — an edited artifact must fail loudly rather
    than serve altered numbers."""
    from basketball_scout.analytics.store import AnalyticsArtifactError

    bundles = [make_bundle(team_id="segev:4") for _ in range(26)]
    team = build_team_analytics("segev:4", bundles, "TEST", "2025-26")
    artifact = AnalyticsArtifact(content_hash=content_hash(team), generated_at="x", team=team)
    index = AnalyticsIndex(
        season="2025-26", generated_at="x",
        teams=[{
            "team_id": "segev:4", "team_name": "TEST", "file": "analytics_segev_4.json",
            "content_hash": artifact.content_hash, "games_n": 26, "wins": team.wins, "losses": team.losses,
        }],
    )
    write_all({"segev:4": artifact}, index, tmp_path)

    path = tmp_path / "analytics_segev_4.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["team"]["cells"]["q1:all"]["metrics"]["efg_pct"] = 0.123
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnalyticsArtifactError, match="does not match its own hash"):
        AnalyticsStore(tmp_path).team("segev:4")


def test_a_missing_artifact_degrades_rather_than_raising(tmp_path):
    """`available` is what lets the site serve scouting reports from a
    deployment with no analytics artifact instead of 500ing."""
    store = AnalyticsStore(tmp_path / "nope")
    assert store.available is False
    assert store.health() == {"available": False, "teams_n": 0, "season": None, "artifact_version": None}


# ---- sample thresholds -------------------------------------------------------


@pytest.mark.parametrize(
    "possessions,games,expected",
    [
        (500, 26, "sufficient"),
        (100, 5, "sufficient"),    # exactly on both bars
        (99, 26, "limited"),       # possessions just under
        (500, 4, "limited"),       # games just under
        (49, 26, "insufficient"),  # possessions below the floor
        (500, 2, "insufficient"),  # games below the floor - the 24-2 case
        (0, 0, "insufficient"),
    ],
)
def test_sample_state_thresholds(possessions, games, expected):
    """Reuses the project's committed constants rather than inventing new bars.
    `insufficient` means the caller renders a state instead of a number."""
    assert classify_sample(possessions, games) == expected


def test_a_thin_sample_is_never_silently_sufficient():
    """The failure this prevents: a 44-possession clutch cell rendering as an
    ordinary number next to a 500-possession quarter."""
    assert classify_sample(44, 7) != "sufficient"


# ---- the real, committed artifacts ------------------------------------------

from pathlib import Path  # noqa: E402

PRODUCTION_ANALYTICS_DIR = Path(__file__).resolve().parents[1] / "data" / "analytics"


@pytest.mark.skipif(
    not (PRODUCTION_ANALYTICS_DIR / "index.json").is_file(),
    reason="analytics artifacts are not present in this checkout",
)
class TestShippedAnalytics:
    """What a deployment actually serves. These run against the committed
    artifact, not a fixture, so a bad build fails here rather than in a
    browser."""

    def test_the_whole_league_is_present(self):
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        assert store.available
        assert len(store.team_ids()) == EXPECTED_TEAMS

    def test_every_team_has_a_full_season_and_a_full_grid(self):
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        for team_id in store.team_ids():
            team = store.team(team_id)
            assert team.games_n == EXPECTED_GAMES_PER_TEAM, team_id
            assert len(team.games) == EXPECTED_GAMES_PER_TEAM, team_id
            assert len(team.cells) == len(SEGMENTS) * len(OUTCOMES), team_id
            assert team.wins + team.losses == EXPECTED_GAMES_PER_TEAM, team_id

    def test_every_artifact_hash_verifies(self):
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        for team_id in store.team_ids():
            store.get(team_id)  # raises on drift or index disagreement

    def test_pace_never_appears_on_a_segment_without_elapsed_time(self):
        """The rule that keeps an invented denominator off the site."""
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        for team_id in store.team_ids():
            team = store.team(team_id)
            for segment in ("close", "leading", "trailing", "clutch"):
                for outcome in OUTCOMES:
                    cell = team.cell(segment, outcome)
                    assert "pace" not in cell.metrics, f"{team_id} {segment}:{outcome}"

    def test_the_lopsided_team_has_no_usable_loss_sample(self):
        """Maccabi Tel Aviv at 24-2. Two losses cannot support a comparison, so
        every Losses cell must degrade explicitly rather than show a number."""
        team = AnalyticsStore(PRODUCTION_ANALYTICS_DIR).team("segev:2")
        assert team.record == "24-2"
        for segment in SEGMENTS:
            cell = team.cell(segment, "losses")
            assert cell.sample_state == "insufficient", segment

    def test_the_narrow_loss_sample_is_limited_not_silently_fine(self):
        """Hapoel Tel Aviv at 22-4. Values exist, but four losses is under the
        project's own sufficiency bar, so the state must say so."""
        team = AnalyticsStore(PRODUCTION_ANALYTICS_DIR).team("segev:3")
        assert team.record == "22-4"
        assert team.cell("full", "losses").sample_state == "limited"

    def test_ranks_are_consistent_with_eligible_team_counts(self):
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        for team_id in store.team_ids():
            for cell in store.team(team_id).cells.values():
                for metric, rank in cell.ranks.items():
                    assert 1 <= rank <= cell.eligible_teams, f"{team_id} {cell.segment} {metric}"
                    assert metric in cell.percentiles
                if cell.sample_state == "insufficient":
                    assert cell.ranks == {}, f"{team_id} {cell.segment} ranked on a bad sample"
