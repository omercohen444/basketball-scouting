#!/usr/bin/env python
"""Build the deterministic EvidencePack for one team (or every team).

Entirely offline: reads the already-ingested team-game stats and cached raw PBP,
makes no network calls and needs no API key. This is the agent layer's input
contract, and it is useful on its own — the later FastAPI/UI stage can consume
this JSON without any agent involvement.

Usage:
    python scripts/scouting_report/build_pack.py --team-id segev:4
    python scripts/scouting_report/build_pack.py --all --out-dir artifacts/scouting_report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.agents.evidence_pack import (  # noqa: E402
    build_evidence_pack,
    load_league_data,
)
from basketball_scout.config import load_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--team-id", help='Provider-qualified team id, e.g. "segev:4".')
    parser.add_argument("--all", action="store_true", help="Build a pack for every team (QA sweep).")
    parser.add_argument("--stats-dir", default="data/processed/stats")
    parser.add_argument("--out-dir", help="Write pack JSON here (default: no file, summary only).")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    if not args.team_id and not args.all:
        print("error: pass --team-id or --all", file=sys.stderr)
        return 2

    settings = load_settings()
    raw_stats_dir = Path(args.stats_dir)
    stats_dir = (
        raw_stats_dir if raw_stats_dir.is_absolute() else (settings.data_dir.parent / raw_stats_dir).resolve()
    )

    league = load_league_data(settings, stats_dir=stats_dir)
    if not league.pairs:
        print(f"error: no ingested games found under {stats_dir}", file=sys.stderr)
        return 1

    team_ids = sorted(league.pairs) if args.all else [args.team_id]
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    print(f"{'team':12s} {'name':26s} {'rec':>7s} {'items':>5s} {'cand':>5s}  states")
    for team_id in team_ids:
        try:
            pack = build_evidence_pack(team_id, league)
        except KeyError as exc:
            print(f"{team_id}: FAILED — {exc}", file=sys.stderr)
            failures += 1
            continue

        leaks = [
            i.evidence_id
            for i in pack.evidence
            if not i.win_loss.agent_rankable and i.win_loss.effect_size is not None
        ]
        if leaks:
            print(f"{team_id}: FAILED — unmasked effect_size on {leaks}", file=sys.stderr)
            failures += 1

        print(
            f"{pack.team_id:12s} {pack.team_name[:26]:26s} {pack.wins:>3d}-{pack.losses:<3d} "
            f"{len(pack.evidence):>5d} {len(pack.screening.candidate_ids):>5d}  "
            f"{','.join(pack.pack_states) or '-'}"
        )

        if out_dir:
            path = out_dir / f"pack_{team_id.replace(':', '_')}.json"
            path.write_text(
                json.dumps(pack.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
            )

    if out_dir:
        print(f"\nwrote pack JSON to {out_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
