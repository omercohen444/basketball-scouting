# WORKLOG

Running project log. Newest entry first. Concise and useful to a future
session — not a place for terminal output.

---

## 2026-08-19 — Run 14: Product foundation — FastAPI, Supabase, PDF, frontend (`no-video-mvp`)

**Objective:** Turn the proven agent layer into a deployable product: portable
deterministic evidence, a repository abstraction, Supabase persistence, a
protected generation endpoint, PDF, a working frontend, CI, and Railway
readiness. Autonomous overnight run; started at `8eaf6cc`, worktree clean.

**The problem that shaped everything: `data/` is git-ignored.** The agent layer
rebuilt each pack by walking 297 cached PBP games and 182 processed records, so
a deployment had no way to run at all. Solved by serializing the deterministic
layer once, offline: `data/evidence_packs/` holds 14 versioned artifacts
(~600 KB, tracked) wrapping each `EvidencePack` in an envelope with provenance
the pack schema deliberately does not carry (source game ids, fingerprint,
counts, versions — the pack is `extra="forbid"` because it is the agent
contract). `pack_hash` is sha256 over a canonical serialization and is
recomputed at load, so a tampered artifact fails loudly. Rebuilds are
byte-stable, so `build_production_packs.py --check` answers "do the committed
artifacts still match the source data?" in one command.

**Architecture added.** `persistence/` (five-method `Protocol`, in-memory +
Supabase-over-PostgREST adapters), `reports/` (`PublicReport` contract, ReportLab
PDF, and `ReportService` — the single generate path shared by the API and the
CLI), `web/` (FastAPI, six routes, Jinja frontend). Layering is one-directional
and `web/` holds no basketball logic.

**Deliberately not `supabase-py`.** The contract is five methods over three
tables, `httpx` was already in the tree, and Run 13 had already seen one install
silently downgrade `pydantic` under a verified pin. Plain httpx keeps the wire
format visible in one file and adds no resolution risk.

**Supabase is live.** The CLI turned out to be logged in, so
`supabase db query --linked` applied `0001_init.sql` and `0002_seed_teams.sql`
through the Management API — no DB password needed. Three tables, JSONB-first
(`report_json` is the exact public payload the API serves; `evidence_json` is
the pack artifact it came from). RLS enabled on all three with **no policies**
and privileges revoked from `anon`/`authenticated`, so a leaked anon key reads
nothing. Verified live: `has_table_privilege('anon',
'public.scouting_reports', 'SELECT')` returns `false`.

**Two real defects found by building against the live project:**

1. `SUPABASE_URL` in `.env` already ended in `/rest/v1`, so appending it again
   produced `/rest/v1/rest/v1/...` and a `PGRST125 "Invalid path"` that looks
   exactly like a missing table. `rest_base_url()` now accepts both forms.
2. Supabase's default privileges do **not** cover tables created through the
   Management API — `service_role` had no SELECT and PostgREST answered `42501`.
   The migration now grants explicitly rather than inheriting.

**Three defects found by the tests, all fixed in the source rather than the
test:** `generated_at` has second precision, so two reports saved in the same
second tied and "latest" resolved by uuid order (both repositories now break the
tie deterministically); `build_context` called `repository.list_teams()` during
app construction, so an unreachable database stopped the process from starting;
the rate limiter pruned before insertion, making its cap a ceiling-plus-one.
Also: the 404 handler was registered on FastAPI's `HTTPException` subclass while
the router raises Starlette's base class, so framework-default error bodies were
escaping — now registered on the base.

**Performance:** `/api/teams` was issuing one Supabase round-trip per team
(~5 s). Added `latest_report_refs()` to the repository contract — one query for
the whole league, ~420 ms live.

**Cost and safety model, tested rather than asserted.** No public route can
construct an agent backend (verified by wiring a factory that raises and driving
every public route including the PDF). The entire caller-supplied surface is a
team id from a fixed 14-entry allowlist plus a boolean; `GenerateRequest` is
`extra="forbid"`, which turns "no free text reaches a prompt" into an assertion.
Admin token compared with `compare_digest`; unset means generation is *disabled*,
never open; the admin limiter counts unauthenticated attempts so the token
cannot be probed. Errors leak nothing — a connection string, a file path and an
exception message are all confirmed absent from responses.

**LIVE ACCEPTANCE — passed.** `segev:4` HAPOEL JERUSALEM through the real
product path: committed pack → CrewAI (`gemini-3.5-flash`) → validation →
Supabase → API → PDF. **3 provider calls, 0 repair retries, 0 transient retries,
0 hard rejections, 1 warning, 140.5 s.** The warning is `R8` (a recommendation
claimed high confidence on moderate-reliability clutch evidence) — the validator
working on live output. The full write path was rehearsed first with the
deterministic stub backend, at zero provider cost, before spending anything.
Details in `artifacts/acceptance/README.md`; the generated PDF is committed
alongside it.

**Tests: 740 passed** (541 baseline + 199 new), no credentials, no network, no
regressions. New coverage includes all 14 shipped packs loading and
hash-checking, tamper/version rejection, Supabase wire mapping via
`MockTransport`, the never-save-an-invalid-report rule, transient-vs-permanent
provider classification, every API route and error shape, XSS escaping of
model-authored prose, and the migration's RLS posture. `test_production_end_to_
end.py` additionally drives all 14 **real** packs through the whole chain with
the stub backend and requires a valid PDF from each — the synthetic-pack tests
cannot answer whether the committed evidence survives the renderer.

**CI passing** on GitHub Actions, first run. It installs `requirements-ci.txt`
(the full list minus CrewAI, which no offline test needs;
`tests/test_requirements.py` keeps the two manifests from drifting), asserts no
credential is present, and separately proves the 14 shipped packs load and the
app serves `/health` and `/api/teams` in a clean checkout with nothing
configured.

**Railway: ready, not deployed** (per instruction). `railway.json`, `Procfile`,
`.python-version`, and `main.py` — a shim that puts `src/` on the path so
`uvicorn main:app --host 0.0.0.0 --port $PORT` works on a platform that only
runs pip install and a start command.

**Docs:** `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DEPLOYMENT.md`,
rewritten `README.md`, expanded `.env.example`, and
`artifacts/stitch_handoff/` — a designer-facing handoff with real API fixtures
(report, teams, errors, OpenAPI) and seven hard constraints the final frontend
may not break.

**Unresolved / deliberate:**

- Only Jerusalem was generated with a real provider. Batch generation exists
  (`--all`, gated behind `--yes`, with `--dry-run` and `--stub`), but spending
  39 calls to demonstrate a working loop would be waste.
- The frontend is a functional placeholder by design; final visual design is a
  separate step against the Stitch handoff.
- `requirements.txt` includes CrewAI, so a Railway image is large. A serve-only
  deployment can install `requirements-ci.txt` instead — the admin endpoint then
  degrades to a clean 503 because `agents/crew.py` is imported lazily.

**Documentation honesty correction made this run.** `PROJECT_SPEC.md` and
`BUILD_PLAN.md` still described video as the current stage. Both now record
reality — as *records* of decisions already taken, each citing where it was
decided, with §1-7 of the spec left intact as the audit trail. A first draft of
the gate table asserted PASS for Gates 1-4; that was fabrication. This branch's
log records only Gate 0 PASS and CP1 PARTIAL and carries no CP2-CP4 record
(that work lives on `fresh-video-eval`), and the table now says exactly that.

**The frontend was also checked in a real browser**, not only through the test
client: home, a loaded report, the empty state and the error page all render
correctly in dark mode, which is how the raw-timestamp date range was spotted
and fixed.

**Next recommended technical action:** deploy to Railway (§5 of
`docs/DEPLOYMENT.md`), then generate the remaining 13 reports with
`scripts/ops/generate_reports.py --all --yes`. Everything else is downstream of
having a public URL.

---

## 2026-08-19 — Run 13: Agent layer, no-video MVP (`no-video-mvp`)

**Objective:** Management re-scoped the MVP to **no video** (the video layer was
built and fresh-game validated, but failed reliability gates). Build the agent
layer on the now-trusted deterministic stats foundation:
`PBP → EvidencePack → 3 agents → scouting report`. Scope was explicitly reduced
from an earlier 22–30h design to fit ~8–12 active hours.

**Repository management.** `stats-layer @ 1aa2af5` is preserved untouched as the
clean deterministic stats checkpoint. All work happened on new branch
`no-video-mvp` in a new worktree `C:\AI_DEV10\basketball_analytics_mvp`, created
from `1aa2af5`. `master`/scouting and `fresh-video-eval` were not modified.
Note `data/` and `.env` are git-ignored and therefore do **not** travel between
worktrees — both were copied in manually (297 cached PBP games, 182 processed
team-game records).

**Architecture: 3 agents, TRIAGE → INTERPRET → COMPOSE.** The dropped Video
Analysis Agent is replaced by a genuinely distinct role, keeping
`PROJECT_SPEC.md`'s locked "exactly three CrewAI agents" satisfied.

- **Evidence Triage** — Python hands it a deterministically pre-ranked pool of 20;
  it may only drop, reorder within that set, and annotate. It cannot introduce an
  id Python did not select, so it structurally cannot miss a top signal. Agent 1
  is *triage*, not analysis, precisely because selection is already deterministic
  and trusted (`compute_signal_flags`, `is_agent_rankable`, `rank_actionable_signals`).
- **Tactical Scout** — statistical signal → basketball tendency, *proposing* a
  claim strength. `resolve_claim_strength()` recomputes it from provenance and may
  **downgrade, never upgrade**.
- **Head Scout** — cites `implication_id`s, never evidence ids, so "introduces no
  new evidence" is true by construction rather than a policed rule.

**Two structural choices that removed whole validation rules.** The Head Scout
cannot cite evidence directly, and no agent writes numbers at all (`render.py`
attaches every figure from the pack at render time). So "no new evidence" and
"quoted numbers match source" need no checks — preferred structural impossibility
over detection. `ScoutingReport` deliberately has no `key_evidence` field.

**Two real defects found and fixed:**

1. **`effect_size` leaked past the rankability gate.** Verified on `segev:2`
   (24-2): 13 of 15 evidence objects carried a numeric effect (`net_rating`
   = 2.4358) while `win_loss_signal` was `None`, because 2 losses fails
   `AGENT_RANKABLE_MIN_LOSSES = 3`. Now masked with an explicit `effect_status`;
   `build_pack.py --all` fails loudly on any leak.
2. **`reliability_tier` mislabelled net rating "low" for every team.** CV is
   `std/|mean|` and net rating sits near zero, so CV explodes without bound.
   Metrics that cross zero opt out via `MetricSpec.cv_applicable`.

**Degenerate case handled, not discovered late.** Maccabi Tel Aviv (24-2) has
**zero** agent-rankable W/L signals — and W/L is the report's main section. The
pack raises `no_win_loss_evidence`, masks all W/L blocks, prompts inject an
explicit prohibition, and validation R6 rejects outcome framing. It is the only
team of 14 in this state, and it is used as a regression gate rather than a demo.

**Scope cuts applied (approved).** No citation-token/number-templating system; 8
MVP-critical validation rules only; no recorded-response framework (deterministic
stub agents instead); **no `season_scouting.py` at all** — `GameEnrichment`'s
per-game profile objects already expose scoring mix, second chance, fast break
and assisted share, so every one of the 25 evidence items goes through the
existing `build_evidence`. The only casualty is season rim/shot-zone share, which
is `provisional_deterministic` and would have needed a second full 182-game PBP
walk; it is declared in `unavailable_evidence`.

**Results.** Packs build offline for **all 14 teams** (25 items, 20 candidates,
zero leaks, ~6s for the whole league — no caching needed). Full three-stage chain
runs clean via the stub backend for `segev:4` / `segev:11` / `segev:2`: **0 hard
rejects, 0 provider calls**. Tests: **539 passed** (444 existing + 95 new), no
credentials, no network, no regressions.

**BLOCKER — live model run could not be executed.** The Gemini API key returns
`429 RESOURCE_EXHAUSTED — "Your prepayment credits are depleted"`. That is a
billing state, not a rate limit and not transient; retrying cannot help. Verified
against both `google-genai` directly and the CrewAI/LiteLLM path. **Needs a
credit top-up (or a different provider key) — this is a management/account
action, not an engineering one.**

Confirmed despite the blocker: **TLS works through CrewAI/LiteLLM/httpx**, not
just `google-genai` — the request reached Google and returned an
application-level API error rather than a certificate failure, so
`truststore.inject_into_ssl()`'s global `ssl` patch does cover httpx. The plan
had flagged this as must-verify-not-assume.

**Environment.** `crewai 1.15.16` installed into conda `basketball_scouting_env`
(this worktree has no `.venv`; the conda env is what runs the suite). It pulled
~130 packages and **downgraded `pydantic` 2.13.4 → 2.12.5**; a `pip freeze`
snapshot was taken first and the full 444-test suite re-verified immediately
after install. Pinned in `requirements.txt` as a stage-scoped addition.
CrewAI telemetry is disabled in `crew.py` — it phones home on every kickoff by
default and its exporter warnings buried real provider errors.

**Files:** 8 new modules under `src/basketball_scout/agents/`, 2 CLI scripts
under `scripts/scouting_report/`, 6 new test files, `artifacts/scouting_report/`
(README + pack + 3 stub reports, all tracked), `requirements.txt`.

**Not started / out of scope:** FastAPI, UI, PDF, Supabase, player-level
analytics, any video revival.

**BLOCKER RESOLVED, same session.** Credits were topped up and the live run
executed with **no code change**, exactly as predicted.

**Live results — all three teams, 9 provider calls (3 per report), 0 repair
retries, 0 hard rejections:**

- `segev:4` HAPOEL JERUSALEM (18-8) — primary demo, 10 signals / 6 implications
  / 4 recommendations.
- `segev:11` BEER SHEVA (10-16) — regression, 1 warning (confidence exceeded
  evidence reliability).
- `segev:2` MACCABI TEL AVIV (24-2) — degradation gate **confirmed end to end**:
  no W/L columns render, no outcome framing in the prose, every claim
  league-relative, `no_win_loss_evidence` banner present.

**One genuine quality defect found in live output, and a new check for it.** The
first `segev:4` run cited the same implication as both a strength and a
vulnerability, and the strength reading ("offensive efficiency is highly stable")
contradicted its own cited effect size (ORtg W 121.8 / L 110.8, d≈0.97). This is
the residual risk of the no-numbers design: an agent cannot state a *wrong*
number, but it can still mischaracterise a number's *magnitude* qualitatively.
Added `W-dual-framing` — pure set intersection over section `implication_refs`,
deliberately **not** an adjective-versus-effect-size heuristic, which is the
fragile linguistic validation this checkpoint ruled out. Warning, not rejection,
because one bundle can legitimately carry an offensive positive and a defensive
negative. The re-run resolved the framing on its own and hedged to "relatively
steady"; the confidence check then caught two recommendations claiming high
confidence on moderate-reliability clutch evidence — the validator working as
intended on live output.

Final tests: **541 passed** (444 existing + 97 new), no credentials, no network.

**Next recommended technical action:** the agent layer is proven end to end and
FastAPI/UI/PDF may now begin. `artifacts/scouting_report/report_*.json` is the
contract the API should serve — `sections`, `recommendations` and `key_evidence`
are already render-ready, and `unavailable_evidence` should surface in the UI so
a reader sees what the data cannot show. If live prose ever violates validation,
tighten the prompts; **do not loosen the validators**.

---

## 2026-08-16 — Run 12: Deterministic Scouting Feature Pack (`stats-layer`)

**Objective:** Management decision — anything reliably derivable from
Segev/PBP/Python should not be duplicated as a video metric. Consolidate
already-validated deterministic capabilities (geometry, fastbreak,
possession, scoring-source profiles) behind one coherent, tested,
product-facing contract for downstream agents/report/video-join. Explicitly
NOT new metric research — reuse over recomputation.

**Audit first** (brief §2): confirmed worktree/branch/HEAD
(`3a622f7634af9fa7007640ecc886f1b599885d8c`, clean, 5 commits ahead of
`master`), then read `models.py`, `possession.py`, `possession_context.py`,
`scoring_sources.py`, `boxscore.py`, `engine.py`, `enrichment.py`,
`evidence.py`, `profile.py`, `turnover_taxonomy.py`, `pbp/canonical.py`,
`geometry.py`, `fastbreak.py` before writing anything, to avoid duplicating
functionality that already exists.

**New module: `stats/scouting_features.py`.** Two object families (matching
the repo's existing raw/aggregate split):

- Event-level: `DeterministicShotFact` (one per FGA — identity, official
  scoring, `and_one`, `official_assist`, coarse zone/rim/distance/
  eligibility from `geometry.py`, `fast_break` from `fastbreak.py`) and
  `DeterministicPossessionFact` (repackages `possession.Possession` — no
  possession-boundary logic duplicated).
- Aggregation-level: `TeamScoutingSummary`, which *references* (never
  rebuilds) the caller's existing `FastBreakProfile`/`AssistedProfile`/
  `ShotScoringMix`/`SecondChanceProfile`/`PointsOffTurnoversProfile` from
  `enrichment.GameEnrichment`, plus two new-but-trivial aggregations this
  pack genuinely needed and nothing else already computed:
  `ShotZoneDistribution` and `TransitionShotFacts` (FGA-count/rate view of
  the fast-break flag, complementing — not duplicating —
  `FastBreakProfile`'s points-only view).
- New `MetricProvenance`/`ValidationState` taxonomy (`provider_fact` /
  `validated_deterministic` / `provisional_deterministic` / `partial` /
  `deferred`) — no equivalent existed in the repo; every shot fact carries
  per-field provenance so shot_zone (provisional) is never confused with
  fast_break (validated) or distance (partial).

**One small shared-utility change:** `possession._find_and1_shot_ids`
promoted to public `find_and1_shot_ids` (pure rename, confirmed via the full
existing possession suite passing unchanged) so this pack reuses the exact
182-game-validated and-1 query instead of reimplementing it.

**Deliberately narrow, per brief:** no general foul→FT-sequence causal
linkage beyond and-1 (raw provider foul fields documented as available but
not wrapped — avoiding a new fragile heuristic); no generic last-passer
identity (video track's own audit: 38/62 made / 0/78 missed-blocked FGA
linked — explicitly deferred, not attempted); no video metrics of any kind.

**Tests:** 26 new (`test_scouting_features.py`) — identity/join keys,
official-points-authoritative, made/missed/blocked exclusivity, missing-
coordinate honesty, and-1 reuse, official-assist, fast-break-never-becomes-
half_court (structural field-absence check), provenance state-per-field,
possession scored/turnover/duration/OT-quarter-number compatibility, zone
distribution and transition aggregation arithmetic (incl. no divide-by-
zero), team-summary reuse-not-recompute. Full suite: **444 passed, 0
failed** (was 418; +26, 0 regressions).

**Artifacts:** `artifacts/scouting_feature_pack/README.md` (field-by-field
source/provenance/limitations/exclusions writeup) +
`example_output.json` (bounded — 3 example shot facts, 3 example possession
facts, both teams' full summaries for game 136, reproducible via
`scripts/scouting_feature_pack/build_report.py`). Real-data sanity: 140
shot facts / 152 possession facts for game 136; home 70 FGA/55.0% eFG%, away
70 FGA/47.1% eFG%; home fast-break FGA rate 7.1% (consistent with CP2.4B's
validated prevalence range).

**Zero provider/network calls** — cached game 136 PBP only.

**Not started / explicitly out of scope:** Stats Enrichment v3, new metric
research, agents, website, PDF, video metric implementation, player/lineup
analytics — none touched.

**Next recommended technical action:** none required from this pack alone;
it is ready for a future orchestration layer (FastAPI/report/agent) to
consume once that stage begins. If a genuinely necessary trivial field
becomes apparent while wiring a consumer, report it rather than silently
expanding this pack.

---

## 2026-08-16 — Run 11: CP2.4 hardening — seed-211 Gate 5 + lane-depth boundary fix (`stats-layer`)

**Objective:** Management follow-up to Run 10 — recover the accepted seed-211
ground truth (committed on master, `64b6cb8`) read-only, measure the real
Gate 5 baseline, diagnose every mismatch, and apply only general/principled
corrections (no event-specific tuning). Target: ≥95% coarse-zone reliability,
19/20 or 20/20 seed-211.

**GT recovery.** `git show 64b6cb8:artifacts/cp2/cp2_seed211_accepted_ground_truth.csv`
— never merged, never copied into a tracked file; `scripts/cp2/run_seed211_gate5.py`
re-fetches it the same way every run. SHA256
`ec19d209e0964ac59c5d9fc6de8cfcef4cd9dcd79537cd6168ae6a2ee62c2fc5`. All 20 labels
are from game 136.

**Baseline Gate 5: 19/20 (95.0%).** One mismatch (action 1360059, a lay-up
human-labeled `paint`/`lane_2pt`, classified `midrange_2pt`). Root-cause dug
into the *data*, not the event: across all 8 CP2.4 games, Segev shot
coordinates sit on a strict provider-side grid (`y_m` pitch 0.28m, `x_m`
pitch 0.15m) — not freehand. The FIBA free-throw line (5.80m) falls between
grid rows 5.60 and 5.88, only 0.08m from 5.88 — so a genuine paint attempt at
the line can snap to the row just beyond it from quantization alone.
Classified as category C (coordinate noise / boundary ambiguity), not a
wrong geometry rule.

**Accepted fix:** `LANE_DEPTH_BOUNDARY_TOLERANCE_M = 0.14` (half the measured
y-grid pitch — the standard nearest-grid-line allowance, derived from
dataset-wide grid measurement, not from this one event) added to
`_is_within_lane`'s depth test in `geometry.py`. Verified: fixes the one
seed-211 mismatch, creates zero seed-211 regressions, and across the full
8-game/1,104-shot set changes **only** the `lane_2pt`/`midrange_2pt` split
(483→492 / 154→145, exactly 9 shots) — every distance-sanity median and the
92.16% official-family agreement figure are bit-for-bit unchanged. Considered
and **rejected** (no diagnosed evidence): the same treatment for the lane
x-boundary and corner thresholds; a distance-eligibility band change (2.0–4.0m
2PT distribution reviewed, no natural gap found); a systematic coordinate
offset (pooled dunk-centroid offset is small, ~7-14cm, but per-game centroids
disagree in sign and magnitude across only 3–7 dunks/game — noise, not
calibration, and the brief explicitly says not to fragile-calibrate per game).

**Final Gate 5: 20/20 (100%).** Target reached (Level A). Explicitly flagged
as provisional per brief §11 — seed-211 both diagnosed and confirmed the fix,
so it is a tuned diagnostic set, not unbiased held-out validation. **Fresh,
genuinely unseen human-labeled sample is still required for final KEEP** —
none exists in this worktree (seed-211/game-136 is the only human shot-zone
label set found anywhere in the repo); this is reported to management, not
fabricated.

**Verdict update:** SHOT_ZONE → **PROVISIONAL_MOVE_TO_PBP_DETERMINISTIC**
(was PARTIAL in Run 10). DISTANCE → unchanged, still PARTIAL (no distance
rule changed). FastBreak: not reopened, no change.

**Tests:** 3 new (`test_pbp_geometry.py`, none referencing a seed-211 action
ID — encode the general grid-tolerance rule and its boundaries). Full suite:
**418 passed, 0 failed** (was 415; +3, 0 regressions).

**Files changed:** `src/basketball_scout/pbp/geometry.py`
(`LANE_DEPTH_BOUNDARY_TOLERANCE_M` + `_is_within_lane` update),
`tests/test_pbp_geometry.py` (+3 tests), `scripts/cp2/run_seed211_gate5.py`
(new — reproducible original-vs-hardened Gate 5 evaluator, GT read-only from
master), `artifacts/cp2/coords/seed211_gate5.json` (new),
`artifacts/cp2/coords/coordinate_validation.json` (regenerated —
`lane_2pt`/`midrange_2pt` counts only), `artifacts/cp2/coords/
coordinate_validation_report.md` (restructured into ORIGINAL CP2.4 / CP2.4
HARDENING parts, original result preserved, not overwritten).

**Not started / explicitly deferred:** Gate 6 (video spot-check) — unrelated
to this pass, still blocked (game 136 sync `"quality": "failed"`, no other
game calibrated). Fine RA-vs-paint boundary, `secondary_transition` — still
out of MVP scope.

**Unresolved issue for management:** same as Run 10, now sharper — a fresh,
unseen human shot-zone label sample (or an explicit waiver) is the one thing
standing between PROVISIONAL_MOVE_TO_PBP_DETERMINISTIC and final lock.

**Next recommended technical action:** if/when a fresh label sample is
supplied, re-run `scripts/cp2/run_seed211_gate5.py` against it unchanged (no
further tuning) as the final validation; if it holds near 95%+, promote per
Run 10's "next recommended technical action" (wire `geometry.py` into the
stats enrichment layer).

---

## 2026-08-16 — Run 10: CP2.4 deterministic PBP validation (coords + fastBreak) (`stats-layer`)

**Objective:** Validate, without video, whether (A) Segev `coordX`/`coordY` shot
coordinates can support a coarse deterministic shot zone + distance, and (B) the
provider `fastBreak` flag can support an MVP `fast_break`/`non_fast_break` binary.
Implement only if the evidence justifies it; do not force promotion.

**CP2.4A — coordinates.** New `pbp/geometry.py` (independent of the video
pipeline's `pbp/canonical.py`, no video dependency). Hypothesis
`x_m=coordX/100, y_m=coordY/100`, basket at `(7.5, 1.575)` on a 15m-wide court —
tested, not assumed. Validated on 8 games / 1,104 shots: all distance-sanity
gates pass (dunks median 0.485m/95.0%≤1.5m; layups 1.773m; 2PT jumpers 5.142m;
3PT jumpers 9.305m/96.4%≥6.0m); official 2PT/3PT family agreement 92.16%
outside a ±0.30m arc-ambiguity band (target ≥85%); orientation confirmed
consistent across home/away and all 4 quarters (layup-distance medians within
~0.2m of each other) — no flip logic needed. **Correction to an earlier
same-session ad-hoc read:** of the 83 official/geometric disagreements, only
51 (61%) are corner-3-shaped; the other 32 (39%) are official-2PT "long twos"
geometrically 6.4–8.6m out, unexplained by corner geometry — reported as an
open finding, not silently attributed to corners. **Two of six promotion
gates (brief §10) could not be executed**: no "seed-211" human shot-zone
label dataset exists anywhere in the repo (`data/validation/
video_events_ground_truth.csv` is header-only), and game 136's video sync is
already marked `"quality": "failed"` (`operator_lag_std_s=13.39`) in
`data/manifest/matchday.json`, with no other game calibrated — building new
sync was explicitly out of scope. Verdict: **SHOT_ZONE / DISTANCE: PARTIAL**
— all 4 executable gates pass; gates 5–6 (human/video ground truth) are a
genuine resource gap for management to resolve, not a model failure.

**CP2.4B — fastBreak.** New `stats/fastbreak.py`, a lightweight per-shot
state-machine walk over raw actions (parallels `scoring_timeline.py`'s
pattern), complementary to `possession.py`'s aggregate `fast_break_points`
(not a duplicate — no per-attempt diagnostic existed). MVP rule:
`fast_break := provider.fastBreak == True`; `non_fast_break` is only "provider
didn't flag it" — never `half_court`/`defense_set`, per the brief's binding
semantic. Validated on 10 games / 1,594 attempts: pooled prevalence 8.28%
(reported as diagnostic only per brief §15, not gated); 95.5% of positives
are first-attempt-of-possession; 94.4% resolve ≤8s after a possession-change
boundary (median 5.0s); 88.6% triggered by defensive rebound or live
turnover — strong independent timing/semantic corroboration. Converse check:
5.7% (64/1,123) of first-attempt provider-negatives have elapsed ≤4s — real,
measured false-negative-risk evidence supporting the brief's warning that a
negative must never be read as "defense set." Verdict: **POSSESSION_TYPE:
PASS — AUTHORITATIVE_MVP_SIGNAL** for the positive claim; negative remains
supporting-only by design.

**Gitignore fix (small, in-scope).** `artifacts/cp*/*` + `!artifacts/cp*/*.md`
pruned CP1-style one-level-deep paths; CP2.4's required artifact paths
(`artifacts/cp2/coords/...`, `artifacts/cp2/fastbreak/...`) are one level
deeper, so the negation never got a chance to apply and both small (<10KB)
required JSON deliverables were silently ignored. Fixed by un-ignoring the
two subdirectories explicitly, then re-applying the same
markdown/json-summaries-only rule one level in — preserves the original
large-raw-dump protection, just extended to CP2.4's nesting.

**Tests:** 43 new deterministic tests (28 geometry, 15 fastbreak) covering
normalization, distance, coarse zones, the official-points hard constraint,
the ambiguity band, eligibility banding, missing/bad input, provider
true/false/missing, rebound/turnover/opponent-score boundaries, post-OREB
non-candidacy, quarter start/end, free-throw final-of-trip handling, and
output determinism. Full offline suite: **415 passed, 0 failed** (was 372;
+43 new, 0 broken).

**Files changed:** `src/basketball_scout/pbp/geometry.py` (new),
`src/basketball_scout/stats/fastbreak.py` (new),
`tests/test_pbp_geometry.py` (new), `tests/test_stats_fastbreak.py` (new),
`scripts/cp2/run_cp24_validation.py` (new, reproducible validation runner),
`artifacts/cp2/coords/coordinate_validation_report.md` + `.json` (new),
`artifacts/cp2/fastbreak/fastbreak_validation_report.md` + `.json` (new),
`.gitignore` (narrow fix above).

**Not started / explicitly deferred:** fine RA-vs-paint zone boundary,
`secondary_transition`, seed-211/video-spot-check gates (blocked, see
above), any product/enrichment work beyond CP2.4's validation scope.

**Unresolved issue for management:** promotion of SHOT_ZONE/DISTANCE past
gates 5–6 needs either a seed-211-equivalent human label set to be supplied,
or an explicit waiver decision — this worktree cannot manufacture either.

**Next recommended technical action:** if gates 5–6 are waived or a label
set is supplied, wire `geometry.py` into the stats enrichment layer
(shot-zone/distance fields on `TeamGameComponents` or an evidence object,
following the `use_canonical_aggregate`/provenance pattern from Run 9) and
do the same for `fastbreak.py`'s `fast_break` binary. Until then, no further
action on this track — do not implement zone/distance into production
ahead of a gate decision.

---

## 2026-08-15 — Run 9: Stats Enrichment v2 — final semantic integrity check (`stats-layer`, not committed)

**Objective:** Two final checks before commit — the canonical-aggregate-
value question raised by the previous handoff's own wording, and a timeout
deduplication confirmation. One real bug found in each. No new features,
no scope expansion.

**1. Canonical aggregate vs. per-game mean — real bug, fixed.** Audited
what v1's own accepted engine treats as "canonical": found v1 already has
**two coexisting conventions** — `profile.py`'s `basic.metrics` (unweighted
mean of per-game ratios) for the ten core metrics, vs. `_season_shot_mix`
(volume-weighted, sum-of-raw-counts) for shot-mix shares. Standard
basketball-statistics convention (season eFG%, TOV%, ORtg, etc. are always
volume-weighted, matching how `_season_shot_mix` already treats shot-mix)
sides with the weighted convention. `EvidenceObject.value` was using the
unweighted mean for season-level ten-metric evidence — genuinely
inconsistent with basketball convention and with v1's own shot-mix
precedent. **Fixed:** new `segment_metrics.build_canonical_aggregate_metrics()`
sums raw `TeamGameComponents` across every eligible game and calls the
*same* `formulas.py` functions once on the totals — no new formula, same
pattern `compute_segment_metrics` already uses for possession subsets,
applied instead to pre-aggregated per-game components. `build_evidence()`
gained `use_canonical_aggregate: bool` (season-level evidence in the v2
preview script now passes `True`); `stability.mean` is completely
unaffected — it still and always means "unweighted per-game distribution",
now with an explicit `stability_definition` provenance field alongside
`value_definition` so a consumer never has to guess which convention
either field uses. Real-data confirmation (Beer Sheva season eFG%):
canonical value 0.5166 vs. unweighted mean 0.5202 — a genuine, measurable
divergence, not just the synthetic textbook example. Segment-level
evidence (clutch, quarter, score-state cuts) still uses the unweighted
mean — no raw-component aggregation path exists yet for possession-derived
segments — honestly labeled via the same provenance field, not silently
left ambiguous.

**2. Timeout deduplication — real bug, fixed.** Two consecutive timeouts
by the same team with no possession between them were each independently
matched to the identical next possession and its points/FGA/FGM were
**summed twice** (`after_timeout_possessions_found=2, points=8` for a
game truly containing one 4-point possession). Fixed: the matched
possession is now deduplicated by `possession_index` — contributes to the
sample at most once regardless of how many timeout actions point to it.
`own_timeouts_with_team` is unaffected (still counts every real timeout
action — a legitimate, separate fact). Confirmed with the exact
adversarial case from both this run and different-team-consecutive-
timeout variant.

**Full revalidation:** offline suite **366 passed, 6 skipped, 0 failed**
(up from 362: +4 new — 2 timeout-dedup regression tests, 3 canonical-
aggregate tests net of one consolidated). v1 182-game sweep: 0 anomalies
(v1 untouched). v2 182-game sweep: 0 anomalies (both fixes verified across
all 14 teams' real data, including NaN/Inf and rank/percentile bounds
checks now run against the canonical-aggregate values).

**Files changed:** `stats/segment_metrics.py` (+`build_canonical_aggregate_metrics`),
`stats/evidence.py` (`use_canonical_aggregate` param,
`value_definition`/`stability_definition` provenance, expanded class
docstring), `stats/possession_context.py` (timeout dedup fix + docstring),
`scripts/enrichment_v2_validate_and_preview.py` (wire canonical aggregate
into season evidence + markdown clarification), 2 test files extended.

**Not committed.** Final `git status`/diff in the handoff report.

---

## 2026-08-15 — Run 8: Stats Enrichment v2 — agent-oriented evidence layer (`stats-layer`, not committed)

**Objective:** Make the accepted v1 enrichment layer (commit `a01c484`)
substantially more useful to the future Data Analysis Agent — league
context, stability, recent-window deltas, a single evidence-object
representation, and transparent (non-composite) candidate-signal flags —
plus three bounded audits (run-response, after-timeout, turnover taxonomy).
No redesign of v1; no new formulas duplicated.

**Built:** `stats/league_context.py` (deterministic rank/percentile vs. the
rest of the league; competition-ranking tie handling; direction is
caller-supplied, never invented), `stats/stability.py` (mean/median/std/IQR/
min/max/CV over per-game values, each gated by a minimum-n applicability
rule), `stats/evidence.py` (`RecentDelta`, the full `EvidenceObject`
combining league context + stability + recent + win/loss over one
already-computed per-game value, and four transparent `SignalFlags` —
`league_extreme`/`win_loss_signal`/`recent_shift`/`stable_pattern` — each a
documented module-level threshold, deliberately not one composite score).

**OREB consequences** (`scoring_sources.py`, extends `SecondChanceProfile`
without duplicating v1's `second_chance_points`): `zero_point_rate_after_oreb`,
`fga_after_oreb`, `multi_oreb_possessions`, `additional_orebs` —
required one new possession field, `fga_after_first_oreb`, mirroring the
existing `points_after_first_oreb` pattern exactly.

**Bounded audits, all three implementable, all three implemented:**
- **Turnover taxonomy** — audited all 182 games' raw `turnover.parameters.type`:
  10 stable provider categories, 0% null, no artifacts. Implemented
  verbatim (`turnover_taxonomy.py`) — provider categories exposed as-is,
  nothing coarser invented on top.
- **Run-response** (`possession_context.py`) — audited the exact
  double-count risk named in the brief (a continuing run past the 8-point
  threshold must trigger once, not retrigger every time the total keeps
  climbing) and verified the fix with real data. Response = the run-on
  team's first possession (by the validated possession model) at or after
  the run-crossing play; truncation (no subsequent possession, e.g. run
  ends the game) excludes the instance rather than fabricating a value.
- **After-own-timeout** (`possession_context.py`) — audited real edge
  cases: 3.9% of timeouts carry no team attribution (excluded, not
  guessed), 3.3% occur mid-free-throw-trip (handled automatically by the
  "first possession whose offense matches the calling team" rule, which
  naturally skips a continuing trip rather than needing a special case).
  Timeouts where the calling team doesn't get the ball next (defensive/
  reactive timeouts) are excluded by the same offense-match check.

**Real-data validation across all 182 games / 14 teams: 0 anomalies** —
percentile always in [0,100], rank always in [1, eligible_teams], recent
deltas equal their own component difference to floating tolerance, no
NaN/Inf anywhere in evidence output, OREB-consequence totals never exceed
game totals, run-response/after-timeout sample counts never exceed their
own opportunity counts (no double counting).

**Tests:** 40 new across 4 new test files (`test_stats_league_context.py`,
`test_stats_stability.py`, `test_stats_evidence.py`,
`test_stats_possession_context.py`) — direction/tie/insufficient-sample
handling for league context; n-gated applicability for stability; recent-
delta arithmetic and all four signal-flag thresholds for evidence; run-
crossing no-double-count, truncation exclusion, and timeout team-mismatch
exclusion for the two bounded-audit features. **352 passed, 6 skipped
(network-marked), 0 failed** (up from 312).

**Review artifacts:** `artifacts/stats_enrichment_v2/` (git-ignored, new
`.gitignore` rule added) — league QA summary plus JSON+Markdown evidence
previews for the same deterministic three-team selection as v1 (Maccabi
Tel Aviv 24-2 / Beer Sheva 10-16 / Maccabi Raanana 6-20), covering the ten
season metrics' league context/stability/recent-shift plus five
high-value segment cuts (clutch eFG%/TOV%, Q4 net rating, behind-6+ eFG%,
1H net rating) with their win/loss evidence.

**Files changed:** `stats/possession.py` (+`fga_after_first_oreb` field
only — no behavior change to existing fields), `stats/profile.py`
(aggregate the new OREB fields, recompute their season rates),
`stats/scoring_sources.py` (`SecondChanceProfile` extension), `.gitignore`
(+1 rule), 6 new `stats/*.py` modules, 4 new test files,
`scripts/enrichment_v2_validate_and_preview.py`.

**Not committed.** Final `git status`/diff in the handoff report.

---

## 2026-08-15 — Run 7: Enrichment v1 final hardening before commit (`stats-layer`, not committed)

**Objective:** Resolve one correctness blocker (and-1 possession continuation)
plus three bounded management decisions (AST/TO definition, unresolved
assists, preview artifact tracking) before the enrichment layer is approved
to commit. No scope expansion.

**1. AND-1 BLOCKER — corrected, not a false alarm.** Management was right to
reject the earlier ~0/250-sample claim. Exhaustive audit of all 182 cached
games, corrected query semantics (opponent commits the foul, `fouledOn`
matches the shooter, `kind=="shooting"`, `freeThrows>0`, adjacency measured
by skipping only the true non-boundary "attached" action types —
`assist`/`block`/`deflection`/**`foul-drawn`**): **729 confirmed and-1
sequences across 180/182 games, 6.5% of all 11,268 made shots.** The
original miss was a real bug in the audit query, not a property of the
data: it never skipped `foul-drawn`, which Segev *always* inserts between
the shot and the deciding foul, so "next real action" never reached the
foul. Root-caused and fixed: `possession.py` now pre-scans each game
(`_find_and1_shot_ids`) and leaves a qualifying made shot's possession open
until its free-throw sequence resolves, instead of always closing on the
make. Assist linkage updated to also resolve against a still-open (and-1)
possession, not only closed ones.

**Second bug found and fixed during the same hardening pass**: the and-1
fix initially broke game 74 (3 FGA silently vanished game-wide) — a stray
"offensive rebound logged right after a made shot" provider oddity
(already known, previously harmless) collided with an and-1 possession
still open pending its FT: the existing fallback path called `open_new()`
directly, silently overwriting (losing) the in-progress and-1 possession's
FGA/FGM/points instead of closing it first. Fixed structurally, not just at
that one call site: `open_new()` itself now force-closes any still-open
possession before opening a new one, so this class of bug cannot recur at
a different call site later. Re-verified: game 74 (and all 182) reconcile
exactly again.

**2. AST/TO DEFINITION — changed per management decision.** `Possession`
gained `raw_assist_count` — every provider assist action for a possession,
resolved or not — and `segment_metrics.py`'s AST/TO numerator now sums
that (was `assisted_fgm`, the shot-linkage-only count). Verified: **all
ten** core metrics (not nine) now reconcile *exactly* between the
possession model and the season-level `engine.py` on a full game (game
136: home/away AST 26/17 match `boxscore.py` exactly). Shot-level
assisted/unassisted attribution (`AssistedProfile`) is unchanged in meaning
but explicitly separated from AST/TO in its docstring.

**3. UNRESOLVED ASSISTS — bounded check performed, resolver NOT built.**
One deterministic question asked and answered: can `assist -> foul ->
associated made shot` be resolved safely? Checked all 732 season-wide
occurrences of this pattern for an unambiguous preceding made shot,
same team: **0 resolvable** (709 have no preceding made shot within the
adjacency window at all; the remaining 23 have a team mismatch). Per
instruction, stopped there — no resolver, no guessing. `AssistedProfile`
gained explicit provenance fields instead:
`total_provider_assists`/`resolved_shot_attributed_assists`/
`unresolved_assist_count`/`unresolved_assist_rate`, threaded through
`profile.py`'s season aggregation (rate recomputed from summed counts,
matching the existing shot-mix convention) — so a downstream agent can
never mistake `assisted_fgm_pct` for 100%-coverage ground truth.

**4. PREVIEW ARTIFACTS — narrow `.gitignore` rule added.**
`artifacts/stats_enrichment/` only (not a blanket `artifacts/` rule) —
verified `artifacts/cp1/`'s tracked `.md` files are unaffected.

**5. PREVIEW SELECTION — verified, not corrupted, not changed.** Re-ran
selection after all fixes: highest = Maccabi Tel Aviv (24-2, 0.923), median
= Beer Sheva (10-16, 0.385, the 8th of 14 teams by wins descending — a real
tie with Maccabi Ramat Gan at 10 wins broken deterministically by
`team_id`), lowest = Maccabi Raanana (6-20, 0.231) — identical teams/records
to the prior handoff; Markdown and JSON agree (generated from the same
objects in the same run).

**6. W/L SAMPLE SAFETY — demonstrated, not redesigned.** Every ranked
differentiator row already carries `sample_wins`/`sample_losses` next to
`effect_size` (both in the Markdown table's `n (W/L)` column and the full
`MetricSignal` JSON — `sample_sufficient`/`effect_note` included), and
`is_agent_rankable()` already gates the ranked list at
n_wins>=3/n_losses>=3/finite-nonzero-variance before anything is shown.
Confirmed by direct inspection of a real regenerated preview; no design
change made.

**7. Full revalidation:** offline suite **311 passed, 6 skipped, 0
failed** (up from 301: +9 and-1 regression tests, +2 segment-metrics
reconciliation tests, +2 AssistedProfile provenance tests). Full 182-game
enrichment sweep: **0 anomalies** (after both bug fixes — the and-1
implementation alone introduced the game-74 regression, caught precisely
because the sweep is run after every change, not assumed clean).

**Files changed this run:** `stats/possession.py` (and-1 continuation +
`raw_assist_count` + the `open_new()` structural safety fix),
`stats/segment_metrics.py` (AST numerator + docstring),
`stats/scoring_sources.py` (`AssistedProfile` provenance fields),
`stats/profile.py` (aggregate the new fields, recompute
`unresolved_assist_rate`), `.gitignore` (+1 narrow rule), 3 test files
extended (`test_stats_possession.py` +9, `test_stats_scoring_sources.py`
+2) plus 1 new (`test_stats_segment_metrics.py`, 2 tests). Regenerated
(not newly created) preview artifacts under `artifacts/stats_enrichment/`
— now git-ignored.

**Not committed.** Final `git status`/diff in the handoff report.

---

## 2026-08-15 — Run 6: Full PBP enrichment layer v1 (`stats-layer`, not committed)

**Objective:** Build the full deterministic PBP analytics enrichment layer
on top of the accepted 182-game foundation (Run 5): possession-level state,
game-flow/clutch/score-state/recent/home-away segmentation, scoring-source
statistics, assisted/unassisted profile, shot mix, runs/droughts,
score-dynamics/comebacks, and a generalized W/L effect-size comparison over
all of it — structured evidence for a future Data Analysis Agent, no
narrative interpretation anywhere. Full detail (definitions, architecture)
in the new `docs/STATS_ENRICHMENT.md`. **Not committed** — returned for
management review per instruction.

**Built:** `stats/possession.py` (canonical FIBA possession-state builder —
the foundation everything else derives from; never uses `userTime`),
`stats/scoring_timeline.py` (chronological scoring-play list),
`stats/segments.py` (quarter/half/clutch/score-state/close-score/late-close
classification), `stats/segment_metrics.py` (possession subset -> the SAME
`formulas.py` functions the season engine already uses — no new metric
arithmetic anywhere), `stats/scoring_sources.py` (points off turnovers,
second chance, provider fast-break, assisted/unassisted, shot/scoring mix),
`stats/runs_droughts.py` (scoring runs, custom scoring/FG-drought metric),
`stats/dynamics.py` (ties/lead-changes/largest-lead-deficit/comebacks),
`stats/enrichment.py` (per-game orchestration -> `GameEnrichment`),
`stats/profile.py` (cross-game windows, JSON-serializable team profiles,
segmented W/L differentiator ranking). `stats/winloss.py` extended
(non-breaking) with `build_metric_signal`/`compute_signal_from_pairs`/
`is_agent_rankable` so segmented metrics reuse the exact same accepted
effect-size machinery as the season-level ten metrics.

**Validation on real data — critical, found and fixed 2 real bugs before
trusting the model:**
- Possession totals initially mismatched the already-validated
  `boxscore.py` component totals by small amounts (ORB off by 1, TOV off
  by several). Root causes: (1) a fallback branch for an unattributed
  offensive-rebound context forgot to increment the ORB counter; (2) a
  turnover with no shot ever attempted in that possession (e.g. a
  5-second violation, or any turnover immediately following the prior
  possession's made basket) fell through unopened and was silently
  dropped. Both fixed; re-verified: 9 of the 10 core metrics now
  reconcile **exactly** between the possession model and `engine.py` on a
  full game (game 136) — the 10th, `ast_to_ratio`, differs by a small,
  understood, documented amount (see below).
- Real-data investigation surfaced a genuine Segev provider nuance: some
  `assist` actions link (`parentActionId`) to a `foul` action instead of a
  made shot — apparently representing and-1-adjacent plays. Not guessed
  at; surfaced explicitly as `unresolved_assist_count` (5/43 assists in
  game 136, ~10% typical across the season) rather than silently
  mis-attributed as "unassisted".
- Confirmed a real "team offensive rebound logged right after a made shot"
  provider oddity exists (not a bug); handled conservatively (see docs).

**Full 182-game sweep result: 0 anomalies.** Re-built `GameEnrichment` for
every one of the 182 already-accepted games (from the existing raw cache +
already-validated `TeamGameStats`, no new network calls) and checked: shot
count / scoring-mix reconciliation, quarter+half+OT additive reconciliation
against game totals, exactly one win/loss and one home/away row per game,
clutch⊆late_close possession-count consistency, assisted+unassisted==FGM,
second-chance points never exceeding total points, largest run never
exceeding final score. All 14 teams present with clean per-team data.

**Three real-team previews generated** (highest / median / lowest
win%, deterministic selection): Maccabi Tel Aviv (24-2), Beer Sheva
(10-16), Maccabi Raanana (6-20) — Markdown + machine-readable JSON under
`artifacts/stats_enrichment/` (git-ignored, not auto-committed). Spot-check
of the Beer Sheva preview: effect-size ranking visibly differs from what
raw-difference ranking would produce (e.g. `orb_pct` effect 0.985 ranked
above `ast_to_ratio`'s larger raw difference but weaker effect) — same
finding pattern as the season-level signal engine, now confirmed at the
segmented level too.

**Tests:** 70 new tests across 6 new test files (possession continuity/
boundaries incl. and-1-negligible documentation and a real-data regression
against games 178/209/224; segment classification incl. every clutch
threshold boundary and the clutch⊆late_close⊆close_score nesting; runs/
8+-run counting/period-non-boundary; droughts incl. the exact 3:00
boundary, FT-vs-FG-drought distinction, and no-bridge-across-quarter
behavior verified via the internal gap list; dynamics incl. tie-through-
lead-change and correct-denominator comeback/blown-lead rates; scoring
sources; profile window selection by actual date not Segev id). **301
passed, 6 skipped (network-marked), 0 failed** — up from 239.

**Files changed:** 9 new `src/basketball_scout/stats/*.py` modules, 1
extended (`winloss.py`, additive only — existing behavior/tests
unaffected), 6 new test files, `scripts/enrichment_validate_and_preview.py`
(new), `docs/STATS_ENRICHMENT.md` (new). No video-track files touched.

**Features requested but simplified/deferred, with reasons:** and-1
possession-continuation lookahead (negligible real incidence, documented
simplification, not a missing feature); exact `unresolved_assist_count`
attribution (would require guessing without further Segev-semantics
investigation — surfaced as an explicit count instead); shot-zone geometry
— explicitly out of scope per the brief (video track's territory).

**Not committed.** `git status`/`git diff --stat` in the handoff report;
nothing staged.

---

## 2026-08-15 — Run 5: Statistics track, targeted 182-game dataset recovery (`stats-layer`)

**Objective:** Management supplied 4 specific Segev ids (148, 178, 209, 224)
claimed to be the missing regular-season games and asked for a targeted
(not broad-scan) diagnosis. Still no commit.

**Root cause, per id (inspected individually, not assumed to share a cause):**

- **148** (Kiryat Ata-Hapoel Holon) — **not actually missing.** Already
  present in the 178-game set from Run 4 (`gameFinished=True`, clean data).
  Management's list appears to have reused the id of the pair's *known*
  meeting rather than its true missing second meeting. Reported as a
  correction, not silently absorbed.
- **178, 209, 224** — genuinely excluded, but for a shared *pipeline* cause
  with different *data-provenance* stories: `gameInfo.gameFinished=False` in
  both `getActions` and `getBoxScore` for all three (a real upstream Segev
  metadata bug), despite every quarter having its own `end-of-quarter`
  marker and `getBoxScore`'s `home/awayQuarterScores` summing exactly to
  `home/awayScore`, which itself matches the independently-computed
  `getActions` score (178: 103-87; 209: 71-82; 224: 76-103 — all reconciled
  both ways). Per-quarter action density (shots/rebounds/FT/etc.) is uniform
  across all 4 quarters for all three, matching a normal complete game — no
  sign of truncation. Underlying timestamp quality differs per game (178:
  100% of actions share one bulk-insert `userTime`; 209: quarters 1-3 bulk,
  quarter 4 live-timed; 224: essentially fully live-timed, only the
  finished-flag/end-of-game marker missing) — irrelevant to the stats engine
  (never reads `userTime`), but confirms the 3 failures are not one uniform
  data event. **Correction to management's hint:** id 178's `getBoxScore`
  was described as an incomplete/3-quarter snapshot; direct inspection this
  session shows a complete, self-consistent 4-quarter boxscore.

**Classification:** all three (148 excluded as not-applicable) are
**VALID_SEGEV_DATA** — no `RECOVERABLE`/`OFFICIAL_FALLBACK`/`UNRECOVERABLE`
case applied; no basket.co.il fallback was needed.

**Fix (smallest necessary):** `stats/schedule.py` — `DiscoveredGame` gained
`quarters_verified_complete: bool | None`, computed by new
`_quarters_verified_complete()` purely from the action stream's own
`end-of-quarter` markers (source-observable, no inference/fabrication).
`is_usable` now accepts `game_finished OR quarters_verified_complete`
(previously `game_finished` alone). 8 new regression tests, including one
proving a game shaped exactly like the real 178/209/224 (stale flag, closed
quarters) is discovered as usable, not silently dropped.

**Rerun result: 182/182.** Full id 1-450 rescan (cache-backed, no new
network calls beyond the initial per-id `getBoxScore` cross-checks) with the
fixed filter: **all 91 pairs now have exactly 2 meetings, all 14 teams at
exactly 26 games** — the full official contract met exactly, not forced.
Re-ingested all 182 through the engine: **zero errors**, 364 team-game rows,
**zero reconciliation/range/pace-symmetry issues** in the sweep. 9 OT games
(2 double-OT) correctly normalized. W/L report verified clean for all 14
teams.

**Tests:** 239 passed, 6 skipped, 0 failed (up from 232).

**Files changed:** `stats/schedule.py` only (`DiscoveredGame` field +
`_quarters_verified_complete()` + `is_usable` logic), `tests/test_stats_schedule.py`
(+8 tests). `data/processed/stats/` (gitignored) now holds the final 182-game set.

**Remaining source-quality risk:** the `gameFinished` flag is now known
unreliable for at least 3 games in this one season; nothing rules out the
same staleness recurring for future seasons/games, so the completeness
fallback should stay in place permanently, not be treated as a one-off patch.

---

## 2026-08-15 — Run 4: Statistics track, management review response (`stats-layer`)

**Objective:** Resolve three targeted management review items on the
provisionally-accepted stats track: (1) expand the historical dataset from
one round-robin to the full double round-robin, (2) fix W/L ranking to use a
standardized effect size instead of raw difference, restricted by default to
actionable factors, (3) audit pace/possession/OT/FTR conventions. Still no
commit — provisionally accepted, not yet integrated.

**Item 3 (pace/possession audit) — verified already correct, no formula
changed.** `pace()` averages `team_possessions`+`opponent_possessions`
before dividing, so it is symmetric by construction (`home.pace ==
away.pace`, confirmed numerically on real games including a double-OT game).
`minutes_played` is always the real elapsed minutes (`game_minutes()`:
40/45/50 for 0/1/2 OT periods). Strengthened docstrings in `formulas.py` and
added 2 regression tests (`test_pace_is_symmetric_regardless_of_call_order`,
`test_pace_uses_actual_ot_minutes_not_fixed_regulation`). FTR convention for
the record: `FTR = FTA / FGA` (attempts, not makes).

**Item 2 (W/L ranking) — `winloss.py` rewritten.** Added `MetricSignal`
fields `category` (`outcome_context`/`actionable`), `pooled_std`,
`effect_size` (signed, `(win_mean-loss_mean)/pooled_sd`, Cohen's-d style,
ddof=1), `effect_note` (`insufficient_sample_for_variance` when either group
has n<2; `zero_pooled_variance` when pooled variance is exactly 0 — both
return `None` rather than fabricating inf/nan). New
`rank_actionable_signals()` is the default entry point: ACTIONABLE metrics
only (eFG%, TOV%, ORB%, FTR, 3PA rate, AST/TO), ranked by `|effect_size|`.
`compute_signals()` still returns all ten, unranked, for overview.
`rank_signals()` is a generic sort helper. 15 new tests, including one that
constructs a case where raw-difference ranking and effect-size ranking
produce different top metrics (`test_effect_size_ranking_differs_from_raw_difference_ranking`)
— verified this actually happens on real data too (Beer Sheva: raw diff
would rank `ast_to_ratio` highest; effect-size correctly ranks `orb_pct`
highest instead, because ORB%'s within-group variance is much tighter).

**Item 1 (182-game regular season) — new `select_double_round_robin_games()`
in `schedule.py`.** Re-checked `getBoxScore` for a round/phase field (one
bounded request) — none exists (`boxscore.gameInfo` has scores/timeouts/
quarter/finished/ids only). Selection rule instead: group discovered
"Winner League"+finished games by unordered `{home_team_id, away_team_id}`,
sort each pair chronologically, keep the first two — deterministic, uses
only data already in hand, needs no "is this a playoff" label (playoff
rematches are excluded purely by being a 3rd+ chronological meeting).

**Live scan result:** continuous id range 1-450 (297 responsive ids, ~2.5
min at 0.1s spacing, cache-reused across sub-scans). Found **all 14 teams,
all 91 unordered pairs**; 87 pairs resolved to exactly 2 meetings, **4 pairs
resolved to only 1** (Galil Elion-Hapoel Holon, Hapoel Jerusalem-Beer Sheva,
Galil Elion-Maccabi Ramat Gan, Hapoel Holon-Kiryat Ata) even after the full
scan (including the June playoff id block 354-450, which correctly does
*not* get picked as a false second meeting — verified the algorithm still
returns exactly the same 178 either way). **Final selection: 178/182
games**, not the full 182 — reported honestly, not forced. One instructive
anomaly found and resolved along the way: game id=23 (Bnei Herzliya-Hapoel
Holon, played 2025-12-24) sat far outside the id block its air-date would
suggest (surrounded by September Winner Cup ids) — likely a reschedule that
kept an early-assigned id; it was the second meeting for what would
otherwise have been a 5th incomplete pair. No similar recovery was found for
the remaining 4 pairs after scanning 1-450 continuously; a genuine source
gap (postponed/unplayed/differently-recorded fixture) is the working
hypothesis, not confirmed.

**Full ingestion + sweep on the 178-game set:** 178/178 built with zero
engine errors. 356 team-game rows, **0 range/reconciliation/pace-symmetry
issues**. Per-team counts: 8 teams at 26 games, 4 teams at 25 (each missing
exactly 1 of the 4 short pairs), 2 teams at 24 (Galil Elion and Hapoel
Holon, each involved in 2 of the 4 short pairs) — sum 356 = 178×2, exactly
consistent with the pair-count shortfall. 9 OT games detected (2 of them
double-OT), all correct. `data/processed/stats/` (gitignored) now holds
these 178 games, replacing the prior 93-game development sample.

**Tests:** 232 passed, 6 skipped (network-marked), 0 failed — up from 217 (2
new pace tests + 9 new schedule-selection tests + rewritten
`test_stats_winloss.py`, net +15 tests there).

**Files changed this run:** `stats/formulas.py` (docstrings only),
`stats/winloss.py` (rewritten), `stats/schedule.py` (added
`select_double_round_robin_games`/`RegularSeasonSelection`, extended
`DiscoveredGame` with team ids), `scripts/winloss_report.py` (rewritten for
new API/output), `tests/test_stats_formulas.py` (+2),
`tests/test_stats_schedule.py` (+9), `tests/test_stats_winloss.py`
(rewritten). No video-track files touched. No commit, no merge.

**Remaining risk:** the 4-pair/178-vs-182 gap is unresolved and its root
cause unconfirmed (genuinely never played vs. recorded outside the scanned
id space vs. some other source anomaly) — flagged for management, not
guessed at.

---

## 2026-08-15 — Run 3: Statistics layer (parallel track, isolated worktree, branch `stats-layer`)

**Objective:** Build the deterministic PBP/statistics layer (team-game
components, the ten core metrics, W/L signal engine) independent of the
concurrent video-metric track. Autonomous run, no commit (per instructions —
left on `stats-layer` for review).

**Built, all offline-tested:** `src/basketball_scout/stats/` — `models.py`
(`TeamGameComponents`/`DerivedMetrics`/`TeamGameStats`, JSON round-trippable),
`formulas.py` (pure arithmetic for all ten metrics + Oliver/basketball-
reference possession estimate; every ratio returns `None`, never `0.0`/`inf`,
on a zero denominator), `boxscore.py` (Segev action stream -> raw components;
deliberately **not** built on `pbp/canonical.py`, which is the video
pipeline's shot-only contract — out of bounds per the brief), `engine.py`
(orchestration; rejects a tied aggregated score as malformed PBP rather than
guessing a winner), `winloss.py` (win-vs-loss averages/diff/sample-size per
metric, ranked by |difference|, `MIN_SUFFICIENT_SAMPLE=5` documented not a
significance test), `store.py` (flat JSON, no DB), `schedule.py` (discovery
adapter, see below). Two CLIs: `scripts/build_team_game_stats.py`,
`scripts/winloss_report.py`. New tracked fixture
`data/validation/segev_game136_full.json` (the real, complete 867-action
game 136, needed because the existing trimmed fixture is shot-only).
**150 new tests, 217 passed / 6 skipped total** (`python -m pytest`, no
credentials, no network for the suite itself).

**Real-data validation:** game 136 reconciles exactly to the known result
(Maccabi TA 95–84 Hapoel Jerusalem); `NetRating = ORtg − DRtg` holds exactly;
a team's `defensive_rating` is provably identical to its opponent's
`offensive_rating` (same formula, same points/possessions — a structural
invariant, tested exactly not approximately).

**Multi-game discovery (bounded live investigation, ~20 min):** no
`getSchedule`/`getGames`-style JSON-RPC method exists on the Segev API (10
plausible names tried, all `-33000 method not found`).
`https://basket.co.il/pbp/json/games_all.json` is real and public but is a
"next games" widget (12 upcoming 2026-27 fixtures at time of writing), not a
season archive. **What does work:** the Segev numeric `game_id` space itself
is dense and self-describing — every id in a bounded 30-270 probe returned a
real `gameInfo` with its own `competition.name` (Winner League / Winner Cup /
Women / Leumit / School all interleaved). Filtering on that self-reported
field (not an inferred id mapping — the basket.co.il widget id space is a
completely different range, confirmed) is a clean, reliable discovery
strategy. `stats/schedule.py` implements it as a bounded, rate-limited range
scan; nothing hardcodes a game count.

**Executed on real data:** ranged-scanned ids 45–140 (96 ids, 93 usable
Winner League/finished games, 3 correctly filtered out as Cup/other), fully
ingested with **zero engine errors** — essentially the full ~91-game
round-robin target, already exceeded. Full-dataset sanity sweep (186
team-game rows): 0 range/reconciliation issues, all 93 games have a clean
two-team pair, 4 real OT games detected and handled correctly (including one
double-OT game, 50 minutes). W/L report demonstrated for two real teams
(Maccabi Tel Aviv 12-1, Beer Sheva 6-8) — signals and sample-sufficiency
flags both look correct on inspection.

**Open gap (documented, not solved):** the exact game_id boundaries of "one
complete round-robin" vs. the full double round-robin season are not
identified — the same pairing (Beer Sheva–Maccabi TA) appears at two ids
months apart, and ids are not perfectly date-sorted (some fixtures
rescheduled), so a clean date/round cut needs either a round-number field
(only `getActions`' `gameInfo` was checked; `getBoxScore` might have one) or
management judgment on which id range to standardize on. Not attempted
tonight per the brief's explicit "do not force this if unreliable" guidance —
93 real games were ingested as the bounded sample instead, which already
covers the ~91-game MVP target in practice.

**Not attempted (out of the priority order for tonight):** quarter/shot-type/
clutch splits — explicitly gated to "only after core is solid," and the
timebox was better spent validating the core on 93 real games than adding
scope. CrewAI agents, FastAPI, Supabase, PDF, player/lineup analytics — all
explicitly out of bounds per the brief.

**Boundaries respected:** `src/basketball_scout/video/`,
`docs/VIDEO_STAGE_PLAN.md`, `artifacts/cp1/`, and existing video validation
fixtures were not touched. No merge, no commit, nothing pushed.

### Next recommended technical action

1. Management decides the round-robin id-range cutoff (see "open gap" above)
   — likely needs one more bounded check of `getBoxScore` for a round/matchday
   field, or an explicit date cutoff.
2. If the stats layer is accepted, wire `data/processed/stats/` output into
   whatever the next stage (persistence / agents) expects — currently flat
   JSON by design ("no database yet unless required").
3. Optional, cheap-if-added-later: quarter/clutch splits, once core is
   reviewed.

---

## 2026-08-15 — Run 2: CP0 (Video Stage Plan) approved; CP1 authorized and in progress

**Objective:** Produce an implementation-ready Video Analytics Stage Plan (CP0)
via read-only investigation, then execute CP1 (Source & API Feasibility) only.

**CP0 — approved.** Full plan saved at `docs/VIDEO_STAGE_PLAN.md`. Live
read-only investigation (no product code written) overturned or resolved
several bootstrap-era unknowns:

- **SegevSport PBP is solved.** Public, unauthenticated JSON-RPC 2.0 API:
  `https://stats.segevstats.com/realtimestat_heb/api/?method=getActions&game_id=<id>`.
  Every action carries `userTime` (real wall-clock), which **collapses the
  PBP↔video sync problem** from "unknown nonlinear stoppages" to "one constant
  offset per quarter" (`video_t = userTime_s + offset_quarter`, slope 1.0).
  Correction: the briefing's lead file `b-func.js` returns 404; the real file
  is `pbp/js/new-func.js`.
- **Gemini video clipping (`video_metadata.start_offset/end_offset`) is the
  critical unverified risk.** SDK docstring says "for clipping"; official docs
  omit it entirely; a Google staff member confirmed an escalated bug report
  that it was unsupported. Cost swings ~$1.35 (honored) vs ~$205 (ignored) for
  the full matchday. First thing CP1 must test, via a deterministic VIDEO-token
  count, not a two-call heuristic.
- **Gate 0 (public YouTube full-game video) is unverified and may fail.** The
  league's official VOD is on `winnerleague.tv` (Sportradar OTT), not YouTube.
- 2025-26 "Winner League" season is complete and is the correct target;
  2026-27 has not started (fixtures dated 08/09/2026).

Full evidence matrix, architecture, sync design, metric definitions, cost
model, and CP1–CP4 execution plans are in `docs/VIDEO_STAGE_PLAN.md`.

**Checkpoint governance (binding for this stage):** CP1/CP2/CP3/CP4 are
separate execution packages. After every checkpoint — PASS, PARTIAL, or FAIL —
execution **stops for mandatory management review**. A PASS does not
self-authorize starting the next checkpoint.

**CP1 — executed to completion across three management interactions. Final
verdict: PARTIAL.** Full detail in `artifacts/cp1/cp1_report.md`.

**First pass** hit two real blockers and stopped honestly rather than
fabricate: no `GEMINI_API_KEY`, and the target YouTube video's frames not
rendering through the available browser-automation screenshot capture.
**Management accepted this as legitimate**, supplied a real key via `.env`,
and directed that calibration be resolved by a human operator supplying real
timestamps rather than by defeating the automation limitation.

**Gate 0, CP1-C, CP1-A, CP1-E — all PASS, with real evidence:**
- **Gate 0:** public full-game YouTube video confirmed and cross-checked
  against Segev `game_id=136` (Maccabi Tel Aviv 95 - Hapoel Jerusalem 84,
  2026-01-11); breadth confirmed via 3 other rounds from the same official
  channel.
- **CP1-C:** `gemini-2.5-flash` (the plan's placeholder default) is listed by
  the API but returns **HTTP 404 "no longer available to new users"** — a
  real, important finding. Pinned **`gemini-3.5-flash`** instead, verified
  working.
- **CP1-A (the decisive gate):** three real clipped calls (5s/20s/40s) gave
  **exactly linear VIDEO-token scaling** (455/1820/3640 tokens, all 91 tok/s;
  40s = precisely 2× 20s). `video_metadata` offsets are unambiguously honored
  — the single largest cost/viability risk from CP0 is resolved.
- **CP1-E:** 3 real events (Q1/Q3/Q4) classified end-to-end through the full
  pipeline — 3/3 successful, confident non-`uncertain` answers, clean
  `finish_reason=STOP`, full usage/latency captured.

**CP1-B (quota):** soft gap, as instructed by management — service-tier
header observed (`x-gemini-service-tier: standard`), but explicit
quota-remaining accounting is not exposed by the SDK. Not pursued further.

**CP1-D — a core CP0 synchronization assumption was falsified by real data.**
A human operator supplied 9 real timestamps for game 136. The naive
`slope=1.0` model produced residuals of -60 to -69s (unusable). The
residuals were **proportionally consistent** (5.7%/4.3%/6.3% of elapsed real
time) — the signature of a genuine slope error, not noise or a cut. Fitted
slope: **0.943/0.957/0.937 across three independent quarters, averaging
0.9456** — the video runs ~5.4% "faster" than real PBP time. Applying the
fitted slope brought **3 of 4 quarters cleanly within tolerance** (-2.8s,
-9.5s, -1.3s). The 4th (Q2) remained marginal (+15.5s); one additional
bisection timestamp resolved the ambiguity decisively: residual **grew** to
+26.9s at a point *closer* to the anchor — the opposite of what a pure slope
error would produce, meaning Q2 contains a genuine discontinuity (a real
edit/cut) that a single anchor + slope cannot capture.

**Conclusion:** the CP0 finding that PBP `userTime` collapses sync to "one
constant offset per quarter" is directionally correct and highly valuable —
it drove 3 of 4 quarters (75%) to clean calibration once slope was correctly
measured. But `slope=1.0` was wrong (real value ~0.946), and "one anchor
always suffices per quarter" does not universally hold (1 of 4 quarters
needed a second anchor). Per management instruction, **no redesign was
attempted** — `--set-slope` was added to `calibrate_game.py` as a direct
application of the plan's own already-specified §7.5 remedy, not a new
mechanism.

**150/150 tests passing** (46 new this run). No commit made.

### Corrections and hygiene fixed this run

- `.gitignore` previously blanket-ignored all of `artifacts/`, conflicting
  with the plan's own contract (§19.1). Fixed to ignore only
  `artifacts/probe/` and `artifacts/runs/`, with `artifacts/cp*/*.md`
  explicitly tracked.
- `docs/VIDEO_SPIKE_NOTES.md` rewritten as a short pointer to
  `docs/VIDEO_STAGE_PLAN.md` — its bootstrap-era claims are all superseded.

### Next recommended technical action

1. **Approve a targeted revision to `VIDEO_STAGE_PLAN.md` §7** (slope as a
   per-game fitted value, not a 1.0 default; mandatory second anchor point
   per quarter; immediate bisection on a borderline/large residual) — see
   the CP1 audit report §4/§5 for exact recommended wording.
2. **Do not start CP2** until that revision is approved (per governance).
3. Once approved, CP2 (metric feasibility, ~20-event ground truth) can
   proceed on the already-classified pipeline — nothing about source, model,
   or the classification mechanism needs to be redone.

### Plan revision applied (same day, post-verdict)

Management accepted the PARTIAL verdict and issued the recommended
synchronization revision. Applied to `docs/VIDEO_STAGE_PLAN.md` §1.1-1.3,
§3 (A25), §7.1/§7.3/§7.4/§7.5/§7.7, §8.1, §10, §11.2-11.3, §15.4, and §21
(T6) — **documentation only, no synchronization code changed** (the
existing `GameSync`/`fit_slope()` mechanism already implements exactly the
piecewise-affine model now formally described). Key changes: `slope=1.0` is
no longer described as an assumption anywhere; slope is fit at the game
level (not per-quarter by default); two observations per quarter (anchor +
check) are now standard; a confirmed discontinuity's MVP resolution is to
**exclude that quarter**, not the whole game, and not an automatic
segmented mapping; downstream aggregation output gains mandatory
`quarters_usable`/`quarters_excluded` provenance columns. CP1's underlying
evidence (measured slope 0.9456, Q2's confirmed discontinuity) is
unchanged — only the plan's prescribed response to that evidence was
revised. `artifacts/cp1/cp1_report.md` §5 was annotated (not rewritten) to
flag its now-superseded "segment that quarter" language and point to the
new §9 addendum. **CP2 still not started.** No commit made.

---

## 2026-08-14 — Run 1: Bootstrap and preparation

**Objective:** Prepare the repository, persistent documentation and the technical
scaffolding for the Video Analytics risk stage. Explicitly *not* to build the
product.

### Work completed

- **Repository initialized from empty.** No commits existed before this run.
  Created `src/basketball_scout/`, `scripts/spikes/`, `tests/`,
  `data/{raw,processed,validation}/`, `docs/`.
- **Documentation:** `CLAUDE.md`, `PROJECT_SPEC.md`, `BUILD_PLAN.md`, `README.md`,
  `docs/VIDEO_SPIKE_NOTES.md`, `data/validation/README.md`, `scripts/spikes/README.md`.
- **Hygiene:** `.gitignore`, `.env.example` (empty placeholders), `pyproject.toml`
  (pytest `pythonpath=src`, so no install step), minimal `requirements.txt`.
- **Video core** (`src/basketball_scout/video/`): metric registry, event/window
  model with timecode handling, structured classification schema, prompt builder,
  ground-truth fixture I/O with agreement scoring, and an isolated Gemini client.
- **Spikes:** `probe_segevsport.py` (URL-driven HTTP diagnostic) and
  `gemini_video_event.py` (single-event classification CLI).
- **Fixture:** `data/validation/video_events_ground_truth.csv`, header-only.
- **103 offline smoke tests**, all passing.

### Tests / results

`.venv\Scripts\python.exe -m pytest` → **103 passed in 2.34s**, with no
credentials and no network access.

Live verifications performed:

- Probe harness run against `https://basket.co.il/` → HTTP 200, 162.6 KB.
- Gemini request `--dry-run` → correct wire shape, no API call.

### Decisions implemented

- **Metrics are a registry, not hard-coded.** The response schema, prompt, CLI
  and fixture columns all derive from `video/metrics.py`, so replacing a
  provisional metric is a one-line edit plus regenerating the fixture template.
  Covered by a test that swaps in a different metric.
- **Every metric always offers `uncertain`.** Forcing a binary choice
  manufactures false confidence and corrupts the aggregates.
- **Agreement is reported two ways** — overall, and "decisive" excluding pairs
  where either side said `uncertain`. Without the second number, a model that
  answers `uncertain` for everything is unreadable.
- **Only one module knows the provider** (`gemini_client.py`). Metrics, events,
  schema and prompts are provider-agnostic, so an OpenCV/YOLO pivot or a
  provider swap touches one file.
- **Fixture mode is capped at `--limit 1`** by default, so a bulk run must be
  asked for explicitly.
- Chose `.venv` (reproducible from `requirements.txt`) over the pre-existing
  `basketball_scouting_env` conda env. Either satisfies the dependencies.

### Environment findings (both would have blocked the next run)

1. **TLS was broken for every HTTPS host.** `certifi`'s roots are rejected on
   this machine; the needed root CA is in the Windows certificate store
   (proxy/AV interception), and stale `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`
   variables point at an unrelated conda env. Fixed with `truststore` via
   `src/basketball_scout/net.py`, called at the top of every entry point.
   **This affects the Gemini SDK too**, not just `requests`.
2. **`sitecustomize` noise.** Every Python run prints
   `ModuleNotFoundError: No module named 'truststore'` to stderr, from Anaconda's
   global `sitecustomize.py`. Harmless, not ours, ignore it.

### Unresolved issues

- **No `GEMINI_API_KEY` available**, so nothing requiring a live call is
  verified. The SDK *syntax* was confirmed by introspecting google-genai 2.18.1;
  the *behaviour* was not. `docs/VIDEO_SPIKE_NOTES.md` §2 lists each open point
  and its cheap check. The biggest: **whether `video_metadata` offsets are
  actually honoured for YouTube URLs** — if they are ignored, every call ingests
  the full broadcast and the cost model collapses.
- `GEMINI_VIDEO_MODEL` defaults to `gemini-2.5-flash` as a **placeholder**.
  Confirm with `--list-models` before relying on it.
- **PBP extraction method still unknown.** The probe found a strong lead:
  `basket.co.il` loads `/pbp/js/new-func.js` and `/pbp/js/games.js`, i.e. there
  is a dedicated `/pbp/` area. Not investigated further — out of scope for a
  preparation run.
- Clock alignment between PBP and the YouTube broadcast is unsolved and is the
  most likely Gate 2 failure point.

### Next recommended technical action

**Gate 0, then §2.3 of `docs/VIDEO_SPIKE_NOTES.md`.**

1. Obtain a real full-game YouTube URL plus matching PBP for the same game.
2. Set `GEMINI_API_KEY`, then run `--list-models` and pin `GEMINI_VIDEO_MODEL`.
3. **Before labelling anything**, verify the time window is honoured: classify
   one event, then re-run with a window from a different part of the game. If
   the answers and evidence strings don't change, offsets are being ignored —
   stop and report, because that invalidates the approach as designed.
4. Only then calibrate clock alignment and start the 20-event Gate 1 labelling.

---
