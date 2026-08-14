#!/usr/bin/env python
"""Classify ONE localized shot event from a YouTube video with Gemini.

This is the Gate 1 / Gate 2 harness: it tests the link

    PBP event -> video time window -> Gemini classification -> structured result

for a single event at a time, so each result can be inspected and argued about
before anything is run in bulk.

It deliberately does NOT process a full game. Fixture mode is capped by
``--limit`` (default 1) so a bulk run has to be asked for explicitly.

Usage
-----
Inspect the outgoing request without spending an API call (no key needed):

    python scripts/spikes/gemini_video_event.py --dry-run \
        --url "https://www.youtube.com/watch?v=XXXX" --at 1:12:30

Verify which model ids this key can actually use:

    python scripts/spikes/gemini_video_event.py --list-models

Classify one ad-hoc event:

    python scripts/spikes/gemini_video_event.py \
        --url "https://www.youtube.com/watch?v=XXXX" --at 1:12:30 \
        --event-id G001-E017 --period 3 --clock 04:12 --team "Team A"

Classify events from the ground-truth fixture and write model labels back:

    python scripts/spikes/gemini_video_event.py --from-fixture --event-id G001-E017 --update-fixture
    python scripts/spikes/gemini_video_event.py --from-fixture --limit 5 --update-fixture

Show Gate 1 agreement (no API calls):

    python scripts/spikes/gemini_video_event.py --agreement

Exit codes: 0 ok, 1 a classification failed, 2 bad usage/config.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.config import ConfigError, load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402
from basketball_scout.video.events import ShotEvent, VideoWindow, parse_timecode, window_around  # noqa: E402
from basketball_scout.video.ground_truth import (  # noqa: E402
    GroundTruthError,
    agreement,
    load_rows,
    write_rows,
)
from basketball_scout.video.metrics import DEFAULT_METRICS, MetricError, select_metrics  # noqa: E402
from basketball_scout.video.schema import ClassifiedEvent  # noqa: E402

FIXTURE_NAME = "video_events_ground_truth.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify one localized shot event from a YouTube video with Gemini.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_argument_group("mode")
    mode.add_argument("--dry-run", action="store_true", help="Build and print the request; no API call.")
    mode.add_argument("--list-models", action="store_true", help="List model ids available to this key.")
    mode.add_argument("--agreement", action="store_true", help="Print Gate 1 agreement from the fixture.")
    mode.add_argument("--from-fixture", action="store_true", help="Take events from the ground-truth CSV.")

    event = parser.add_argument_group("ad-hoc event")
    event.add_argument("--url", help="Full-game YouTube URL.")
    event.add_argument("--at", help="Shot moment in the video: 754, 754s, 12:34 or 1:02:03.")
    event.add_argument("--start", help="Explicit window start (overrides --at).")
    event.add_argument("--end", help="Explicit window end (overrides --at).")
    event.add_argument("--pre-roll", type=float, default=8.0, help="Seconds before --at (default: 8).")
    event.add_argument("--post-roll", type=float, default=4.0, help="Seconds after --at (default: 4).")
    event.add_argument("--event-id", help="Event id. In fixture mode, selects the row.")
    event.add_argument("--game-id", default="", help="Source game id.")
    event.add_argument("--team", default="", help="Shooting team.")
    event.add_argument("--period", type=int, help="Quarter number.")
    event.add_argument("--clock", default="", help="Game clock at the event.")
    event.add_argument("--pbp", default="", help="Raw play-by-play description.")

    run = parser.add_argument_group("run")
    run.add_argument("--metrics", help="Comma-separated metric keys (default: all).")
    run.add_argument("--model", help="Override GEMINI_VIDEO_MODEL.")
    run.add_argument("--fps", type=float, help="Frames per second sampled from the window.")
    run.add_argument(
        "--media-resolution", choices=["LOW", "MEDIUM", "HIGH"], help="Media resolution hint."
    )
    run.add_argument(
        "--mime-type",
        help="Override file_data mime_type (default: unset, which is correct for YouTube URLs).",
    )
    run.add_argument("--limit", type=int, default=1, help="Max fixture events per run (default: 1).")
    run.add_argument("--fixture", help=f"Path to the fixture (default: data/validation/{FIXTURE_NAME}).")
    run.add_argument("--update-fixture", action="store_true", help="Write model labels back to the CSV.")
    run.add_argument("--save", action="store_true", help="Save each result JSON under data/validation/runs/.")
    return parser


def _fixture_path(args: argparse.Namespace, settings) -> Path:
    return Path(args.fixture) if args.fixture else settings.validation_dir / FIXTURE_NAME


def _ad_hoc_event(args: argparse.Namespace) -> ShotEvent:
    if not args.url:
        raise SystemExit("--url is required (or use --from-fixture)")

    if args.start and args.end:
        window = VideoWindow(
            video_url=args.url,
            start_seconds=parse_timecode(args.start),
            end_seconds=parse_timecode(args.end),
        )
    elif args.at:
        window = window_around(
            args.url, args.at, pre_roll=args.pre_roll, post_roll=args.post_roll
        )
    else:
        raise SystemExit("give --at, or both --start and --end")

    return ShotEvent(
        event_id=args.event_id or f"adhoc-{int(window.start_seconds)}",
        window=window,
        game_id=args.game_id,
        team=args.team,
        period=args.period,
        game_clock=args.clock,
        description=args.pbp,
    )


def _report(result: ClassifiedEvent) -> None:
    print(f"\n=== {result.event_id} ===")
    print(f"window : {result.start_seconds:.0f}s -> {result.end_seconds:.0f}s")
    print(f"model  : {result.model}   latency: {result.latency_seconds}s")
    if result.error:
        print(f"ERROR  : {result.error}")
        if result.raw_text:
            print(f"raw    : {result.raw_text[:500]}")
        return
    for key, outcome in result.outcomes.items():
        print(f"  {key:<18} {outcome.label:<16} conf={outcome.confidence:.2f}")
        if outcome.evidence:
            print(f"  {'':<18} evidence: {outcome.evidence}")


def _save_result(result: ClassifiedEvent, settings) -> Path:
    out_dir = settings.validation_dir / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in result.event_id)
    path = out_dir / f"{stamp}_{safe_id}.json"
    path.write_text(
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _print_agreement(fixture: Path, metrics) -> int:
    rows = load_rows(fixture, metrics)
    print(f"{fixture}  ({len(rows)} rows)\n")
    if not rows:
        print("No labelled events yet. See data/validation/README.md for the protocol.")
        return 0
    for result in agreement(rows, metrics).values():
        print(result.summary_line())
    print(
        "\n'decisive' excludes pairs where either side answered 'uncertain'.\n"
        "~20 events measures feasibility, not accuracy."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    enable_system_trust_store()  # see basketball_scout.net — required on this machine

    try:
        settings = load_settings().with_overrides(
            gemini_video_model=args.model,
            gemini_video_fps=args.fps,
            gemini_media_resolution=args.media_resolution,
        )
        metrics = select_metrics(
            [m.strip() for m in args.metrics.split(",")] if args.metrics else None
        )
    except (ConfigError, MetricError) as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    fixture = _fixture_path(args, settings)

    if args.agreement:
        try:
            return _print_agreement(fixture, metrics)
        except GroundTruthError as exc:
            print(f"FIXTURE ERROR: {exc}", file=sys.stderr)
            return 2

    # Imported here so --agreement and --help work without the SDK installed.
    from basketball_scout.video.gemini_client import (
        GeminiVideoClassifier,
        ProviderError,
        build_request,
    )

    if args.list_models:
        try:
            models = GeminiVideoClassifier(settings).list_models()
        except (ConfigError, ProviderError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"{len(models)} models visible to this key:")
        for name in sorted(models):
            marker = "  <-- configured" if settings.gemini_video_model in name else ""
            print(f"  {name}{marker}")
        return 0

    # ---- Collect the events to run -------------------------------------
    rows = []
    if args.from_fixture:
        try:
            rows = load_rows(fixture, metrics)
        except GroundTruthError as exc:
            print(f"FIXTURE ERROR: {exc}", file=sys.stderr)
            return 2
        if args.event_id:
            rows = [r for r in rows if r.event_id == args.event_id]
            if not rows:
                print(f"event_id {args.event_id!r} not found in {fixture}", file=sys.stderr)
                return 2
        if not rows:
            print(
                f"{fixture} has no events yet. Add rows first — see "
                "data/validation/README.md.",
                file=sys.stderr,
            )
            return 2
        if args.limit and len(rows) > args.limit:
            print(f"Limiting to {args.limit} of {len(rows)} events (raise with --limit).")
            rows = rows[: args.limit]
        try:
            events = [r.to_shot_event(pre_roll=args.pre_roll, post_roll=args.post_roll) for r in rows]
        except (GroundTruthError, ValueError) as exc:
            print(f"FIXTURE ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        events = [_ad_hoc_event(args)]

    # ---- Dry run: show the request, spend nothing ----------------------
    if args.dry_run:
        for event in events:
            request = build_request(event, settings, metrics, mime_type=args.mime_type)
            print(f"\n=== DRY RUN: {event.event_id} ===")
            print(json.dumps(request.debug_dict(), indent=2, ensure_ascii=False))
        print("\nNo API call made. Drop --dry-run to classify for real.")
        return 0

    # ---- Real classification -------------------------------------------
    try:
        classifier = GeminiVideoClassifier(settings)
    except (ConfigError, ProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failures = 0
    results: list[ClassifiedEvent] = []
    for event in events:
        result = classifier.classify(event, metrics, mime_type=args.mime_type)
        results.append(result)
        _report(result)
        failures += bool(result.error)
        if args.save:
            print(f"  saved: {_save_result(result, settings)}")

    if args.update_fixture and args.from_fixture:
        # Always round-trip the fixture through the FULL registry, never the
        # run's --metrics subset: writing a subset would drop the other
        # metrics' columns and destroy hand-entered human labels.
        by_id = {r.event_id: r for r in results}
        all_rows = load_rows(fixture, DEFAULT_METRICS)
        for row in all_rows:
            result = by_id.get(row.event_id)
            if result and result.ok:
                row.model.update({k: o.label for k, o in result.outcomes.items()})
        write_rows(fixture, all_rows, DEFAULT_METRICS)
        print(f"\nUpdated model labels in {fixture}")
        _print_agreement(fixture, metrics)

    print(f"\n{len(results) - failures}/{len(results)} classified successfully.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
