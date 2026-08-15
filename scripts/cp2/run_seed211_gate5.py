"""CP2.4 hardening — Gate 5 (seed-211 coarse-zone agreement) evaluator.

Reads the accepted seed-211 human ground truth READ-ONLY from master commit
64b6cb8 (via `git show`, never merged into this worktree, never written to a
tracked location) and evaluates the deterministic coarse-zone classifier
against all 20 labels using this worktree's own cached game-136 PBP.

Computes BOTH:
  - "original": the pre-hardening rule (LANE_DEPTH_M, zero boundary
    tolerance) — reproduces the CP2.4 baseline result without needing to
    check out or diff any prior commit.
  - "hardened": the current `pbp/geometry.py` rule (includes
    LANE_DEPTH_BOUNDARY_TOLERANCE_M).

This keeps "preserve the original result, don't rewrite history" fully
reproducible from one script rather than a point-in-time snapshot.

Run:
    PYTHONPATH=src .venv\\Scripts\\python.exe scripts\\cp2\\run_seed211_gate5.py
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

from basketball_scout.pbp.geometry import (
    ARC_AMBIGUITY_BAND_M,
    ARC_RADIUS_M,
    BASKET_X_M,
    CORNER_BASELINE_Y_MAX_M,
    CORNER_SIDELINE_DISTANCE_MAX_M,
    COURT_WIDTH_M,
    LANE_DEPTH_M,
    LANE_DEPTH_BOUNDARY_TOLERANCE_M,
    LANE_HALF_WIDTH_M,
    basket_distance_m,
    classify_coarse_zone,
    is_rim_attempt,
    normalize_coordinate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GT_COMMIT = "64b6cb8"
GT_PATH_IN_MASTER = "artifacts/cp2/cp2_seed211_accepted_ground_truth.csv"
OUT_PATH = REPO_ROOT / "artifacts" / "cp2" / "coords" / "seed211_gate5.json"

FINE_TO_COARSE = {
    "ra": "lane_2pt",
    "paint": "lane_2pt",
    "mr": "midrange_2pt",
    "lc3": "corner_3",
    "rc3": "corner_3",
    "atb3": "atb_3",
}


def load_gt_readonly() -> tuple[str, list[dict]]:
    """`git show <commit>:<path>` — read-only retrieval, no merge, no write
    into a tracked location. Returns (raw_csv_text, parsed_rows)."""
    result = subprocess.run(
        ["git", "show", f"{GT_COMMIT}:{GT_PATH_IN_MASTER}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
    )
    raw = result.stdout
    rows = list(csv.DictReader(io.StringIO(raw)))
    return raw, rows


def load_game_136_actions() -> dict[int, dict]:
    path = REPO_ROOT / "data" / "raw" / "pbp" / "segev_136.json"
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {a["id"]: a for a in data["actions"] if a.get("type") == "shot"}


def _original_is_within_lane(coord) -> bool:
    """Pre-hardening rule: LANE_DEPTH_M with zero tolerance."""
    return abs(coord.x_m - BASKET_X_M) <= LANE_HALF_WIDTH_M and coord.y_m <= LANE_DEPTH_M


def _original_classify(coord, official_points: int) -> str | None:
    if official_points == 3:
        dist_from_sideline = min(coord.x_m, COURT_WIDTH_M - coord.x_m)
        is_corner = dist_from_sideline <= CORNER_SIDELINE_DISTANCE_MAX_M and coord.y_m <= CORNER_BASELINE_Y_MAX_M
        return "corner_3" if is_corner else "atb_3"
    if official_points == 2:
        return "lane_2pt" if _original_is_within_lane(coord) else "midrange_2pt"
    return None


def _boundary_diagnosis(coord, distance_m: float, official_points: int) -> dict:
    lane_depth_excess = coord.y_m - LANE_DEPTH_M
    lane_x_excess = abs(coord.x_m - BASKET_X_M) - LANE_HALF_WIDTH_M
    arc_excess = abs(distance_m - ARC_RADIUS_M)
    sideline_dist = min(coord.x_m, COURT_WIDTH_M - coord.x_m)
    corner_sideline_excess = sideline_dist - CORNER_SIDELINE_DISTANCE_MAX_M
    corner_baseline_excess = coord.y_m - CORNER_BASELINE_Y_MAX_M
    return {
        "near_lane_depth_boundary": abs(lane_depth_excess) <= 0.30,
        "lane_depth_excess_m": round(lane_depth_excess, 3),
        "near_lane_x_boundary": abs(lane_x_excess) <= 0.30,
        "lane_x_excess_m": round(lane_x_excess, 3),
        "near_3pt_arc": arc_excess <= ARC_AMBIGUITY_BAND_M,
        "arc_distance_excess_m": round(distance_m - ARC_RADIUS_M, 3),
        "near_corner_break": official_points == 3 and abs(corner_sideline_excess) <= 0.30,
        "corner_sideline_excess_m": round(corner_sideline_excess, 3) if official_points == 3 else None,
        "corner_baseline_excess_m": round(corner_baseline_excess, 3) if official_points == 3 else None,
    }


def evaluate(classify_fn) -> list[dict]:
    _, gt_rows = load_gt_readonly()
    actions_by_id = load_game_136_actions()
    results = []
    for row in gt_rows:
        action_id = int(row["event_id"].split(":")[1])
        fine = row["human_shot_zone"]
        human_coarse = FINE_TO_COARSE.get(fine)
        action = actions_by_id.get(action_id)
        if action is None:
            results.append({"action_id": action_id, "error": "action not found in cached game 136 PBP"})
            continue

        params = action.get("parameters") or {}
        coord = normalize_coordinate(params.get("coordX"), params.get("coordY"))
        official_points = params.get("points")
        shot_type = params.get("type")
        distance = basket_distance_m(coord) if coord is not None else None
        zone = classify_fn(coord, official_points) if coord is not None else None

        entry = {
            "action_id": action_id,
            "human_fine_zone": fine,
            "human_coarse_zone": human_coarse,
            "deterministic_zone": zone,
            "match": zone == human_coarse,
            "coordX": params.get("coordX"), "coordY": params.get("coordY"),
            "x_m": round(coord.x_m, 3) if coord else None,
            "y_m": round(coord.y_m, 3) if coord else None,
            "distance_m": round(distance, 3) if distance is not None else None,
            "official_points": official_points,
            "pbp_shot_type": shot_type,
            "is_rim_attempt": is_rim_attempt(shot_type),
        }
        if coord is not None:
            entry["boundary_diagnosis"] = _boundary_diagnosis(coord, distance, official_points)
        results.append(entry)
    return results


def summarize(results: list[dict]) -> dict:
    correct = sum(1 for r in results if r.get("match"))
    total = len(results)
    return {
        "correct": correct,
        "total": total,
        "accuracy_pct": round(correct / total * 100, 2) if total else None,
        "results": results,
        "mismatches": [r for r in results if not r.get("match")],
    }


def main() -> None:
    original = summarize(evaluate(_original_classify))
    hardened = summarize(evaluate(classify_coarse_zone))

    out = {
        "gt_source_commit": GT_COMMIT,
        "gt_path": GT_PATH_IN_MASTER,
        "gt_row_count": 20,
        "original_cp24": original,
        "cp24_hardening": hardened,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"ORIGINAL CP2.4:   {original['correct']}/{original['total']} = {original['accuracy_pct']}%  "
          f"mismatches={[m['action_id'] for m in original['mismatches']]}")
    print(f"CP2.4 HARDENING:  {hardened['correct']}/{hardened['total']} = {hardened['accuracy_pct']}%  "
          f"mismatches={[m['action_id'] for m in hardened['mismatches']]}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
