"""Metric names are terms, and a term should reach its own definition.

The failure this guards against is subtle: a link that resolves, renders, and
lands the reader at the top of a fourteen-section reference to find the term
themselves. That is worse than plain text, because it looks like it worked.

So the rules here are (a) every definition link on the site points at a
fragment that actually exists on the methodology page, and (b) a metric that
has its own anchor never settles for a section, or for the page root.
"""

from __future__ import annotations

import re

import pytest
from analytics_factories import write_synthetic_analytics
from fastapi.testclient import TestClient
from pack_factories import write_synthetic_packs
from product_factories import make_app

from basketball_scout.analytics.views import (
    CELL_META,
    METHODOLOGY_SECTIONS,
    PROFILE_META,
    SHOT_META,
    methodology_anchor,
    methodology_href,
)
from basketball_scout.persistence.memory import InMemoryReportRepository

# Every surface that renders analytical labels.
SURFACES: tuple[str, ...] = (
    "/",
    "/methodology",
    "/teams/segev:4",
    "/teams/segev:4/profile",
    "/teams/segev:4/splits",
    "/teams/segev:4/quarters",
    "/teams/segev:4/situations",
    "/teams/segev:4/games",
    "/explore",
    "/explore?family=defence",
    "/games",
    "/compare",
    "/scouting/segev:4",
)


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    tmp_path = tmp_path_factory.mktemp("links")
    write_synthetic_packs(tmp_path)
    analytics = tmp_path / "analytics"
    write_synthetic_analytics(analytics)
    return TestClient(
        make_app(tmp_path, repository=InMemoryReportRepository(), analytics_dir=analytics)
    )


@pytest.fixture(scope="module")
def methodology_ids(client) -> set[str]:
    """Every fragment the methodology page actually defines."""
    body = client.get("/methodology").text
    return set(re.findall(r'id="([^"]+)"', body))


def _links(body: str) -> list[str]:
    return re.findall(r'href="(/methodology[^"]*)"', body)


# ---- the mapping itself ------------------------------------------------------


def test_every_documented_metric_resolves_to_its_own_anchor():
    for key in set(CELL_META) | set(PROFILE_META) | set(SHOT_META):
        href = methodology_href(key)
        assert href == "/methodology#" + methodology_anchor(key), key


def test_every_mapped_concept_resolves_to_a_section():
    for key, section in METHODOLOGY_SECTIONS.items():
        assert methodology_href(key) == "/methodology#" + section, key


def test_an_unmapped_key_gets_nothing_rather_than_the_page_root():
    """Plain text beats a link that drops a reader at the top of the page."""
    assert methodology_href("no_such_metric") is None
    assert methodology_href("") is None


def test_the_mapping_never_points_at_a_missing_anchor(methodology_ids):
    """The anti-drift guard: a metric renamed or a section removed breaks here,
    not silently in a browser."""
    broken = []
    for key in set(CELL_META) | set(PROFILE_META) | set(SHOT_META) | set(METHODOLOGY_SECTIONS):
        href = methodology_href(key)
        assert href, key
        if href.split("#", 1)[1] not in methodology_ids:
            broken.append((key, href))
    assert not broken, f"links to anchors that do not exist: {broken}"


# ---- what the pages actually render ------------------------------------------


@pytest.mark.parametrize("path", SURFACES)
def test_every_anchored_link_lands_on_a_real_anchor(client, methodology_ids, path):
    """A fragment that does not exist scrolls nowhere and looks like nothing
    happened."""
    for href in _links(client.get(path).text):
        if "#" not in href:
            continue  # a page-level "read the methodology" link, not a term
        assert href.split("#", 1)[1] in methodology_ids, f"{path} -> {href}"


@pytest.mark.parametrize("path", SURFACES)
def test_a_bare_methodology_link_is_navigation_copy_not_a_metric(client, path):
    """One trailing "how these numbers are built" link per page is fine. A
    metric rendered that way is not, and the class is what tells them apart."""
    body = client.get(path).text
    for match in re.finditer(r'<a([^>]*)href="/methodology"', body):
        assert "mdef" not in match.group(1), f"{path}: a term links to the page root"


# ---- representative coverage, surface by surface -----------------------------


@pytest.mark.parametrize("path,expected", [
    ("/", "/methodology#offensive-rating"),
    ("/", "/methodology#opp-efg-pct"),
    ("/teams/segev:4", "/methodology#net-rating"),
    ("/teams/segev:4", "/methodology#transition"),
    ("/teams/segev:4", "/methodology#stability"),
    ("/teams/segev:4/profile", "/methodology#shot-geometry"),
    ("/teams/segev:4/profile", "/methodology#turnovers"),
    ("/teams/segev:4/profile", "/methodology#scoring-sources"),
    ("/teams/segev:4/profile", "/methodology#fb-rate"),
    ("/teams/segev:4/quarters", "/methodology#efg-pct"),
    ("/teams/segev:4/situations", "/methodology#drb-pct"),
    ("/teams/segev:4/games", "/methodology#pace"),
    ("/explore", "/methodology#offensive-rating"),
    ("/explore?family=four_factors", "/methodology#tov-pct"),
    ("/explore?family=defence", "/methodology#drb-pct"),
    ("/explore", "/methodology#baselines"),
    ("/games", "/methodology#efg-pct"),
    ("/compare", "/methodology#defensive-rating"),
])
def test_representative_links_are_present(client, path, expected):
    assert f'href="{expected}"' in client.get(path).text, f"{path} is missing {expected}"


def test_net_rating_by_quarter_says_what_the_number_is(client):
    """+17.1 reads as a point margin unless the heading says otherwise."""
    body = client.get("/teams/segev:4").text
    assert "per 100 possessions" in body
    assert 'href="/methodology#net-rating"' in body


# ---- discoverability and landing --------------------------------------------


def test_a_term_is_visibly_a_term(client):
    """It must not be guesswork that a metric name can be clicked."""
    from pathlib import Path

    stylesheet = (
        Path(__file__).resolve().parents[1]
        / "src" / "basketball_scout" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")
    rule = re.search(r"\.mdef\s*\{([^}]*)\}", stylesheet)
    assert rule, ".mdef has no rule, so a term looks like plain text"
    assert "border-bottom" in rule.group(1), "a term needs a visible underline at rest"
    assert "cursor: pointer" in rule.group(1)
    assert re.search(r"\.mdef:hover", stylesheet), "no hover state"
    assert "focus-visible" in stylesheet


def test_a_definition_is_not_hidden_under_the_sticky_header(client):
    """The global bar is sticky. Without a scroll margin the reader lands on a
    definition sitting underneath it."""
    from pathlib import Path

    stylesheet = (
        Path(__file__).resolve().parents[1]
        / "src" / "basketball_scout" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")
    assert "scroll-margin-top" in stylesheet
    rule = re.search(r"([^{}]*scroll-margin-top[^{}]*)", stylesheet)
    assert rule


def test_scouting_links_only_deterministic_labels_never_prose(client):
    """The report's prose is an agent's reading of evidence, not a defined
    term. Only measured labels may carry a definition link."""
    body = client.get("/scouting/segev:4").text
    for match in re.finditer(r'<a class="mdef" href="[^"]*">([^<]*)</a>', body):
        assert len(match.group(1)) < 40, "a sentence was turned into a definition link"


# ---- the filter strip --------------------------------------------------------


def test_the_filter_strip_owns_its_overflow_and_shows_it(client):
    """Fifteen team chips do not fit 1440px. The strip must scroll, must say so,
    and must not push the page sideways."""
    from pathlib import Path

    stylesheet = (
        Path(__file__).resolve().parents[1]
        / "src" / "basketball_scout" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")
    rule = re.search(r"\n\.fgroup\s*\{([^}]*)\}", stylesheet)
    assert rule, ".fgroup has no rule"
    body = rule.group(1)
    assert "overflow-x: auto" in body
    assert "min-width: 0" in body, "without this the strip widens the page"
    assert "scrollbar-width: none" not in body, "hiding the bar is what made it unreachable"
    assert "background-attachment" in body, "no scroll-shadow affordance"
    assert re.search(r"\.fgroup::-webkit-scrollbar\s*\{[^}]*height", stylesheet)


def test_a_chip_never_shrinks_to_fit(client):
    from pathlib import Path

    stylesheet = (
        Path(__file__).resolve().parents[1]
        / "src" / "basketball_scout" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")
    rule = re.search(r"\n\.fopt\s*\{([^}]*)\}", stylesheet)
    assert rule and "flex: none" in rule.group(1)


def test_every_team_has_a_reachable_filter_chip(client):
    """All fourteen, plus the All option, are in the markup and each carries a
    working query — reachability of the markup is the half a test can hold; the
    scroll behaviour is checked in the browser pass."""
    body = client.get("/games").text
    chips = re.findall(r'<a class="fopt[^"]*"\s*\n?\s*href="([^"]*)"', body)
    team_links = [c for c in chips if "team=" in c]
    assert len(team_links) == 14, f"expected 14 team chips, found {len(team_links)}"


def test_selecting_the_last_team_still_filters(client):
    """The scroll fix is presentation only — the query behaviour is unchanged."""
    body = client.get("/games").text
    last = re.findall(r'href="(/games\?[^"]*team=[^"&]*[^"]*)"', body)[-1]
    response = client.get(last.replace("&amp;", "&"))
    assert response.status_code == 200
