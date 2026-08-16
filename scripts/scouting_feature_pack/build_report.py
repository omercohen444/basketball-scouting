"""Deterministic Scouting Feature Pack — reproducible example + sanity report.

Builds the pack for one cached game (136) end-to-end and writes a small,
bounded example artifact — a few sample shot facts, a few possession facts,
and both teams' full summaries. Not a raw dump of every event in every
cached game; see artifacts/scouting_feature_pack/README.md for the full
field/provenance/limitations writeup.

Run:
    PYTHONPATH=src .venv\\Scripts\\python.exe scripts\\scouting_feature_pack\\build_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

from basketball_scout.stats.enrichment import build_game_enrichment
from basketball_scout.stats.possession import build_possessions
from basketball_scout.stats.scouting_features import (
    build_possession_facts,
    build_shot_facts,
    build_team_scouting_summary,
    team_id_map_from_game_info,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GAME_ID = "136"
OUT_PATH = REPO_ROOT / "artifacts" / "scouting_feature_pack" / "example_output.json"


def main() -> None:
    raw_path = REPO_ROOT / "data" / "raw" / "pbp" / f"segev_{GAME_ID}.json"
    with raw_path.open(encoding="utf-8") as fh:
        result = json.load(fh)

    game_info = result["gameInfo"]
    actions = result["actions"]
    internal_game_id = f"segev:{GAME_ID}"
    team_id_map = team_id_map_from_game_info(game_info, source_provider="segev")

    shot_facts = build_shot_facts(internal_game_id, actions, team_id_map=team_id_map)
    poss_result = build_possessions(actions, regulation_periods=4)
    possession_facts = build_possession_facts(internal_game_id, poss_result.possessions, team_id_map=team_id_map)

    home_id, away_id = team_id_map["home"], team_id_map["away"]
    home_shots = [f for f in shot_facts if f.team_id == home_id]
    away_shots = [f for f in shot_facts if f.team_id == away_id]
    home_poss_facts = [p for p in possession_facts if p.offense_team_id == home_id]
    away_poss_facts = [p for p in possession_facts if p.offense_team_id == away_id]

    home_enrichment, away_enrichment = build_game_enrichment(
        result, internal_game_id=internal_game_id, team_id=home_id, opponent_id=away_id,
        team_side="home", win=game_info["homeTeam"].get("Score", 0) > game_info["awayTeam"].get("Score", 0),
        game_date=str(game_info.get("time") or ""), regulation_periods=4, ot_periods=0,
    )

    home_summary = build_team_scouting_summary(
        home_id, home_shots, home_poss_facts, opponent_id=away_id, game_id=internal_game_id,
        provider_fast_break=home_enrichment.fast_break, assisted=home_enrichment.assisted,
        shot_mix=home_enrichment.shot_mix, second_chance=home_enrichment.second_chance,
        points_off_turnovers=home_enrichment.points_off_turnovers,
    )
    away_summary = build_team_scouting_summary(
        away_id, away_shots, away_poss_facts, opponent_id=home_id, game_id=internal_game_id,
        provider_fast_break=away_enrichment.fast_break, assisted=away_enrichment.assisted,
        shot_mix=away_enrichment.shot_mix, second_chance=away_enrichment.second_chance,
        points_off_turnovers=away_enrichment.points_off_turnovers,
    )

    out = {
        "game_id": internal_game_id,
        "team_id_map": team_id_map,
        "counts": {
            "total_shot_facts": len(shot_facts),
            "total_possession_facts": len(possession_facts),
        },
        "example_shot_facts": [f.to_dict() for f in home_shots[:3]],
        "example_possession_facts": [p.to_dict() for p in home_poss_facts[:3]],
        "home_team_scouting_summary": home_summary.to_dict(),
        "away_team_scouting_summary": away_summary.to_dict(),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(f"shot_facts: {len(shot_facts)}  possession_facts: {len(possession_facts)}")
    print(f"home fga={home_summary.fga} efg%={home_summary.efg_pct}")
    print(f"away fga={away_summary.fga} efg%={away_summary.efg_pct}")


if __name__ == "__main__":
    main()
