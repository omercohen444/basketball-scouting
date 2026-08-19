# Deployment

Target: **Railway**, from the `no-video-mvp` branch of
`github.com/omercohen444/basketball-scouting`.

Status as of 2026-08-19: the repository is deployment-ready and the Supabase
schema is applied and seeded. **The Railway deployment itself has not been
performed** — see §5.

---

## 1. Why this deploys at all

The obvious problem with deploying this project is that its analytics read 297
cached play-by-play files and 182 processed team-game records, and `data/raw`
and `data/processed` are git-ignored. A deployment has neither.

It is solved by serialization, not by shipping the cache. `data/evidence_packs/`
holds 14 versioned, hash-checked `EvidencePack` artifacts (~600 KB total, in
Git). The production path is:

```
data/evidence_packs/pack_<team>.json   →  agents  →  validation  →  Supabase
                                                                      ↓
                                                    FastAPI  →  HTML / JSON / PDF
```

No PBP cache, no stats walk, no network call to rebuild anything. Regenerating
the artifacts is a local developer step
(`scripts/scouting_report/build_production_packs.py`), never a request-time one.

---

## 2. What the platform must provide

| Setting | Value |
|---|---|
| Builder | Nixpacks (auto-detected from `requirements.txt`) |
| Python | `3.11` (pinned in `.python-version`) |
| Install | `pip install -r requirements.txt` (Nixpacks default) |
| Start | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Healthcheck | `GET /health` |

`railway.json` declares all of this, so a Railway service pointed at the repo
picks it up without manual configuration. `Procfile` carries the same start
command for any other platform.

`main.py` is a four-line shim that puts `src/` on the path and re-exports the
app. It exists because the project deliberately is not pip-installed (the test
suite uses `pythonpath = ["src"]`, so there is no install step to get wrong).

Nothing binds to `localhost`, nothing reads a Windows-specific path, and the
port comes from `$PORT`.

### Image size note

`requirements.txt` includes CrewAI, which pulls ~130 packages (litellm,
chromadb, onnxruntime). That is needed only by
`POST /api/admin/reports/generate`. A serve-only deployment can install
`requirements-ci.txt` instead — everything public still works, and the admin
endpoint degrades to a clean `503 generation_unavailable` because
`agents/crew.py` is imported lazily. Reports would then be generated locally
with `scripts/ops/generate_reports.py`, writing to the same Supabase project.

---

## 3. Environment variables

Set these on the Railway service. `PORT` is supplied by the platform.

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | yes | Either `https://<ref>.supabase.co` or `.../rest/v1`; both are accepted |
| `SUPABASE_SECRET_KEY` | yes | Server secret key. Backend only |
| `REPORT_ADMIN_TOKEN` | yes | Long random string. Unset ⇒ generation disabled (503), never open |
| `GEMINI_API_KEY` | only to generate | Not needed to serve saved reports |
| `AGENT_MODEL` | no | Defaults to `gemini-3.5-flash`, the verified pin |
| `LOG_LEVEL` | no | Defaults to `INFO` |
| `API_RATE_LIMIT_PER_MINUTE` | no | Defaults to 120; `0` disables |
| `ADMIN_RATE_LIMIT_PER_HOUR` | no | Defaults to 12; `0` disables |

Without `SUPABASE_*` the app still boots and serves `/health`, `/api/teams` and
an honest empty state — it falls back to in-memory storage and logs a warning.
That is deliberate: a half-configured deployment should be diagnosable, not
dead. It is not a production configuration.

---

## 4. Supabase

**Already applied** to project `fcgbreeurhbjisghqebk` (`basketball-scouting`) on
2026-08-19, and verified live:

- `teams`, `scouting_reports`, `generation_runs` created;
- 14 teams seeded from `data/evidence_packs/index.json`;
- RLS enabled on all three tables with **no policies**, and privileges revoked
  from `anon` and `authenticated` — verified:
  `has_table_privilege('anon', 'public.scouting_reports', 'SELECT')` is `false`;
- `service_role` granted explicitly (Supabase's default privileges do **not**
  cover tables created through the Management API — verified the hard way).

To reproduce on a fresh project, run both files in the SQL editor in order:

```
supabase/migrations/0001_init.sql
supabase/migrations/0002_seed_teams.sql
```

Then confirm from the repo:

```powershell
python scripts\ops\supabase_admin.py check
```

which prints `schema ready : True` and the row count, or the exact remaining
step if not.

---

## 5. Deploying (not yet done)

Railway's UI produced repeated errors during setup on 2026-08-18, so deployment
was deliberately deferred. The GitHub App is already installed and has access to
the repository.

1. Railway → **New Project** → **Deploy from GitHub repo** →
   `omercohen444/basketball-scouting`.
2. Service → **Settings** → **Source** → set the branch to `no-video-mvp`.
3. Service → **Variables** → add `SUPABASE_URL`, `SUPABASE_SECRET_KEY`,
   `REPORT_ADMIN_TOKEN`, and `GEMINI_API_KEY` if the deployment should be able
   to generate.
4. Deploy. `railway.json` supplies the build, start command and healthcheck.
5. Service → **Settings** → **Networking** → **Generate Domain**.
6. Smoke-test the public URL:

```
GET  /health                          → {"status":"ok", "storage":"supabase", "evidence_packs":{"teams_n":14,…}}
GET  /api/teams                       → 14 teams
GET  /                                → the opponent picker
GET  /teams/segev:4                   → the Hapoel Jerusalem report
GET  /api/reports/{report_id}/pdf     → a PDF
```

`/health` reporting `"storage": "memory"` means the Supabase variables did not
reach the process.

---

## 6. Generating reports

Never from a browser. Either:

```powershell
# locally, straight to the configured Supabase project
python scripts\ops\generate_reports.py --team-id segev:4
python scripts\ops\generate_reports.py --all --dry-run
python scripts\ops\generate_reports.py --all --yes
```

or against a deployment:

```
POST /api/admin/reports/generate
X-Admin-Token: <REPORT_ADMIN_TOKEN>
Content-Type: application/json

{"team_id": "segev:4", "force_regenerate": false}
```

Each report costs 3 provider calls and takes roughly 2–3 minutes. Nothing
regenerates an existing report unless forced, `--dry-run` spends nothing, and
`--all` refuses to run against a real provider without `--yes`.

---

## 7. After changing the analytics

If anything upstream of the agent layer changes (stats, enrichment, evidence
selection), the shipped artifacts are stale. Rebuild and commit them:

```powershell
python scripts\scouting_report\build_production_packs.py
python scripts\ops\supabase_admin.py emit-seed     # if a record changed
python -m pytest
```

`build_production_packs.py --check` rebuilds in memory and compares hashes
against what is committed without writing — use it to prove the artifacts still
match the source data.
