# WORKLOG

Running project log. Newest entry first. Concise and useful to a future
session — not a place for terminal output.

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
