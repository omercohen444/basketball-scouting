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
from analytics_factories import make_bundle, make_facts, make_league, make_possession

from basketball_scout.analytics import views

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


# ---- the view layer ----------------------------------------------------------


def _league(n: int = 4):
    """A small league whose teams differ, so ranking has something to do."""
    teams = {}
    for i in range(2, 2 + n):
        bundles = [
            make_bundle(
                team_id=f"segev:{i}", win=(g % 2 == 0),
                facts=make_facts(fb_fga=4 + i, fb_fgm=2 + i, fb_points=5 + 2 * i),
            )
            for g in range(6)
        ]
        teams[f"segev:{i}"] = build_team_analytics(f"segev:{i}", bundles, f"TEAM {i}", "2025-26")
    return teams


def test_a_rate_with_no_denominator_is_absent_rather_than_zero():
    team = build_team_analytics("segev:2", [], "TEST", "2025-26")
    values = views.profile_values(team)
    assert values["fb_rate"] is None
    assert views.profile_cell("fb_rate", values) is None


def test_the_transition_attempt_rate_is_style_and_the_rate_allowed_is_not():
    """Running more is not being better. Conceding transition is."""
    teams = _league()
    cells = {c.key: c for c in views.transition_view(teams["segev:2"], views.profile_ranks(teams)["segev:2"])}
    assert cells["fb_rate"].is_style is True
    assert cells["fb_rate"].tint == 0
    assert cells["fb_rate_allowed"].is_style is False


def test_transition_offers_no_half_court_counterpart():
    """A false provider flag means only that the provider did not call the play
    a fast break. There is no complement to render, so none exists."""
    view = views.transition_view(_league()["segev:2"])
    keys = {c.key for c in view}
    assert not any("half" in k or "court" in k or "set" in k for k in keys)


def test_scoring_sources_separate_the_partition_from_the_context():
    sources = views.scoring_sources_view(_league()["segev:2"])
    assert {c.key for c in sources.partition} == {"share_2pt", "share_3pt", "share_ft"}
    assert sources.partition_reconciles is True
    # Contextual sources overlap each other and must never be summed.
    assert {c.key for c in sources.context} >= {"pot_pg", "second_chance_pg", "fb_points_pg"}
    # The two lists are disjoint, so nothing can be drawn as both.
    assert not ({c.key for c in sources.partition} & {c.key for c in sources.context})


def test_every_partition_share_is_style_so_no_scoring_split_gets_coloured():
    """A team scoring more from three is not better at scoring."""
    for cell in views.scoring_sources_view(_league()["segev:2"]).partition:
        assert cell.is_style is True
        assert cell.tint == 0


def test_shot_profile_is_never_ranked_and_never_tinted():
    """Twenty labelled shots from one arena is enough to say where a team
    shoots from. It is not enough to place them in a league order."""
    profile = views.shot_profile_view(_league()["segev:2"])
    assert profile.is_experimental is True
    for zone in profile.zones:
        assert zone.share.rank is None
        assert zone.share.tint == 0
        assert zone.share.is_style is True
        assert zone.attempts > 0  # the count always accompanies the efficiency
    assert profile.rim.tint == 0


def test_zone_efficiency_is_points_over_two_over_attempts():
    """eFG% reduces to that in every zone: inside the arc a make is worth two,
    beyond it three, and (FGM + 0.5*3PM)/FGA gives the same answer for both."""
    profile = views.shot_profile_view(_league()["segev:2"])
    lane = next(z for z in profile.zones if z.key == "lane_2pt")
    # make_facts: 26 lane attempts, 30 lane points per game.
    assert lane.efg_value == pytest.approx((30 / 2) / 26, abs=1e-4)


def test_the_four_turnover_buckets_partition_the_ten_provider_types():
    view = views.turnover_view(_league()["segev:2"])
    assert sum(b.count for b in view.buckets) == view.total
    assert sum(b.share for b in view.buckets) == pytest.approx(1.0, abs=1e-9)


def test_the_raw_provider_categories_survive_into_the_detail():
    """No scouting taxonomy is invented on top of a clean provider field."""
    view = views.turnover_view(_league()["segev:2"])
    by_name = {row.name: row for row in view.detail}
    assert by_name["bad-pass"].committed > 0
    assert view.detail == sorted(view.detail, key=lambda r: (-r.committed, r.name))


def test_a_category_the_team_only_ever_forced_still_gets_a_row():
    """The two taxonomies are independent — a type this team never committed
    but repeatedly took away is exactly the kind of thing worth seeing."""
    team = build_team_analytics(
        "segev:2",
        [make_bundle(facts=make_facts(
            turnovers_by_type={"bad-pass": 5},
            forced_by_type={"bad-pass": 3, "travelling": 4},
        )) for _ in range(4)],
        "TEST", "2025-26",
    )
    by_name = {row.name: row for row in views.turnover_view(team).detail}
    assert by_name["travelling"].committed == 0
    assert by_name["travelling"].forced == 16


def test_consistency_says_nothing_at_all_where_cv_does_not_apply():
    """Net rating sits near zero, so std over |mean| inflates without bound.
    There is no consistency claim to make there and none is made."""
    team = _league()["segev:2"]
    cells = {c.key: c for c in views.consistency_view(team, ("net_rating", "efg_pct", "pace"))}
    assert cells["net_rating"].level is None
    assert cells["net_rating"].cv is None
    assert cells["efg_pct"].level is not None
    assert cells["pace"].level is not None


def test_consistency_always_shows_the_range_even_without_a_level():
    """A metric with no valid coefficient of variation still has a real
    game-to-game spread, and that is worth seeing."""
    cells = {c.key: c for c in views.consistency_view(_league()["segev:2"], ("net_rating",))}
    assert cells["net_rating"].spread != "—"


def test_comeback_reads_as_counts_not_a_percentage():
    """Four from five and nine from twenty are not the same claim, and a bare
    rate would present them as though they were."""
    view = views.comeback_view(_league()["segev:2"])
    assert " of " in view.comeback_display
    assert "%" not in view.comeback_display + view.blown_display


def test_profile_ranks_order_lower_is_better_metrics_from_the_other_end():
    teams = _league(n=4)
    ranks = views.profile_ranks(teams)
    values = {tid: views.profile_values(t)["fb_points_pg"] for tid, t in teams.items()}
    best = max(values, key=lambda tid: values[tid])
    assert ranks[best]["fb_points_pg"][0] == 1


def test_both_halves_of_the_four_factors_come_from_one_cell():
    """They used to be computed two different ways — a two to three point gap
    on the opponent turnover rate, in the same table."""
    team = build_team_analytics(
        "segev:2", [_rebounding_bundle(team_id="segev:2") for _ in range(6)], "T", "2025-26"
    )
    factors = views.team_four_factors(team)
    assert [c.key for c in factors.offense] == ["efg_pct", "tov_pct", "orb_pct", "ft_rate"]
    assert [c.key for c in factors.defense] == ["opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate"]


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
