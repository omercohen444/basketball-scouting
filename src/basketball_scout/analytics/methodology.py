"""The glossary, as data.

Every metric the site renders has an entry here, and the entry describes the
code path that actually produces the number — not a textbook definition of a
metric with the same name. That distinction has already mattered once: the
opponent turnover rate was documented and displayed one way while the team's
own was computed another, two to three points apart in the same table.

Two things keep it honest:

* the entries are keyed by the same constants the view layer renders from
  (``CELL_META``, ``PROFILE_META``, ``SHOT_META``), so a metric added without
  an entry fails the coverage test rather than shipping undocumented;
* ``tests/test_web_methodology.py`` recomputes each documented formula from raw
  components and asserts it equals what the builder produced. A formula that
  reads plausibly but describes different arithmetic fails there.

Formulas are written in the units the artifact stores. Percentages are
fractions until the view layer multiplies them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import (
    LOW_GAMES,
    LOW_POSSESSIONS,
    MIN_GAMES,
    MIN_POSSESSIONS,
    OUTCOMES,
    SEGMENTS,
)
from .views import (
    CELL_META,
    OUTCOME_LABELS,
    PROFILE_META,
    SEGMENT_DEFINITIONS,
    SEGMENT_LABELS,
    SHOT_META,
    MetricMeta,
)

DIRECTION_WORDS: dict[str, str] = {
    "higher_is_better": "Higher is better",
    "lower_is_better": "Lower is better",
    "neutral": "Style — neither end is better",
}


# The displayed formula is RENDERED FROM the expression below, not written
# alongside it. That is the whole point: `tests/test_web_methodology.py`
# evaluates `expr` against real components and asserts it equals what the code
# produces, so the string a reader sees is the string that was verified. A
# formula and an implementation cannot drift apart without the test failing.
DISPLAY_TOKENS: dict[str, str] = {
    "points": "Points", "possessions": "Possessions",
    "opp_points": "Opponent points", "opp_possessions": "Opponent possessions",
    "team_poss": "Team possessions", "opp_poss": "Opponent possessions",
    "minutes": "Minutes played",
    "off_rtg": "Offensive Rating", "def_rtg": "Defensive Rating",
    "fgm": "FGM", "fga": "FGA", "fg3m": "3PM", "fg3a": "3PA",
    "ftm": "FTM", "fta": "FTA", "orb": "ORB", "drb": "DRB",
    "ast": "AST", "tov": "TOV",
    "opp_fgm": "Opponent FGM", "opp_fga": "Opponent FGA", "opp_fg3m": "Opponent 3PM",
    "opp_fta": "Opponent FTA", "opp_orb": "Opponent ORB", "opp_drb": "Opponent DRB",
    "opp_tov": "Opponent TOV",
    "fb_fga": "Fast-break FGA", "fb_fgm": "Fast-break FGM", "fb_points": "Fast-break points",
    "fb_fga_allowed": "Opponent fast-break FGA", "fb_fgm_allowed": "Opponent fast-break FGM",
    "games": "Games",
    "points_2pt": "Two-point points", "points_3pt": "Three-point points",
    "points_ft": "Free-throw points",
    "pot": "Points off turnovers", "opp_turnovers": "Opponent turnovers",
    "second_chance": "Second-chance points",
    "oreb_poss": "Offensive-rebound possessions",
    "scoring_oreb_poss": "Offensive-rebound possessions that scored",
    "assisted_fgm": "Assisted FGM", "unassisted_fgm": "Unassisted FGM",
    "assisted_3pm": "Assisted 3PM", "unassisted_3pm": "Unassisted 3PM",
    "runs_8_for": "Runs of 8+ unanswered points",
    "runs_8_against": "Opponent runs of 8+ unanswered points",
    "largest_run_for_sum": "Sum of each game's largest run",
    "largest_run_against_sum": "Sum of each game's largest conceded run",
    "scoring_droughts": "Stretches of 180+ seconds without scoring",
    "fg_droughts": "Stretches of 180+ seconds without a made field goal",
    "zone_attempts": "Zone attempts", "zone_points": "Zone points",
    "rim_attempts": "Rim attempts",
}

_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]*")


def render_formula(expr: str) -> str:
    """Turn an evaluable expression into the line a reader sees."""
    rendered = _TOKEN_RE.sub(lambda m: DISPLAY_TOKENS.get(m.group(0), m.group(0)), expr)
    return rendered.replace("*", "×").replace("/", "÷")


@dataclass(frozen=True)
class GlossaryEntry:
    """One metric, fully described.

    ``expr`` is the arithmetic, in a form both a test and a reader can check.
    ``prose_formula`` is only for the handful of figures that are not an
    expression at all (a season maximum, say) — those are listed explicitly in
    the test rather than silently exempt.
    """

    key: str
    meta: MetricMeta
    expr: str
    reading: str
    note: str = ""
    prose_formula: str = ""

    @property
    def formula(self) -> str:
        return self.prose_formula or render_formula(self.expr)

    @property
    def label(self) -> str:
        return self.meta.label

    @property
    def short(self) -> str:
        return self.meta.short

    @property
    def direction(self) -> str:
        return DIRECTION_WORDS[self.meta.direction]

    @property
    def is_style(self) -> bool:
        return self.meta.direction == "neutral"

    @property
    def unit(self) -> str:
        return {
            "pct": "Percentage",
            "per100": "Per 100 possessions",
            "ratio": "Ratio",
            "count": "Per game",
            "seconds": "Minutes and seconds",
        }.get(self.meta.unit, self.meta.unit)

    @property
    def anchor(self) -> str:
        return self.key.replace("_", "-")


def _entry(key: str, meta_source: dict[str, MetricMeta], expr: str,
           reading: str, note: str = "", prose_formula: str = "") -> tuple[str, GlossaryEntry]:
    return key, GlossaryEntry(key=key, meta=meta_source[key], expr=expr,
                              reading=reading, note=note, prose_formula=prose_formula)


# The possession estimate every rate on the site rests on. Stated once here
# because four other formulas reference it.
POSSESSION_EXPR = "fga - 1.07 * (orb / (orb + opp_drb)) * (fga - fgm) + tov + 0.4 * fta"
POSSESSION_FORMULA = render_formula(POSSESSION_EXPR)

POSSESSION_NOTE = (
    "The standard Dean Oliver estimate, computed once per team from that team's own box "
    "score plus only the opponent's defensive rebound count. The two teams' estimates are "
    "deliberately not averaged into one game figure: letting them differ slightly reflects "
    "real estimation noise rather than papering over it."
)


CORE_GLOSSARY: dict[str, GlossaryEntry] = dict([
    _entry("offensive_rating", CELL_META,
           "100 * points / possessions",
           "Points scored per 100 of this team's own possessions.",
           "Uses the team's own possession estimate, never a game average."),
    _entry("defensive_rating", CELL_META,
           "100 * opp_points / opp_possessions",
           "Points allowed per 100 possessions the opponent had.",
           "The denominator is the opponent's own estimate — their offensive possessions, "
           "which are this team's defensive ones."),
    _entry("net_rating", CELL_META,
           "off_rtg - def_rtg",
           "Net points per 100 possessions.",
           "Carries no consistency label anywhere on the site: it sits near zero, so the "
           "usual measure of relative variability has no meaning for it."),
    _entry("pace", CELL_META,
           "40 * ((team_poss + opp_poss) / 2) / minutes",
           "Estimated possessions per team per 40 minutes.",
           "A game-level figure, so both teams always share it. Normalised against the "
           "game's real elapsed minutes, so an overtime game is not inflated. Absent from "
           "any segment without a defined elapsed time — there is no rigorous denominator "
           "for 'minutes spent trailing', and inventing one would be worse than omitting it."),
    _entry("efg_pct", CELL_META,
           "(fgm + 0.5 * fg3m) / fga",
           "Shooting accuracy with a made three counted at one and a half makes.",
           ""),
    _entry("tov_pct", CELL_META,
           "tov / (fga + 0.44 * fta + tov)",
           "Turnovers per estimated play.",
           "The denominator is plays, not possessions. The opponent version below uses the "
           "same one, so the two are comparable side by side."),
    _entry("orb_pct", CELL_META,
           "orb / (orb + opp_drb)",
           "Share of the contestable offensive glass kept.",
           ""),
    _entry("ft_rate", CELL_META,
           "fta / fga",
           "How often the team reaches the line, relative to how often it shoots.",
           "Attempts, not makes — this measures trips to the line, not free-throw shooting."),
    _entry("fg3a_rate", CELL_META,
           "fg3a / fga",
           "Share of field-goal attempts taken from three.",
           ""),
    _entry("ast_to_ratio", CELL_META,
           "ast / tov",
           "Assists per turnover.",
           "The numerator counts every provider assist action, including ones that could "
           "not be linked to a specific made shot. A linkage failure must never remove a "
           "real assist from this ratio."),
    _entry("opp_efg_pct", CELL_META,
           "(opp_fgm + 0.5 * opp_fg3m) / opp_fga",
           "How well opponents shoot against this team.",
           "The same function as the team's own eFG%, run over the opponent's box score."),
    _entry("opp_tov_pct", CELL_META,
           "opp_tov / (opp_fga + 0.44 * opp_fta + opp_tov)",
           "How often this team forces a turnover, per opponent play.",
           "Higher is better here — it is turnovers forced, not committed."),
    _entry("drb_pct", CELL_META,
           "drb / (drb + opp_orb)",
           "Share of the contestable defensive glass won.",
           "On a segment, defensive rebounds are derived as the opponent possessions in "
           "that same segment which ended in one."),
    _entry("opp_ft_rate", CELL_META,
           "opp_fta / opp_fga",
           "How often this team sends opponents to the line.",
           ""),
])


PROFILE_GLOSSARY: dict[str, GlossaryEntry] = dict([
    _entry("fb_rate", PROFILE_META,
           "fb_fga / fga",
           "Share of shot attempts the provider flagged as a fast break.",
           "Style: a team that runs more is not a better team."),
    _entry("fb_fg_pct", PROFILE_META,
           "fb_fgm / fb_fga",
           "Finishing on those attempts.",
           "Roughly 150 to 240 attempts per team-season, so read it as a tendency."),
    _entry("fb_points_pg", PROFILE_META,
           "fb_points / games",
           "Points from possessions the provider flagged as fast break.",
           "Overlaps points off turnovers — a break off a steal is both."),
    _entry("fb_rate_allowed", PROFILE_META,
           "fb_fga_allowed / opp_fga",
           "How often opponents get out in transition against this team.",
           "The same events grouped by the other team, so it needs no separate analytics."),
    _entry("fb_fg_pct_allowed", PROFILE_META,
           "fb_fgm_allowed / fb_fga_allowed",
           "How well opponents finish in transition against this team.",
           ""),
    _entry("share_2pt", PROFILE_META,
           "points_2pt / points",
           "Share of all points scored on two-pointers.",
           "One of the three shares that partition scoring exactly."),
    _entry("share_3pt", PROFILE_META,
           "points_3pt / points",
           "Share of all points scored on threes.",
           "One of the three shares that partition scoring exactly."),
    _entry("share_ft", PROFILE_META,
           "points_ft / points",
           "Share of all points scored at the line.",
           "One of the three shares that partition scoring exactly."),
    _entry("pot_pg", PROFILE_META,
           "pot / games",
           "Points scored on the possession immediately after an opponent turnover.",
           "Never extended into a later possession. Overlaps fast break and the scoring "
           "shares, so it is never drawn as part of a whole."),
    _entry("points_per_opp_tov", PROFILE_META,
           "pot / opp_turnovers",
           "How much a forced turnover is actually worth to this team.",
           "Separates forcing turnovers from converting them, which the per-game total "
           "conflates."),
    _entry("second_chance_pg", PROFILE_META,
           "second_chance / games",
           "Points scored after the first offensive rebound of a possession.",
           ""),
    _entry("second_chance_conversion", PROFILE_META,
           "scoring_oreb_poss / oreb_poss",
           "How often an offensive rebound turns into a point.",
           "Distinguishes generating second chances from finishing them."),
    _entry("assisted_share", PROFILE_META,
           "assisted_fgm / (assisted_fgm + unassisted_fgm)",
           "Share of made field goals created by a teammate's pass.",
           "Shot-level attribution, stricter than the raw assist count in AST/TO. Some "
           "provider assists cannot be linked to any specific shot; those are excluded "
           "from both sides of this ratio rather than guessed at."),
    _entry("assisted_3pm_share", PROFILE_META,
           "assisted_3pm / (assisted_3pm + unassisted_3pm)",
           "Share of made threes created by a pass.",
           "Speaks to whether a team's threes are set up or self-generated."),
    _entry("runs_8_for_pg", PROFILE_META,
           "runs_8_for / games",
           "How often this team goes on a substantial run.",
           "A run continues until the other team scores. It can span a quarter break."),
    _entry("runs_8_against_pg", PROFILE_META,
           "runs_8_against / games",
           "How often this team concedes one.",
           ""),
    _entry("largest_run_for_pg", PROFILE_META,
           "largest_run_for_sum / games",
           "The size of this team's biggest run in a typical game.",
           ""),
    _entry("largest_run_against_pg", PROFILE_META,
           "largest_run_against_sum / games",
           "The size of the biggest run against this team in a typical game.",
           ""),
    _entry("scoring_droughts_pg", PROFILE_META,
           "scoring_droughts / games",
           "How often the team goes three minutes without a point of any kind.",
           "A project metric, not an official category. Free throws end it. Measured on "
           "the quarter clock and never carried across a quarter break."),
    _entry("fg_droughts_pg", PROFILE_META,
           "fg_droughts / games",
           "How often the team goes three minutes without a basket.",
           "Free throws do not end this one, so a team can look like it kept scoring "
           "while going without a field goal."),
    _entry("longest_fg_drought_s", PROFILE_META,
           "",
           "The worst case rather than the typical one.",
           "",
           prose_formula="Longest single stretch without a made field goal, across the season"),
])


SHOT_GLOSSARY: dict[str, GlossaryEntry] = dict([
    _entry("zone_share_lane_2pt", SHOT_META,
           "zone_attempts / fga",
           "Lane attempts as a share of all attempts.",
           "Experimental — see the shot geometry section."),
    _entry("zone_share_midrange_2pt", SHOT_META,
           "zone_attempts / fga",
           "Mid-range attempts as a share of all attempts.",
           "Experimental — see the shot geometry section."),
    _entry("zone_share_corner_3", SHOT_META,
           "zone_attempts / fga",
           "Corner-three attempts as a share of all attempts.",
           "Left and right corners are pooled. Experimental."),
    _entry("zone_share_atb_3", SHOT_META,
           "zone_attempts / fga",
           "Above-the-break three attempts as a share of all attempts.",
           "Experimental — see the shot geometry section."),
    _entry("rim_share", SHOT_META,
           "rim_attempts / fga",
           "Share of attempts finished at the basket.",
           "Derived from the play-by-play shot type — dunk, lay-up or alley-oop — not from "
           "coordinates, which makes it the sturdiest figure in the shot block."),
])

ZONE_EFG_EXPR = "zone_points / 2 / zone_attempts"
ZONE_EFG_FORMULA = render_formula(ZONE_EFG_EXPR)
ZONE_EFG_NOTE = (
    "This is effective field-goal percentage for that zone. Inside the arc every make is "
    "worth two and beyond it every make is worth three, so (FGM + 0.5 × 3PM) ÷ FGA reduces "
    "to points ÷ 2 ÷ attempts in both cases."
)


GLOSSARY: dict[str, GlossaryEntry] = {**CORE_GLOSSARY, **PROFILE_GLOSSARY, **SHOT_GLOSSARY}


GLOSSARY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Efficiency", "Everything on this site is per possession rather than per game, so a "
                   "fast team and a slow one can be compared directly.",
     ("offensive_rating", "defensive_rating", "net_rating", "pace")),
    ("Four factors — offence", "The four things a team controls on its own possessions.",
     ("efg_pct", "tov_pct", "orb_pct", "ft_rate")),
    ("Four factors — defence", "The same four, computed from the opponent's box score. "
                              "Both halves run through the same functions, so they are "
                              "comparable side by side.",
     ("opp_efg_pct", "opp_tov_pct", "drb_pct", "opp_ft_rate")),
    ("Style", "Descriptions rather than judgements. These are never coloured good or bad "
              "anywhere on the site.",
     ("fg3a_rate", "ast_to_ratio")),
    ("Transition", "From the provider's own fast-break flag. Read the transition section "
                   "below before using these.",
     ("fb_rate", "fb_fg_pct", "fb_points_pg", "fb_rate_allowed", "fb_fg_pct_allowed")),
    ("Scoring sources", "The first three partition scoring exactly. The rest overlap each "
                        "other and are never summed.",
     ("share_2pt", "share_3pt", "share_ft", "pot_pg", "points_per_opp_tov",
      "second_chance_pg", "second_chance_conversion", "assisted_share", "assisted_3pm_share")),
    ("Scoring rhythm", "Runs and droughts. Descriptive patterns — none of them claims a cause.",
     ("runs_8_for_pg", "runs_8_against_pg", "largest_run_for_pg", "largest_run_against_pg",
      "scoring_droughts_pg", "fg_droughts_pg", "longest_fg_drought_s")),
    ("Shot location — experimental", "Complete data, provisional validation. Never ranked "
                                     "and never coloured.",
     ("zone_share_lane_2pt", "zone_share_midrange_2pt", "zone_share_corner_3",
      "zone_share_atb_3", "rim_share")),
)


def glossary_groups() -> list[tuple[str, str, list[GlossaryEntry]]]:
    return [
        (label, blurb, [GLOSSARY[key] for key in keys if key in GLOSSARY])
        for label, blurb, keys in GLOSSARY_GROUPS
    ]


def documented_keys() -> set[str]:
    return set(GLOSSARY)


# ---- filters -----------------------------------------------------------------


def outcome_rows() -> list[tuple[str, str, str]]:
    definitions = {
        "all": "Every game in the season.",
        "wins": "Only the games this team won.",
        "losses": "Only the games this team lost.",
    }
    return [(key, OUTCOME_LABELS[key], definitions[key]) for key in OUTCOMES]


def segment_rows() -> list[tuple[str, str, str]]:
    return [(key, SEGMENT_LABELS[key], SEGMENT_DEFINITIONS[key]) for key in SEGMENTS]


SEGMENT_NOTES: tuple[str, ...] = (
    "A segment is assigned from what was true at the START of a possession, never from what "
    "happened during it. A possession that begins two points behind and ends four ahead is a "
    "trailing possession.",
    "Q1 + Q2 + Q3 + Q4 does not re-sum to Full Game to the last decimal. Full Game is driven "
    "off the stored box-score records so the site's season row is identical to the scouting "
    "reports'; the quarters are summed from possessions, which differ by a handful of "
    "defensive rebounds across a whole season.",
    "Overtime is excluded from every segment and is not offered as one. Nine of the season's "
    "182 games went to overtime, giving about 17 possessions per team across the year — far "
    "too few to say anything.",
    "Leading and Trailing are defined on the margin (at least one point ahead, at least one "
    "behind) rather than as a union of score-state bins. The bins have a known off-by-one at "
    "exactly five points behind, and the margin definition is unaffected by it.",
)


SAMPLE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Sufficient", "No badge",
     f"At least {MIN_POSSESSIONS} possessions and {MIN_GAMES} games, and at least "
     f"{LOW_POSSESSIONS} possessions and {LOW_GAMES} games."),
    ("Limited", "Amber badge, values shown",
     f"Above the floor but under {LOW_POSSESSIONS} possessions or {LOW_GAMES} games. The "
     "numbers are real and are shown, with a marker."),
    ("Insufficient", "Grey badge, comparison withheld",
     f"Under {MIN_POSSESSIONS} possessions or {MIN_GAMES} games. The cell renders a state "
     "rather than a value, and is left out of league ranking entirely."),
)

SAMPLE_NOTES: tuple[str, ...] = (
    "The badge names whichever count actually binds. A clutch cell can span fourteen games "
    "and sixty possessions — saying 'limited: 14 games' there would give a reason that is "
    "not the reason.",
    "Suppression means the value is withheld, not hidden: the cell says what is wrong and "
    "how large the sample is. An insufficient cell is also left unranked rather than ranked "
    "last, because comparing a two-game sample against a twenty-game one is not a ranking.",
    "Thresholds are the project's own, reused from the evidence-pack builder rather than "
    "invented for the website.",
)


BASELINE_ROWS: tuple[tuple[str, str], ...] = (
    ("vs Season", "Shown when the outcome filter is All. The comparison is the team's own "
                  "full-game value across every game."),
    ("vs Win Baseline", "Shown under Wins. The comparison is the team's own full-game value "
                        "in the games it won."),
    ("vs Loss Baseline", "Shown under Losses. The comparison is the team's own full-game "
                         "value in the games it lost."),
)

BASELINE_NOTE = (
    "The baseline always uses the same outcome filter as the segment. Under Losses · Q4 the "
    "comparison is that team's full-game play in losses — not its overall season. Comparing "
    "against the season there would fold 'worse in losses' and 'worse in the fourth' into "
    "one number, and the column would stop meaning anything."
)
