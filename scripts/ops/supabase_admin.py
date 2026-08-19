#!/usr/bin/env python
"""Supabase operations for the product stage.

    python scripts/ops/supabase_admin.py emit-seed     # regenerate the seed migration (offline)
    python scripts/ops/supabase_admin.py check         # live connectivity + schema probe
    python scripts/ops/supabase_admin.py seed-teams    # live upsert of the 14 teams
    python scripts/ops/supabase_admin.py inspect       # live: what is stored right now

Only ``emit-seed`` is offline. The rest need ``SUPABASE_URL`` and
``SUPABASE_SECRET_KEY`` in the environment or ``.env``.

**DDL is not applied from here.** PostgREST cannot execute ``CREATE TABLE``, and
the Management API needs a personal access token this project deliberately does
not hold. ``supabase/migrations/0001_init.sql`` is applied once by a human in the
SQL editor; ``check`` tells you whether that has happened.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.agents.pack_store import PackStore  # noqa: E402
from basketball_scout.config import ConfigError, load_settings, require_supabase  # noqa: E402
from basketball_scout.net import enable_system_trust_store  # noqa: E402
from basketball_scout.persistence.models import TeamRecord  # noqa: E402
from basketball_scout.persistence.repository import RepositoryError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = REPO_ROOT / "supabase" / "migrations" / "0002_seed_teams.sql"

SEED_HEADER = """\
-- Seed the opponent allowlist.
--
-- GENERATED FILE — do not hand-edit. Regenerate with:
--     python scripts/ops/supabase_admin.py emit-seed
--
-- The source of truth is data/evidence_packs/index.json, which is itself
-- generated from the ingested season. Keeping the seed derived from the shipped
-- packs means the database allowlist and the application allowlist cannot drift.
--
-- Idempotent: re-running updates names/records in place.

"""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _teams_from_packs(settings) -> list[TeamRecord]:
    store = PackStore(settings.evidence_packs_dir)
    return [
        TeamRecord(
            team_id=e.team_id,
            team_name=e.team_name,
            season=e.season,
            games_n=e.games_n,
            wins=e.wins,
            losses=e.losses,
        )
        for e in store.entries()
    ]


def cmd_emit_seed(settings) -> int:
    teams = _teams_from_packs(settings)
    rows = ",\n".join(
        "    ({}, {}, {}, {}, {}, {}, true)".format(
            _sql_literal(t.team_id),
            _sql_literal(t.team_name),
            _sql_literal(t.season),
            t.games_n,
            t.wins,
            t.losses,
        )
        for t in teams
    )
    sql = (
        SEED_HEADER
        + "insert into public.teams (team_id, team_name, season, games_n, wins, losses, active)\nvalues\n"
        + rows
        + "\non conflict (team_id) do update set\n"
        "    team_name  = excluded.team_name,\n"
        "    season     = excluded.season,\n"
        "    games_n    = excluded.games_n,\n"
        "    wins       = excluded.wins,\n"
        "    losses     = excluded.losses,\n"
        "    active     = excluded.active,\n"
        "    updated_at = now();\n"
    )
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(sql, encoding="utf-8")
    print(f"wrote {SEED_PATH.relative_to(REPO_ROOT)} ({len(teams)} teams)")
    return 0


def _repository(settings):
    enable_system_trust_store()
    url, key = require_supabase(settings)
    from basketball_scout.persistence.supabase import SupabaseReportRepository

    return SupabaseReportRepository(url, key, timeout=settings.http_timeout_seconds)


def cmd_check(settings) -> int:
    repo = _repository(settings)
    # Host only — never the key, and never the full project URL query string.
    print(f"project host : {settings.supabase_url.split('//')[-1].split('/')[0]}")
    result = repo.health()
    print(f"reachable    : {result['reachable']}")
    print(f"schema ready : {result['schema_ready']}")
    if result.get("teams_n") is not None:
        print(f"teams rows   : {result['teams_n']}")
    if result.get("detail"):
        print(f"detail       : {result['detail']}")
    if not result["schema_ready"]:
        print(
            "\nNEXT ACTION (one step, human-only):\n"
            "  Open the Supabase dashboard -> SQL Editor -> New query,\n"
            "  paste supabase/migrations/0001_init.sql, run it,\n"
            "  then paste supabase/migrations/0002_seed_teams.sql and run it.\n"
            "  Re-run this command to confirm."
        )
        return 2
    return 0


def cmd_seed_teams(settings) -> int:
    repo = _repository(settings)
    teams = _teams_from_packs(settings)
    n = repo.upsert_teams(teams)
    print(f"upserted {n} teams")
    for team in repo.list_teams():
        print(f"  {team.team_id:12s} {team.team_name:26s} {team.record}")
    return 0


def cmd_inspect(settings) -> int:
    repo = _repository(settings)
    teams = repo.list_teams()
    print(f"teams: {len(teams)}")
    found = 0
    for team in teams:
        latest = repo.get_latest_report(team.team_id)
        if latest:
            found += 1
            print(
                f"  {team.team_id:12s} {team.team_name:26s} "
                f"{latest.generated_at}  {latest.report_id}  "
                f"warnings={latest.validation_summary.get('warnings_n', '?')}"
            )
    print(f"teams with a saved report: {found}/{len(teams)}")
    return 0


COMMANDS = {
    "emit-seed": cmd_emit_seed,
    "check": cmd_check,
    "seed-teams": cmd_seed_teams,
    "inspect": cmd_inspect,
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)

    settings = load_settings()
    try:
        return COMMANDS[args.command](settings)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 3
    except RepositoryError as exc:
        print(f"supabase error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
