# CP2.4A — Shot-Coordinate Geometry Validation Report

Date: 2026-08-16
Scope: `stats-layer` worktree only, offline (cached Segev PBP, no network/model calls).
Reproduce with: `PYTHONPATH=src .venv\Scripts\python.exe scripts\cp2\run_cp24_validation.py`
Raw numeric output: `artifacts/cp2/coords/coordinate_validation.json`
Implementation under test: `src/basketball_scout/pbp/geometry.py`

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
- Agreement: **975 / 1,058 = 92.16%** (target ≥85% — **PASS**)

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

Full breakdown and example lists (8 corner-like + 8 non-corner) are in
`coordinate_validation.json` under `official_vs_geometric_family_agreement`.

## 6. Coarse zone / rim-attempt / eligibility distributions (sanity, not a gate)

Across all 1,104 shots: `lane_2pt` 483 (44%), `midrange_2pt` 154 (14%), `corner_3` 64
(6%), `atb_3` 403 (36%); `rim_attempt` (dunk/lay-up) 455 (41%);
`distance_eligibility` — `over_10ft` 637, `under_10ft` 409, `uncertain` 58 (5.3%). All
numbers are basketball-plausible (paint attempts and above-the-break threes dominate,
corner threes are a minority as expected on a real shot chart).

## 7. Gates 5 and 6 — NOT EXECUTABLE (reported, not silently skipped)

The promotion checklist (brief §10) requires:

- **Gate 5**: seed-211 coarse-zone agreement ≥18/20 against "accepted seed-211 human
  labels."
- **Gate 6**: cross-game human/video spot-check ≥10/12.

**Neither gate could be executed with what exists in this worktree:**

- No "seed-211" human-labeled shot-zone dataset exists anywhere in the repository.
  `data/validation/video_events_ground_truth.csv` — the only plausibly-relevant file — is
  header-only (zero data rows). No other file matching "seed"/"211" with label content was
  found in `data/` or `docs/`.
- Game 136 is the only game with any video calibration at all, and its calibration is
  already recorded as unreliable: `data/manifest/matchday.json` marks its sync
  `"quality": "failed"` with `"operator_lag_std_s": 13.39`. The brief explicitly disallows
  building new synchronization for the other 7 games ("do not build a new full
  synchronization system... If video access would require substantial new engineering:
  document that and keep the validation bounded") — building a reliable spot-check
  facility from scratch would be exactly that.

This is reported as a genuine blocker for management, not resolved unilaterally — per
CLAUDE.md §5 and the brief's own instruction ("If anything unexpected requires an
architecture decision: STOP and report it.").

## 8. Promotion-gate checklist (brief §10)

| # | Gate | Result |
|---|---|---|
| 1 | Origin/scale sanity across games | **PASS** — §3 |
| 2 | No evidence of a different coordinate system in any game | **PASS** — all 8 games fit one convention, no per-game outlier scale/origin found |
| 3 | Official 2PT/3PT consistency outside ambiguity band | **PASS** — 92.16% ≥85%, §5 |
| 4 | Orientation/left-right convention resolved | **PASS** — §4 |
| 5 | Seed-211 coarse-zone agreement ≥18/20 | **NOT EXECUTABLE** — §7 (no such dataset exists) |
| 6 | Cross-game human/video spot-check ≥10/12 | **NOT EXECUTABLE** — §7 (no usable video calibration outside game 136, and game 136's own calibration already failed) |

Gates 1–4 (all quantitative, geometry/scoring-internal) pass strongly. Gates 5–6
(external human/video ground truth) cannot be run with what currently exists in this
worktree — this is a resource gap, not a failed measurement.

## 9. Verdict

**SHOT_ZONE: PARTIAL.** Geometric/scoring-internal validation (gates 1–4) passes
cleanly and consistently across 8 games and 1,104 shots. The two human-ground-truth
gates (5–6) required by the brief cannot be executed with data currently in this
worktree. Per brief §10 ("If validation fails, do NOT force a coordinate implementation
into production... explain exactly which assumption failed") and §22 item 15's instruction
to stop rather than make architecture/continuation calls: **this is not a coordinate-model
failure** — every executable check passed — but promotion past gates 5–6 requires a
management decision on how to obtain (or waive) seed-211 labels / a reliable video
spot-check, which is out of this task's authority.

**DISTANCE: PARTIAL**, same basis as SHOT_ZONE — the underlying `distance_m` values
pass every distance-sanity and eligibility-banding check available (§3, §6), but
distance also feeds `classify_coarse_zone` indirectly via the same unresolved
gates-5/6 ground truth question, so it is held to the same verdict rather than promoted
ahead of the zone taxonomy it supports.

## 10. Implementation notes

`geometry.py` implements the coarse MVP taxonomy only (`lane_2pt`, `midrange_2pt`,
`corner_3`, `atb_3` + separate `rim_attempt`) — no fine RA-vs-Paint hairline classifier,
per brief §7 (`DEFER_POST_MVP`). Official `points` is authoritative for the 2PT/3PT
family in `classify_coarse_zone` and can never be overridden by geometry.
Distance-eligibility uses the required ±1m-uncertainty bands (`>=3.5m` → `over_10ft`,
`<2.5m` → `under_10ft`, else `uncertain`) — no hairline 3.05m cutoff. Missing/invalid
input never raises; it returns an honest `None`/`"uncertain"`.

## 11. Limitations

- Ground-truth gates 5–6 not executable (§7) — the single largest open item.
- The corner-3 vs above-the-break-3 split (`CORNER_SIDELINE_DISTANCE_MAX_M=2.2`,
  `CORNER_BASELINE_Y_MAX_M=4.0`) is a coarse heuristic tuned against this session's own
  83-disagreement sample, not an independently-sourced FIBA boundary; it is good enough
  for the MVP coarse taxonomy but should not be treated as precise.
- 32/83 (2.9% of all sampled shots) official-2PT "long two" shots disagree with the
  coordinate-implied family for reasons not fully explained (§5) — worth a follow-up
  look if/when distance-eligibility work near the arc becomes higher-priority.
- Sample is 8 games / 1,104 shots; solid for aggregate medians/percentiles, but not
  large enough to rule out a rare per-game data-entry anomaly outside this set.
