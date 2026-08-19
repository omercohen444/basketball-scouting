-- Basketball Analytics and AI Scouting System — initial product schema.
--
-- Apply once, in the Supabase SQL editor (Dashboard -> SQL Editor -> New query),
-- or with `supabase db push` if the CLI is linked. Idempotent: safe to re-run.
--
-- Security posture — read this before changing anything here
-- ---------------------------------------------------------
-- These tables are reached ONLY by the FastAPI backend, using the server secret
-- key, which bypasses row-level security. No browser ever talks to PostgREST
-- directly. Therefore:
--
--   * RLS is ENABLED on every table, and NO policies are created. With RLS on
--     and no policy, the `anon` and `authenticated` roles can read and write
--     nothing — even if the anon key leaks, and even though these tables live in
--     the PostgREST-exposed `public` schema.
--   * Privileges are additionally REVOKED from `anon` and `authenticated`, so
--     the restriction does not depend on RLS alone.
--
-- If a future stage genuinely needs public reads, add an explicit SELECT policy
-- for `anon` on `scouting_reports` scoped to `status = 'published'` — as a
-- deliberate, reviewed change, not by disabling RLS.

-- =============================================================================
-- teams — the fixed opponent allowlist
-- =============================================================================
create table if not exists public.teams (
    team_id     text primary key,
    team_name   text        not null,
    season      text        not null,
    games_n     integer     not null default 0,
    wins        integer     not null default 0,
    losses      integer     not null default 0,
    active      boolean     not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

comment on table public.teams is
    'Selectable opponents. Seeded from the shipped evidence-pack index (data/evidence_packs/index.json) — the application''s allowlist is that file, not this table.';

create index if not exists teams_active_name_idx on public.teams (active, team_name);

-- =============================================================================
-- scouting_reports — one row per generated, validated, saved report
-- =============================================================================
-- report_json holds the complete public contract (reports.contracts.PublicReport)
-- and is what the API serves verbatim. evidence_json holds the deterministic
-- EvidencePack artifact it was generated from, so any report can be audited
-- without the git-ignored play-by-play cache.
--
-- JSONB rather than a relational explosion of claims/evidence/recommendations:
-- nothing in this product queries inside a report, the shape is versioned by
-- report_version, and a normalised model would be a second source of truth for
-- a structure the agent layer already owns.
create table if not exists public.scouting_reports (
    id                 uuid        primary key,
    team_id            text        not null references public.teams (team_id),
    team_name          text        not null,
    season             text        not null,
    generated_at       timestamptz not null,
    status             text        not null default 'published',
    report_version     text        not null,
    evidence_version   text        not null,
    definition_version text        not null,
    backend            text        not null,
    model_name         text,
    pack_hash          text        not null default '',
    report_json        jsonb       not null,
    evidence_json      jsonb,
    validation_summary jsonb       not null default '{}'::jsonb,
    created_at         timestamptz not null default now(),

    constraint scouting_reports_status_check
        check (status in ('published', 'archived'))
);

comment on table public.scouting_reports is
    'Saved scouting reports. A report only reaches this table after passing deterministic validation with zero hard rejections.';
comment on column public.scouting_reports.report_json is
    'reports.contracts.PublicReport — the exact payload the API returns. Public-safe by construction: no prompts, provider payloads or secrets.';
comment on column public.scouting_reports.evidence_json is
    'The versioned, hash-checked EvidencePack artifact the report was generated from.';

create index if not exists scouting_reports_latest_idx
    on public.scouting_reports (team_id, status, generated_at desc);

-- =============================================================================
-- generation_runs — audit trail, including attempts that produced no report
-- =============================================================================
-- This table exists because a rejected or failed generation is never saved as a
-- report; without it, a failure would leave no trace at all.
create table if not exists public.generation_runs (
    id             uuid        primary key,
    team_id        text        not null,
    started_at     timestamptz not null,
    finished_at    timestamptz not null,
    duration_ms    integer     not null default 0,
    status         text        not null,
    backend        text        not null,
    model_name     text,
    provider_calls integer     not null default 0,
    stage_attempts jsonb       not null default '{}'::jsonb,
    rejects_n      integer     not null default 0,
    warnings_n     integer     not null default 0,
    report_id      uuid        references public.scouting_reports (id) on delete set null,
    error_message  text,
    created_at     timestamptz not null default now(),

    constraint generation_runs_status_check
        check (status in ('succeeded', 'skipped', 'rejected', 'error'))
);

comment on table public.generation_runs is
    'Every generation attempt, successful or not. Provider-call accounting and failure forensics live here.';

create index if not exists generation_runs_team_time_idx
    on public.generation_runs (team_id, started_at desc);

-- =============================================================================
-- Lock the tables down (see the header note)
-- =============================================================================
alter table public.teams             enable row level security;
alter table public.scouting_reports  enable row level security;
alter table public.generation_runs   enable row level security;

revoke all on public.teams            from anon, authenticated;
revoke all on public.scouting_reports from anon, authenticated;
revoke all on public.generation_runs  from anon, authenticated;

-- The backend's only role. Granted explicitly rather than relying on Supabase's
-- default privileges: verified live on 2026-08-19 that tables created through
-- the Management API do NOT pick those up (`has_table_privilege('service_role',
-- 'public.teams', 'SELECT')` was false, and PostgREST answered 42501 "permission
-- denied for table teams"). Being explicit also makes the intent readable.
grant select, insert, update, delete on public.teams            to service_role;
grant select, insert, update, delete on public.scouting_reports to service_role;
grant select, insert, update, delete on public.generation_runs  to service_role;
