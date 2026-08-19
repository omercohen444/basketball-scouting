"""Operational CLIs — the offline, testable parts.

These scripts are how reports actually get generated and how the database gets
seeded, so their guard rails matter as much as library code. What is covered
here is everything that does not need a network: argument surfaces, the safety
gate on ``--all``, and the fact that the committed seed migration still matches
the shipped pack index.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from pack_factories import PRODUCTION_PACKS_DIR

from basketball_scout.config import REPO_ROOT

SCRIPTS = REPO_ROOT / "scripts" / "ops"
SEED_SQL = REPO_ROOT / "supabase" / "migrations" / "0002_seed_teams.sql"
INIT_SQL = REPO_ROOT / "supabase" / "migrations" / "0001_init.sql"


def _load(script_name: str):
    """Import a CLI by path; they are scripts, not an installed package."""
    path = SCRIPTS / script_name
    spec = importlib.util.spec_from_file_location(f"ops_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- generate_reports -------------------------------------------------------


def test_generate_requires_a_target(capsys):
    module = _load("generate_reports.py")
    assert module.main([]) == 2
    assert "--team-id" in capsys.readouterr().err


def test_generate_all_refuses_a_real_provider_without_yes(capsys):
    """The expensive footgun: 14 teams x 3 provider calls on a typo."""
    module = _load("generate_reports.py")
    assert module.main(["--all"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_generate_parser_exposes_the_documented_flags():
    module = _load("generate_reports.py")
    options = {action.dest for action in module.build_parser()._actions}
    assert {"team_ids", "all", "force", "stub", "dry_run", "limit", "yes", "pdf_dir", "model"} <= options


# ---- supabase_admin ---------------------------------------------------------


def test_supabase_admin_exposes_exactly_the_documented_commands():
    module = _load("supabase_admin.py")
    assert set(module.COMMANDS) == {"emit-seed", "check", "seed-teams", "inspect"}


def test_sql_literals_are_escaped():
    module = _load("supabase_admin.py")
    assert module._sql_literal("HAPOEL TEL AVIV") == "'HAPOEL TEL AVIV'"
    assert module._sql_literal("O'Brien") == "'O''Brien'"


def test_emit_seed_is_regenerated_from_the_pack_index(tmp_path, monkeypatch):
    """The seed is generated, not hand-written; regenerating must be a no-op."""
    module = _load("supabase_admin.py")
    target = tmp_path / "0002_seed_teams.sql"
    monkeypatch.setattr(module, "SEED_PATH", target)

    from basketball_scout.config import Settings

    assert module.cmd_emit_seed(Settings()) == 0
    assert target.read_text(encoding="utf-8") == SEED_SQL.read_text(encoding="utf-8"), (
        "supabase/migrations/0002_seed_teams.sql is stale — regenerate it with "
        "`python scripts/ops/supabase_admin.py emit-seed`"
    )


# ---- migration content ------------------------------------------------------


@pytest.mark.skipif(
    not (PRODUCTION_PACKS_DIR / "index.json").is_file(),
    reason="production evidence packs are not present in this checkout",
)
def test_seed_covers_every_shipped_team():
    from basketball_scout.agents.pack_store import PackStore

    sql = SEED_SQL.read_text(encoding="utf-8")
    for entry in PackStore(PRODUCTION_PACKS_DIR).entries():
        assert f"'{entry.team_id}'" in sql, f"{entry.team_id} missing from the seed migration"
        assert entry.team_name in sql
    assert sql.count("('segev:") == 14


def test_migration_keeps_the_tables_closed_to_anonymous_access():
    """The security posture is only real if the SQL still says so.

    Whitespace is collapsed first because the file is column-aligned for
    readability, and that alignment must not be what the test depends on.
    """
    flat = " ".join(INIT_SQL.read_text(encoding="utf-8").lower().split())

    for table in ("teams", "scouting_reports", "generation_runs"):
        assert f"alter table public.{table} enable row level security" in flat
        assert f"revoke all on public.{table} from anon, authenticated;" in flat
        assert f"on public.{table} to service_role;" in flat

    assert "create policy" not in flat, (
        "a policy would expose these tables to anon/authenticated; RLS with no "
        "policy is what makes a leaked anon key read nothing"
    )
