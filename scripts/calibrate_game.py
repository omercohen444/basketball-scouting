#!/usr/bin/env python
"""Interactive PBP <-> video calibration for one game (CP1-D / plan §7.5).

Workflow
--------
1. ``--propose`` prints, for each quarter, the first and last **made** field
   goal (the calibration anchor candidates) with their PBP time and a
   description, plus a predicted YouTube seek link once quarter 1 is set.
2. The operator watches the video at that point and records the observed
   video timestamp with ``--set-anchor`` (Q1) or ``--set-check`` (residual
   check on the *last* made FG of an already-anchored quarter).
3. ``--summary`` prints the current calibration quality.

This does not touch the network beyond re-reading the already-cached PBP; the
"watching the video" step is inherently human/operator work (or, in this run,
performed via the browser automation tool — see the CP1 report for how each
anchor was actually observed).

Usage
-----
    python scripts/calibrate_game.py --game-id 136 --propose
    python scripts/calibrate_game.py --game-id 136 --set-anchor 1 --action-id 1360022 \\
        --video-time 412.0 --note "Q1 first made FG"
    python scripts/calibrate_game.py --game-id 136 --set-check 1 --action-id 1360290 \\
        --video-time 1964.0
    python scripts/calibrate_game.py --game-id 136 --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.pbp.canonical import extract_shot_events  # noqa: E402
from basketball_scout.pbp.segev import raw_cache_path  # noqa: E402
from basketball_scout.video.events import format_timecode  # noqa: E402
from basketball_scout.video.manifest import (  # noqa: E402
    GameManifestEntry,
    Manifest,
    TeamRef,
    VideoRef,
    load_manifest,
    save_manifest,
)
from basketball_scout.video.sync import GameSync, QuarterAnchor, ResidualCheck  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-id", required=True, help="Internal game id in the manifest.")
    parser.add_argument("--manifest", help="Path to the manifest JSON (default: data/manifest/matchday.json).")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--propose", action="store_true", help="List anchor candidates per quarter.")
    mode.add_argument("--set-anchor", type=int, metavar="QUARTER", help="Record an anchor for this quarter.")
    mode.add_argument("--set-check", type=int, metavar="QUARTER", help="Record a residual check for this quarter.")
    mode.add_argument("--summary", action="store_true", help="Print current calibration quality.")
    mode.add_argument(
        "--set-slope", type=float, metavar="SLOPE",
        help="Override the game's slope (plan §7.5 drift case) and re-evaluate all checks.",
    )

    parser.add_argument("--action-id", type=int, help="Source action id (for --set-anchor/--set-check).")
    parser.add_argument("--video-time", type=float, help="Observed video timestamp in seconds.")
    parser.add_argument("--note", default="", help="Free-text note for an anchor.")
    return parser


def _manifest_path(args: argparse.Namespace, settings) -> Path:
    return Path(args.manifest) if args.manifest else settings.manifest_dir / "matchday.json"


def _load_shot_events(game_id: str, segev_game_id: int, settings) -> list:
    cache = raw_cache_path(segev_game_id, settings)
    if not cache.is_file():
        raise SystemExit(
            f"No cached PBP for segev_game_id={segev_game_id}. Run scripts/fetch_pbp.py first."
        )
    data = json.loads(cache.read_text(encoding="utf-8"))
    result = extract_shot_events(game_id, data.get("actions", []))
    return result.events


def cmd_propose(entry: GameManifestEntry, events: list, sync: GameSync | None) -> None:
    quarters = sorted({e.quarter for e in events})
    print(f"Video: {entry.video.url}  (duration: {entry.video.duration_s}s)")
    print(f"{len(events)} shot events across quarters {quarters}\n")

    for q in quarters:
        made = [e for e in events if e.quarter == q and e.outcome == "made"]
        if not made:
            print(f"Q{q}: no made field goals found — cannot anchor this quarter automatically.")
            continue
        first, last = made[0], made[-1]

        anchor = sync.map_to_video(q, first.user_time_s) if sync else None
        predicted = f"  predicted: {anchor:.0f}s -> {entry.video.url}&t={int(anchor)}s" if anchor else ""

        print(f"Q{q} anchor candidate (first made FG):")
        print(f"  action_id={first.source_action_id}  quarter_time={first.quarter_time}  "
              f"team={first.team_side}  jersey=#{first.player_jersey}  "
              f"{first.shot_type} {first.points}pt")
        print(f"  pbp_user_time_s={first.user_time_s}{predicted}")

        print(f"Q{q} residual-check candidate (last made FG):")
        predicted_last = sync.map_to_video(q, last.user_time_s) if sync else None
        predicted_last_s = f"  predicted: {predicted_last:.0f}s" if predicted_last else ""
        print(f"  action_id={last.source_action_id}  quarter_time={last.quarter_time}  "
              f"team={last.team_side}  jersey=#{last.player_jersey}  "
              f"{last.shot_type} {last.points}pt")
        print(f"  pbp_user_time_s={last.user_time_s}{predicted_last_s}")
        print()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings()
    manifest_path = _manifest_path(args, settings)
    manifest = load_manifest(manifest_path)

    try:
        entry = manifest.get(args.game_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    sync = entry.sync or GameSync(video_duration_s=entry.video.duration_s or 0.0)
    events = _load_shot_events(entry.game_id, entry.segev_game_id, settings)

    if args.propose:
        cmd_propose(entry, events, sync)
        return 0

    if args.summary:
        print(json.dumps(sync.summary(), indent=2, ensure_ascii=False))
        return 0

    if args.set_anchor is not None:
        if args.action_id is None or args.video_time is None:
            print("ERROR: --set-anchor requires --action-id and --video-time", file=sys.stderr)
            return 2
        source = next((e for e in events if e.source_action_id == args.action_id), None)
        if source is None:
            print(f"ERROR: action_id {args.action_id} not found in quarter events", file=sys.stderr)
            return 2
        sync.anchors.append(
            QuarterAnchor(
                quarter=args.set_anchor,
                source_action_id=args.action_id,
                pbp_user_time_s=source.user_time_s,
                video_time_s=args.video_time,
                note=args.note,
            )
        )
        entry.sync = sync
        manifest.upsert(entry)
        save_manifest(manifest_path, manifest)
        print(f"Anchor set: Q{args.set_anchor} action_id={args.action_id} "
              f"video_time_s={args.video_time}")
        print(json.dumps(sync.summary(), indent=2, ensure_ascii=False))
        return 0

    if args.set_slope is not None:
        sync.slope = args.set_slope
        # Re-derive every existing check's prediction under the new slope so
        # `--summary` reflects reality immediately, not stale residuals.
        rebuilt = []
        for check in sync.checks:
            source = next((e for e in events if e.source_action_id == check.source_action_id), None)
            if source is None:
                rebuilt.append(check)
                continue
            predicted = sync.map_to_video(check.quarter, source.user_time_s)
            if predicted is None:
                rebuilt.append(check)
                continue
            rebuilt.append(
                ResidualCheck(
                    quarter=check.quarter,
                    source_action_id=check.source_action_id,
                    predicted_video_s=predicted,
                    observed_video_s=check.observed_video_s,
                )
            )
        sync.checks = rebuilt
        entry.sync = sync
        manifest.upsert(entry)
        save_manifest(manifest_path, manifest)
        print(f"Slope set to {args.set_slope}")
        for c in sync.checks:
            print(f"  Q{c.quarter} action_id={c.source_action_id}: "
                  f"residual={c.residual_s:+.1f}s  status={c.status}")
        print(json.dumps(sync.summary(), indent=2, ensure_ascii=False))
        return 0

    if args.set_check is not None:
        if args.action_id is None or args.video_time is None:
            print("ERROR: --set-check requires --action-id and --video-time", file=sys.stderr)
            return 2
        source = next((e for e in events if e.source_action_id == args.action_id), None)
        if source is None:
            print(f"ERROR: action_id {args.action_id} not found in quarter events", file=sys.stderr)
            return 2
        predicted = sync.map_to_video(args.set_check, source.user_time_s)
        if predicted is None:
            print(f"ERROR: no anchor yet for Q{args.set_check} — set one first", file=sys.stderr)
            return 2
        check = ResidualCheck(
            quarter=args.set_check,
            source_action_id=args.action_id,
            predicted_video_s=predicted,
            observed_video_s=args.video_time,
        )
        sync.checks.append(check)
        entry.sync = sync
        manifest.upsert(entry)
        save_manifest(manifest_path, manifest)
        print(f"Check recorded: {json.dumps(check.to_dict(), ensure_ascii=False)}")
        print(f"residual={check.residual_s:+.1f}s  status={check.status}  "
              f"(predicted {format_timecode(predicted)}, observed {format_timecode(args.video_time)})")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
