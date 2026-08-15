# Statistics enrichment layer (v1)

Deterministic PBP analytics built on top of the accepted season-level stats
foundation (`WORKLOG.md` Runs 3-5). Purpose: give a future Data Analysis
Agent a rich, trustworthy, structured evidence space — **not** to draw
conclusions itself. Nothing in this layer writes narrative judgments
("bad in Q4", "clutch weakness"); it only ever produces numbers, sample
counts, and provenance.

## Architecture

```
raw PBP actions
  -> possession.py        canonical possession-state (the foundation)
  -> scoring_timeline.py  chronological scoring-play list (for runs/droughts/dynamics)
  -> segments.py          pure classification: quarter/half/clutch/score-state/close-score
  -> segment_metrics.py   possession subset -> TeamGameComponents -> the SAME ten formulas.py functions
  -> scoring_sources.py   points off TO, second chance, fast break, assisted/unassisted, shot mix
  -> runs_droughts.py     scoring runs, scoring/FG droughts
  -> dynamics.py          ties, lead changes, largest lead/deficit, comebacks
  -> enrichment.py        orchestrates all of the above into one GameEnrichment per team per game
  -> profile.py           cross-game aggregation, windows, JSON-serializable team profiles
  -> winloss.py (extended) generic W/L effect-size signals over ANY segmented metric
```

No new metric arithmetic exists outside `formulas.py` — every segment
(quarter, clutch, score-state, ...) reduces to "which possessions go into
`TeamGameComponents`", then calls the exact same trusted functions the
season-level engine already uses (`segment_metrics.py`). This is why the
ten core metrics stay numerically identical to the accepted implementation
(verified: 9 of 10 fields match exactly when computed over a full game's
possessions vs. the original `engine.py`; `ast_to_ratio` differs by a
small, documented, and intentional amount — see "Known discrepancy" below).

## Possession model (`possession.py`)

FIBA statistical possession concept: starts when a team gains live-ball
control, ends on a turnover, a made field goal / final made free throw, or
a missed field goal / final missed free throw followed by a **defensive**
rebound. An **offensive** rebound continues the same possession.

**Never uses `userTime`** for ordering or clock state — three real games in
this dataset (178, 209, 224) have bulk/backfilled `userTime` with no live
meaning at all (see WORKLOG.md 2026-08-15 Run 5). The authoritative
timeline is `(quarter, source action id)` for ordering (ids are strictly
increasing per game in true recording order) and `quarterTime` (`MM:SS`
countdown) for clock state.

**Deliberate simplification**: a made field goal always closes the
possession immediately, including the rare "and-1" case. A live check
across 3 real games (~250 made shots) found zero genuine
personal-shooting-foul-and-1 sequences immediately following a make. Any
free throw arriving with no possession open (technical fouls, or a genuine
and-1 should one occur) is handled by an explicit "orphan FT" fallback —
its points/FTA/FTM still count toward game totals, just not attributed to
a shot-continuation possession.

**Known real-data anomaly, handled not ignored**: some games contain a
"team offensive rebound" action immediately after a made shot (already
over, not a real basketball rebound). Treated conservatively: opens a small
phantom possession crediting the ORB (so raw box-score ORB totals still
reconcile exactly), flagged in `warnings`.

**Known discrepancy — `ast` field**: the possession model's assist count
only includes assists it could confidently link to a made shot's action id.
Real data shows Segev sometimes links an `assist` action's
`parentActionId` to a `foul` action instead of a shot (apparently
representing and-1-adjacent plays) — those are not attributed to any
specific shot (would require guessing) and are surfaced separately as
`unresolved_assist_count`. This makes the possession-derived `ast`
(and therefore `ast_to_ratio` computed from a possession subset) a small,
explicit undercount versus the original `engine.py`'s raw assist-action
tally. All nine other metrics are unaffected and match exactly.

## Segment taxonomy (`segments.py`)

Every possession is classified purely from facts observable at its
**start** — never split mid-possession, never re-derived from `userTime`.

| segment_type | segment_value | definition |
|---|---|---|
| `quarter` | `Q1`..`Q4`, `OT` | OT periods pooled into one bucket |
| `half` | `1H`, `2H` | regulation only — OT never folded in |
| `clutch` | `clutch` | Q4/OT, clock <=5:00, \|margin\|<=5, all at possession start |
| `score_state` | 5 mutually-exclusive bins | `ahead_6_plus`/`ahead_1_5`/`tied`/`behind_1_5`/`behind_6_plus`, offense perspective |
| `close_score` | `close_score` | \|margin\|<=5 at start, any period |
| `late_close` | `late_close` | Q4/OT AND \|margin\|<=5 at start (no clock condition) |
| `venue` | `home`/`away` | season aggregation window |
| `recent` | `full_season`/`last_10`/`last_5` | ordered by actual game date, never Segev id |

Nesting invariant (enforced by construction, tested):
`clutch => late_close => close_score`.

## Scoring-source definitions (`scoring_sources.py`)

- **Points off turnovers** (FIBA): points scored during the possession
  immediately following an opposition turnover — never extended into a
  later possession.
- **Second-chance points** (FIBA): points scored **after** the first
  offensive rebound of a possession, up to the possession's end. Points
  scored before that rebound are excluded. `second_chance_scoring_conversion`
  = % of offensive-rebound possessions producing >=1 point after the OREB.
- **Fast-break points**: explicitly **provider-defined** — Segev's own
  `fastBreak` flag on shot/FT actions. Never reinterpreted as the video
  pipeline's `possession_type` metric (a separate, human/model-judged
  concept) — kept distinct with `source="segev_provider_flag"`.
- **Assisted/unassisted**: via the existing assist->shot linkage. Unlinked
  assist actions are counted (`unresolved_assist_count`), never silently
  folded into "unassisted".
- **Shot/scoring mix**: `2PA share`/`3PA share` = share of FGA;
  `scoring_share_2pt/3pt/ft` = share of total points; the three scoring
  shares reconcile to 1.0 within floating tolerance whenever points > 0.

## Runs & droughts (`runs_droughts.py`)

**Runs**: consecutive points by one team without the opponent scoring.
Non-scoring events and period boundaries never end a run (a run can span a
quarter break). A 12-0 run is one run of 12, never split into 8-0 + 4-0.

**Droughts** — explicit **custom project metric, not an official FIBA
category**. Continuous *playing time* (quarter clock only, never
`userTime`) without a score, threshold `>= 3:00 (180s)`, never bridged
across a quarter boundary. Two independent kinds: scoring drought (FTs DO
end it) and FG drought (FTs do NOT end it — only a made field goal does).
Includes the leading gap (period start to first score) and trailing gap
(last score to period end) as real drought candidates, since a team going
scoreless for the first/last 3+ minutes of a quarter is a genuine drought.

## Score dynamics (`dynamics.py`)

Factual: `times_tied` (excludes the trivial initial 0-0 — mathematically
guaranteed since the very first scoring play can never itself produce a
0-0 margin), `lead_changes` (a lead passing through a tie still counts as
one change, e.g. team-leads -> tied -> opponent-leads), `largest_lead`,
`largest_deficit`.

Transparent project-derived: `trailed_by_10_plus`/`led_by_10_plus` flags
plus `won_after_trailing_10_plus`/`lost_after_leading_10_plus` (via
season-level `comeback_conversion_rate`/`blown_10_plus_lead_rate`).
**Denominators are always the count of games where the opportunity
occurred** — never every game played.

## Sample / provenance contract

Every aggregate carries `games_n` and, where relevant,
`possessions_n`/`fga_n`/`fta_n`/`turnover_n`. Zero sample is represented as
an absent/`None` metric value, never a fabricated 0. `MIN_SUFFICIENT_SAMPLE`
(reporting-only, =5) and the stricter agent-rankable threshold
(`n_wins>=3, n_losses>=3, finite non-zero pooled variance`, §18) are
deliberately different and both explicit in `winloss.py`.

## W/L effect-size extension (`winloss.py`)

`build_metric_signal()` is now the single shared implementation behind
both the original ten season-level metrics and every segmented/enriched
one — `compute_signal_from_pairs()` is the generic entry point taking
`[(value_or_None, was_a_win), ...]`. The standardized effect
(`(win_mean-loss_mean)/pooled_sd`) and its zero-variance/insufficient-sample
handling are identical everywhere; nothing about the accepted season-level
behavior changed. `rank_actionable_signals()` (season-level) and
`build_top_wl_differentiators()` (`profile.py`, segmented) both apply the
same `is_agent_rankable()` gate before ranking by `|effect_size|`.

## Query interface (`profile.py`)

`build_team_profile(team_id, pairs, window=...)` — the single entry point
a future agent needs; returns a plain JSON-serializable dict covering the
basic profile, game-flow (quarters/halves), clutch, score-state, recent
form, home/away, scoring sources (FOR — the opponent's row is the identical
computation from their own perspective, so no separate "AGAINST" formula
exists anywhere), assisted/unassisted, shot mix, runs/droughts, and score
dynamics. `build_top_wl_differentiators(pairs)` returns the ranked
segmented W/L signal list. No possession/quarter-marker/`userTime` detail
ever leaks into this output.

## Precomputed segment set (avoiding Cartesian explosion, §19)

Precomputed per game: Q1-Q4, OT, 1H, 2H, the 5 score-state bins, clutch,
close_score, late_close. Recent windows / home-away / W-L comparison are
cross-game concerns applied on top in `profile.py`. Nothing like
`last_5 x away x Q2 x behind_6_plus x losses` is precomputed — the
architecture supports composing it later without a schema change (every
segment is just a possession-list filter), but it is not built now.

## Explicitly deferred (not in this layer)

- Lineup / on-off analytics, player usage or matchup models, lineup
  reconstruction.
- Shot-zone geometry from PBP coordinates (RA/PAINT/MR/LC3/RC3/ATB3) —
  being validated separately in the video track; not duplicated here.
- Any agent interpretation, narrative generation, or UI presentation.
- CrewAI, FastAPI, database persistence (still flat JSON, per the existing
  "no database yet unless required" convention).

## Known open items

- **`ast_to_ratio` discrepancy** at the segment level vs. `engine.py`'s
  season-level value — documented above, does not affect the other nine
  core metrics.
- **`unresolved_assist_count`** is a genuine, real, non-trivial fraction of
  total assists (roughly one in ten across sampled real games) — worth a
  closer look at Segev's assist->foul linkage semantics if the
  assisted/unassisted split needs to be exact rather than a safe lower
  bound in a later stage.
- Personal fouls (`TeamGameComponents.pf`) are not tracked at the
  possession level — always 0 in segment-derived components. Has zero
  effect on any of the ten formulas (none use `pf`); flagged so it is never
  mistaken for a real foul count if reused elsewhere later.
