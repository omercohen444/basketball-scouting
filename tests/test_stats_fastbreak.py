"""Per-shot fast-break diagnostics: provider flag handling, possession-change
timing, defensive rebound / turnover / opponent-score boundaries, and the
binding semantic that a provider-negative is never "defense set"."""

from __future__ import annotations

from basketball_scout.stats.fastbreak import (
    build_fastbreak_events,
    classify,
)


def _shot(id_, quarter, team, clock, fast_break, made="made", coord=(750.0, 175.0)):
    return {
        "id": id_,
        "quarter": quarter,
        "type": "shot",
        "quarterTime": clock,
        "parameters": {
            "team": team,
            "fastBreak": fast_break,
            "made": made,
            "coordX": coord[0],
            "coordY": coord[1],
        },
    }


def _rebound(id_, quarter, team, clock, kind="defensive"):
    return {
        "id": id_,
        "quarter": quarter,
        "type": "rebound",
        "quarterTime": clock,
        "parameters": {"team": team, "type": kind},
    }


def _turnover(id_, quarter, team, clock):
    return {
        "id": id_,
        "quarter": quarter,
        "type": "turnover",
        "quarterTime": clock,
        "parameters": {"team": team},
    }


def _quarter_marker(id_, quarter, clock, kind):
    return {"id": id_, "quarter": quarter, "type": "quarter", "quarterTime": clock, "parameters": {"type": kind}}


def _free_throw(id_, quarter, team, clock, number, awarded, fast_break, made="made"):
    return {
        "id": id_,
        "quarter": quarter,
        "type": "freeThrow",
        "quarterTime": clock,
        "parameters": {
            "team": team,
            "fastBreak": fast_break,
            "made": made,
            "freeThrowNumber": number,
            "freeThrowsAwarded": awarded,
        },
    }


# ---- provider flag handling --------------------------------------------

def test_provider_true_classifies_fast_break():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=True),
    ])
    shot = events[-1]
    assert shot.provider_fast_break is True
    assert classify(shot).is_fast_break is True


def test_provider_false_classifies_non_fast_break():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=False),
    ])
    shot = events[-1]
    assert classify(shot).is_fast_break is False


def test_provider_missing_field_is_none_not_false():
    action = _shot(1, 1, 1, "10:00", fast_break=True)
    del action["parameters"]["fastBreak"]
    events = build_fastbreak_events("g1", [action])
    assert events[0].provider_fast_break is None


def test_provider_missing_field_still_classifies_as_non_fast_break():
    """Missing is never a confident positive — classify() treats it the same
    bucket as False (bool(None) is False), matching brief semantics: the
    only positive claim is an explicit True."""
    action = _shot(1, 1, 1, "10:00", fast_break=True)
    del action["parameters"]["fastBreak"]
    events = build_fastbreak_events("g1", [action])
    assert classify(events[0]).is_fast_break is False


# ---- possession-change boundary + timing --------------------------------

def test_defensive_rebound_sets_boundary_and_elapsed_is_measured():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=True),
    ])
    shot = events[-1]
    assert shot.is_first_attempt_of_possession is True
    assert shot.possession_change_type == "defensive_rebound"
    assert shot.elapsed_since_possession_change_s == 5.0


def test_turnover_sets_boundary_to_other_team():
    events = build_fastbreak_events("g1", [
        _turnover(1, 1, 2, "08:00"),  # team 2 (away) turns it over
        _shot(2, 1, 1, "07:54", fast_break=True),  # team 1 (home) scores off it
    ])
    shot = events[-1]
    assert shot.possession_change_type == "opponent_turnover"
    assert shot.elapsed_since_possession_change_s == 6.0


def test_opponent_made_basket_sets_new_boundary():
    events = build_fastbreak_events("g1", [
        _shot(1, 1, 1, "10:00", fast_break=False, made="made"),
        _shot(2, 1, 2, "09:50", fast_break=True),
    ])
    shot = events[-1]
    assert shot.possession_change_type == "opponent_score"
    assert shot.elapsed_since_possession_change_s == 10.0


def test_second_attempt_after_offensive_rebound_is_not_a_candidate():
    """A shot by the SAME team that already consumed the boundary (e.g. a
    putback after their own offensive rebound) must not be flagged as a
    fast-break candidate — the boundary is consumed by the first attempt."""
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=False),  # first attempt, consumes boundary
        _rebound(3, 1, 1, "09:53", kind="offensive"),  # offensive rebound: not a defensive_rebound boundary
        _shot(4, 1, 1, "09:50", fast_break=True),  # putback
    ])
    putback = events[-1]
    assert putback.is_first_attempt_of_possession is False
    assert putback.elapsed_since_possession_change_s is None
    assert putback.possession_change_type is None


def test_quarter_start_sets_boundary_with_no_team_until_first_action():
    events = build_fastbreak_events("g1", [
        _quarter_marker(1, 2, "10:00", "start-of-quarter"),
        _shot(2, 2, 1, "09:58", fast_break=False),
    ])
    shot = events[-1]
    # team unknown at quarter-start, so boundary_team is None -> never "first"
    assert shot.is_first_attempt_of_possession is False


def test_end_of_quarter_clears_boundary():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "01:00"),
        _quarter_marker(2, 1, "00:00", "end-of-quarter"),
        _shot(3, 2, 1, "10:00", fast_break=True),
    ])
    shot = events[-1]
    assert shot.is_first_attempt_of_possession is False
    assert shot.elapsed_since_possession_change_s is None


# ---- free throws: only final FT of a trip is evaluated -------------------

def test_free_throw_non_final_is_ignored():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _free_throw(2, 1, 1, "09:55", number=1, awarded=2, fast_break=False),
    ])
    assert events == []


def test_free_throw_final_is_evaluated():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _free_throw(2, 1, 1, "09:55", number=2, awarded=2, fast_break=True),
    ])
    assert len(events) == 1
    assert events[0].action_type == "freeThrow"
    assert events[0].is_first_attempt_of_possession is True


def test_free_throw_made_sets_opponent_score_boundary():
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _free_throw(2, 1, 1, "09:55", number=1, awarded=1, fast_break=False, made="made"),
        _shot(3, 1, 2, "09:50", fast_break=True),
    ])
    shot = events[-1]
    assert shot.possession_change_type == "opponent_score"


# ---- deterministic output stability --------------------------------------

def test_build_fastbreak_events_is_deterministic_across_runs():
    actions = [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=True),
        _turnover(3, 1, 1, "09:40"),
        _shot(4, 1, 2, "09:33", fast_break=False),
    ]
    first = [e.to_dict() for e in build_fastbreak_events("g1", actions)]
    second = [e.to_dict() for e in build_fastbreak_events("g1", list(reversed(actions)))]
    assert first == second


def test_classify_never_derives_defense_set_semantics():
    """Structural guard: FastBreakClassification exposes only is_fast_break
    plus supporting diagnostic context — no half_court/defense_set field
    exists to be (mis)populated."""
    events = build_fastbreak_events("g1", [
        _rebound(1, 1, 1, "10:00"),
        _shot(2, 1, 1, "09:55", fast_break=False),
    ])
    result = classify(events[0]).to_dict()
    assert set(result.keys()) == {"is_fast_break", "elapsed_since_possession_change_s", "possession_change_type"}
