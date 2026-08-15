#!/usr/bin/env python
"""Discover and ingest Segev games into the deterministic team-game stats layer.

Two modes:

    # Explicit ids (repeatable), e.g. games already known/cached:
    python scripts/build_team_game_stats.py --game-id 136 --game-id 137 --season 2025-26

    # Bounded range scan + discovery (see stats/schedule.py for what this can
    # and cannot find):
    python scripts/build_team_game_stats.py --range 45 140 --season 2025-26

Every id is fetched (or read from the existing raw cache) via
``pbp.segev.fetch_and_cache_actions``, filtered to finished "Winner League"
games (``--competition`` to change), run through the deterministic engine,
and written to ``data/processed/stats/segev_<id>.json`` (``--stats-dir`` to
change). Not usable/skipped ids are reported, not silently dropped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402
from basketball_scout.pbp.segev import SegevError, fetch_and_cache_actions  # noqa: E402
from basketball_scout.stats.engine import EngineError, build_team_game_stats  # noqa: E402
from basketball_scout.stats.schedule import DEFAULT_COMPETITION, discover_games  # noqa: E402
from basketball_scout.stats.store import save_game  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-id", type=int, action="append", dest="game_ids", help="Explicit Segev game id. Repeatable.")
    parser.add_argument("--range", type=int, nargs=2, metavar=("START", "END"), help="Inclusive game_id range to scan.")
    parser.add_argument("--season", required=True, help='e.g. "2025-26"')
    parser.add_argument("--competition", default=DEFAULT_COMPETITION, help="Required competition.name match.")
    parser.add_argument("--stats-dir", default="data/processed/stats", help="Output directory for processed stats.")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds between requests during a range scan.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not args.game_ids and not args.range:
        print("Pass --game-id (repeatable) or --range START END.", file=sys.stderr)
        return 2

    enable_system_trust_store()
    settings = load_settings()
    raw_stats_dir = Path(args.stats_dir)
    stats_dir = raw_stats_dir if raw_stats_dir.is_absolute() else (settings.data_dir.parent / raw_stats_dir).resolve()

    candidate_ids: list[int] = []
    usable_ids: list[int] = []

    if args.range:
        start, end = args.range
        print(f"Scanning game_id range [{start}, {end}] (competition={args.competition!r})...")
        discovered = discover_games(
            range(start, end + 1), settings, delay_seconds=args.delay,
            on_progress=lambda gid, status: print(f"  {gid}: {status}"),
        )
        usable_ids = [g.game_id for g in discovered if g.game_finished and g.competition == args.competition]
        print(f"Discovered {len(discovered)} responsive ids, {len(usable_ids)} usable ({args.competition}, finished).")
        candidate_ids = usable_ids

    if args.game_ids:
        candidate_ids = sorted(set(candidate_ids) | set(args.game_ids))

    built = 0
    skipped = 0
    for game_id in candidate_ids:
        try:
            result = fetch_and_cache_actions(game_id, settings)
        except SegevError as exc:
            print(f"game_id={game_id}: FETCH FAILED — {exc}", file=sys.stderr)
            skipped += 1
            continue
        try:
            home, away = build_team_game_stats(result, season=args.season)
        except EngineError as exc:
            print(f"game_id={game_id}: ENGINE FAILED — {exc}", file=sys.stderr)
            skipped += 1
            continue
        path = save_game(stats_dir, home, away)
        print(f"game_id={game_id}: {home.team_name} {home.final_score_for} - "
              f"{away.final_score_for} {away.team_name} -> {path}")
        built += 1

    print(f"\n{built} game(s) written to {stats_dir}, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
