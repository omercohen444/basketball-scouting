"""Team crest lookup.

An explicit map, not a matcher. Fuzzy matching was tried and is not safe here:
the league's own team names and the crest filenames disagree in ways a
similarity score gets wrong — ``ELIZUR NETANYA`` against ``elitzur_netanya``
is a different transliteration, and ``BEER SHEVA`` against
``hapoel_beer_sheva_dimona`` carries a club prefix and a second town the league
name omits. Fourteen lines of dictionary cannot silently mis-assign a crest.

Two directories exist on purpose. ``team_logos/`` holds the supplied artwork at
full resolution, up to 4168x4168, and is the archival copy. ``team_logos/web/``
holds 128px versions and is what the site actually serves — the league table
draws fourteen crests at around 24px, and shipping 2.9 MB to paint them would
be absurd.
"""

from __future__ import annotations

LOGO_DIR = "/static/team_logos/web"

TEAM_LOGOS: dict[str, str] = {
    "segev:2": "maccabi_tel_aviv.png",
    "segev:3": "hapoel_tel_aviv.png",
    "segev:4": "hapoel_jerusalem.png",
    "segev:5": "hapoel_holon.png",
    "segev:6": "bnei_herzliya.png",
    "segev:7": "maccabi_ramat_gan.png",
    "segev:8": "hapoel_haemek.png",
    "segev:9": "ironi_ness_ziona.png",
    "segev:10": "hapoel_galil_elion.png",
    "segev:11": "hapoel_beer_sheva_dimona.png",
    "segev:12": "ironi_kiryat_ata.png",
    "segev:13": "maccabi_raanana.png",
    "segev:14": "maccabi_rishon_lezion.png",
    "segev:15": "elitzur_netanya.png",
}


def logo_url(team_id: str | None) -> str | None:
    """The served crest for a team, or ``None``.

    Accepts the canonical ``segev:4`` and the URL slug ``segev_4``, because
    both forms reach the templates. ``None`` for anything unknown, so a caller
    renders a monogram rather than a broken image.
    """
    if not team_id:
        return None
    filename = TEAM_LOGOS.get(team_id) or TEAM_LOGOS.get(team_id.replace("_", ":"))
    return f"{LOGO_DIR}/{filename}" if filename else None


def initials(team_name: str | None) -> str:
    """Fallback monogram — the first letters of up to two significant words.

    Club prefixes are dropped so the three Maccabi sides do not all read "MA".
    """
    if not team_name:
        return "?"
    skip = {"hapoel", "maccabi", "ironi", "bnei", "elitzur", "elizur"}
    words = [w for w in team_name.split() if w]
    significant = [w for w in words if w.lower() not in skip] or words
    return "".join(w[0] for w in significant[:2]).upper()
