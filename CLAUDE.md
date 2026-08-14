# CLAUDE.md

Operating instructions for Claude Code sessions in this repository.

Read `PROJECT_SPEC.md` (what we're building) and `BUILD_PLAN.md` (what order,
and the risk gates) before making changes. Read `WORKLOG.md` for where the last
session left off.

---

## 1. The one thing to remember

**There is a hard one-week implementation window.** Nearly every judgement call
follows from that: prefer simple reliable implementations, test risk early, get
a working end-to-end flow before making any single component good.

---

## 2. Scope discipline

Within an agreed task you have freedom to make normal engineering decisions.

You do **not** have authority to silently:

- expand product scope;
- add major frameworks or features;
- remove agreed deliverables;
- reinterpret project requirements;
- make a continuation/cut decision after a failed risk gate.

If one of those becomes necessary, **report it and stop at that boundary.**

Notice something that belongs to a later stage? Write it in the audit report or
`WORKLOG.md`. Do not implement it.

Specifically out of scope until its stage begins: the 91-game PBP pipeline,
advanced basketball statistics, W/L analytics, Supabase tables, CrewAI agents,
FastAPI endpoints, the website, PDF reports, 7-game video processing, full-game
Gemini processing, YOLO training, player analytics, auth, deployment.

---

## 3. Risk-first execution

- The riskiest unknown gets tested first, with the smallest possible test.
- Do not continue a failing approach because time has already been invested in it.
- Do not optimize a component once it is good enough for the MVP.
- Preserve extensibility **only when it is cheap**. No speculative infrastructure.
- Do not couple core analytics directly to one website's raw format when a small
  adapter seam is cheap — but do not build a multi-league platform now.

---

## 4. Large-run workflow

Work happens in **large coherent execution packages**, not micromanaged
functions. For each package:

1. You receive the outcome, scope, constraints and acceptance criteria.
2. Inspect the repo and relevant docs first.
3. Make a concrete implementation plan.
4. Execute substantially against it.
5. Solve ordinary implementation problems independently — don't ask permission
   for routine decisions.
6. Stop and report **only** for a genuine blocker or a decision that would
   materially change product scope.
7. Run appropriate tests/checks.
8. Finish with a detailed audit report an independent reviewer can verify.

---

## 5. When to stop for a blocker

Stop and report when:

- a risk gate fails (Gate 5 continuation decisions are **not yours**);
- the required product decision is genuinely ambiguous and the readings lead to
  materially different work;
- an external dependency is unavailable or behaves differently than the plan assumed;
- proceeding would require inventing data, credentials or API behaviour.

Otherwise keep going. Before stopping, finish everything that does **not**
depend on the answer, then state exactly what is blocked and why.

---

## 6. Never fabricate

- Do not invent API syntax. If the correct call cannot be established from the
  installed SDK, **isolate the uncertain integration point and document what
  remains to verify** — see `docs/VIDEO_SPIKE_NOTES.md` for the established
  pattern.
- Do not populate fake ground truth, sample statistics or placeholder results
  that could be mistaken for real measurements.
- Do not hide uncertainty. Report what failed, with the output.

---

## 7. Secrets

- Secrets come from the environment or a git-ignored `.env`. **Never hard-code
  a key, and never commit one.**
- `.env.example` holds empty placeholders only.
- Missing credentials must fail at the point of use with an actionable message,
  never at import — the test suite and `--help` must work on a machine with no key.
- Never print a key. `Settings.redacted()` exists for debug output.

---

## 8. Testing expectations

- Tests protect **critical behaviour**, not a coverage percentage.
- Tests must pass with **no credentials and no network**. Anything needing a
  real API call is marked `@pytest.mark.network` and is not part of a normal run.
- Worth testing here: schemas accept valid values and reject invalid ones;
  config loads safely; spike modules import with no side effects; the provider
  request keeps its verified wire shape.
- Run: `.venv\Scripts\python.exe -m pytest`

---

## 9. Git hygiene

- **Do not commit unless explicitly instructed.**
- **Inspect the final diff before declaring work complete** — `git status` and
  `git diff` (or `git diff --cached`). Do not report done on unread changes.
- Keep out of Git: `.env`, virtualenvs, Python caches, downloaded video, large
  raw datasets, generated artifacts.
- Do **not** exclude the small validation fixtures — reproducible tests need them.

---

## 10. Environment

Python 3.11+. The project venv is `.venv/` (git-ignored):

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\spikes\probe_segevsport.py --url <url>
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py --dry-run --url <url> --at 1:12:30
```

Dependencies are deliberately minimal (`requirements.txt`). **Each later stage
adds its own dependencies when that stage starts** — do not pre-install the
full future stack because it appears in the project design.

### Two machine-specific quirks (both already handled)

1. **TLS.** Verification against `certifi`'s roots fails for *every* HTTPS host
   on this machine; the required root CA is in the Windows certificate store
   (proxy/AV interception), and stale `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`
   variables point at an unrelated conda env. `basketball_scout.net.
   enable_system_trust_store()` fixes it and is called at the top of every entry
   point. **Any new entry point that talks HTTPS must call it too** — this
   affects the Gemini SDK as much as `requests`.
2. **`sitecustomize` noise.** Every run prints
   `ModuleNotFoundError: No module named 'truststore'` to stderr. That comes
   from Anaconda's global `sitecustomize.py`, not from this project. Harmless;
   ignore it.

A conda env named `basketball_scouting_env` also exists with compatible
dependencies. `.venv` is the reference environment because `requirements.txt`
reproduces it.

---

## 11. Update the worklog

**After any substantial execution run, add an entry to `WORKLOG.md`:** date/run,
objective, work completed, tests and results, decisions implemented, unresolved
issues, and the next recommended technical action.

Keep it concise and useful to a future session. It is not a place for verbose
terminal logs.

---

## 12. Repository map

```
src/basketball_scout/     library code
  config.py               env-based settings; no secrets in code
  net.py                  OS trust store (see quirk 1 above)
  video/
    metrics.py            metric registry — the source everything derives from
    events.py             PBP moment -> video window; timecode handling
    schema.py             structured classification contract
    prompts.py            prompt building (provider-agnostic)
    ground_truth.py       Gate 1 fixture I/O + agreement
    gemini_client.py      the ONLY provider-aware module
scripts/spikes/           throwaway-grade diagnostics, kept runnable
tests/                    offline smoke tests
data/raw/                 git-ignored
data/processed/           git-ignored
data/validation/          TRACKED — small fixtures for reproducible tests
docs/                     design/verification notes
artifacts/                git-ignored spike output
```

**Metrics are provisional.** To replace one, edit `video/metrics.py` — the
schema, prompt, CLI and fixture columns all derive from it. Then regenerate the
fixture template (see `data/validation/README.md`).
