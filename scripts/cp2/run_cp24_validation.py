"""CP2.4 deterministic PBP validation runner (coords + fastbreak).

Reproducible, offline (cached Segev JSON only, no network/model calls).
Regenerates the exact evidence backing:
  artifacts/cp2/coords/coordinate_validation.json
  artifacts/cp2/coords/coordinate_validation_report.md   (numbers only; prose is hand-written)
  artifacts/cp2/fastbreak/fastbreak_validation.json

Run:
    .venv\\Scripts\\python.exe scripts\\cp2\\run_cp24_validation.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from basketball_scout.pbp.geometry import (
    ARC_AMBIGUITY_BAND_M,
    ARC_RADIUS_M,
    basket_distance_m,
    classify_coarse_zone,
    coordinate_implied_is_three,
    is_rim_attempt,
    normalize_coordinate,
)
from basketball_scout.stats.fastbreak import build_fastbreak_events, classify

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_PBP_DIR = REPO_ROOT / "data" / "raw" / "pbp"

COORD_GAME_IDS = ["136", "50", "55", "60", "73", "100", "150", "200"]
FASTBREAK_GAME_IDS = ["136", "50", "55", "60", "73", "100", "150", "200", "178", "209"]


def load_game(game_id: str) -> dict:
    path = RAW_PBP_DIR / f"segev_{game_id}.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


# ---------------------------------------------------------------- coords ---

def run_coordinate_validation() -> dict:
    per_shot_type: dict[str, list[float]] = {}
    zone_counts: dict[str, int] = {}
    eligibility_counts: dict[str, int] = {}
    rim_count = 0
    total_shots = 0
    games_used = []
    home_teams = set()

    agree = 0
    disagree = 0
    ambiguous = 0
    disagreement_examples_corner = []
    disagreement_examples_non_corner = []
    disagreement_corner_like = 0
    disagreement_non_corner = 0
    disagreement_official_points_counts: dict[str, int] = {}

    by_side: dict[str, list[float]] = {"home": [], "away": []}
    by_quarter: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}

    for gid in COORD_GAME_IDS:
        game = load_game(gid)
        home_name = game["gameInfo"]["homeTeam"]["name"]
        home_teams.add(home_name)
        n_shots_this_game = 0
        for action in game["actions"]:
            if action.get("type") != "shot":
                continue
            params = action.get("parameters") or {}
            raw_x, raw_y = params.get("coordX"), params.get("coordY")
            coord = normalize_coordinate(raw_x, raw_y)
            if coord is None:
                continue
            n_shots_this_game += 1
            total_shots += 1
            official_points = params.get("points")
            shot_type = params.get("type")
            distance = basket_distance_m(coord)

            if shot_type == "jump-shot":
                bucket = "2pt_jumper" if official_points == 2 else "3pt_jumper" if official_points == 3 else "jump-shot_other"
            else:
                bucket = shot_type or "unknown"
            per_shot_type.setdefault(bucket, []).append(distance)
            if is_rim_attempt(shot_type):
                rim_count += 1

            zone = classify_coarse_zone(coord, official_points)
            zone_counts[zone or "none"] = zone_counts.get(zone or "none", 0) + 1

            from basketball_scout.pbp.geometry import distance_eligibility
            elig = distance_eligibility(official_points, distance)
            eligibility_counts[elig] = eligibility_counts.get(elig, 0) + 1

            implied_three = coordinate_implied_is_three(distance)
            if implied_three is None:
                ambiguous += 1
            else:
                official_is_three = official_points == 3
                if implied_three == official_is_three:
                    agree += 1
                else:
                    disagree += 1
                    sideline_dist = min(coord.x_m, 15.0 - coord.x_m)
                    is_corner_like = sideline_dist <= 2.2 and coord.y_m <= 4.0
                    if is_corner_like:
                        disagreement_corner_like += 1
                    else:
                        disagreement_non_corner += 1
                    key = str(official_points)
                    disagreement_official_points_counts[key] = disagreement_official_points_counts.get(key, 0) + 1
                    example = {
                        "game_id": gid, "action_id": action["id"],
                        "official_points": official_points,
                        "distance_m": round(distance, 3),
                        "x_m": round(coord.x_m, 3), "y_m": round(coord.y_m, 3),
                        "sideline_dist_m": round(sideline_dist, 3),
                        "corner_like": is_corner_like,
                    }
                    if is_corner_like and len(disagreement_examples_corner) < 8:
                        disagreement_examples_corner.append(example)
                    elif not is_corner_like and len(disagreement_examples_non_corner) < 8:
                        disagreement_examples_non_corner.append(example)

            side = "home" if params.get("team") == 1 else "away" if params.get("team") == 2 else None
            if side and shot_type == "lay-up":
                by_side[side].append(distance)
            q = action.get("quarter")
            if q in by_quarter and shot_type == "lay-up":
                by_quarter[q].append(distance)

        games_used.append({"game_id": gid, "home_team": home_name, "shots_with_coords": n_shots_this_game})

    shot_type_stats = {}
    for stype, dists in per_shot_type.items():
        shot_type_stats[stype] = {
            "n": len(dists),
            "median_m": round(statistics.median(dists), 3),
            "mean_m": round(statistics.mean(dists), 3),
            "pct_le_1_5m": round(sum(1 for d in dists if d <= 1.5) / len(dists) * 100, 1),
            "pct_ge_6_0m": round(sum(1 for d in dists if d >= 6.0) / len(dists) * 100, 1),
        }

    total_family_checked = agree + disagree
    agreement_pct = round(agree / total_family_checked * 100, 2) if total_family_checked else None

    orientation_check = {
        "layup_median_by_side_m": {k: round(statistics.median(v), 3) if v else None for k, v in by_side.items()},
        "layup_median_by_quarter_m": {str(k): round(statistics.median(v), 3) if v else None for k, v in by_quarter.items()},
    }

    return {
        "sample": {
            "games": games_used,
            "n_games": len(COORD_GAME_IDS),
            "n_distinct_home_teams": len(home_teams),
            "total_shots_with_coords": total_shots,
        },
        "distance_by_shot_type": shot_type_stats,
        "coarse_zone_distribution": zone_counts,
        "rim_attempt_count": rim_count,
        "distance_eligibility_distribution": eligibility_counts,
        "official_vs_geometric_family_agreement": {
            "ambiguity_band_m": ARC_AMBIGUITY_BAND_M,
            "arc_radius_m": ARC_RADIUS_M,
            "agree": agree,
            "disagree": disagree,
            "ambiguous_excluded": ambiguous,
            "agreement_pct_outside_band": agreement_pct,
            "disagreement_corner_like": disagreement_corner_like,
            "disagreement_non_corner": disagreement_non_corner,
            "disagreement_official_points_distribution": disagreement_official_points_counts,
            "disagreement_examples_corner_like": disagreement_examples_corner,
            "disagreement_examples_non_corner": disagreement_examples_non_corner,
        },
        "orientation_consistency": orientation_check,
    }


# ------------------------------------------------------------- fastbreak ---

def run_fastbreak_validation() -> dict:
    per_game = []
    all_events = []
    for gid in FASTBREAK_GAME_IDS:
        game = load_game(gid)
        events = build_fastbreak_events(gid, game["actions"])
        all_events.extend(events)
        positives = [e for e in events if classify(e).is_fast_break]
        per_game.append({
            "game_id": gid,
            "attempts": len(events),
            "positives": len(positives),
            "rate_pct": round(len(positives) / len(events) * 100, 2) if events else None,
        })

    positives = [e for e in all_events if classify(e).is_fast_break]
    negatives = [e for e in all_events if not classify(e).is_fast_break]

    pos_first = [e for e in positives if e.is_first_attempt_of_possession]
    pos_elapsed = [e.elapsed_since_possession_change_s for e in pos_first if e.elapsed_since_possession_change_s is not None]

    change_type_counts: dict[str, int] = {}
    for e in positives:
        key = e.possession_change_type if e.possession_change_type is not None else "none/not_first_attempt"
        change_type_counts[key] = change_type_counts.get(key, 0) + 1

    provider_null = [e for e in all_events if e.provider_fast_break is None]

    neg_first = [e for e in negatives if e.is_first_attempt_of_possession and e.elapsed_since_possession_change_s is not None]
    short_elapsed_negatives = [e for e in neg_first if e.elapsed_since_possession_change_s <= 4.0]
    neg_change_type_counts: dict[str, int] = {}
    for e in short_elapsed_negatives:
        key = e.possession_change_type or "none"
        neg_change_type_counts[key] = neg_change_type_counts.get(key, 0) + 1

    total_positives = len(positives)
    total_attempts = len(all_events)

    return {
        "sample": {
            "games": FASTBREAK_GAME_IDS,
            "n_games": len(FASTBREAK_GAME_IDS),
            "total_attempts": total_attempts,
        },
        "per_game": per_game,
        "pooled": {
            "positives": total_positives,
            "attempts": total_attempts,
            "prevalence_pct": round(total_positives / total_attempts * 100, 2) if total_attempts else None,
        },
        "provider_null_field_count": len(provider_null),
        "positive_first_attempt_pct": round(len(pos_first) / total_positives * 100, 2) if total_positives else None,
        "positive_elapsed_seconds": {
            "n": len(pos_elapsed),
            "median": round(statistics.median(pos_elapsed), 2) if pos_elapsed else None,
            "mean": round(statistics.mean(pos_elapsed), 2) if pos_elapsed else None,
            "max": round(max(pos_elapsed), 2) if pos_elapsed else None,
            "pct_le_8s": round(sum(1 for x in pos_elapsed if x <= 8.0) / len(pos_elapsed) * 100, 1) if pos_elapsed else None,
            "pct_le_10s_after_opp_make": None,  # see change_type-specific note in report
        },
        "positive_change_type_distribution": change_type_counts,
        "false_negative_risk": {
            "definition": "provider fastBreak=false (or missing), is_first_attempt_of_possession=True, elapsed<=4.0s",
            "count": len(short_elapsed_negatives),
            "denominator_first_attempt_negatives": len(neg_first),
            "pct": round(len(short_elapsed_negatives) / len(neg_first) * 100, 2) if neg_first else None,
            "change_type_distribution": neg_change_type_counts,
        },
    }


def main() -> None:
    coord_result = run_coordinate_validation()
    fb_result = run_fastbreak_validation()

    coords_out = REPO_ROOT / "artifacts" / "cp2" / "coords" / "coordinate_validation.json"
    coords_out.parent.mkdir(parents=True, exist_ok=True)
    coords_out.write_text(json.dumps(coord_result, indent=2), encoding="utf-8")

    fb_out = REPO_ROOT / "artifacts" / "cp2" / "fastbreak" / "fastbreak_validation.json"
    fb_out.parent.mkdir(parents=True, exist_ok=True)
    fb_out.write_text(json.dumps(fb_result, indent=2), encoding="utf-8")

    print("Wrote", coords_out)
    print("Wrote", fb_out)


if __name__ == "__main__":
    main()
