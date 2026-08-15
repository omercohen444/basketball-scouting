"""segment_metrics.py: possession-subset metrics reconcile exactly with the
season-level engine when applied over a full game's possessions.

2026-08-15 management hardening: after the and-1 fix and the AST/TO
raw-assist-count convention change, all ten core metrics — not nine —
must reconcile exactly, since ast_to_ratio now uses the same raw
assist-action convention as boxscore.py.
"""

from __future__ import annotations

import json

import pytest

from basketball_scout.config import REPO_ROOT
from basketball_scout.stats.engine import build_team_game_stats
from basketball_scout.stats.possession import build_possessions
from basketball_scout.stats.segment_metrics import compute_segment_metrics

FIXTURE = REPO_ROOT / "data" / "validation" / "segev_game136_full.json"

_METRIC_FIELDS = (
    "offensive_rating", "defensive_rating", "net_rating", "pace",
    "efg_pct", "tov_pct", "orb_pct", "ft_rate", "fg3a_rate", "ast_to_ratio",
)


def test_all_ten_metrics_reconcile_exactly_over_a_full_game():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = build_possessions(data["actions"], regulation_periods=4)
    home_poss = [p for p in result.possessions if p.offense_team == "home"]
    away_poss = [p for p in result.possessions if p.offense_team == "away"]

    home_metrics = compute_segment_metrics(home_poss, away_poss, segment_minutes=40.0)
    away_metrics = compute_segment_metrics(away_poss, home_poss, segment_minutes=40.0)

    home_stats, away_stats = build_team_game_stats(
        {"gameInfo": data["gameInfo"], "actions": data["actions"]}, season="2025-26"
    )

    for field in _METRIC_FIELDS:
        assert getattr(home_metrics, field) == pytest.approx(getattr(home_stats.metrics, field)), field
        assert getattr(away_metrics, field) == pytest.approx(getattr(away_stats.metrics, field)), field


def test_ast_numerator_matches_raw_boxscore_assist_count():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = build_possessions(data["actions"], regulation_periods=4)
    home_poss = [p for p in result.possessions if p.offense_team == "home"]
    away_poss = [p for p in result.possessions if p.offense_team == "away"]

    # Known real values (WORKLOG.md / prior audits): 26 home, 17 away.
    assert sum(p.raw_assist_count for p in home_poss) == 26
    assert sum(p.raw_assist_count for p in away_poss) == 17
