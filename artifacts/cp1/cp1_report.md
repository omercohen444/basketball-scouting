# CP1 Report — Source & API Feasibility

**Date:** 2026-08-15
**Scope:** docs/VIDEO_STAGE_PLAN.md §15 (CP1 execution plan), executed under checkpoint
governance (CP2 not started; awaiting management review).

**Status: CP1 EXECUTION COMPLETE. Final verdict: PARTIAL.** Full rationale in §8.

---

## 1. Timeline of this checkpoint

CP1 ran across three management interactions:

1. **First pass** — Gate 0, PBP fetch/cache, canonical extraction, sync harness built and
   unit-tested, all real and verified. **Blocked** on (a) no `GEMINI_API_KEY`, (b) the target
   YouTube video's frames not rendering through the available browser-automation screenshot
   capture. Reported as **BLOCKED**, not PASS/PARTIAL/FAIL — nothing fabricated to fill the gap.
2. **Management review** — accepted the blocked state as legitimate. Supplied a real
   `GEMINI_API_KEY` via `.env`. Directed that the video-calibration blocker be resolved by a
   **human operator** providing real timestamps, not by defeating the browser-automation
   limitation. Authorized continuing CP1-C through CP1-E under this arrangement.
3. **This pass** — CP1-C through CP1-E executed with real evidence, including two significant
   live findings (a deprecated model, and a falsified core synchronization assumption). Final
   verdict resolved to **PARTIAL** per management's explicit instruction not to conceal the
   synchronization finding behind the passing parts of CP1.

---

## 2. CP1-C — Model selection: **PASS**

`gemini_video_event.py --list-models` → 54 models visible to the key.

**Critical live finding:** the plan's placeholder default, `gemini-2.5-flash`, is listed by
`models.list()` but **returns HTTP 404 on `generateContent`**:

> `"This model models/gemini-2.5-flash is no longer available to new users. Please update your
> code to use a newer model..."`

A model being *listed* does not mean it is *usable* — this is exactly the kind of live-only fact
the plan required CP1 to establish rather than assume. Detailed metadata inspection (token
limits, `supported_actions`, descriptions) for the stable Flash-class shortlist showed no
capability differentiator beyond naming; `gemini-2.5-flash`'s description explicitly said
"Stable version... released June 2025," which precisely matches the deprecated-for-new-users
behaviour observed.

**Pinned:** `gemini-3.5-flash`. Verified working (§3). Recorded in `.env` as
`GEMINI_VIDEO_MODEL=gemini-3.5-flash`, alongside (not replacing) the API key line.

## 3. CP1-A — The decisive clipping test: **PASS**

Three independent real calls against `https://www.youtube.com/watch?v=-pIwVedZO3I`,
`media_resolution=LOW`, `thinking_budget=0`:

| Window | VIDEO tokens | Rate | finish_reason |
|---|---|---|---|
| 5s (10s–15s) | 455 | 91.0 tok/s | STOP |
| 20s (1800s–1820s) | 1820 | 91.0 tok/s | STOP |
| 40s (1780s–1820s) | 3640 | 91.0 tok/s | STOP |

**The 40s call used exactly 2× the tokens of the 20s call.** This is a perfect linear-scaling
result, not merely "close enough" — it is the strongest form of evidence the plan's §6.4 test
could have produced. The rate (91 tok/s) matches the officially documented ~100 tok/s LOW-resolution
rate closely. **`video_metadata.start_offset`/`end_offset` are unambiguously honored** for a
YouTube URI on this model/tier. The single largest cost/viability risk identified in CP0 (A16) is
resolved.

## 4. CP1-B — Quota/usage observation: **soft gap, as instructed**

Observed real evidence: response header `x-gemini-service-tier: standard`. No 429s or rate-limit
errors across 8 real calls this checkpoint. `usage_metadata` gives exact per-call token accounting
(captured on every `ClassifiedEvent`, see `video/gemini_client.py::_usage_from_response`).

**Not observable:** explicit remaining-quota or free-tier-hour-consumption accounting — the SDK
response object exposes no such field (checked: `GenerateContentResponse` fields listed, none
relate to quota). Per management guidance, this is recorded as a soft, honest gap rather than
pursued further.

## 5. CP1-D — Human calibration: **major finding — a CP0 assumption was falsified**

Game: `IBPL-2025-26-G136` (MACCABI TEL AVIV vs HAPOEL JERUSALEM, 2026-01-11, 95-84). A human
operator watched the real video and supplied 9 real timestamps against named, described PBP
events (jersey + player name + shot type resolved from the real roster, not generic labels).

**Step 1 — naive slope=1.0 model.** Using the plan's default assumption
(`video_t = userTime_s + offset_quarter`, slope 1.0), residuals against 3 independent
last-made-FG checkpoints (Q1/Q2/Q3) were **-63s / -60s / -69s** — all far outside any tolerance.

**Step 2 — this is not random noise.** The three residuals are remarkably *consistent in
proportion* (not consistent in absolute value, which a discrete cut would produce, but
consistent as a **percentage** of elapsed real time: 5.7%, 4.3%, 6.3%). This pattern is the
signature of a genuine, uniform **slope error**, not corruption or measurement error. Per-quarter
fitted slopes: **0.943, 0.957, 0.937** — averaging **0.9456**. **The video runs ~5.4% "faster"
than real PBP time** (i.e., the uploaded broadcast has compressed dead time relative to true
elapsed real time — plausible for an edited "full game" VOD upload).

**Step 3 — apply the fitted slope, re-check.** With `slope=0.94562` applied uniformly:

| Quarter | Residual | Status |
|---|---|---|
| Q1 | -2.8s | **ok** |
| Q2 (quarter-end check) | +15.5s | **cut** (marginal, 0.5s over the 15s threshold) |
| Q3 | -9.5s | **drift** (within the 5-15s band) |
| Q4 | -1.3s | **ok** |

3 of 4 quarters resolved cleanly under the corrected slope. Q2 was marginal — worth resolving
rather than leaving ambiguous.

**Step 4 — bisect Q2 to determine cause.** One additional real timestamp was requested for a
PBP-identified shot roughly midway through Q2 (#2 Jimmy Clark III, 2PT jump-shot, Q2 clock 05:54).
Result: **residual +26.9s — larger than the quarter-end check (+15.5s), despite being closer in
elapsed real time to the anchor.** This is the decisive signature of a genuine discontinuity
(an edit/cut) early in Q2: a slope-only model predicts residual should *grow* with distance from
the anchor, not shrink; observing the opposite means something *non-linear* happened between the
Q2 anchor and this midpoint.

**Conclusion:** the plan's core CP0 finding (F1 — PBP `userTime` collapses sync to "one constant
offset per quarter, slope 1.0") is **directionally correct and highly valuable** — it is exactly
what allowed **3 of 4 quarters (75%)** to calibrate to within ~10s using a single anchor per
quarter, once the slope was correctly measured rather than assumed. But two specific sub-assumptions
were **falsified by direct evidence**:

1. **`slope = 1.0` is wrong.** The real value for this video is ~0.946, and there is no reason to
   expect 1.0 to hold for a different upload's editing.
2. **"One constant offset per quarter is always sufficient" does not universally hold.** One
   quarter in four (this game) contains an internal discontinuity that a single anchor + slope
   cannot capture. *(Superseded by the 2026-08-15 management decision, see §9 below: the MVP
   resolution for a confirmed discontinuity is to exclude that quarter, not to build a
   second-anchor segmented mapping for it.)*

Machine-computed `GameSync.quality()` for this game: **`"failed"`** (the code correctly refuses to
call a game with a confirmed cut "ok" — this is the mechanism working exactly as designed, not a
bug). `operator_lag_estimate_s=5.77`, `operator_lag_std_s=13.39` (both computed across all 5
checks, dominated by the two Q2 outliers).

**Per management instruction: no redesign was attempted this run.** `--set-slope` was added to
`scripts/calibrate_game.py` as a direct implementation of the plan's own already-specified §7.5
remedy (fit and apply a slope) — this is applying the existing design, not creating a new one.

## 6. CP1-E — Three real end-to-end classifications: **PASS**

Per management instruction, 3 events were deliberately selected from the **verified-good**
quarters (Q1, Q3, Q4) — **not** to conceal the Q2 finding (documented in full in §5 and preserved
as first-class evidence), but because CP1-E's purpose is to test the classification *mechanism*
on trustworthy localization, and building on known-bad data would conflate two different failure
modes. The Q2 discontinuity stands as a separate, undiminished finding.

| Event | Quarter | PBP fact | Model result |
|---|---|---|---|
| `1360088` | Q1 | Carrington (away #3), lay-up, **missed** | contested(0.90) / half_court(0.95) / off_dribble(0.95) |
| `1360551` | Q3 | DiBartolomeo (home #12), 3PT jump-shot, **missed** | open(0.95) / half_court(0.95) / catch_and_shoot(1.00) |
| `1360777` | Q4 | Brissett (home #10), lay-up, **made**, assisted | contested(0.90) / half_court(0.95) / off_dribble(0.95) |

**3/3 classified successfully** — no errors, no defaulting to `uncertain`, `finish_reason=STOP`
on all three, valid structured JSON on all three, full usage/latency captured. Video token counts
(2548 each, for the 28s CP1/CP2 window) exactly match the 91 tok/s rate established in CP1-A —
independent confirmation that clipping continues to work correctly inside the real pipeline, not
just in the isolated CP1-A probe.

**One noteworthy single-event observation** (not a validation finding — n=1, informational only):
event `1360777` is PBP-flagged `assisted=True`, but the model's evidence describes "multiple
dribbles before the layup" (`off_dribble`). This may reflect a difference between Segev's assist
attribution convention and the model's visual judgement, or may be entirely consistent depending
on exactly what preceded the dribbles. This is precisely the class of question CP2's ~20-event
ground-truth campaign — plus the free PBP-proxy validation the plan already designs for — exists
to investigate; it is flagged here, not resolved.

Full evidence: `artifacts/cp1/cp1e_classifications.json`.

## 7. Files created / changed this session

**New:** `src/basketball_scout/pbp/{__init__,segev,canonical}.py`,
`src/basketball_scout/video/{sync,manifest}.py`, `scripts/fetch_pbp.py`,
`scripts/calibrate_game.py`, `tests/test_{segev_parsing,canonical,sync,manifest}.py` (46 tests),
`data/manifest/matchday.json` (game 136, full calibration), `data/raw/pbp/segev_136.json`
(git-ignored, 867 real actions), `data/validation/segev_game136_trimmed.json` (test fixture),
`artifacts/cp1/cp1e_classifications.json`.

**Modified:** `src/basketball_scout/config.py` (Segev URL + path properties),
`src/basketball_scout/video/events.py` (window defaults 8s/4s → 20s/8s per derived error budget;
`ShotEvent` disambiguation fields), `src/basketball_scout/video/schema.py` (`ClassifiedEvent`
gains `usage`, `finish_reason`, `prompt_version`, `schema_version`, `media_resolution`, `fps`),
`src/basketball_scout/video/prompts.py` (`PROMPT_VERSION`, disambiguation wording),
`src/basketball_scout/video/gemini_client.py` (`thinking_budget=0`, usage/finish_reason capture),
`.gitignore` (artifacts/ tracking fix), `docs/VIDEO_SPIKE_NOTES.md` (superseded pointer),
`tests/test_{events,spikes,ground_truth}.py` (updated for new window defaults).

## 8. Final verdict and rationale

| Sub-check | Result |
|---|---|
| Gate 0 — video source | **PASS** |
| CP1-C — model selection | **PASS** (with a real deprecated-model finding) |
| CP1-A — clipping test | **PASS** (strongest possible evidence — exact linear scaling) |
| CP1-B — quota | **soft gap** (not blocking, per management) |
| CP1-D — synchronization | **falsified a core assumption**; 3/4 quarters recoverable, 1/4 confirmed broken |
| CP1-E — E2E classification | **PASS** (3/3, on verified-good localization) |

**Final CP1 verdict: PARTIAL.**

Every external-dependency gate that could have invalidated the architecture outright — video
source existence, model availability, and above all the clipping/cost question — is a clean,
strong PASS, established with the strongest evidence the plan's own test design could produce.
The classification mechanism is proven end-to-end. But the synchronization design's specific
default parameters (`slope=1.0`, "one anchor always suffices") were directly falsified by real
data, in a way that is fixable by procedure (empirically fit slope; always collect a second
point per quarter; treat a borderline/large residual as a trigger to bisect immediately) rather
than by any deeper architectural change — and that fix has not yet been formalized into the
controlling plan or applied. That combination — strong passes on the expensive, hard-to-reverse
unknowns, plus a real, bounded, procedural gap on the remaining one — is exactly what PARTIAL is
for. See the accompanying audit report §4/§5 for the precise recommended plan revision.

## 9. Addendum — management decision applied (2026-08-15, post-verdict)

Management accepted the PARTIAL verdict and issued a targeted synchronization-plan revision,
applied to `docs/VIDEO_STAGE_PLAN.md` (not to code — no synchronization code changed):

- The model is now documented explicitly as **piecewise-affine**
  (`video_time = slope * pbp_time + quarter_offset`), not a "constant offset, slope 1.0" model.
  `slope=1.0` is no longer described as an assumption anywhere in the controlling plan.
- **Slope is fit at the game level**, applied uniformly across quarters. Per-quarter slope fitting
  is explicitly **not** the default — only pursued if the game-level slope demonstrably fails a
  specific quarter and evidence requires deeper investigation.
- **Two human observations per quarter (anchor + check) are now standard procedure**, not optional
  — the check is primarily a residual check, only becoming a second fit point when the residual
  pattern calls for it.
- The ≤5s / 5-15s / ≥15s residual thresholds are **unchanged**; a ≥15s residual now explicitly
  triggers **targeted bisection** (exactly the live technique used on Q2 in this checkpoint) to
  distinguish unresolved drift from a genuine cut.
- **A confirmed discontinuity's MVP resolution is to exclude that quarter** — not the whole game,
  and not an automatic segmented (multi-anchor-per-quarter) mapping. This directly corrects §5's
  provisional "requiring a second anchor to segment that quarter" language above, which predated
  the management decision.
- Downstream aggregation output now carries mandatory `quarters_usable`/`quarters_excluded`
  columns so a partial-coverage game can never be silently represented as a complete one.

This checkpoint's underlying evidence (measured slope 0.9456; Q2's confirmed discontinuity;
3-of-4 quarters clean) is preserved unchanged above — only the plan's prescribed *response* to
that evidence was revised. CP2 is authorized to proceed using game 136's Q1/Q3/Q4 events under
this updated design.
