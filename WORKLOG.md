# WORKLOG

Running project log. Newest entry first. Concise and useful to a future
session — not a place for terminal output.

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
