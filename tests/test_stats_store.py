"""Flat-file JSON persistence round-trip for TeamGameStats."""

from __future__ import annotations

from pathlib import Path

from basketball_scout.stats.models import DerivedMetrics, TeamGameComponents, TeamGameStats
from basketball_scout.stats.store import game_file_path, load_all_games, load_game, save_game

COMPONENTS = TeamGameComponents(
    fgm=30, fga=70, fg3m=8, fg3a=25, ftm=15, fta=20, orb=10, drb=30, ast=18, tov=12, pf=16, points=83
)
METRICS = DerivedMetrics(
    offensive_rating=110.0, defensive_rating=100.0, net_rating=10.0, pace=88.0,
    efg_pct=0.5, tov_pct=0.13, orb_pct=0.28, ft_rate=0.28, fg3a_rate=0.35, ast_to_ratio=1.5,
)


def make_pair(source_game_id: str = "136") -> tuple[TeamGameStats, TeamGameStats]:
    home = TeamGameStats(
        internal_game_id=f"segev:{source_game_id}", source_provider="segev",
        source_game_id=source_game_id, season="2025-26", game_date="2026-01-11",
        team_id="segev:2", team_name="MACCABI TEL AVIV", opponent_id="segev:4",
        opponent_name="HAPOEL JERUSALEM", is_home=True, final_score_for=95,
        final_score_against=84, win=True, regulation_periods=4, ot_periods=0,
        game_minutes=40.0, possessions_for=86.3, possessions_against=85.9,
        components_for=COMPONENTS, components_against=COMPONENTS, metrics=METRICS,
        action_counts={"shot": 140},
    )
    away = TeamGameStats(
        internal_game_id=f"segev:{source_game_id}", source_provider="segev",
        source_game_id=source_game_id, season="2025-26", game_date="2026-01-11",
        team_id="segev:4", team_name="HAPOEL JERUSALEM", opponent_id="segev:2",
        opponent_name="MACCABI TEL AVIV", is_home=False, final_score_for=84,
        final_score_against=95, win=False, regulation_periods=4, ot_periods=0,
        game_minutes=40.0, possessions_for=85.9, possessions_against=86.3,
        components_for=COMPONENTS, components_against=COMPONENTS, metrics=METRICS,
        action_counts={"shot": 140},
    )
    return home, away


def test_save_and_load_single_game_round_trips(tmp_path: Path):
    home, away = make_pair()
    path = save_game(tmp_path, home, away)
    assert path == game_file_path(tmp_path, "segev", "136")
    loaded_home, loaded_away = load_game(path)
    assert loaded_home == home
    assert loaded_away == away


def test_load_all_games_returns_empty_list_for_missing_dir(tmp_path: Path):
    assert load_all_games(tmp_path / "does_not_exist") == []


def test_load_all_games_flattens_multiple_game_files(tmp_path: Path):
    save_game(tmp_path, *make_pair("136"))
    save_game(tmp_path, *make_pair("137"))
    games = load_all_games(tmp_path)
    assert len(games) == 4
    source_ids = {g.source_game_id for g in games}
    assert source_ids == {"136", "137"}


def test_save_game_is_idempotent_overwrite(tmp_path: Path):
    home, away = make_pair()
    save_game(tmp_path, home, away)
    save_game(tmp_path, home, away)  # re-running must not duplicate or error
    games = load_all_games(tmp_path)
    assert len(games) == 2
