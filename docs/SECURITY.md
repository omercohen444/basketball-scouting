# Security baseline

Scope: the public scouting product (FastAPI + Supabase + Gemini). Not a formal
audit — a stated threat model with the control for each threat and where it is
tested.

---

## 1. What is worth protecting

| Asset | Why it matters |
|---|---|
| `GEMINI_API_KEY` | Directly billable. Abuse is money |
| `SUPABASE_SECRET_KEY` | Full read/write on the product database, bypasses RLS |
| `REPORT_ADMIN_TOKEN` | The only thing standing between a visitor and paid generation |
| Stored reports | The product's output. Tampering would put fabricated basketball claims under a trustworthy-looking provenance block |
| Deterministic evidence | The thing the whole design exists to keep honest |

---

## 2. Threats and controls

### 2.1 Secret exposure

- Secrets come from the environment or a git-ignored `.env`; nothing is
  hard-coded. `.env` has been ignored since the first commit and CI fails if it
  appears.
- `Settings.redacted()` covers every secret, including the two added for this
  stage. Tested.
- `persistence/supabase.py` strips the key from any string that could reach a
  log or an exception — including a response body that echoes it back. Tested
  with a deliberately hostile mock.
- Outbound request logging is turned down in production so query strings do not
  land in logs.
- No page, template, static asset or API response carries a credential. Tested
  by scanning every rendered surface.

**Residual:** anyone with repository or platform access has the deployment
variables. That is inherent, not mitigated.

### 2.2 Anonymous abuse of paid generation

The expensive operation is one endpoint.

- `POST /api/admin/reports/generate` requires `X-Admin-Token`, compared with
  `secrets.compare_digest` (constant time).
- An **unset** `REPORT_ADMIN_TOKEN` disables generation with a 503. A missing
  secret is never a permissive default. Tested.
- No public route can construct an agent backend. Tested by wiring a factory
  that raises on construction and driving every public route including the PDF.
- The admin rate limiter counts **unauthenticated** attempts too, so the token
  cannot be probed without limit. Tested.
- Existing reports are not regenerated unless `force_regenerate` is set.

### 2.3 Prompt injection and free-text abuse

The strongest control is that there is nothing to inject into.

- The entire caller-supplied surface is a `team_id` from a fixed 14-entry
  allowlist and a boolean.
- `GenerateRequest` is `extra="forbid"`, so a body carrying `prompt`,
  `system`, `instructions` or extra evidence is rejected with 422 before
  anything runs. Tested with all four.
- Agent inputs come from a committed, hash-checked evidence pack. A visitor
  cannot influence a prompt at all.
- Agent *outputs* are validated deterministically: unknown ids rejected,
  personnel and scheme vocabulary rejected, unsupported metrics rejected,
  outcome framing rejected when the team has no rankable win/loss evidence.

### 2.4 Malformed or hostile identifiers

- Team ids pass a shape gate before anything else, and an id that fails it is
  **not echoed back** — so a hostile value never reaches a log line, a template
  or a query string. Tested with SQL-ish, script-ish, traversal and oversized
  inputs.
- Report ids must look like the UUIDs the service mints; anything else is a 404
  rather than a 400, which reveals less.
- Jinja autoescaping is on. Model-authored prose containing `<script>` renders
  escaped — tested with a backend that deliberately injects it.
- ReportLab's mini-HTML markup is escaped in the PDF path for the same reason.

### 2.5 Stack-trace and internal-detail leakage

- One handler catches everything unhandled, logs it server-side with full
  detail, and answers a generic `internal_error`. Tested that a connection
  string, a file path and an exception message do not appear in the response.
- Storage failures become a generic 503; the one exception is a missing schema,
  which gets its own `storage_not_initialised` code because the remedy is a
  specific one-step action.
- Every `/api` error has the same shape, so a frontend has one branch to write
  and no error path falls back to a framework default.

### 2.6 Database write abuse and report tampering

- The public frontend never talks to Supabase. Only the backend holds the
  server key.
- RLS is enabled on all three tables with **no policies**, and privileges are
  revoked from `anon` and `authenticated`. With RLS on and no policy, a leaked
  anon key reads nothing — verified live:
  `has_table_privilege('anon', 'public.scouting_reports', 'SELECT')` → `false`.
- `service_role` is granted explicitly in the migration rather than inheriting
  Supabase's default privileges, which do not cover tables created through the
  Management API.
- Every write goes through `ReportService`, which refuses to save a report
  carrying hard validation rejections and records the failed attempt in
  `generation_runs` instead.
- Evidence packs are hash-checked at load, so an edited artifact fails loudly
  rather than quietly changing what an agent is told.

### 2.7 Denial of service

Honestly bounded rather than solved.

- In-process fixed-window rate limiting per client address, with a bounded key
  table so a flood of distinct addresses cannot grow memory. `/health` is never
  limited, so a platform healthcheck cannot lock itself out.
- Reads are a single database query; the team list is one query for the whole
  league.
- **Accepted limits:** counters reset on restart and do not coordinate across
  processes. Adding Redis for this product would be infrastructure without a
  user. Platform-level protection (Railway/Cloudflare) is the right layer if it
  ever matters.

---

## 3. Control summary

| Control | Where |
|---|---|
| Environment-only secrets, redacted snapshots | `config.py` |
| Secret stripping in storage errors | `persistence/supabase.py` |
| Fixed 14-team allowlist from shipped packs | `reports/service.py` |
| Shape gate on ids, no echo of rejected input | `reports/service.py`, `web/api.py` |
| Closed request models (`extra="forbid"`) | `web/api.py` |
| Constant-time admin token check | `web/security.py` |
| Rate limiting incl. unauthenticated attempts | `web/ratelimit.py`, `web/security.py` |
| Sanitized errors, no framework defaults | `web/errors.py` |
| Deterministic agent-output validation | `agents/validation.py` |
| Never save an invalid report | `reports/service.py` |
| Evidence integrity at load | `agents/pack_store.py` |
| RLS + explicit grants | `supabase/migrations/0001_init.sql` |

Tests: `tests/test_web_security.py`, plus the security assertions inside
`test_web_api.py`, `test_web_frontend.py`, `test_persistence.py` and
`test_pack_store.py`.

---

## 4. Explicitly out of scope

No user accounts, sessions, passwords or password resets exist, so the entire
authentication attack surface is absent by design. There is no file upload, no
user-generated content, no email, and no third-party embed. CORS is not
configured because the frontend is served by the same application.
