#!/usr/bin/env python
"""Build the website's deterministic analytics artifacts.

    python scripts/ops/build_analytics_artifacts.py
    python scripts/ops/build_analytics_artifacts.py --check
    python scripts/ops/build_analytics_artifacts.py --out-dir data/analytics

Offline: no credentials, no network, no provider call. Reads the git-ignored
``data/processed/stats`` and ``data/raw/pbp`` caches and writes committed
artifacts, which is how a deployment gets analytics without carrying 92 MB of
play-by-play.

This is a *separate* artifact from the evidence packs and cannot affect them.
Packs feed three agents a curated 25-item slice; this feeds a browsable site
the whole grid. Sharing one artifact would mean every website need became a
change to the agent contract, and a change to the agent contract means
regenerating fourteen scouting reports.

``--check`` rebuilds in memory, compares hashes against what is committed, and
writes nothing — so a stale artifact fails visibly instead of silently serving
last week's numbers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from basketball_scout.analytics.build import (  # noqa: E402
    IncompleteLeagueError,
    build_all,
    write_all,
)
from basketball_scout.analytics.schema import INDEX_FILENAME  # noqa: E402
from basketball_scout.analytics.store import (  # noqa: E402
    AnalyticsArtifactError,
    AnalyticsStore,
    default_analytics_dir,
)
from basketball_scout.config import load_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--stats-dir", default="data/processed/stats",
                        help="per-game stats cache (default: %(default)s)")
    parser.add_argument("--out-dir", help="artifact directory (default: <DATA_DIR>/analytics)")
    parser.add_argument("--check", action="store_true",
                        help="rebuild in memory and compare hashes; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    settings = load_settings()

    stats_dir = Path(args.stats_dir)
    if not stats_dir.is_absolute():
        stats_dir = REPO_ROOT / stats_dir
    out_dir = Path(args.out_dir) if args.out_dir else default_analytics_dir(settings.data_dir)

    if not stats_dir.is_dir():
        print(f"error: no per-game stats at {stats_dir}", file=sys.stderr)
        return 1

    try:
        artifacts, index = build_all(settings, stats_dir=stats_dir)
    except IncompleteLeagueError as exc:
        # The whole point of the guard: a partial cache must not produce an
        # artifact that looks complete.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"season       : {index.season}")
    print(f"teams        : {len(artifacts)}")
    print(f"out-dir      : {out_dir}")
    print(f"mode         : {'CHECK (no write)' if args.check else 'write'}\n")

    print(f"{'team':12s} {'name':24s} {'rec':>6s} {'games':>6s} {'cells':>6s}  hash")
    for team_id in sorted(artifacts):
        team = artifacts[team_id].team
        print(f"{team_id:12s} {team.team_name[:24]:24s} {team.record:>6s} "
              f"{team.games_n:6d} {len(team.cells):6d}  {artifacts[team_id].content_hash[7:19]}…")

    if args.check:
        return _check(artifacts, out_dir)

    write_all(artifacts, index, out_dir)

    # Prove the written bytes load and verify through the production reader,
    # not just that we serialized something.
    store = AnalyticsStore(out_dir)
    reloaded = store.load_all()
    if len(reloaded) != len(artifacts):
        print(f"\nerror: wrote {len(artifacts)} teams but only {len(reloaded)} reload",
              file=sys.stderr)
        return 1

    total_kb = sum(p.stat().st_size for p in out_dir.glob("*.json")) / 1024
    print(f"\nwrote {len(artifacts)} artifacts + {INDEX_FILENAME} to {out_dir} ({total_kb:.0f} KB)")
    print("all reloaded and hash-verified through AnalyticsStore.")
    return 0


def _check(artifacts, out_dir: Path) -> int:
    problems: list[str] = []
    try:
        store = AnalyticsStore(out_dir)
        committed = {e.team_id: e.content_hash for e in store.entries()}
    except AnalyticsArtifactError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    for team_id, artifact in sorted(artifacts.items()):
        if team_id not in committed:
            problems.append(f"{team_id}: missing from the committed index")
        elif committed[team_id] != artifact.content_hash:
            problems.append(
                f"{team_id}: committed {committed[team_id][7:19]}… "
                f"-> rebuilt {artifact.content_hash[7:19]}…"
            )
    for team_id in committed:
        if team_id not in artifacts:
            problems.append(f"{team_id}: committed but no longer built")

    if problems:
        print("\nCHECK FAILED — committed artifacts are stale:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nRe-run without --check to refresh them.", file=sys.stderr)
        return 1

    print(f"\nCHECK OK — all {len(artifacts)} committed artifacts match the source data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
