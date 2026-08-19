"""Two teams, aligned.

The rank beside every value is what makes the page readable: a three-point gap
in offensive rating means one thing at the top of the league and another in the
middle, and only the rank says which. The other rule these tests hold is that a
style metric never gets a winner — neither end of pace is better, so declaring
one is a claim the data does not support.
"""

from __future__ import annotations

import pytest
from analytics_factories import make_bundle, write_synthetic_analytics
from fastapi.testclient import TestClient
from pack_factories import write_synthetic_packs
from product_factories import make_app

from basketball_scout.analytics.build import build_team_analytics
from basketball_scout.analytics.views import (
    compare_groups,
    compare_splits,
    default_compare_pair,
    resolve_compare_pair,
)
from basketball_scout.persistence.memory import InMemoryReportRepository


@pytest.fixture
def client(tmp_path) -> TestClient:
    write_synthetic_packs(tmp_path)
    analytics = tmp_path / "analytics"
    write_synthetic_analytics(analytics)
    return TestClient(
        make_app(tmp_path, repository=InMemoryReportRepository(), analytics_dir=analytics)
    )


def _team(team_id: str, *, wins: int = 13, games: int = 26, **metrics):
    bundles = [make_bundle(team_id=team_id, win=(i < wins)) for i in range(games)]
    team = build_team_analytics(team_id, bundles, f"TEAM {team_id[-1]}", "2025-26")
    team.cells["full:all"].metrics.update(metrics)
    return team


def _pair(**a_metrics):
    return {
        "segev:2": _team("segev:2", **a_metrics),
        "segev:3": _team("segev:3"),
    }


# ---- the comparison ----------------------------------------------------------


def test_a_metric_with_a_better_end_gets_a_leader():
    teams = {
        "segev:2": _team("segev:2", offensive_rating=120.0),
        "segev:3": _team("segev:3", offensive_rating=110.0),
    }
    groups = compare_groups(teams["segev:2"], teams["segev:3"])
    row = next(r for g in groups for r in g.rows if r.key == "offensive_rating")
    assert row.leader == "a"


def test_a_lower_is_better_metric_leads_from_the_other_end():
    teams = {
        "segev:2": _team("segev:2", defensive_rating=112.0),
        "segev:3": _team("segev:3", defensive_rating=104.0),
    }
    groups = compare_groups(teams["segev:2"], teams["segev:3"])
    row = next(r for g in groups for r in g.rows if r.key == "defensive_rating")
    assert row.leader == "b", "conceding fewer points is the better end"


def test_a_style_metric_never_gets_a_leader():
    """Neither end of pace is better. Declaring a winner would be a claim the
    data does not support, so no side is marked."""
    teams = {
        "segev:2": _team("segev:2", pace=82.0),
        "segev:3": _team("segev:3", pace=68.0),
    }
    groups = compare_groups(teams["segev:2"], teams["segev:3"])
    for key in ("pace", "ft_rate", "fg3a_rate"):
        row = next((r for g in groups for r in g.rows if r.key == key), None)
        if row is not None:
            assert row.is_style is True
            assert row.leader is None, key


def test_two_equal_values_produce_no_leader():
    teams = {
        "segev:2": _team("segev:2", offensive_rating=115.0),
        "segev:3": _team("segev:3", offensive_rating=115.0),
    }
    groups = compare_groups(teams["segev:2"], teams["segev:3"])
    row = next(r for g in groups for r in g.rows if r.key == "offensive_rating")
    assert row.leader is None


def test_both_dots_land_inside_the_track():
    """Near-identical values must not render as one dot at each extreme, and
    neither may run off the end of the bar."""
    teams = {
        "segev:2": _team("segev:2", efg_pct=0.541),
        "segev:3": _team("segev:3", efg_pct=0.540),
    }
    groups = compare_groups(teams["segev:2"], teams["segev:3"])
    row = next(r for g in groups for r in g.rows if r.key == "efg_pct")
    for position in (row.position(row.a), row.position(row.b)):
        assert 0.0 <= position <= 100.0
        assert position not in (0.0, 100.0), "a padded axis keeps both dots off the ends"


def test_the_comparison_covers_both_halves_of_the_four_factors():
    groups = {g.label: g for g in compare_groups(*_pair().values())}
    assert "Offensive four factors" in groups
    assert "Defensive four factors" in groups
    assert {r.key for r in groups["Defensive four factors"].rows} == {
        "opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate"
    }


def test_the_season_profile_is_compared_too():
    groups = {g.label for g in compare_groups(*_pair().values())}
    assert "Transition" in groups
    assert "Scoring identity" in groups


# ---- the win/loss table ------------------------------------------------------


def test_the_split_table_is_withheld_when_either_team_is_below_the_floor():
    """A comparison where one half rests on a two-game sample is not a
    comparison, so the whole table goes rather than half of it."""
    healthy = _team("segev:2", wins=13)
    degenerate = _team("segev:3", wins=25)  # one loss
    assert degenerate.cell("full", "losses").sample_state == "insufficient"
    assert compare_splits(healthy, degenerate) == []
    assert compare_splits(degenerate, healthy) == []


def test_the_split_table_appears_when_both_teams_clear_the_floor():
    rows = compare_splits(_team("segev:2", wins=13), _team("segev:3", wins=13))
    assert rows
    assert all(r.label for r in rows)


def test_the_split_table_excludes_the_tautological_metrics():
    """A team outscoring its opponent in the games it wins is true by
    definition, so rating metrics are not a difference worth showing."""
    labels = {r.label for r in compare_splits(_team("segev:2"), _team("segev:3"))}
    assert "Net Rating" not in labels
    assert "Offensive Rating" not in labels


# ---- pair resolution ---------------------------------------------------------


def test_the_default_pair_is_the_top_two_by_net_rating():
    teams = {
        "segev:2": _team("segev:2", net_rating=2.0),
        "segev:3": _team("segev:3", net_rating=18.0),
        "segev:4": _team("segev:4", net_rating=9.0),
    }
    assert default_compare_pair(teams) == ("segev:3", "segev:4")


def test_an_unknown_id_falls_back_rather_than_raising():
    teams = _pair()
    left, right = resolve_compare_pair(teams, "segev:999", "")
    assert left in teams and right in teams


def test_comparing_a_team_with_itself_is_nudged_apart():
    """It renders, but it says nothing."""
    teams = {f"segev:{i}": _team(f"segev:{i}") for i in range(2, 5)}
    left, right = resolve_compare_pair(teams, "segev:2", "segev:2")
    assert left != right


# ---- the route ---------------------------------------------------------------


def test_the_compare_route_opens_on_a_real_pair(client):
    """An empty pair of selectors is a worse first impression than a default."""
    response = client.get("/compare")
    assert response.status_code == 200
    assert "against" in response.text


@pytest.mark.parametrize("query", [
    "", "?a=segev:4&b=segev:2", "?a=segev:2&b=segev:2",
    "?a=nonsense&b=nonsense", "?a=segev:4",
])
def test_every_pair_renders(client, query):
    assert client.get(f"/compare{query}").status_code == 200


def test_the_page_is_a_shareable_link(client):
    """State lives entirely in the query string."""
    body = client.get("/compare?a=segev:4&b=segev:2").text
    assert "/compare?a=" in body  # the swap link round-trips the pair
