# CP2.4A — Shot-Coordinate Geometry Validation Report

Scope: `stats-layer` worktree only, offline (cached Segev PBP, no network/model calls).
Implementation under test: `src/basketball_scout/pbp/geometry.py`

This report has two parts, clearly separated per management's instruction not to
rewrite history:

- **PART 1 — ORIGINAL CP2.4** (2026-08-16, first run): the initial validation, before
  seed-211 was available in this worktree.
- **PART 2 — CP2.4 HARDENING** (2026-08-16, follow-up): seed-211 recovered read-only
  from master, baseline Gate 5 measured, one mismatch diagnosed and fixed with a
  general rule, full re-validation.

======================================================================

# PART 1 — ORIGINAL CP2.4

Reproduce with: `PYTHONPATH=src .venv\Scripts\python.exe scripts\cp2\run_cp24_validation.py`
Raw numeric output: `artifacts/cp2/coords/coordinate_validation.json` (now reflects
post-hardening numbers — see Part 2 §H4 for the exact before/after diff)

## 1. Sample

8 cached 2025-26 games, 1,104 shot actions with both `coordX`/`coordY` present:

| game_id | home_team | shots w/ coords |
|---|---|---|
| 136 | MACCABI TEL AVIV | 140 |
| 50 | BEER SHEVA | 137 |
| 55 | MACCABI TEL AVIV | 140 |
| 60 | BNEI HERZLIYA | 146 |
| 73 | MACCABI TEL AVIV | 136 |
| 100 | HAPOEL TEL AVIV | 120 |
| 150 | HAPOEL TEL AVIV | 148 |
| 200 | ELIZUR NETANYA | 137 |

5 distinct home teams/arenas (≥3 required). Includes game 136 (required). Includes
Maccabi Tel Aviv as both home (games 136, 55, 73) — the away-side comparison uses
away possessions from the other 5 games, giving a genuine home/away split.

## 2. Coordinate hypothesis tested

```
x_m = coordX / 100
y_m = coordY / 100
attacked-basket centre = (7.5 m, 1.575 m)
court modeled as 15 m wide
```

This was **not** assumed true — it was tested against the distance-sanity gates below
and the official-scoring consistency check, using real shot data, before being written
into `geometry.py`. No alternative convention (different scale factor, different origin,
a need to flip x/y per team or quarter) was found necessary; see §4.

## 3. Distance-by-shot-type sanity gates (brief §5)

| shot type | n | median (m) | gate | result |
|---|---|---|---|---|
| dunk | 40 | 0.485 | median ≤1.0m, ≥90% ≤1.5m | **PASS** (95.0% ≤1.5m) |
| lay-up | 415 | 1.773 | median ≤~2.5m | **PASS** |
| 2PT jump-shot | 182 | 5.142 | median 3.5–5.5m | **PASS** |
| 3PT jump-shot | 467 | 9.305 | median 8.5–9.8m, ≥90% ≥6.0m | **PASS** (96.4% ≥6.0m) |

All four distance-sanity gates pass. "2PT jump-shot" / "3PT jump-shot" are the raw
`type=="jump-shot"` actions split by the **official** `points` field, not by geometry.
**Unaffected by CP2.4 hardening** — see Part 2 §H4.

## 4. Orientation / no-flip-needed check (brief §4, ≥2 cases)

Layup distance (a shot type tight enough around the rim that any orientation error
would show up immediately as a large median shift) split by team side and by quarter:

- By side: home median 1.688m, away median 1.803m
- By quarter: Q1 1.803m, Q2 1.773m, Q3 1.892m, Q4 1.703m

All six medians fall within a ~0.2m band of each other — statistically indistinguishable
given the ±1m distance uncertainty this system already carries. **No orientation flip is
needed**: coordinates are recorded relative to the attacking basket for every shot,
regardless of team or quarter, across all 8 games. This satisfies gate (4) of §10 and the
≥2-case requirement of §4.

## 5. Official 2PT/3PT consistency + ambiguity band (brief §6)

Diagnostic-only check: does the coordinate-implied family (`distance > 6.75m` arc radius,
excluding a ±0.30m ambiguity band) match the **official** `points` field? Official scoring
is never overridden by this check — it exists solely to validate the coordinate model.

- Checked (outside band): 975 + 83 = 1,058 shots (46 fell inside the ±0.30m band and were
  excluded, as specified)
- Agreement: **975 / 1,058 = 92.16%** (target ≥85% — **PASS**). **Unaffected by CP2.4
  hardening.**

### Disagreement breakdown (83 cases) — corrected from an earlier same-session ad-hoc read

An initial small-sample read of this session mistakenly concluded "every disagreement is
explained by FIBA corner-3 geometry." Re-checked against the full 83-case set, that claim
is **only partially true**:

- **51 / 83 (61%)** are corner-shaped (within 2.2m of a sideline and within 4.0m of the
  baseline) — consistent with FIBA's straight corner-3 segment (~6.6m) sitting inside the
  6.75m arc radius used for this diagnostic, exactly as expected.
- **32 / 83 (39%)** are **not** corner-shaped. All 32 are `official_points=2` "long twos"
  taken near the top of the arc, with geometric distance 6.4–8.6m from the basket (well
  outside the ±0.30m band) yet scored as a 2. Example: game 136, action 1360045 —
  distance 7.965m, well away from any sideline (3.9m), scored as a 2-point make.

  Two plausible, non-exclusive explanations (neither confirmed against video, per the
  brief's bounded-scope instruction — this is reported as an open finding, not resolved):
  1. Genuine "foot on/near the line" 2-point calls — the shot chart coordinate is recorded
     at a release point the official scorer judged to be inside the arc even though the
     6.75m nominal radius used here places it outside; real games have such shots.
  2. Shot-chart coordinate imprecision for this action type specifically (all 32 are
     `type=="jump-shot"`, none are corner threes miscoded) — the general-position charting
     may be coarser near the top of the arc than near the basket.

  This does **not** change the PASS verdict on the §6 gate (92.16% ≥85% either way), and
  it does not affect `classify_coarse_zone`, since that function always takes
  `official_points` as authoritative and never reclassifies a 2 as a 3 — but it is a real,
  non-trivial (2.9% of all 1,104 shots) source of "long 2 vs 3" boundary noise worth
  carrying into any future distance-eligibility work near the arc.

Full breakdown and example lists are in `coordinate_validation.json` under
`official_vs_geometric_family_agreement`.

## 6. Coarse zone / rim-attempt / eligibility distributions (sanity, not a gate)

Original (pre-hardening) distribution across all 1,104 shots: `lane_2pt` 483 (44%),
`midrange_2pt` 154 (14%), `corner_3` 64 (6%), `atb_3` 403 (36%); `rim_attempt`
(dunk/lay-up) 455 (41%); `distance_eligibility` — `over_10ft` 637, `under_10ft` 409,
`uncertain` 58 (5.3%). See Part 2 §H4 for the post-hardening distribution.

## 7. Gate 5 status at original CP2.4 time — NOT EXECUTABLE (superseded by Part 2)

At original CP2.4 time, **no seed-211 dataset existed anywhere in this worktree**
(`data/validation/video_events_ground_truth.csv` was header-only). Gate 5 was reported
NOT_EXECUTABLE. **This is now resolved — see Part 2.** Gate 6 (cross-game video
spot-check) remains NOT EXECUTABLE; see §8 below (unchanged from original CP2.4).

## 8. Gate 6 — NOT EXECUTABLE (still true; out of scope for this hardening pass)

Game 136 is the only game with any video calibration at all, and its calibration is
already recorded as unreliable: `data/manifest/matchday.json` marks its sync
`"quality": "failed"` with `"operator_lag_std_s": 13.39`. The brief explicitly disallows
building new synchronization for the other 7 games. This management-facing blocker is
unchanged by the hardening work in Part 2 — it requires a genuinely fresh, unseen
human/video sample, which does not exist in this worktree (see Part 2 §H8).

## 9. Original verdict (superseded — see Part 2 §H9 for the current verdict)

At original CP2.4 time: gates 1–4 PASS, gates 5–6 NOT EXECUTABLE →
**SHOT_ZONE/DISTANCE: PARTIAL**. Part 2 updates this after seed-211 was recovered.

======================================================================

# PART 2 — CP2.4 HARDENING (follow-up, 2026-08-16)

Management decision: current coordinate result is promising but needs a stronger
reliability target (≥95% coarse-zone reliability; seed-211 target 19/20 or 20/20).
Scope: recover seed-211 read-only, measure baseline Gate 5, diagnose every mismatch,
apply only general/principled corrections, re-validate everything, report honestly.

Reproduce with:
```
PYTHONPATH=src .venv\Scripts\python.exe scripts\cp2\run_seed211_gate5.py
PYTHONPATH=src .venv\Scripts\python.exe scripts\cp2\run_cp24_validation.py
```
Raw output: `artifacts/cp2/coords/seed211_gate5.json`,
`artifacts/cp2/coords/coordinate_validation.json`

## H1. Ground truth — recovered read-only

- **Source commit:** `64b6cb8` (master, NOT merged into `stats-layer`)
- **Path:** `artifacts/cp2/cp2_seed211_accepted_ground_truth.csv`
- **SHA256** (of the exact bytes retrieved via `git show 64b6cb8:<path>`):
  `ec19d209e0964ac59c5d9fc6de8cfcef4cd9dcd79537cd6168ae6a2ee62c2fc5`
- **Git blob id:** `d2267673197d9fb1a9df32840461df526458f6f5`
- Retrieved via `git show <commit>:<path>` only — never `git merge`, never checked out,
  never copied into a tracked file in this worktree. `scripts/cp2/run_seed211_gate5.py`
  re-fetches it the same way on every run, so nothing is duplicated at rest.
- All 20 labeled events are from game 136 (event_id prefix `IBPL-2025-26-G136:`).

**20 action IDs and coarse mapping** (`ra`/`paint`→`lane_2pt`, `mr`→`midrange_2pt`,
`lc3`/`rc3`→`corner_3`, `atb3`→`atb_3`, exactly as specified):

| action_id | human fine zone | human coarse zone |
|---|---|---|
| 1360034 | atb3 | atb_3 |
| 1360059 | paint | lane_2pt |
| 1360063 | rc3 | corner_3 |
| 1360124 | paint | lane_2pt |
| 1360161 | mr | midrange_2pt |
| 1360181 | atb3 | atb_3 |
| 1360186 | atb3 | atb_3 |
| 1360483 | atb3 | atb_3 |
| 1360488 | atb3 | atb_3 |
| 1360520 | atb3 | atb_3 |
| 1360536 | ra | lane_2pt |
| 1360584 | ra | lane_2pt |
| 1360643 | paint | lane_2pt |
| 1360733 | ra | lane_2pt |
| 1360735 | atb3 | atb_3 |
| 1360750 | ra | lane_2pt |
| 1360755 | paint | lane_2pt |
| 1360793 | atb3 | atb_3 |
| 1360819 | paint | lane_2pt |
| 1360845 | ra | lane_2pt |

Distribution: 10 `lane_2pt`, 1 `midrange_2pt`, 1 `corner_3`, 8 `atb_3` — no `lc3`
example and only one `rc3`/`mr` example, so this set stress-tests the lane/ATB boundary
far more than the corner boundary.

## H2. Baseline Gate 5 result (before any hardening)

**19 / 20 = 95.0%.**

One mismatch: action_id **1360059**.

## H3. Mismatch diagnosis

| field | value |
|---|---|
| action_id | 1360059 |
| human fine zone | paint |
| human coarse zone | lane_2pt |
| deterministic zone (pre-fix) | midrange_2pt |
| coordX / coordY | 900.0 / 588.0 |
| normalized (x_m, y_m) | (9.0, 5.88) |
| basket distance | 4.559 m |
| official points | 2 |
| PBP shot_type | lay-up |
| distance to lane-depth boundary (5.8m) | **+0.08 m beyond** |
| distance to lane x-boundary (2.45m half-width) | -0.95 m inside (not close) |
| distance to 3PT arc (6.75m) | -2.19 m (not close) |
| distance to corner break | n/a (2PT shot) |

**Root-cause investigation.** A lay-up (trusted rim-proximate PBP shot type) charted at
4.56m from the basket is unusually far for a lay-up (8-game lay-up median is 1.77m,
§3) — worth checking whether this is charting noise. Checked the full 8-game coordinate
set for the underlying data-collection mechanism: **shot coordinates are not freehand**
— `y_m` values fall on a strict grid with **0.28m pitch** and `x_m` values on a
**0.15m pitch**, confirmed across all 8 games (every consecutive gap between distinct
`y_m` values is exactly 0.28; `x_m` gaps are exact multiples of 0.15). Segev's shot
chart is provider-side quantized, not continuously recorded.

The FIBA free-throw line (5.80m, the true lane-depth boundary) sits **between** grid
rows 5.60 and 5.88 — 0.20m from 5.60, only **0.08m from 5.88**. Any real paint attempt
released at or near the free-throw line is therefore more likely to snap to the 5.88
row than to fall exactly on 5.80, purely from grid quantization — independent of where
the shot was actually taken.

**Classification: C — COORDINATE NOISE / BOUNDARY AMBIGUITY.** This is not a wrong
geometry rule (FIBA's 5.8m lane depth is correct), not an orientation error, not a PBP
shot-type opportunity beyond what's already used, and not a borderline human label (a
lay-up scored a paint 2 at the free-throw line is an entirely ordinary basketball play).
It is a direct, data-grounded consequence of the provider's coordinate grid.

## H4. General rule change considered and accepted

**Rule:** widen the lane-depth boundary test by half the empirically measured `y_m`
grid pitch (0.28m / 2 = **0.14m**) — `LANE_DEPTH_BOUNDARY_TOLERANCE_M = 0.14`, applied
as `y_m <= LANE_DEPTH_M + LANE_DEPTH_BOUNDARY_TOLERANCE_M` in `_is_within_lane`.

**Rationale (general, not event-specific):** this is the standard "nearest grid line"
allowance for a threshold test against quantized data — derived from the provider's own
measured data granularity (a property of the whole dataset, verified across all 8
games), not tuned to make action 1360059 pass. The x-axis lane boundary and the corner
boundary were separately checked for similar evidenced problems and found none (no
seed-211 mismatch touches either) — so neither was touched, per the "only fix what's
diagnosed" constraint.

**Fixes:** the sole seed-211 mismatch (1360059).

**Regressions in seed-211:** none — the other 19 labels are unaffected (verified by
full re-run, not by construction).

**Effect across all 8 CP2.4 games (1,104 shots) — before/after:**

| metric | before | after | changed? |
|---|---|---|---|
| dunk distance median | 0.485m | 0.485m | no |
| lay-up distance median | 1.773m | 1.773m | no |
| 2PT jumper distance median | 5.142m | 5.142m | no |
| 3PT jumper distance median | 9.305m | 9.305m | no |
| official 2PT/3PT agreement outside band | 92.16% | 92.16% | no |
| `lane_2pt` count | 483 | 492 | **+9** |
| `midrange_2pt` count | 154 | 145 | **-9** |
| `corner_3` count | 64 | 64 | no |
| `atb_3` count | 403 | 403 | no |
| `rim_attempt` count | 455 | 455 | no (shot-type derived, not geometry) |
| `distance_eligibility` distribution | unchanged | unchanged | no |

Exactly 9 shots move from `midrange_2pt` to `lane_2pt` across the full 8-game set
(the seed-211 case plus 8 more with the same 5.88 grid row and lane-x-range
membership) — no shot-type distance gate, no official-family-agreement number, and no
game/arena consistency check is affected. **No regression on any structural gate.**

## H5. General rule changes considered and rejected

- **Loosening `LANE_HALF_WIDTH_M` (x-axis) or the corner thresholds by the same
  half-grid logic:** considered for consistency (x_m grid pitch is 0.15m), but rejected
  — no seed-211 mismatch evidences a problem on either boundary, and the brief
  explicitly disallows moving a boundary without a diagnosed failure. Left unchanged.
- **Distance-eligibility band (§8 of the brief):** reviewed the 8-game 2PT distance
  distribution in the 2.0–4.0m range (179 shots); it is smooth and unimodal-ish with no
  natural gap that would justify tightening or widening the existing 2.5m/3.5m
  `uncertain` band around the true 10ft/3.048m line. **No change made** — the current
  band is retained as-is, not optimized against seed-211 (seed-211 doesn't even test
  distance-eligibility, only zone).
- **Systematic per-game/global coordinate offset (§9 of the brief):** pooled dunk
  centroid across all 8 games is (7.429, 1.715) vs. assumed basket (7.5, 1.575) — a
  small pooled offset (dx=-0.07m, dy=+0.14m), but per-game dunk centroids (n=3–7 dunks
  per game, too few for a stable per-game estimate) range from (7.088, 1.260) to
  (7.763, 2.030) with **inconsistent sign and no common pattern** across games/arenas.
  This is consistent with ordinary shot-location variance among a handful of dunks per
  game, not a calibration artifact. Per the brief's explicit instruction ("if offsets
  differ materially by game/arena, DO NOT introduce per-game calibration... prefer
  coarse uncertainty over fragile calibration"), **no offset correction was made.**

## H6. Final Gate 5 result (after hardening)

**20 / 20 = 100.0%.** No remaining mismatches.

## H7. Target reached

**Level A reached: ≥19/20 (in fact 20/20) AND all structural 8-game gates remain
passed AND no new systematic mismatch class appears** (§H4 — the only effect is a
9-shot lane/midrange reclassification with every other structural number identical).

## H8. Fresh-validation requirement (explicit, per brief §11)

**Seed-211 is a tuned diagnostic set, not final validation.** It was used both to
measure Gate 5 and to identify and justify the one accepted rule change — using the
same 20 events to both diagnose and confirm the fix means a 20/20 result on this set
cannot be reported as unbiased held-out accuracy.

**Final KEEP requires a fresh blind validation sample not used to derive any rule in
this pass.** No such sample exists in this worktree: seed-211 (game 136 only) is the
only human-labeled shot-zone set found anywhere in the repository, and no other game
has usable video calibration to generate one (game 136's own sync is marked
`"quality": "failed"`, §8). **This worktree cannot generate that fresh sample itself**
— per the brief's explicit instruction, it is not fabricated here. What management
needs to supply or authorize: a second human-labeled shot-zone sample (ideally spanning
multiple games/arenas, since seed-211 is single-game) that this rule was never tuned
against.

## H9. Updated verdict (supersedes Part 1 §9)

**SHOT_ZONE: PROVISIONAL_MOVE_TO_PBP_DETERMINISTIC.** Gates 1–4 pass (Part 1), the
hardened Gate 5 reaches 20/20 (100%) with a single general, cross-game-verified rule
change and zero structural regressions (§H4). Gate 6 (video spot-check) remains not
executable and is unrelated to this hardening pass. Per brief §11, this is explicitly
**provisional** — final lock requires the fresh blind validation described in §H8.

**DISTANCE: unchanged from Part 1 — still PARTIAL.** No rule changes were made to
distance semantics (§H5); it continues to pass every executable distance-sanity check
and inherits the same "provisional, pending fresh validation" status as shot zone,
since it is validated by the same coordinate model.

## H10. Remaining state

No remaining seed-211 mismatches. The two disagreement classes documented in Part 1 §5
(corner-shaped vs. non-corner official-2PT "long twos") are unrelated to this hardening
pass and remain open, low-priority findings, not classification defects — they don't
touch `classify_coarse_zone`'s output (official points is always authoritative there).

## H11. Tests added

`tests/test_pbp_geometry.py` — 3 new tests, none referencing a seed-211 action ID:
`test_lane_depth_within_tolerance_still_counts_as_lane`,
`test_lane_depth_clearly_beyond_tolerance_is_midrange`,
`test_lane_depth_tolerance_does_not_affect_lane_x_boundary`. All encode the general
grid-quantization rule and its boundary (in-tolerance / clearly-beyond / x-axis
untouched), not the specific mismatch that motivated it.

## H12. FastBreak (brief §12)

Not reopened. No rule or code change. `stats/fastbreak.py` and its CP2.4 verdict
(`fast_break := provider.fastBreak == True`; `non_fast_break` supporting-only,
`secondary_transition` deferred) are unchanged.
