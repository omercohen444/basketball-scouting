"""The presentation boundary.

These tests are mostly about what the view models *refuse* to do. A template
gets whatever it is handed, so the rules that keep the site honest — style
metrics are never coloured, thin samples always announce themselves, a
tautological metric is never "the reason" a team wins — have to hold here or
they do not hold at all.
"""

from __future__ import annotations

import pytest
from analytics_factories import make_bundle

from basketball_scout.analytics.build import build_team_analytics
from basketball_scout.analytics.schema import SegmentCell
from basketball_scout.analytics.views import (
    CELL_META,
    DISPLAY_LABEL_OVERRIDES,
    METRIC_FAMILIES,
    METRIC_META,
    OPPONENT_META,
    SEGMENT_DEFINITIONS,
    SEGMENT_LABELS,
    ExplorerRow,
    SampleView,
    display_label,
    explorer_columns,
    explorer_rows,
    format_value,
    largest_differences,
    metric_cell,
    sample_view,
    split_rows,
)


def _team(wins: int = 13, games: int = 26):
    bundles = [make_bundle(win=(i < wins)) for i in range(games)]
    return build_team_analytics("segev:4", bundles, "TEST", "2025-26")


def _cell(**metrics) -> SegmentCell:
    return SegmentCell(
        segment="full", outcome="all", games=26, possessions=2000,
        sample_state="sufficient", metrics=metrics,
        ranks={k: 2 for k in metrics}, percentiles={k: 92.3 for k in metrics},
        eligible_teams=14,
    )


# ---- style metrics are never coloured ---------------------------------------


@pytest.mark.parametrize("key", ["pace", "ft_rate", "fg3a_rate"])
def test_style_metrics_carry_no_direction_and_no_tint(key):
    """A fast team is not a good team, and a high free-throw rate is a style,
    not a virtue. These rank like anything else but must never be shaded — the
    tint is forced to zero rather than left to the template's discretion."""
    cell = metric_cell(key, _cell(**{key: 0.34 if key != "pace" else 75.2}))
    assert cell is not None
    assert cell.is_style
    assert cell.tint == 0, "a style metric ranked 2nd of 14 still gets no tint"
    assert cell.rank == 2, "...but it is still ranked, because the rank is a fact"


@pytest.mark.parametrize("key", ["offensive_rating", "efg_pct", "orb_pct"])
def test_directional_metrics_do_tint(key):
    cell = metric_cell(key, _cell(**{key: 0.5}))
    assert cell is not None and not cell.is_style
    assert cell.tint > 0


def test_a_metric_absent_from_the_cell_yields_nothing_to_render():
    """Not a zero, not a dash-with-a-rank — nothing. This is how Pace stays off
    segments that have no elapsed time."""
    assert metric_cell("pace", _cell(efg_pct=0.55)) is None


def test_tint_is_zero_without_a_percentile():
    cell = metric_cell("efg_pct", SegmentCell(segment="q1", outcome="all", metrics={"efg_pct": 0.5}))
    assert cell is not None and cell.tint == 0


# ---- sample state -----------------------------------------------------------


def test_insufficient_loss_sample_says_so_in_the_users_words():
    """Maccabi Tel Aviv at 24-2. The product must say what is wrong, not
    silently render two games as a comparison."""
    view = SampleView(state="insufficient", games=2, possessions=157, outcome="losses")
    assert view.badge == "Insufficient sample — 2 losses"
    assert not view.is_usable


def test_limited_sample_still_shows_but_announces_itself():
    """Hapoel Tel Aviv at 22-4: values exist and are shown, but four losses is
    under the project's own sufficiency bar."""
    view = SampleView(state="limited", games=4, possessions=300, outcome="losses")
    assert view.badge == "Limited sample — 4 losses"
    assert view.is_usable


def test_a_single_game_is_not_pluralised():
    assert SampleView(state="insufficient", games=1, possessions=9, outcome="losses").badge \
        == "Insufficient sample — 1 loss"


def test_a_healthy_sample_gets_no_badge():
    assert SampleView(state="sufficient", games=26, possessions=2000).badge is None


def test_sample_view_carries_the_outcome_through_from_the_cell():
    view = sample_view(SegmentCell(segment="q4", outcome="losses", games=2,
                                   possessions=40, sample_state="insufficient"))
    assert view.outcome == "losses"
    assert "loss" in (view.badge or "")


def test_the_badge_names_whichever_count_actually_binds():
    """Clutch spans plenty of games but few possessions. Saying "14 games" there
    would give a reason that is not the reason the cell is thin."""
    thin_possessions = SampleView(state="limited", games=14, possessions=61)
    assert thin_possessions.badge == "Limited sample — 61 possessions"

    thin_games = SampleView(state="limited", games=4, possessions=800, outcome="losses")
    assert thin_games.badge == "Limited sample — 4 losses"


def test_the_insufficient_badge_uses_the_floor_not_the_warning_line():
    """80 possessions is under the "limited" line but over the floor, so a cell
    that is insufficient at 80 must be insufficient on games, and say so."""
    view = SampleView(state="insufficient", games=2, possessions=80, outcome="losses")
    assert view.badge == "Insufficient sample — 2 losses"


# ---- the mislabelled legacy metric ------------------------------------------


def test_the_legacy_trailing_label_is_corrected_on_the_way_out():
    """The shipped bin starts at a margin of -5, so "Trailing 6+" overstates it.
    Correcting the bin would move the value and invalidate every stored report,
    so the data stays and the label is fixed at the boundary."""
    corrected = display_label("EV.behind_6_plus.efg_pct", "Effective FG% When Trailing 6+")
    assert corrected == "Effective FG% When Trailing 5+"
    assert "6+" not in corrected


def test_an_id_without_an_override_keeps_its_own_label():
    assert display_label("EV.season.efg_pct", "Effective FG%") == "Effective FG%"


def test_no_override_silently_renames_something_unrelated():
    assert set(DISPLAY_LABEL_OVERRIDES) == {"EV.behind_6_plus.efg_pct"}


# ---- defensive four factors -------------------------------------------------


def test_opponent_factors_have_the_right_directions():
    """Letting the opponent shoot well is bad; forcing turnovers is good. A
    naive copy of the offensive directions would colour half of these
    backwards."""
    assert OPPONENT_META["opp_efg_pct"].direction == "lower_is_better"
    assert OPPONENT_META["opp_ft_rate"].direction == "lower_is_better"
    assert OPPONENT_META["opp_tov_pct"].direction == "higher_is_better"
    assert OPPONENT_META["drb_pct"].direction == "higher_is_better"


def test_one_lookup_resolves_both_halves_of_a_cell():
    """The builder stores the offensive ten and the defensive four in one
    metrics dict, so metric_cell has to find either without being told which."""
    assert set(CELL_META) == set(METRIC_META) | set(OPPONENT_META)
    for key in OPPONENT_META:
        assert CELL_META[key] is OPPONENT_META[key]


# ---- win / loss -------------------------------------------------------------


def test_largest_differences_never_leads_with_net_rating():
    """The defect this exists for: ranked naively, Net Rating is the biggest
    win/loss difference for nearly every team — a team outscores its opponent
    in the games it wins. True, and useless as an insight."""
    rows = split_rows(_team())
    top = largest_differences(rows)
    assert top, "there should be some actionable difference"
    assert all(not r.is_outcome_context for r in top)
    assert "net_rating" not in {r.meta.key for r in top}


def test_outcome_context_metrics_are_kept_but_flagged():
    """They are useful context — just never the headline reason."""
    rows = split_rows(_team())
    flagged = {r.meta.key for r in rows if r.is_outcome_context}
    assert flagged == {"offensive_rating", "defensive_rating", "net_rating", "pace"}
    assert rows[0].is_outcome_context is False, "actionable metrics sort first"


def test_a_style_metric_has_no_favoured_side():
    """Pace differing between wins and losses is a fact, not a virtue, so the
    view refuses to say which side is better."""
    rows = {r.meta.key: r for r in split_rows(_team())}
    assert rows["pace"].favours_wins is None


def test_an_insufficient_side_is_withheld_rather_than_shown():
    """A 24-2 team has no usable loss sample, so the losses column must come
    back empty instead of quietly reporting two games."""
    team = _team(wins=24, games=26)
    rows = {r.meta.key: r for r in split_rows(team)}
    assert rows["efg_pct"].losses is None
    assert rows["efg_pct"].delta is None
    assert rows["efg_pct"].delta_display == "—"


def test_delta_display_scales_percentages_into_points():
    rows = {r.meta.key: r for r in split_rows(_team())}
    row = rows["efg_pct"]
    if row.delta is not None:
        assert "." in row.delta_display and "%" not in row.delta_display


# ---- formatting -------------------------------------------------------------


def test_percentages_render_as_percentages_and_ratings_do_not():
    assert format_value(METRIC_META["efg_pct"], 0.537) == "53.7%"
    assert format_value(METRIC_META["offensive_rating"], 118.36) == "118.4"
    assert format_value(METRIC_META["ft_rate"], 0.338) == "0.34"


def test_net_rating_always_carries_its_sign():
    assert format_value(METRIC_META["net_rating"], 9.4) == "+9.4"
    assert format_value(METRIC_META["net_rating"], -14.1) == "-14.1"


def test_a_missing_value_renders_as_a_dash():
    assert format_value(METRIC_META["efg_pct"], None) == "—"


def test_every_segment_has_a_label_and_a_definition():
    """A situational number is meaningless without its definition, so the UI
    must always be able to state one."""
    from basketball_scout.analytics.schema import SEGMENTS

    for segment in SEGMENTS:
        assert segment in SEGMENT_LABELS, segment
        assert segment in SEGMENT_DEFINITIONS, segment
        assert SEGMENT_DEFINITIONS[segment].endswith(".")


# ---- the explorer -----------------------------------------------------------


def _league(**per_team):
    """A synthetic league keyed by team id, each entry a games-per-team count."""
    return {
        tid: build_team_analytics(tid, [make_bundle(win=(i < wins)) for i in range(games)],
                                  f"TEAM {tid}", "2025-26")
        for tid, (wins, games) in per_team.items()
    }


def test_the_explorer_ranks_every_team_it_can_rank():
    teams = _league(**{f"segev:{i}": (13, 26) for i in range(2, 6)})
    rows = explorer_rows(teams, segment="q1", outcome="all", family="efficiency")
    assert len(rows) == 4
    assert [r.rank for r in rows] == [1, 2, 3, 4]


def test_a_cell_below_the_floor_is_listed_but_left_unranked():
    """Absence is information. A team with too little of a segment still
    appears — it just carries a state instead of a position."""
    teams = _league(**{"segev:2": (26, 26), "segev:3": (13, 26)})
    rows = explorer_rows(teams, segment="full", outcome="losses", family="efficiency")
    undefeated = next(r for r in rows if r.team_id == "segev:2")
    assert undefeated.sample.state == "insufficient"
    assert undefeated.rank == 0
    assert not undefeated.sample.is_usable


def test_vs_season_is_measured_against_the_same_outcome_not_the_whole_season():
    """The column would stop meaning anything otherwise: in Losses/Q4 the
    baseline must be that team's full-game play *in losses*.

    Wins and losses are given genuinely different scorelines here, so the two
    candidate baselines cannot coincide and the assertion below is real.
    """
    bundles = [
        make_bundle(win=True, score_for=95, score_against=75) for _ in range(13)
    ] + [
        make_bundle(win=False, score_for=70, score_against=90) for _ in range(13)
    ]
    teams = {"segev:2": build_team_analytics("segev:2", bundles, "TEST", "2025-26")}
    team = teams["segev:2"]

    segment_value = team.cell("q1", "losses").metrics["net_rating"]
    loss_baseline = team.cell("full", "losses").metrics["net_rating"]
    season_baseline = team.cell("full", "all").metrics["net_rating"]
    assert loss_baseline != pytest.approx(season_baseline), "fixture is not discriminating"

    row = explorer_rows(teams, segment="q1", outcome="losses", family="efficiency")[0]
    assert row.vs_season == pytest.approx(segment_value - loss_baseline)
    assert row.vs_season != pytest.approx(segment_value - season_baseline)


def test_the_full_game_row_has_nothing_to_compare_itself_against():
    teams = _league(**{"segev:2": (13, 26)})
    row = explorer_rows(teams, segment="full", outcome="all", family="efficiency")[0]
    assert row.vs_season is None
    assert row.vs_season_display == "—"


def _row(*, primary_key: str, vs_season: float) -> ExplorerRow:
    return ExplorerRow(rank=1, team_id="segev:2", team_name="T", cells={},
                       sample=SampleView(state="sufficient", games=26, possessions=2000),
                       vs_season=vs_season, vs_season_display="", primary_key=primary_key)


def test_a_fall_in_turnover_rate_reads_as_better_not_worse():
    """Direction is read from the metric, so the arrow on TOV% points the other
    way from the arrow on eFG%."""
    good = _row(primary_key="tov_pct", vs_season=-0.03)
    bad = _row(primary_key="tov_pct", vs_season=+0.03)
    assert good.vs_season_direction == 1
    assert bad.vs_season_direction == -1


def test_a_style_metric_never_reads_as_better_or_worse():
    assert _row(primary_key="pace", vs_season=+8.0).vs_season_direction == 0


def test_every_family_names_columns_that_exist():
    for family in METRIC_FAMILIES:
        columns = explorer_columns(family)
        assert columns
        for key, label in columns:
            assert key in METRIC_META, key
            assert label
