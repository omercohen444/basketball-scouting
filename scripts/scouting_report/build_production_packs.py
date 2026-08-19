#!/usr/bin/env python
"""Serialize the shippable production EvidencePack artifacts for every team.

This is a **local developer step**, not a request-time one. It walks the
git-ignored PBP cache once and writes small, versioned, hash-checked artifacts
that *are* committed, so a deployment (Railway) can run the agent pipeline with
no raw data present.

    python scripts/scouting_report/build_production_packs.py
    python scripts/scouting_report/build_production_packs.py --check

``--check`` rebuilds in memory and compares hashes against what is on disk
without writing anything — use it to prove the committed artifacts still match
the source data.

Entirely offline: no API key, no network.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.agents.evidence_pack import (  # noqa: E402
    build_evidence_pack,
    load_league_data,
)
from basketball_scout.agents.pack_store import (  # noqa: E402
    PackArtifactError,
    PackStore,
    artifact_path,
    build_artifact,
    build_index,
    default_packs_dir,
    write_artifact,
    write_index,
)
from basketball_scout.config import load_settings  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--stats-dir", default="data/processed/stats")
    parser.add_argument("--out-dir", help="Default: <DATA_DIR>/evidence_packs")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Rebuild in memory and compare against the committed artifacts; write nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    settings = load_settings()

    raw_stats_dir = Path(args.stats_dir)
    stats_dir = (
        raw_stats_dir
        if raw_stats_dir.is_absolute()
        else (settings.data_dir.parent / raw_stats_dir).resolve()
    )
    out_dir = Path(args.out_dir) if args.out_dir else default_packs_dir(settings.data_dir)

    league = load_league_data(settings, stats_dir=stats_dir)
    if not league.pairs:
        print(f"error: no ingested games found under {stats_dir}", file=sys.stderr)
        return 1

    artifacts = []
    failures = 0
    print(f"{'team':12s} {'name':26s} {'rec':>7s} {'items':>5s} {'src':>4s}  hash")
    for team_id in sorted(league.pairs):
        pack = build_evidence_pack(team_id, league)

        leaks = [
            i.evidence_id
            for i in pack.evidence
            if not i.win_loss.agent_rankable and i.win_loss.effect_size is not None
        ]
        if leaks:
            print(f"{team_id}: FAILED — unmasked effect_size on {leaks}", file=sys.stderr)
            failures += 1

        source_game_ids = [stats.source_game_id for stats, _ in league.pairs[team_id]]
        artifact = build_artifact(pack, source_game_ids)
        artifacts.append(artifact)

        print(
            f"{pack.team_id:12s} {pack.team_name[:26]:26s} {pack.wins:>3d}-{pack.losses:<3d} "
            f"{len(pack.evidence):>5d} {len(source_game_ids):>4d}  {artifact.pack_hash[7:19]}…"
        )

    if failures:
        return 1

    if args.check:
        return _check(artifacts, out_dir)

    for artifact in artifacts:
        write_artifact(artifact, out_dir)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    index_path = write_index(build_index(artifacts, generated_at=generated_at), out_dir)

    print(f"\nwrote {len(artifacts)} pack artifacts + index to {out_dir}")
    print(f"index: {index_path}")

    # Prove the written artifacts load and verify through the production path.
    store = PackStore(out_dir)
    loaded = store.load_all()
    print(f"verified {len(loaded)} artifacts load and hash-check through PackStore")
    return 0


def _check(artifacts, out_dir: Path) -> int:
    """Compare freshly built packs against what is committed on disk."""
    problems: list[str] = []
    for artifact in artifacts:
        path = artifact_path(out_dir, artifact.pack.team_id)
        if not path.is_file():
            problems.append(f"{artifact.pack.team_id}: missing artifact {path.name}")
            continue
        try:
            from basketball_scout.agents.pack_store import load_artifact

            on_disk = load_artifact(path)
        except PackArtifactError as exc:
            problems.append(f"{artifact.pack.team_id}: {exc}")
            continue
        if on_disk.pack_hash != artifact.pack_hash:
            problems.append(
                f"{artifact.pack.team_id}: hash drift — disk {on_disk.pack_hash[7:19]}… "
                f"vs rebuilt {artifact.pack_hash[7:19]}…"
            )

    if problems:
        print("\nCHECK FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nRe-run without --check to regenerate.", file=sys.stderr)
        return 1

    print(f"\nCHECK OK — all {len(artifacts)} committed artifacts match the source data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
