#!/usr/bin/env python
"""Generate scouting reports through the production path.

    python scripts/ops/generate_reports.py --team-id segev:4
    python scripts/ops/generate_reports.py --all --dry-run
    python scripts/ops/generate_reports.py --all --stub
    python scripts/ops/generate_reports.py --team-id segev:4 --force --pdf-dir artifacts/pdf

This is the same code path the admin API endpoint uses — ``ReportService`` over
the shipped EvidencePacks and the configured repository — so a report produced
here is byte-identical to one produced through HTTP. Having one path rather than
two is the point.

Cost discipline. Every provider call is money, so:

* nothing regenerates an existing report unless ``--force`` is given;
* ``--dry-run`` reports exactly what *would* be generated, spending nothing;
* ``--stub`` runs the deterministic stub agents end to end with zero calls;
* ``--all`` requires ``--yes`` (or ``--dry-run``) before touching 14 teams.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.agents.pack_store import PackStore  # noqa: E402
from basketball_scout.config import ConfigError, load_settings  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402
from basketball_scout.reports.pdf import build_report_pdf, pdf_filename  # noqa: E402
from basketball_scout.reports.service import (  # noqa: E402
    ReportService,
    UnknownTeamError,
    default_backend_factory,
)
from basketball_scout.web.context import build_repository  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--team-id", action="append", dest="team_ids", help="Repeatable.")
    parser.add_argument("--all", action="store_true", help="Every supported team.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if a report exists.")
    parser.add_argument("--stub", action="store_true", help="Deterministic stub agents; no provider calls.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; spend nothing.")
    parser.add_argument("--limit", type=int, help="Stop after N teams.")
    parser.add_argument("--yes", action="store_true", help="Required to run --all for real.")
    parser.add_argument("--pdf-dir", help="Also write each report's PDF here.")
    parser.add_argument("--model", help="Override the agent model id.")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    if not args.team_ids and not args.all:
        print("error: pass --team-id (repeatable) or --all", file=sys.stderr)
        return 2

    settings = load_settings()
    if not args.stub:
        # Any path that can reach the provider needs the OS trust store here.
        enable_system_trust_store()

    store = PackStore(settings.evidence_packs_dir)
    if not store.available:
        print(
            f"error: no evidence packs under {settings.evidence_packs_dir}. "
            f"Run scripts/scouting_report/build_production_packs.py first.",
            file=sys.stderr,
        )
        return 1

    try:
        repository = build_repository(settings)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if args.stub:
        from basketball_scout.agents.pipeline import StubBackend

        backend_factory = lambda: StubBackend()  # noqa: E731
        model_name = "stub"
    else:
        model = args.model or settings.agent_model
        backend_factory = default_backend_factory(settings, model)
        model_name = model

    service = ReportService(
        pack_store=store,
        repository=repository,
        backend_factory=backend_factory,
        model_name=model_name,
    )

    try:
        team_ids = (
            store.team_ids()
            if args.all
            else [service.resolve_team_id(t) for t in (args.team_ids or [])]
        )
    except UnknownTeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.limit:
        team_ids = team_ids[: args.limit]

    if args.all and not args.dry_run and not args.stub and not args.yes:
        print(
            f"refusing to generate {len(team_ids)} reports with a real provider without --yes.\n"
            f"Re-run with --dry-run to see the plan, --stub to rehearse it offline, "
            f"or add --yes to proceed.",
            file=sys.stderr,
        )
        return 2

    print(f"storage      : {getattr(repository, 'name', 'unknown')}")
    print(f"packs        : {store.packs_dir}  ({len(store.team_ids())} teams, season {store.index.season})")
    print(f"backend      : {'stub' if args.stub else 'crewai'}  model={model_name}")
    print(f"mode         : {'DRY RUN' if args.dry_run else 'live'}{'  (force)' if args.force else ''}")
    print(f"teams        : {len(team_ids)}\n")

    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else None
    if pdf_dir:
        pdf_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    total_calls = 0
    started = time.monotonic()

    for team_id in team_ids:
        entry = next(e for e in store.entries() if e.team_id == team_id)
        label = f"{team_id:12s} {entry.team_name[:24]:24s}"

        if args.dry_run:
            existing = None
            try:
                existing = service.repository.get_latest_report(team_id)
            except Exception as exc:  # noqa: BLE001 - dry run must never fail hard
                print(f"{label}  storage unreadable: {exc}")
                continue
            if existing and not args.force:
                print(f"{label}  SKIP   (report {existing.report_id} from {existing.generated_at})")
            else:
                print(f"{label}  WOULD GENERATE  (3 provider calls)")
            continue

        outcome = service.generate(team_id, force_regenerate=args.force)
        total_calls += outcome.provider_calls
        detail = (
            f"calls={outcome.provider_calls} retries={outcome.transient_retries} "
            f"rejects={len(outcome.rejects)} warnings={len(outcome.warnings)} "
            f"{outcome.duration_ms / 1000:.1f}s"
        )
        print(f"{label}  {outcome.status.upper():10s} {detail}")
        if outcome.stored_report_id:
            print(f"{'':37s}  report {outcome.stored_report_id}  persisted={outcome.persisted}")
        for warning in outcome.warnings:
            print(f"{'':37s}  ! {warning}")
        if outcome.error:
            print(f"{'':37s}  error: {outcome.error}")
        if not outcome.ok:
            failures += 1

        if pdf_dir and outcome.report is not None:
            path = pdf_dir / pdf_filename(outcome.report)
            path.write_bytes(build_report_pdf(outcome.report))
            print(f"{'':37s}  wrote {path}")

    if not args.dry_run:
        print(
            f"\ndone in {time.monotonic() - started:.1f}s  "
            f"provider_calls={total_calls}  failures={failures}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
