#!/usr/bin/env python
"""Fetch and cache Segev PBP for one or more games (CP1 step 1).

Usage
-----
    python scripts/fetch_pbp.py --game-id 136
    python scripts/fetch_pbp.py --game-id 136 --game-id 10 --refresh

Writes raw JSON to ``data/raw/pbp/segev_<id>.json`` (git-ignored) and prints a
summary. Idempotent: a cached game is not re-fetched unless ``--refresh``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402
from basketball_scout.pbp.segev import (  # noqa: E402
    SegevError,
    fetch_and_cache_actions,
    raw_cache_path,
    summarize,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-id", type=int, action="append", required=True, dest="game_ids",
        help="Segev numeric game id. Repeatable.",
    )
    parser.add_argument("--refresh", action="store_true", help="Bypass the cache.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    enable_system_trust_store()
    settings = load_settings()

    failures = 0
    for game_id in args.game_ids:
        try:
            result = fetch_and_cache_actions(game_id, settings, refresh=args.refresh)
        except SegevError as exc:
            print(f"game_id={game_id}: FAILED — {exc}", file=sys.stderr)
            failures += 1
            continue

        summary = summarize(result)
        cached_at = raw_cache_path(game_id, settings)
        print(f"\n=== game_id={game_id} ===")
        print(f"cache: {cached_at}")
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    print(f"\n{len(args.game_ids) - failures}/{len(args.game_ids)} fetched successfully.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
