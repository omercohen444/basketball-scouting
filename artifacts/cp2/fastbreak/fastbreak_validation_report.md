# CP2.4B — FastBreak Provider-Flag Validation Report

Date: 2026-08-16
Scope: `stats-layer` worktree only, offline (cached Segev PBP, no network/model calls).
Reproduce with: `PYTHONPATH=src .venv\Scripts\python.exe scripts\cp2\run_cp24_validation.py`
Raw numeric output: `artifacts/cp2/fastbreak/fastbreak_validation.json`
Implementation under test: `src/basketball_scout/stats/fastbreak.py`

## 1. Sample

10 cached 2025-26 games (≥10 requested): 136, 50, 55, 60, 73, 100, 150, 200, 178, 209.
1,594 evaluated attempts (all `shot` actions + the **final** free throw of each trip —
non-final free throws are excluded, since only the last FT of a trip can plausibly close
out a fast-break-and-foul sequence).

## 2. Event counts and provider values

| game_id | attempts | provider positives | rate |
|---|---|---|---|
| 136 | 168 | 14 | 8.33% |
| 50 | 157 | 7 | 4.46% |
| 55 | 162 | 10 | 6.17% |
| 60 | 170 | 20 | 11.76% |
| 73 | 149 | 15 | 10.07% |
| 100 | 142 | 7 | 4.93% |
| 150 | 180 | 15 | 8.33% |
| 200 | 157 | 17 | 10.83% |
| 178 | 175 | 19 | 10.86% |
| 209 | 134 | 8 | 5.97% |

Pooled: **132 / 1,594 = 8.28%**. `fastBreak` field was present (non-null) on all 1,594
evaluated attempts — 0 missing values in this sample.

Per brief §15, this range is reported as a **diagnostic sanity check only**, not a
pass/fail gate. All 10 per-game rates land in a plausible 4.5%–11.8% band with no game
so far outside the others as to suggest a per-game data anomaly.

## 3. Semantic consistency: is-first-attempt-of-possession

95.45% of provider positives (126/132) are the first live-ball attempt of their
possession — matching the basic definition of a fast break (a quick score before the
defense can reset). The remaining 6 positives are non-first attempts (e.g. a quick
putback after an offensive rebound within an already-live possession) — see §5.

## 4. Timing consistency (brief §14 — diagnostic cutoffs, not a redefinition)

For the 126 first-attempt positives with a measurable elapsed time since the preceding
possession-change boundary:

- median 5.0s, mean 5.29s, max 12.0s
- **94.4% (119/126) occur ≤8s** after the possession-change boundary

This is strong positive-side timing consistency: the provider's `fastBreak=true` calls
overwhelmingly correspond to plays that happened fast, by an independent PBP-derived
timing measure the provider flag itself was not built from.

## 5. Change-of-possession type for positives

| change type | count |
|---|---|
| defensive_rebound | 59 |
| opponent_turnover | 58 |
| opponent_score | 9 |
| none / not-first-attempt | 6 |

117/132 (88.6%) of positives are triggered by the two classic fast-break origins
(defensive rebound, live-ball turnover) — exactly the semantic pattern expected. The 9
`opponent_score` cases are makes-and-run situations (inbound-and-go). The 6
`none/not-first-attempt` cases are the non-first-attempt positives from §3 — a small
(4.5% of positives) genuine edge case where the provider tagged a later shot within a
continuing possession (e.g., a fast putback) as a fast break; this module's own semantics
(first-attempt-only elapsed/change-type tracking) do not compute a change type for these,
which is honest given they are not, by this module's definition, a "first break off a
possession change" — but the provider's own judgment call there is plausible.

## 6. False-negative risk (brief §14 — converse check)

Definition used: `provider.fastBreak in (false, missing)`, `is_first_attempt_of_possession
== True`, `elapsed_since_possession_change_s <= 4.0s`.

- **64 / 1,123 (5.7%)** of first-attempt provider-negatives meet this criterion.
- Change-of-possession types among these 64: defensive_rebound 23, opponent_turnover 25,
  opponent_score 16.

This is real, non-trivial evidence supporting the brief's explicit semantic warning
(§12): a nontrivial minority of provider-negative attempts happen very quickly after a
possession change — the kind of timing a genuine fast break would show — meaning
`non_fast_break` must **not** be read as "the defense had time to set." 5.7% is a bounded,
reportable false-negative-risk rate, not a reason to reject the field (the brief does not
set a pass/fail threshold on this figure, only asks it be measured and reported).

## 7. Null/missing field behavior

`fastBreak` was present on all 1,594 evaluated attempts in this sample (0 missing).
`classify()` in `fastbreak.py` still handles a missing field defensively
(`bool(None) is False` → routed to `non_fast_break`, never to a confident "defense set"
claim) — this code path exists for robustness even though it wasn't exercised by data in
this specific sample; it is covered by a dedicated unit test
(`test_provider_missing_field_is_none_not_false` /
`test_provider_missing_field_still_classifies_as_non_fast_break`).

## 8. Comparison with the game-136 prior audit

An earlier session's CP1-era spot check of game 136 alone found `fastBreak=true` on a
similar order of a small handful of clearly-transition plays. This run's game-136 figures
(168 attempts, 14 positives, 8.33% — see §2) are consistent in shape (a single-digit
percentage of attempts, concentrated on rebound/turnover-triggered possessions) with that
earlier informal read, and now sit inside a systematic, reproducible 10-game measurement
rather than a one-off look.

## 9. Recommended MVP semantics

Per brief §16, exactly as specified:

```
fast_break := provider.fastBreak == True
```

`elapsed_since_possession_change_s` and `possession_change_type` are recorded as
**supporting diagnostic context only** — they are not part of the classification rule and
must not become one without a separate decision. For `fastBreak == False` (or missing),
the only derived label is `non_fast_break` — never `half_court`, `defense_set`, or
`secondary_transition` (the last remains `DEFER_POST_MVP`, unimplemented).

## 10. Verdict

**POSSESSION_TYPE: PASS — AUTHORITATIVE_MVP_SIGNAL** for the positive claim
(`fast_break := provider.fastBreak == True`):

- 95.5% of positives are first-attempt-of-possession (matches the basic definition)
- 94.4% of positives resolve within 8s of a possession-change boundary (independent
  timing corroboration)
- 88.6% of positives are triggered by the two canonical fast-break origins
  (defensive rebound / live turnover)
- Per-game and pooled prevalence are stable and basketball-plausible (§2), reported as
  diagnostic only, not gated, per brief §15

The `non_fast_break` label itself is **SUPPORTING_ONLY / not a confident claim of
half-court possession** — the 5.7% false-negative-risk rate (§6) is direct, measured
evidence that a nontrivial share of provider-negative attempts occur on fast timelines,
consistent with the brief's binding semantic warning (§12). This is not a defect in the
provider field or in this module; it is exactly the asymmetry the brief told this task to
expect and measure, not to try to fix with additional logic (`secondary_transition`
remains explicitly out of scope).

No bounded human/video spot-check was performed for CP2.4B specifically — the brief did
not require one for the fastbreak track (that requirement is CP2.4A §8), and no reliable
video calibration exists in this worktree beyond game 136's already-failed sync (see the
coordinate report §7) to have supported one had it been requested.

## 11. Limitations

- `fastBreak` had zero missing values in this 10-game sample, so the missing-field
  handling path is verified only by unit test, not by real-data evidence.
- The 5.7% false-negative-risk figure is a diagnostic proxy (a ≤4s elapsed cutoff on a
  provider-negative), not a ground-truth-verified false-negative count — no video/human
  review confirmed any of the 64 candidate cases are true fast breaks the provider missed;
  it is reported as a risk *rate*, not a defect count.
- Sample is 10 games / 1,594 attempts — solid for the timing/change-type consistency
  checks in this report, but per brief §15's own caution, not large enough to certify any
  single game's rate as anomalous or normal in isolation.
