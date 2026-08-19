"""Team crests.

The mapping is hand-written, so the thing worth testing is that it stays in
agreement with three separate sources of truth: the shipped pack index (which
teams exist), the files on disk (which crests exist), and the URL forms the
templates actually use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pack_factories import PRODUCTION_PACKS_DIR

from basketball_scout.agents.pack_store import PackStore
from basketball_scout.web.logos import LOGO_DIR, TEAM_LOGOS, initials, logo_url

WEB_LOGO_DIR = Path(__file__).resolve().parents[1] / "src" / "basketball_scout" / "web" / "static" / "team_logos" / "web"
ORIGINAL_LOGO_DIR = WEB_LOGO_DIR.parent


@pytest.mark.skipif(not WEB_LOGO_DIR.is_dir(), reason="web logo derivatives are not present")
def test_every_mapped_crest_exists_on_disk():
    missing = [f for f in TEAM_LOGOS.values() if not (WEB_LOGO_DIR / f).is_file()]
    assert not missing, f"mapped but absent: {missing}"


@pytest.mark.skipif(not WEB_LOGO_DIR.is_dir(), reason="web logo derivatives are not present")
def test_no_crest_is_shipped_without_being_mapped():
    """An unmapped file is dead weight in the deployment and probably a typo in
    the map."""
    on_disk = {p.name for p in WEB_LOGO_DIR.glob("*.png")}
    assert on_disk == set(TEAM_LOGOS.values())


@pytest.mark.skipif(
    not (PRODUCTION_PACKS_DIR / "index.json").is_file(),
    reason="production evidence packs are not present",
)
def test_every_league_team_resolves_to_a_crest():
    """The map is keyed by team id, so it must cover exactly the teams the pack
    index says exist — no more, no fewer."""
    league_ids = set(PackStore(PRODUCTION_PACKS_DIR).team_ids())
    assert set(TEAM_LOGOS) == league_ids
    for team_id in league_ids:
        assert logo_url(team_id), team_id


def test_both_url_forms_resolve():
    """`resolve_team_id` accepts the slug form, so templates see both."""
    assert logo_url("segev:4") == f"{LOGO_DIR}/hapoel_jerusalem.png"
    assert logo_url("segev_4") == logo_url("segev:4")


def test_an_unknown_team_gets_nothing_rather_than_a_broken_image():
    assert logo_url("segev:999") is None
    assert logo_url(None) is None
    assert logo_url("") is None


def test_crests_are_served_from_the_downscaled_directory():
    """The originals run to 4168x4168 and 1.8 MB; the league table draws
    fourteen crests at about 24px. Serving the archival copies would ship
    roughly 3 MB to paint them."""
    assert LOGO_DIR.endswith("/web")
    assert logo_url("segev:5").startswith("/static/team_logos/web/")


@pytest.mark.skipif(not WEB_LOGO_DIR.is_dir(), reason="web logo derivatives are not present")
def test_the_served_set_is_far_smaller_than_the_archive():
    served = sum(p.stat().st_size for p in WEB_LOGO_DIR.glob("*.png"))
    archive = sum(p.stat().st_size for p in ORIGINAL_LOGO_DIR.glob("*.png"))
    assert served < archive / 4, f"served {served/1024:.0f}KB vs archive {archive/1024:.0f}KB"


def test_initials_skip_the_club_prefix():
    """Three clubs are called Maccabi and four Hapoel; a naive monogram would
    give half the league the same two letters."""
    assert initials("HAPOEL JERUSALEM") == "J"
    assert initials("MACCABI TEL AVIV") == "TA"
    assert initials("BNEI HERZLIYA") == "H"
    assert initials("GALIL ELION") == "GE"


def test_initials_never_return_empty():
    assert initials(None) == "?"
    assert initials("") == "?"
    assert initials("MACCABI") == "M", "a name that is only a prefix still yields something"
