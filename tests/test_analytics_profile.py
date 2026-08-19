"""The season identity profile — the half of the deterministic layer the
segment grid does not describe.

Shot zones, transition, turnover types, scoring sources and scoring rhythm all
ran over the full league long before anything rendered them. What these tests
protect is the wiring: that the fold sums rather than averages, that the
partition really partitions, that a defensive factor is computed the same way as
its offensive twin, and that a build which quietly lost half the facts fails
instead of shipping a league with no identity in it.
"""

from __future__ import annotations

import json

import pytest
from analytics_factories import make_bundle, make_league, make_possession

from basketball_scout.analytics.build import (
    EXPECTED_LEAGUE_TURNOVERS,
    IncompleteLeagueError,
    assert_profiles_complete,
    build_from_bundles,
    build_segment_cell,
    build_team_analytics,
    build_team_profile,
    opponent_metrics,
    stamp_league_ranks,
    write_all,
)
from basketball_scout.analytics.schema import (
    ANALYTICS_ARTIFACT_VERSION,
    OPPONENT_METRICS,
)
from basketball_scout.analytics.store import AnalyticsArtifactError, AnalyticsStore
from basketball_scout.stats.models import TeamGameComponents


def _box(**kw) -> TeamGameComponents:
    base = dict(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, orb=0, drb=0,
                ast=0, tov=0, pf=0, points=0)
    base.update(kw)
    return TeamGameComponents(**base)


def _team(games: int = 6, **bundle_kwargs):
    bundles = [make_bundle(**bundle_kwargs) for _ in range(games)]
    return build_team_analytics("segev:2", bundles, "TEST", "2025-26")


# ---- opponent (defensive) four factors --------------------------------------


def test_opponent_factors_use_the_same_formulas_as_the_offensive_half():
    """A four-factors table is only readable if both halves are computed the
    same way. These call straight into formulas.py, so oeFG% and eFG% are one
    statistic seen from opposite benches."""
    cf = _box(drb=30)
    ca = _box(fgm=40, fga=90, fg3m=10, fta=20, orb=10, tov=15)
    values = opponent_metrics(cf, ca)

    assert values["opp_efg_pct"] == pytest.approx((40 + 0.5 * 10) / 90, abs=1e-4)
    assert values["opp_tov_pct"] == pytest.approx(15 / (90 + 0.44 * 20 + 15), abs=1e-4)
    assert values["drb_pct"] == pytest.approx(30 / (30 + 10), abs=1e-4)
    assert values["opp_ft_rate"] == pytest.approx(20 / 90, abs=1e-4)


def test_opponent_turnover_rate_is_a_play_rate_not_a_possession_rate():
    """The two conventions differ by two to three points on every team, so
    mixing them would put TOV% and oTOV% in the same table without them being
    comparable. Both use the plays denominator."""
    ca = _box(fga=100, fta=25, tov=16)
    value = opponent_metrics(_box(drb=1), ca)["opp_tov_pct"]
    assert value == pytest.approx(16 / (100 + 0.44 * 25 + 16), abs=1e-4)
    # Not the possession-style denominator that produced the earlier mismatch.
    assert value != pytest.approx(16 / 100, abs=1e-3)


def test_an_opponent_factor_with_no_denominator_is_absent_not_zero():
    values = opponent_metrics(_box(), _box())
    for key in OPPONENT_METRICS:
        assert key not in values


def _rebounding_bundle(**kw):
    """A bundle with contested glass on both sides, so every one of the four
    defensive factors has a denominator."""
    mine = [make_possession(index=i, quarter=1, offense="home", margin=0, orb=1)
            for i in range(10)]
    theirs = [make_possession(index=i, quarter=1, offense="away", margin=0,
                              ended_by="defensive_rebound")
              for i in range(10)]
    return make_bundle(team_possessions=mine, opponent_possessions=theirs, **kw)


@pytest.mark.parametrize("segment", ["q1", "close", "trailing", "clutch"])
def test_every_segment_cell_carries_the_defensive_half(segment):
    cell = build_segment_cell(segment, "all", [_rebounding_bundle() for _ in range(6)])
    if not cell.games:
        pytest.skip("synthetic bundles never enter this segment")
    for key in OPPONENT_METRICS:
        assert key in cell.metrics, f"{segment} is missing {key}"


def test_a_defensive_factor_with_no_contested_glass_is_absent_from_the_cell():
    """Possessions that all end in a made basket leave no defensive rebound to
    win, so the rate is undefined and the key must simply not be there — a
    template cannot render what it was never handed."""
    cell = build_segment_cell("q1", "all", [make_bundle() for _ in range(6)])
    assert "drb_pct" not in cell.metrics
    assert "orb_pct" not in cell.metrics
    # The three that do not depend on rebounding are still present.
    assert "opp_efg_pct" in cell.metrics


def test_defensive_factors_rank_from_opposite_ends_of_their_columns():
    """Letting an opponent shoot well is bad; forcing turnovers is good. Both
    are 'better defence', and they rank from opposite ends of the values."""
    teams = {}
    for i, (oefg, otov) in enumerate([(0.60, 0.10), (0.50, 0.14), (0.45, 0.18)], start=2):
        team = _team()
        cell = team.cells["full:all"]
        cell.metrics["opp_efg_pct"] = oefg
        cell.metrics["opp_tov_pct"] = otov
        teams[f"segev:{i}"] = team
    stamp_league_ranks(teams)

    best, worst = teams["segev:4"].cells["full:all"], teams["segev:2"].cells["full:all"]
    assert best.ranks["opp_efg_pct"] == 1 and worst.ranks["opp_efg_pct"] == 3
    assert best.ranks["opp_tov_pct"] == 1 and worst.ranks["opp_tov_pct"] == 3


# ---- the fold ----------------------------------------------------------------


def test_the_profile_stores_counts_rather_than_averaged_rates():
    """Every stored field is a sum or a max, so a reader can recompute any
    displayed figure from the file and rounding happens in exactly one place."""
    profile = build_team_profile([make_bundle() for _ in range(4)])
    assert profile.shots.fga == 4 * 60
    assert profile.transition.fb_fga == 4 * 7
    assert profile.turnovers.total == 4 * 12
    assert profile.runs.runs_8_plus_against == 4


def test_the_longest_drought_is_a_season_maximum_not_a_total():
    profile = build_team_profile([make_bundle() for _ in range(4)])
    assert profile.runs.longest_fg_drought_s == 260.0
    assert profile.runs.longest_scoring_drought_s == 205.0


def test_zone_attempts_and_unclassified_reconcile_with_total_attempts():
    s = build_team_profile([make_bundle() for _ in range(3)]).shots
    assert sum(s.zone_attempts.values()) + s.unclassified == s.fga


def test_the_scoring_partition_is_exactly_the_points():
    """2PT, 3PT and FT are the one true partition. Everything else in the block
    overlaps and must never be drawn as part of a whole."""
    sc = build_team_profile([make_bundle() for _ in range(5)]).scoring
    assert sc.points_2pt + sc.points_3pt + sc.points_ft == sc.points


def test_transition_carries_both_directions():
    """The allowed side is the same events grouped by opponent, which is the
    half a scout actually acts on."""
    t = build_team_profile([make_bundle() for _ in range(4)]).transition
    assert t.fb_fga == 4 * 7
    assert t.fb_fga_allowed == 4 * 6
    assert t.opp_fga == 4 * 58


def test_forced_turnovers_are_the_opponents_own_taxonomy():
    tv = build_team_profile([make_bundle() for _ in range(3)]).turnovers
    assert tv.forced_total == 3 * 11
    assert set(tv.forced_by_type) == {"bad-pass", "ball-handling", "travelling"}


def test_comeback_denominators_count_opportunities_not_games_played():
    """A team that never trailed by ten cannot have failed to come back from
    it, so the denominator is the count of games where it happened."""
    cb = build_team_profile([make_bundle(win=(i < 3)) for i in range(6)]).comeback
    assert cb.games_leading_10_plus == 3
    assert cb.games_trailing_10_plus == 3
    assert cb.comeback_wins == 0
    assert cb.blown_leads == 0


def test_stability_withholds_cv_where_the_metric_crosses_zero():
    """CV is std over |mean|. Net rating sits near zero, so it inflates without
    bound and would mislabel a solid season as volatile."""
    stability = build_team_profile([make_bundle() for _ in range(6)]).stability
    assert stability["net_rating"].cv_applicable is False
    assert stability["net_rating"].cv is None
    assert stability["efg_pct"].cv_applicable is True
    assert stability["efg_pct"].games == 6


def test_game_dynamics_reach_the_game_rows():
    team = _team(games=4)
    assert all(g.lead_changes == 5 for g in team.games)
    assert all(g.largest_lead == 12 for g in team.games)


# ---- the guard ---------------------------------------------------------------


def test_a_team_with_no_facts_fails_the_profile_guard():
    """A silently empty profile renders as a team with no shots and no
    turnovers, which reads as a league anomaly rather than a stale build."""
    empty = build_team_analytics("segev:2", [], "TEST", "2025-26")
    with pytest.raises(IncompleteLeagueError, match="no shot attempts"):
        assert_profiles_complete({"segev:2": empty})


def test_unclassified_shots_fail_the_build():
    """Coordinate coverage is complete across the season, so anything else means
    the geometry stopped classifying."""
    team = _team()
    team.profile.shots.unclassified = 4
    with pytest.raises(IncompleteLeagueError, match="without a zone"):
        assert_profiles_complete({"segev:2": team})


def test_disagreeing_attempt_totals_fail_the_build():
    team = _team()
    team.profile.transition.fga += 1
    with pytest.raises(IncompleteLeagueError, match="attempt totals disagree"):
        assert_profiles_complete({"segev:2": team})


def test_the_real_league_turnover_total_is_pinned():
    """The raw cache also holds cup, playoff, preseason, second-division, youth
    and women's games — 8,041 turnovers in total against the league's 5,205.
    Loading the wrong population is the one silent way this goes wrong, and the
    number would still look entirely plausible."""
    assert EXPECTED_LEAGUE_TURNOVERS == 5205

    teams = {
        tid: build_team_analytics(tid, bundles, "TEST", "2025-26")
        for tid, bundles in make_league().items()
    }
    with pytest.raises(IncompleteLeagueError, match="wrong game population"):
        assert_profiles_complete(teams, real_league=True)

    # Silent when the caller never claimed to be the real league.
    assert_profiles_complete(teams)


# ---- versioning --------------------------------------------------------------


def test_the_artifact_declares_v2():
    artifacts, index = build_from_bundles(make_league(), {}, "2025-26")
    assert ANALYTICS_ARTIFACT_VERSION == "analytics-v2"
    assert index.artifact_version == "analytics-v2"
    assert all(a.artifact_version == "analytics-v2" for a in artifacts.values())


def test_an_older_artifact_is_refused_rather_than_half_loaded(tmp_path):
    """A v1 file has no profile block at all. Rendering a team page with the
    identity half missing would look like a gap in the league, not a stale
    build directory."""
    artifacts, index = build_from_bundles(make_league(), {}, "2025-26")
    write_all(artifacts, index, tmp_path)

    stale = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    stale["artifact_version"] = "analytics-v1"
    (tmp_path / "index.json").write_text(json.dumps(stale), encoding="utf-8")

    store = AnalyticsStore(tmp_path)
    assert store.available is False
    with pytest.raises(AnalyticsArtifactError, match="analytics-v1"):
        _ = store.index


def test_the_same_bundles_rebuild_to_the_same_hash():
    """Determinism is what makes the builder's --check mode meaningful."""
    first, _ = build_from_bundles(make_league(), {}, "2025-26")
    second, _ = build_from_bundles(make_league(), {}, "2025-26")
    assert ([a.content_hash for a in first.values()]
            == [a.content_hash for a in second.values()])


# ---- the real, committed artifacts -------------------------------------------

from pathlib import Path  # noqa: E402

PRODUCTION_ANALYTICS_DIR = Path(__file__).resolve().parents[1] / "data" / "analytics"


@pytest.mark.skipif(
    not (PRODUCTION_ANALYTICS_DIR / "index.json").is_file(),
    reason="analytics artifacts are not present in this checkout",
)
class TestShippedProfile:
    """What a deployment actually serves. A bad build fails here rather than in
    a browser."""

    def teams(self):
        store = AnalyticsStore(PRODUCTION_ANALYTICS_DIR)
        return [store.team(tid) for tid in store.team_ids()]

    def test_the_whole_league_carries_an_identity_profile(self):
        for team in self.teams():
            p = team.profile
            assert p.shots.fga > 1000, team.team_id
            assert p.turnovers.total > 0, team.team_id
            assert p.transition.fga == p.shots.fga, team.team_id
            assert p.stability, team.team_id

    def test_no_shot_in_the_league_is_left_without_a_zone(self):
        """Coordinate coverage is complete across all 182 games. This is the
        assumption the whole experimental shot profile rests on."""
        for team in self.teams():
            assert team.profile.shots.unclassified == 0, team.team_id
            s = team.profile.shots
            assert sum(s.zone_attempts.values()) == s.fga, team.team_id

    def test_the_league_turnover_total_is_the_regular_season_population(self):
        """5,205 over 182 games — not the 8,041 the raw cache holds once cup,
        playoff, preseason, second-division, youth and women's games are
        counted too."""
        total = sum(t.profile.turnovers.total for t in self.teams())
        assert total == EXPECTED_LEAGUE_TURNOVERS

    def test_turnovers_forced_equal_turnovers_committed_across_the_league(self):
        """Every turnover is one team's mistake and the other's takeaway, so the
        two league totals are the same number seen from both sides."""
        teams = self.teams()
        committed = sum(t.profile.turnovers.total for t in teams)
        forced = sum(t.profile.turnovers.forced_total for t in teams)
        assert committed == forced

    def test_every_team_has_the_full_defensive_four_factors_at_season_scope(self):
        for team in self.teams():
            cell = team.cell("full", "all")
            for key in OPPONENT_METRICS:
                assert key in cell.metrics, f"{team.team_id} missing {key}"
                assert key in cell.ranks, f"{team.team_id} unranked {key}"

    def test_the_scoring_partition_reconciles_for_every_team(self):
        for team in self.teams():
            sc = team.profile.scoring
            assert sc.points_2pt + sc.points_3pt + sc.points_ft == sc.points, team.team_id

    def test_net_rating_never_carries_a_coefficient_of_variation(self):
        for team in self.teams():
            entry = team.profile.stability["net_rating"]
            assert entry.cv_applicable is False
            assert entry.cv is None
