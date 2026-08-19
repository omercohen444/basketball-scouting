"""The league game log.

364 rows, not 182 — one per team per game. An analyst sorting by "best
offensive rating" needs both sides rankable, and a single matchup row forces an
arbitrary choice of whose numbers to show. What these tests hold is that the
perspective stays explicit, that the filters compose, and that nothing here
grows a league rank it has no business carrying.
"""

from __future__ import annotations

import pytest
from analytics_factories import make_bundle, write_synthetic_analytics
from fastapi.testclient import TestClient
from pack_factories import write_synthetic_packs
from product_factories import make_app

from basketball_scout.analytics.build import build_team_analytics
from basketball_scout.analytics.views import (
    GAMES_COLUMNS,
    GAMES_DYNAMICS,
    GamesFilters,
    league_game_rows,
    normalise_games_filters,
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


def _teams(n: int = 3, games: int = 4):
    return {
        f"segev:{i}": build_team_analytics(
            f"segev:{i}",
            [
                make_bundle(
                    game_id=f"segev:{i}0{g}", team_id=f"segev:{i}",
                    win=(g % 2 == 0), is_home=(g % 2 == 0),
                    game_date=f"2025-12-0{g + 1}T19:00:00",
                )
                for g in range(games)
            ],
            f"TEAM {i}", "2025-26",
        )
        for i in range(2, 2 + n)
    }


# ---- the view model ----------------------------------------------------------


def test_there_is_one_row_per_team_per_game():
    rows = league_game_rows(_teams(n=3, games=4))
    assert len(rows) == 12


def test_every_row_carries_its_own_teams_numbers():
    """A row belongs to the team named in it — that is the whole reason there
    are two rows per game rather than one."""
    rows = league_game_rows(_teams(n=2, games=2))
    for row in rows:
        assert row.team_id != row.opponent_id
        assert row.venue in ("vs", "at")
        assert row.result in ("W", "L")


def test_no_figure_in_the_game_log_carries_a_league_rank():
    """A single game is not ranked against a season. A superscript here would
    be the kind of number that looks authoritative and means nothing."""
    for row in league_game_rows(_teams()):
        for cell in row.cells.values():
            assert cell.rank is None
            assert cell.percentile is None
            assert cell.tint == 0


def test_filters_compose():
    teams = _teams(n=3, games=4)
    assert len(league_game_rows(teams, team="segev:2")) == 4
    assert len(league_game_rows(teams, venue="home")) == 6
    assert len(league_game_rows(teams, result="wins")) == 6
    assert len(league_game_rows(teams, team="segev:2", venue="home", result="wins")) == 2


def test_a_filter_that_matches_nothing_returns_nothing_rather_than_everything():
    """The empty state has to be reachable. Silently ignoring an impossible
    filter would show a full table under a heading that promises otherwise."""
    teams = {
        "segev:2": build_team_analytics(
            "segev:2",
            [make_bundle(team_id="segev:2", win=True, is_home=True) for _ in range(3)],
            "TEAM", "2025-26",
        )
    }
    assert league_game_rows(teams, result="losses") == []
    assert league_game_rows(teams, venue="away") == []


def test_sorting_runs_the_right_way_for_each_direction():
    teams = _teams(n=3, games=4)
    ortg = [r.get("offensive_rating").value for r in league_game_rows(teams, sort="offensive_rating")]
    assert ortg == sorted(ortg, reverse=True), "higher offensive rating first"

    drtg = [r.get("defensive_rating").value for r in league_game_rows(teams, sort="defensive_rating")]
    assert drtg == sorted(drtg), "lower defensive rating first"


def test_the_default_sort_is_most_recent_first():
    rows = league_game_rows(_teams(n=2, games=4))
    assert rows[0].game_date >= rows[-1].game_date


def test_an_edited_url_falls_back_rather_than_raising():
    """Every filter value arrives from a URL a user can type."""
    teams = _teams()
    filters = normalise_games_filters(teams, "nonsense", "segev:999", "sideways", "draws")
    assert filters == GamesFilters(sort="date", team="", venue="", result="")


def test_a_filter_link_keeps_the_rest_of_the_state():
    filters = GamesFilters(sort="net_rating", team="segev:4", venue="home", result="")
    link = filters.query(result="wins")
    for expected in ("sort=net_rating", "team=segev:4", "venue=home", "result=wins"):
        assert expected in link


def test_the_default_state_produces_a_clean_url():
    assert GamesFilters().query() == "/games"


# ---- the route ---------------------------------------------------------------


def test_the_games_route_renders(client):
    response = client.get("/games")
    assert response.status_code == 200
    assert "Game log" in response.text


@pytest.mark.parametrize("query", [
    "", "?team=segev:4", "?venue=home", "?venue=away", "?result=wins", "?result=losses",
    "?sort=net_rating", "?sort=lead_changes", "?team=segev:4&venue=home&result=wins",
])
def test_every_filter_combination_renders(client, query):
    assert client.get(f"/games{query}").status_code == 200


def test_an_unknown_filter_value_does_not_500(client):
    response = client.get("/games?team=nope&venue=sideways&result=draws&sort=nonsense")
    assert response.status_code == 200


def test_the_game_log_says_both_rows_belong_to_the_same_game(client):
    """Two rows per game only works if the page says so."""
    body = client.get("/games").text
    assert "Two rows per game" in body


def test_the_columns_offered_are_the_ones_the_rows_carry(client):
    """A header with no cell under it is worse than a missing column."""
    rows = league_game_rows(_teams())
    for key, _label in GAMES_COLUMNS:
        assert any(key in row.cells for row in rows), key
    for key, _label in GAMES_DYNAMICS:
        assert all(key in row.dynamics for row in rows), key
