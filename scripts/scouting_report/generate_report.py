#!/usr/bin/env python
"""Generate a scouting report: EvidencePack -> 3 agents -> validation -> render.

    python scripts/scouting_report/generate_report.py --team-id segev:4 --stub
    python scripts/scouting_report/generate_report.py --team-id segev:4

``--stub`` runs the deterministic stub backend: no API key, no network, no
provider calls. Without it, the CrewAI backend runs the three real agents.
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
from basketball_scout.agents.pipeline import PipelineError, StubBackend, run_pipeline  # noqa: E402
from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--team-id", required=True, help='Provider-qualified team id, e.g. "segev:4".')
    parser.add_argument("--stub", action="store_true", help="Deterministic stub agents; no provider calls.")
    parser.add_argument("--model", help="Override the provider model id.")
    parser.add_argument("--stats-dir", default="data/processed/stats")
    parser.add_argument("--out-dir", default="artifacts/scouting_report")
    parser.add_argument("--no-write", action="store_true", help="Print only; do not write artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    # Any path that can reach the provider needs the OS trust store on this
    # machine (see CLAUDE.md §10 quirk 1) — it patches ssl globally, covering
    # LiteLLM's httpx as well as requests.
    if not args.stub:
        enable_system_trust_store()

    settings = load_settings()
    raw_stats_dir = Path(args.stats_dir)
    stats_dir = (
        raw_stats_dir if raw_stats_dir.is_absolute() else (settings.data_dir.parent / raw_stats_dir).resolve()
    )

    league = load_league_data(settings, stats_dir=stats_dir)
    if args.team_id not in league.pairs:
        print(f"error: unknown team {args.team_id!r}; known: {sorted(league.pairs)}", file=sys.stderr)
        return 2

    pack = build_evidence_pack(args.team_id, league)
    print(f"pack: {pack.pack_id}  {pack.team_name}  {pack.wins}-{pack.losses}  "
          f"items={len(pack.evidence)}  states={pack.pack_states or ['-']}")

    if args.stub:
        backend = StubBackend()
    else:
        from basketball_scout.agents.crew import CrewBackend  # imported late: only path needing crewai

        backend = CrewBackend(settings=settings, model=args.model)

    try:
        result = run_pipeline(pack, backend)
    except PipelineError as exc:
        print(f"PIPELINE FAILED — {exc}", file=sys.stderr)
        return 1

    validation = result.validation
    print(f"backend={result.backend}  attempts={result.stage_attempts}  "
          f"provider_calls={result.provider_calls if not args.stub else 0}")
    print(f"signals={len(result.triage.signals)}  implications={len(result.tactical.implications)}  "
          f"recommendations={len(result.report.recommendations)}")
    print(f"validation: rejects={len(validation.rejects)}  warnings={len(validation.warnings)}")
    for finding in validation.findings:
        print(f"  {finding}")

    if not args.no_write:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = args.team_id.replace(":", "_")
        suffix = "_stub" if args.stub else ""
        json_path = out_dir / f"report_{slug}{suffix}.json"
        md_path = out_dir / f"report_{slug}{suffix}.md"
        json_path.write_text(
            json.dumps(result.rendered, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        md_path.write_text(result.markdown, encoding="utf-8")
        print(f"wrote {json_path}")
        print(f"wrote {md_path}")

    return 0 if validation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
