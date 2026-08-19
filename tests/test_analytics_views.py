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
    DISPLAY_LABEL_OVERRIDES,
    METRIC_META,
    OPPONENT_META,
    SEGMENT_DEFINITIONS,
    SEGMENT_LABELS,
    SampleView,
    display_label,
    format_value,
    largest_differences,
    metric_cell,
    opponent_factors,
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
    view = sample_view(SegmentCell(segment="q4", outcome="losses", games=3,
                                   possessions=40, sample_state="insufficient"))
    assert view.outcome == "losses"
    assert "loss" in (view.badge or "")


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


def test_opponent_factors_are_derived_from_the_opponent_box_score():
    team = _team()
    factors = opponent_factors(team.games)
    assert set(factors) == {"opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate"}
    assert all(0 <= v <= 2 for v in factors.values())


def test_opponent_factors_have_the_right_directions():
    """Letting the opponent shoot well is bad; forcing turnovers is good. A
    naive copy of the offensive directions would colour half of these
    backwards."""
    assert OPPONENT_META["opp_efg_pct"].direction == "lower_is_better"
    assert OPPONENT_META["opp_ft_rate"].direction == "lower_is_better"
    assert OPPONENT_META["opp_tov_pct"].direction == "higher_is_better"
    assert OPPONENT_META["drb_pct"].direction == "higher_is_better"


def test_no_games_yields_no_factors_rather_than_a_division_error():
    assert opponent_factors([]) == {}


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
