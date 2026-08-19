"""The methodology page, and the guards that keep it true.

Two failures are possible here and both matter. A metric can ship without a
definition, which these tests catch by coverage. Or a definition can read
plausibly and describe different arithmetic from the code — which is exactly
what happened once already, when the opponent turnover rate was documented on
one denominator and computed on another, two to three points apart in the same
table.

The second class is caught by recomputing every documented formula from raw
components and asserting it equals what the builder produced. A formula that
does not describe the executing code path fails, not just a missing one.
"""

from __future__ import annotations

import pytest
from analytics_factories import make_bundle, write_synthetic_analytics
from fastapi.testclient import TestClient
from pack_factories import write_synthetic_packs
from product_factories import make_app

from basketball_scout.agents.evidence_pack import UNAVAILABLE
from basketball_scout.analytics import methodology
from basketball_scout.analytics.build import (
    EXPECTED_LEAGUE_TURNOVERS,
    build_team_analytics,
    opponent_metrics,
)
from basketball_scout.analytics.schema import CELL_METRICS
from basketball_scout.analytics.views import (
    CELL_META,
    GAMES_COLUMNS,
    PROFILE_META,
    SHOT_META,
    profile_values,
)
from basketball_scout.persistence.memory import InMemoryReportRepository
from basketball_scout.stats import formulas
from basketball_scout.stats.models import TeamGameComponents


@pytest.fixture
def client(tmp_path) -> TestClient:
    write_synthetic_packs(tmp_path)
    analytics = tmp_path / "analytics"
    write_synthetic_analytics(analytics)
    return TestClient(
        make_app(tmp_path, repository=InMemoryReportRepository(), analytics_dir=analytics)
    )


def _box(**kw) -> TeamGameComponents:
    base = dict(fgm=38, fga=84, fg3m=11, fg3a=29, ftm=17, fta=23, orb=12, drb=27,
                ast=21, tov=14, pf=19, points=104)
    base.update(kw)
    return TeamGameComponents(**base)


# ---- coverage ----------------------------------------------------------------


def test_every_metric_the_site_can_render_has_a_definition():
    """A metric added to the view layer without an entry here would ship
    undocumented, and nobody would notice until a reader asked."""
    rendered = set(CELL_META) | set(PROFILE_META) | set(SHOT_META)
    missing = rendered - methodology.documented_keys()
    assert not missing, f"undocumented metrics: {sorted(missing)}"


def test_every_definition_describes_something_the_site_renders():
    """The other direction: a stale entry for a metric that no longer exists
    is a promise the page cannot keep."""
    rendered = set(CELL_META) | set(PROFILE_META) | set(SHOT_META)
    orphaned = methodology.documented_keys() - rendered
    assert not orphaned, f"documented but never rendered: {sorted(orphaned)}"


def test_every_definition_is_actually_filled_in():
    for key, entry in methodology.GLOSSARY.items():
        assert entry.formula.strip(), key
        assert entry.reading.strip(), key
        assert entry.direction, key


def test_every_glossary_group_names_metrics_that_exist():
    for _label, _blurb, keys in methodology.GLOSSARY_GROUPS:
        for key in keys:
            assert key in methodology.GLOSSARY, key


def test_every_documented_metric_appears_in_a_group():
    """An entry that no group renders is documented and unreachable."""
    grouped = {key for _l, _b, keys in methodology.GLOSSARY_GROUPS for key in keys}
    assert methodology.documented_keys() - grouped == set()


def test_the_game_log_columns_are_documented_too():
    for key, _label in GAMES_COLUMNS:
        assert key in methodology.GLOSSARY, key


# ---- the formulas describe the executing code --------------------------------
#
# These evaluate `entry.expr` — the exact expression the page renders its
# formula from — against real components, and compare the result to what the
# code computes. A definition that reads plausibly but describes different
# arithmetic fails here, which is the failure that actually happened once:
# the opponent turnover rate was documented on a possession denominator while
# the code used a plays denominator, two to three points apart in one table.


def _namespace() -> dict[str, float]:
    cf = _box()
    ca = _box(fgm=34, fga=80, fg3m=9, fg3a=25, ftm=15, fta=19,
              orb=10, drb=30, ast=18, tov=17, points=95)
    team_poss = formulas.estimate_possessions(cf, ca)
    opp_poss = formulas.estimate_possessions(ca, cf)
    return {
        "points": cf.points, "possessions": team_poss,
        "opp_points": ca.points, "opp_possessions": opp_poss,
        "team_poss": team_poss, "opp_poss": opp_poss, "minutes": 40.0,
        "off_rtg": formulas.offensive_rating(cf.points, team_poss),
        "def_rtg": formulas.defensive_rating(ca.points, opp_poss),
        "fgm": cf.fgm, "fga": cf.fga, "fg3m": cf.fg3m, "fg3a": cf.fg3a,
        "ftm": cf.ftm, "fta": cf.fta, "orb": cf.orb, "drb": cf.drb,
        "ast": cf.ast, "tov": cf.tov,
        "opp_fgm": ca.fgm, "opp_fga": ca.fga, "opp_fg3m": ca.fg3m,
        "opp_fta": ca.fta, "opp_orb": ca.orb, "opp_drb": ca.drb, "opp_tov": ca.tov,
        "_cf": cf, "_ca": ca,
    }


def _documented(key: str, namespace: dict) -> float:
    """Evaluate the formula exactly as the page states it."""
    return eval(methodology.GLOSSARY[key].expr, {"__builtins__": {}}, namespace)  # noqa: S307


CELL_KEYS = tuple(k for k in CELL_METRICS if k != "pace")


@pytest.mark.parametrize("key", CELL_KEYS)
def test_the_displayed_formula_matches_what_the_code_computes(key):
    ns = _namespace()
    cf, ca = ns["_cf"], ns["_ca"]
    computed = {
        "offensive_rating": lambda: formulas.offensive_rating(cf.points, ns["possessions"]),
        "defensive_rating": lambda: formulas.defensive_rating(ca.points, ns["opp_possessions"]),
        "net_rating": lambda: formulas.net_rating(ns["off_rtg"], ns["def_rtg"]),
        "efg_pct": lambda: formulas.effective_fg_pct(cf.fgm, cf.fg3m, cf.fga),
        "tov_pct": lambda: formulas.turnover_pct(cf.tov, cf.fga, cf.fta),
        "orb_pct": lambda: formulas.off_reb_pct(cf.orb, ca.drb),
        "ft_rate": lambda: formulas.free_throw_rate(cf.fta, cf.fga),
        "fg3a_rate": lambda: formulas.three_point_rate(cf.fg3a, cf.fga),
        "ast_to_ratio": lambda: formulas.ast_to_ratio(cf.ast, cf.tov),
        "opp_efg_pct": lambda: opponent_metrics(cf, ca)["opp_efg_pct"],
        "opp_tov_pct": lambda: opponent_metrics(cf, ca)["opp_tov_pct"],
        "drb_pct": lambda: opponent_metrics(cf, ca)["drb_pct"],
        "opp_ft_rate": lambda: opponent_metrics(cf, ca)["opp_ft_rate"],
    }[key]()
    assert _documented(key, ns) == pytest.approx(computed, abs=1e-4), (
        f"the page says: {methodology.GLOSSARY[key].formula}"
    )


def test_the_displayed_pace_formula_matches_the_code():
    ns = _namespace()
    documented = _documented("pace", ns)
    computed = formulas.pace(ns["team_poss"], ns["opp_poss"], ns["minutes"])
    assert documented == pytest.approx(computed, abs=1e-9)


def test_the_displayed_possession_estimate_matches_the_code():
    """Every rate on the site rests on this one, so the page states it."""
    ns = _namespace()
    documented = eval(  # noqa: S307
        methodology.POSSESSION_EXPR, {"__builtins__": {}}, ns
    )
    assert documented == pytest.approx(
        formulas.estimate_possessions(ns["_cf"], ns["_ca"]), abs=1e-9
    )


PROFILE_NAMESPACE_KEYS = (
    "fb_fga", "fb_fgm", "fb_points", "fb_fga_allowed", "fb_fgm_allowed",
    "fga", "opp_fga", "games", "points", "points_2pt", "points_3pt", "points_ft",
    "pot", "opp_turnovers", "second_chance", "oreb_poss", "scoring_oreb_poss",
    "assisted_fgm", "unassisted_fgm", "assisted_3pm", "unassisted_3pm",
    "runs_8_for", "runs_8_against", "largest_run_for_sum", "largest_run_against_sum",
    "scoring_droughts", "fg_droughts",
)

# Figures that are not an expression at all. Listed explicitly so a new one is
# a deliberate choice rather than a silent exemption.
PROSE_ONLY = {"longest_fg_drought_s"}


def _profile_namespace(team) -> dict[str, float]:
    p = team.profile
    return {
        "fb_fga": p.transition.fb_fga, "fb_fgm": p.transition.fb_fgm,
        "fb_points": p.transition.fb_points,
        "fb_fga_allowed": p.transition.fb_fga_allowed,
        "fb_fgm_allowed": p.transition.fb_fgm_allowed,
        "fga": p.transition.fga, "opp_fga": p.transition.opp_fga,
        "games": p.runs.games,
        "points": p.scoring.points, "points_2pt": p.scoring.points_2pt,
        "points_3pt": p.scoring.points_3pt, "points_ft": p.scoring.points_ft,
        "pot": p.scoring.points_off_turnovers, "opp_turnovers": p.scoring.opponent_turnovers,
        "second_chance": p.scoring.second_chance_points,
        "oreb_poss": p.scoring.oreb_possessions,
        "scoring_oreb_poss": p.scoring.scoring_oreb_possessions,
        "assisted_fgm": p.scoring.assisted_fgm, "unassisted_fgm": p.scoring.unassisted_fgm,
        "assisted_3pm": p.scoring.assisted_3pm, "unassisted_3pm": p.scoring.unassisted_3pm,
        "runs_8_for": p.runs.runs_8_plus_for, "runs_8_against": p.runs.runs_8_plus_against,
        "largest_run_for_sum": p.runs.largest_run_for_sum,
        "largest_run_against_sum": p.runs.largest_run_against_sum,
        "scoring_droughts": p.runs.scoring_droughts_3m, "fg_droughts": p.runs.fg_droughts_3m,
    }


@pytest.mark.parametrize("key", sorted(set(PROFILE_META) - PROSE_ONLY))
def test_the_displayed_profile_formula_matches_the_view(key):
    team = build_team_analytics(
        "segev:2", [make_bundle(team_id="segev:2") for _ in range(6)], "TEST", "2025-26"
    )
    documented = eval(  # noqa: S307
        methodology.GLOSSARY[key].expr, {"__builtins__": {}}, _profile_namespace(team)
    )
    assert profile_values(team)[key] == pytest.approx(documented, abs=1e-9), (
        f"the page says: {methodology.GLOSSARY[key].formula}"
    )


def test_a_prose_only_figure_is_declared_rather_than_faked():
    """A season maximum is not an expression. It says so instead of pretending."""
    for key in PROSE_ONLY:
        entry = methodology.GLOSSARY[key]
        assert entry.prose_formula
        assert not entry.expr


def test_every_other_definition_carries_a_real_expression():
    for key, entry in methodology.GLOSSARY.items():
        if key in PROSE_ONLY:
            continue
        assert entry.expr, key
        assert entry.formula != entry.expr, f"{key} was never rendered for display"


def test_the_displayed_zone_shares_and_efficiency_match_the_view():
    from basketball_scout.analytics.views import shot_profile_view

    team = build_team_analytics(
        "segev:2", [make_bundle(team_id="segev:2") for _ in range(4)], "TEST", "2025-26"
    )
    shots = team.profile.shots
    for row in shot_profile_view(team).zones:
        ns = {"zone_attempts": shots.zone_attempts[row.key],
              "zone_points": shots.zone_points[row.key], "fga": shots.fga}
        share = eval(  # noqa: S307
            methodology.GLOSSARY[f"zone_share_{row.key}"].expr, {"__builtins__": {}}, ns
        )
        efg = eval(methodology.ZONE_EFG_EXPR, {"__builtins__": {}}, ns)  # noqa: S307
        assert row.share.value == pytest.approx(share, abs=1e-9)
        assert row.efg_value == pytest.approx(efg, abs=1e-9)

    rim_ns = {"rim_attempts": shots.rim_attempts, "fga": shots.fga}
    documented_rim = eval(  # noqa: S307
        methodology.GLOSSARY["rim_share"].expr, {"__builtins__": {}}, rim_ns
    )
    assert shot_profile_view(team).rim.value == pytest.approx(documented_rim, abs=1e-9)


def test_the_documented_sample_thresholds_are_the_ones_enforced():
    from basketball_scout.analytics.schema import (
        LOW_GAMES, LOW_POSSESSIONS, MIN_GAMES, MIN_POSSESSIONS,
    )

    text = " ".join(row[2] for row in methodology.SAMPLE_ROWS)
    for value in (MIN_POSSESSIONS, MIN_GAMES, LOW_POSSESSIONS, LOW_GAMES):
        assert str(value) in text, value


def test_the_documented_league_total_is_the_one_the_build_pins():
    assert EXPECTED_LEAGUE_TURNOVERS == 5205


# ---- the page ----------------------------------------------------------------


def test_the_methodology_route_renders(client):
    response = client.get("/methodology")
    assert response.status_code == 200
    assert "How these numbers are built" in response.text


def test_every_metric_gets_its_own_anchor(client):
    """So a link from a page can land on the definition rather than the top."""
    body = client.get("/methodology").text
    for key in methodology.documented_keys():
        anchor = key.replace("_", "-")
        assert f'id="{anchor}"' in body, key


def test_every_section_anchor_linked_from_elsewhere_exists(client):
    """Other pages link into this one. A dead fragment is a broken promise."""
    body = client.get("/methodology").text
    for anchor in ("glossary", "possessions", "outcomes", "segments", "baselines",
                   "samples", "stability", "aggregation", "scoring-sources",
                   "transition", "shot-geometry", "pipeline", "dataset", "limitations"):
        assert f'id="{anchor}"' in body, anchor


def test_the_page_carries_every_unavailable_declaration(client):
    """The seven declarations the evidence packs already make are the clearest
    statement of the product's boundary, and this is where a reader sees them."""
    body = client.get("/methodology").text
    for item in UNAVAILABLE:
        assert item.label in body, item.evidence_id


def test_the_page_states_the_half_court_restriction_prominently(client):
    body = client.get("/methodology").text
    assert "5.7%" in body
    assert "never means half-court" in body or "never mean half-court" in body


def test_the_page_explains_why_a_saved_report_can_differ(client):
    body = client.get("/methodology").text
    assert "volume-weighted" in body
    assert "frozen" in body


def test_the_page_states_the_shot_geometry_limitation(client):
    body = client.get("/methodology").text
    assert "twenty" in body.lower()
    assert "held-out" in body or "blind" in body


def test_the_page_says_the_legacy_label_is_corrected_on_the_way_out(client):
    body = client.get("/methodology").text
    assert "Trailing 5+" in body
    assert "Trailing 6+" not in body


def test_the_page_never_claims_a_significance_test(client):
    """No p-value is computed anywhere in this project."""
    body = client.get("/methodology").text.lower()
    assert "p-value" not in body or "no p-value" in body
    assert "statistically significant" not in body


def test_every_cell_metric_is_documented_including_the_defensive_half(client):
    body = client.get("/methodology").text
    for key in CELL_METRICS:
        assert f'id="{key.replace("_", "-")}"' in body, key
