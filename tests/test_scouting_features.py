"""Deterministic Scouting Feature Pack: consolidation-layer tests.

No new basketball logic is tested here beyond the pack's own assembly
(identity, joins, provenance, aggregation arithmetic) — the underlying
geometry/fastbreak/possession rules are already covered by their own test
files and are not re-verified here.
"""

from __future__ import annotations

from basketball_scout.stats.possession import build_possessions
from basketball_scout.stats.scoring_sources import build_fast_break_profile
from basketball_scout.stats.scouting_features import (
    build_possession_facts,
    build_shot_facts,
    build_shot_zone_distribution,
    build_team_scouting_summary,
    build_transition_shot_facts,
    team_id_map_from_game_info,
)

GAME_ID = "segev:999"
TEAM_ID_MAP = {"home": "segev:2", "away": "segev:4"}


def _shot(id_, quarter, team, clock, coord=(750.0, 175.0), points=2, shot_type="jump-shot",
          made="made", fast_break=False, player=None):
    return {
        "id": id_, "quarter": quarter, "type": "shot", "quarterTime": clock, "playerId": player,
        "parameters": {
            "team": team, "coordX": coord[0], "coordY": coord[1], "points": points,
            "type": shot_type, "made": made, "fastBreak": fast_break,
        },
    }


def _assist(id_, quarter, team, clock, parent_id):
    return {
        "id": id_, "quarter": quarter, "type": "assist", "quarterTime": clock,
        "parameters": {"team": team}, "parentActionId": parent_id,
    }


def _rebound(id_, quarter, team, clock, kind="defensive"):
    return {"id": id_, "quarter": quarter, "type": "rebound", "quarterTime": clock,
            "parameters": {"team": team, "type": kind}}


def _turnover(id_, quarter, team, clock):
    return {"id": id_, "quarter": quarter, "type": "turnover", "quarterTime": clock,
            "parameters": {"team": team}}


def _foul(id_, quarter, team, clock, fouled_on, free_throws):
    return {"id": id_, "quarter": quarter, "type": "foul", "quarterTime": clock,
            "parameters": {"team": team, "kind": "shooting", "fouledOn": fouled_on, "freeThrows": free_throws}}


def _free_throw(id_, quarter, team, clock, number, awarded, made="made"):
    return {"id": id_, "quarter": quarter, "type": "freeThrow", "quarterTime": clock,
            "parameters": {"team": team, "made": made, "freeThrowNumber": number, "freeThrowsAwarded": awarded}}


# ---- team_id_map ------------------------------------------------------

def test_team_id_map_matches_engine_convention():
    game_info = {"homeTeam": {"id": "2"}, "awayTeam": {"id": "4"}}
    result = team_id_map_from_game_info(game_info, source_provider="segev")
    assert result == {"home": "segev:2", "away": "segev:4"}


# ---- shot facts: identity, scoring, joinability ------------------------

def test_shot_fact_identity_and_join_keys():
    actions = [_shot(1, 1, 1, "09:00", player=1043)]
    facts = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)
    assert len(facts) == 1
    f = facts[0]
    assert f.game_id == GAME_ID
    assert f.action_id == 1
    assert f.event_id == f"{GAME_ID}:1"
    assert f.team_id == "segev:2"
    assert f.opponent_id == "segev:4"
    assert f.player_id == 1043
    assert f.quarter == 1


def test_official_points_always_authoritative_in_shot_fact():
    """A shot geometrically far beyond the arc but officially 2PT must still
    report official_points=2 — geometry never overrides scoring."""
    actions = [_shot(1, 1, 1, "09:00", coord=(750.0, 1200.0), points=2)]
    f = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)[0]
    assert f.official_points == 2


def test_made_missed_blocked_are_mutually_exclusive():
    made = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", made="made")], team_id_map=TEAM_ID_MAP)[0]
    missed = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", made="missed")], team_id_map=TEAM_ID_MAP)[0]
    blocked = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", made="blocked")], team_id_map=TEAM_ID_MAP)[0]
    assert (made.made, made.missed, made.blocked) == (True, False, False)
    assert (missed.made, missed.missed, missed.blocked) == (False, True, False)
    assert (blocked.made, blocked.missed, blocked.blocked) == (False, True, True)


def test_shot_with_no_team_is_dropped_not_guessed():
    action = _shot(1, 1, 1, "09:00")
    action["parameters"]["team"] = None
    facts = build_shot_facts(GAME_ID, [action], team_id_map=TEAM_ID_MAP)
    assert facts == []


def test_missing_coordinates_produce_honest_none_zone_not_a_guess():
    action = _shot(1, 1, 1, "09:00")
    action["parameters"]["coordX"] = None
    action["parameters"]["coordY"] = None
    f = build_shot_facts(GAME_ID, [action], team_id_map=TEAM_ID_MAP)[0]
    assert f.coarse_shot_zone is None
    assert f.shot_distance_m is None
    assert f.distance_eligibility == "uncertain"


def test_coarse_shot_zone_and_rim_attempt_from_geometry():
    dunk = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", coord=(750.0, 175.0), shot_type="dunk")],
                             team_id_map=TEAM_ID_MAP)[0]
    assert dunk.coarse_shot_zone == "lane_2pt"
    assert dunk.rim_attempt is True


# ---- and-1 (reused, not reimplemented) ---------------------------------

def test_and_one_detected_via_shared_possession_logic():
    actions = [
        _shot(1, 1, 1, "09:00", made="made", points=2),
        _foul(2, 1, 2, "08:58", fouled_on=None, free_throws=1),
    ]
    # fouledOn must match the shooter's player field for and-1 detection;
    # possession.py compares str(fouledOn) to str(shot.parameters.player).
    actions[0]["parameters"]["player"] = "7"
    actions[1]["parameters"]["fouledOn"] = "7"
    facts = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)
    assert facts[0].and_one is True


def test_no_and_one_for_ordinary_made_shot():
    f = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", made="made")], team_id_map=TEAM_ID_MAP)[0]
    assert f.and_one is False


# ---- official assist ----------------------------------------------------

def test_official_assist_true_when_linked():
    actions = [_shot(1, 1, 1, "09:00", made="made"), _assist(2, 1, 1, "09:00", parent_id=1)]
    f = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)[0]
    assert f.official_assist is True


def test_official_assist_false_when_no_linked_assist_action():
    f = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", made="made")], team_id_map=TEAM_ID_MAP)[0]
    assert f.official_assist is False


# ---- fast break (provider flag, never becomes half_court) ---------------

def test_fast_break_true_reflects_provider_flag():
    actions = [_rebound(1, 1, 1, "10:00"), _shot(2, 1, 1, "09:55", fast_break=True)]
    f = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)[0]
    assert f.fast_break is True


def test_fast_break_false_does_not_imply_half_court_field_exists():
    """Structural guard: DeterministicShotFact has no half_court/defense_set
    field for a fast_break=False shot to be (mis)read as."""
    f = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00", fast_break=False)], team_id_map=TEAM_ID_MAP)[0]
    assert f.fast_break is False
    field_names = set(f.to_dict().keys())
    assert "half_court" not in field_names
    assert "defense_set" not in field_names
    assert "possession_type" not in field_names


# ---- provenance -----------------------------------------------------------

def test_shot_fact_provenance_states_are_distinct_by_field():
    f = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00")], team_id_map=TEAM_ID_MAP)[0]
    p = f.provenance
    assert p.scoring.validation_state == "provider_fact"
    assert p.shot_zone.validation_state == "provisional_deterministic"
    assert p.distance.validation_state == "partial"
    assert p.fast_break.validation_state == "validated_deterministic"
    assert p.assist.validation_state == "provider_fact"


def test_provenance_serializes_with_limitations():
    f = build_shot_facts(GAME_ID, [_shot(1, 1, 1, "09:00")], team_id_map=TEAM_ID_MAP)[0]
    d = f.to_dict()
    assert isinstance(d["provenance"]["shot_zone"]["limitations"], list)
    assert len(d["provenance"]["shot_zone"]["limitations"]) > 0


# ---- possession facts -----------------------------------------------------

def test_possession_fact_scored_and_points():
    actions = [_shot(1, 1, 1, "09:00", made="made", points=2)]
    result = build_possessions(actions, regulation_periods=4)
    facts = build_possession_facts(GAME_ID, result.possessions, team_id_map=TEAM_ID_MAP)
    assert len(facts) == 1
    assert facts[0].possession_scored is True
    assert facts[0].possession_points == 2
    assert facts[0].ended_by == "made_fg"


def test_possession_fact_turnover():
    actions = [_turnover(1, 1, 1, "09:00")]
    result = build_possessions(actions, regulation_periods=4)
    facts = build_possession_facts(GAME_ID, result.possessions, team_id_map=TEAM_ID_MAP)
    assert facts[0].turnover is True
    assert facts[0].possession_scored is False


def test_possession_fact_id_and_team_join_keys():
    actions = [_shot(1, 1, 1, "09:00", made="made")]
    result = build_possessions(actions, regulation_periods=4)
    facts = build_possession_facts(GAME_ID, result.possessions, team_id_map=TEAM_ID_MAP)
    assert facts[0].possession_id == f"{GAME_ID}:0"
    assert facts[0].offense_team_id == "segev:2"
    assert facts[0].defense_team_id == "segev:4"


def test_possession_fact_duration_uses_countdown_clock():
    actions = [_shot(1, 1, 1, "09:00", made="made")]
    result = build_possessions(actions, regulation_periods=4)
    facts = build_possession_facts(GAME_ID, result.possessions, team_id_map=TEAM_ID_MAP)
    assert facts[0].start_clock_s == 540.0  # 9:00
    assert facts[0].duration_s == 0.0  # single-action possession, same clock reading


def test_possession_facts_compatible_with_overtime_quarter_numbers():
    """possession.py is already quarter-number-agnostic; this just confirms
    the fact-mapping layer doesn't add an OT-specific assumption."""
    actions = [_shot(1, 5, 1, "05:00", made="made")]  # quarter 5 = OT1
    result = build_possessions(actions, regulation_periods=4)
    facts = build_possession_facts(GAME_ID, result.possessions, team_id_map=TEAM_ID_MAP)
    assert facts[0].quarter == 5
    assert facts[0].possession_scored is True


# ---- aggregations: zone distribution, transition, no divide-by-zero ------

def test_shot_zone_distribution_counts_and_shares():
    actions = [
        _shot(1, 1, 1, "09:00", coord=(750.0, 175.0), points=2, shot_type="dunk"),   # lane_2pt
        _shot(2, 1, 1, "08:00", coord=(750.0, 950.0), points=3, shot_type="jump-shot"),  # atb_3
    ]
    facts = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)
    dist = build_shot_zone_distribution(facts)
    assert dist.lane_2pt == 1
    assert dist.atb_3 == 1
    assert dist.fga_total == 2
    assert dist.lane_2pt_share == 0.5
    assert dist.atb_3_share == 0.5


def test_shot_zone_distribution_empty_list_no_divide_by_zero():
    dist = build_shot_zone_distribution([])
    assert dist.fga_total == 0
    assert dist.lane_2pt_share is None


def test_transition_shot_facts_rate_and_fg_pct():
    actions = [
        _shot(1, 1, 1, "10:00", made="made", fast_break=True, points=2),
        _shot(2, 1, 1, "08:00", made="missed", fast_break=False, points=2),
    ]
    facts = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)
    transition = build_transition_shot_facts(facts)
    assert transition.fast_break_fga == 1
    assert transition.fast_break_fgm == 1
    assert transition.fast_break_points == 2
    assert transition.fast_break_fga_rate == 0.5
    assert transition.fast_break_fg_pct == 1.0


def test_transition_shot_facts_empty_list_no_divide_by_zero():
    transition = build_transition_shot_facts([])
    assert transition.fast_break_fga_rate is None
    assert transition.fast_break_fg_pct is None


# ---- team scouting summary: reuse, not recomputation ----------------------

def test_team_scouting_summary_reuses_passed_in_profile_not_recomputed():
    actions = [_shot(1, 1, 1, "10:00", made="made", fast_break=True, points=2)]
    shot_facts = build_shot_facts(GAME_ID, actions, team_id_map=TEAM_ID_MAP)
    poss_result = build_possessions(actions, regulation_periods=4)
    poss_facts = build_possession_facts(GAME_ID, poss_result.possessions, team_id_map=TEAM_ID_MAP)
    fb_profile = build_fast_break_profile(poss_result.possessions, games_n=1)

    summary = build_team_scouting_summary(
        "segev:2", shot_facts, poss_facts, opponent_id="segev:4", game_id=GAME_ID,
        provider_fast_break=fb_profile,
    )
    assert summary.provider_fast_break is fb_profile  # same object, not rebuilt
    assert summary.fga == 1
    assert summary.possessions_n == 1
    assert summary.scoring_possessions_n == 1


def test_team_scouting_summary_no_divide_by_zero_on_empty_game():
    summary = build_team_scouting_summary("segev:2", [], [])
    assert summary.fga == 0
    assert summary.fg3a_rate is None
    assert summary.efg_pct is None
    assert summary.possessions_n == 0
