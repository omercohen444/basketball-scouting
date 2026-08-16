# Deterministic Scouting Feature Pack — Validation / Sanity Report

Date: 2026-08-16
Scope: `stats-layer` worktree only, offline (cached Segev PBP, no network/model calls).
Implementation: `src/basketball_scout/stats/scouting_features.py`
Reproduce with: `PYTHONPATH=src .venv\Scripts\python.exe scripts\scouting_feature_pack\build_report.py`
Example output: `artifacts/scouting_feature_pack/example_output.json` (bounded — 3 example
shot facts, 3 example possession facts, both teams' full summaries for game 136; not a
raw dump of every event)

## 1. What this is

A consolidation layer, not new analytics. Every field is either a direct provider fact
or an already-validated deterministic derivation from `geometry.py` (CP2.4A/hardening),
`fastbreak.py` (CP2.4B), `possession.py`, or `scoring_sources.py` — reused via import,
never recomputed with a second parallel implementation. Two object families, matching
the repo's existing raw-event/aggregate-summary separation (`TeamGameComponents` vs.
`TeamGameStats`):

- **Event/possession facts**: `DeterministicShotFact` (one per FGA), `DeterministicPossessionFact`
  (one per possession).
- **Team/game/season aggregation**: `TeamScoutingSummary`, which *references* the
  existing `FastBreakProfile`/`AssistedProfile`/`ShotScoringMix`/`SecondChanceProfile`/
  `PointsOffTurnoversProfile` objects a caller already built via
  `enrichment.build_game_enrichment`, plus two new-but-trivial aggregations this pack
  needed (`ShotZoneDistribution`, `TransitionShotFacts`) — both are plain groupby/count/
  rate arithmetic over already-computed shot facts, not new basketball research.

## 2. Fields exposed, by object

### `DeterministicShotFact` (one per FGA)

| field | source | notes |
|---|---|---|
| `game_id`, `action_id`, `event_id` | provider (passthrough) | `event_id = f"{game_id}:{action_id}"`, stable join key |
| `team_id`, `opponent_id` | derived from `gameInfo` via `team_id_map_from_game_info` | same `f"{provider}:{id}"` convention as `engine.py`'s `TeamGameStats.team_id` |
| `player_id`, `quarter`, `game_clock`, `game_clock_s` | provider (passthrough / `parse_clock_mmss`) | |
| `is_field_goal_attempt`, `made`, `missed`, `blocked` | provider (`made` field: `made`/`missed`/`blocked`) | mutually exclusive; `blocked` implies `missed` |
| `official_points` | provider | 2 or 3, always authoritative — never overridden by geometry |
| `shot_type` | provider | e.g. `dunk`, `lay-up`, `jump-shot` |
| `and_one` | `possession.find_and1_shot_ids` (reused, not reimplemented) | validated across all 182 cached games (729/11,268 made shots) |
| `official_assist` | independently-derived `parentActionId` scan (not imported from video's `pbp/canonical.py`) | linked-assist fact only |
| `coarse_shot_zone` | `pbp.geometry.classify_coarse_zone` | `lane_2pt` / `midrange_2pt` / `corner_3` / `atb_3` / `None` |
| `rim_attempt` | `pbp.geometry.is_rim_attempt` | from trusted `shot_type` (dunk/lay-up), independent of coordinate precision |
| `shot_distance_m`, `distance_uncertainty_m` | `pbp.geometry.build_shot_geometry` | ±1m uncertainty, never centimeter precision |
| `distance_eligibility` | `pbp.geometry.distance_eligibility` | `over_10ft` / `under_10ft` / `uncertain` |
| `fast_break` | `stats.fastbreak.classify` | `provider.fastBreak == True` only — see §4 |
| `provenance` | this pack | see §3 |

### `DeterministicPossessionFact` (one per possession)

`game_id`, `possession_id` (`f"{game_id}:{possession_index}"`), `possession_index`,
`quarter`, `offense_team_id`, `defense_team_id`, `start_clock_s`, `end_clock_s`,
`duration_s` (start minus end — clock counts down), `ended_by`
(`made_fg`/`made_ft`/`turnover`/`defensive_rebound`/`quarter_end`/`orphan_ft`/
`forced_new_possession`), `possession_points`, `possession_scored`, `fga`, `fgm`,
`turnover`, `followed_opponent_turnover`, `had_offensive_rebound`, `fast_break_points`.
All repackaged directly from `possession.Possession` — no possession-boundary logic
lives in this pack; `possession.py`'s clock convention (never `userTime`, `(quarter,
action id)` ordering) is inherited untouched, including OT compatibility (possession.py
is already quarter-number-agnostic).

### `TeamScoutingSummary` (team/game/season scope)

`team_id`, `opponent_id`, `game_id` (`None` for a multi-game aggregation scope),
`games_n`, `fga`, `fg3a`, `fg3a_rate`, `efg_pct` (via `formulas.three_point_rate`/
`effective_fg_pct`, reused not reimplemented), `shot_zone` (`ShotZoneDistribution`),
`transition_from_shots` (`TransitionShotFacts`), `possessions_n`, `turnovers_n`,
`scoring_possessions_n`, plus five **referenced, not recomputed** profile objects:
`provider_fast_break` (`FastBreakProfile`), `assisted` (`AssistedProfile`), `shot_mix`
(`ShotScoringMix`), `second_chance` (`SecondChanceProfile`), `points_off_turnovers`
(`PointsOffTurnoversProfile`) — a caller supplies whatever it already built via
`enrichment.build_game_enrichment`.

## 3. Validation-state taxonomy

`MetricProvenance.validation_state` — five states, chosen to match what this project has
actually decided about each metric, not a generic confidence scale:

| state | meaning | used for |
|---|---|---|
| `provider_fact` | verbatim source field, no derivation | scoring outcome, official assist |
| `validated_deterministic` | Python derivation, cross-game validated, no open gate | fast_break, possession engine |
| `provisional_deterministic` | structural gates pass, fresh held-out validation still pending | coarse shot zone |
| `partial` | some checks pass, promotion held on a named open question | shot distance |
| `deferred` | intentionally not implemented for the MVP | last-passer identity |

`sample_n` on a `MetricProvenance` means "how many events/games the *method* was
validated against" (a property of the derivation, e.g. `1104` for shot-zone geometry
from CP2.4/CP2.4-hardening), not a per-instance count — every shot fact from every game
carries the same number for the same field, by design.

## 4. FastBreak — exact semantics preserved

`fast_break := provider.fastBreak == True`. `False` (or a missing field) means **only**
"provider did not classify this as a fast break" — the `DeterministicShotFact`/
`TransitionShotFacts` objects have no `half_court`/`defense_set`/`possession_type`
field for a negative to be misread as (structurally enforced — see
`test_fast_break_false_does_not_imply_half_court_field_exists`). `secondary_transition`
remains unimplemented (deferred), matching the CP2.4 management decision.

## 5. Shot zone — locked MVP taxonomy, provisional state

Coarse taxonomy only: `lane_2pt`, `midrange_2pt`, `corner_3`, `atb_3`, plus a separate
`rim_attempt` boolean. No fine RA-vs-Paint class exists (`DEFER_POST_MVP`, unchanged).
Official `points` is always authoritative in `classify_coarse_zone` — coordinates only
sub-classify within whichever family the box score already recorded. The accepted CP2.4
hardening (`LANE_DEPTH_BOUNDARY_TOLERANCE_M = 0.14`) is inherited via the `geometry.py`
import, not reimplemented. `validation_state="provisional_deterministic"` — seed-211
reached 20/20 after hardening, but seed-211 both diagnosed and confirmed that fix, so a
fresh blind human-labeled sample is still required before this can become
`validated_deterministic`.

## 6. Distance — preserved as partial, no false precision

`shot_distance_m` always carries `distance_uncertainty_m` alongside it (currently a
constant ±1m from `geometry.py`, inherited unchanged). `distance_eligibility` uses the
existing three-band semantics (`>=3.5m` → `over_10ft`, `<2.5m` → `under_10ft`, else
`uncertain`) — not tightened or reoptimized here, matching CP2.4 hardening's explicit
finding that the current band has no empirical justification to change.
`validation_state="partial"`, preserved rather than silently promoted.

**Known quirk, disclosed rather than fixed here** (out of this pack's scope):
`distance_uncertainty_m` is set to the constant 1.0 even when `shot_distance_m` is
`None` (missing coordinates) — a pre-existing `geometry.py` behavior, not something this
pack changed. A consumer should treat `distance_uncertainty_m` as meaningful only when
`shot_distance_m` is not `None`.

## 7. Foul / free-throw facts — deliberately narrow

Only `and_one: bool` is exposed on `DeterministicShotFact`, reusing `possession.py`'s
already-validated (182-game, 729/11,268 made shots) and-1 adjacency detector verbatim.
**No general foul→FT-sequence causal linkage was built** beyond and-1 — per the work
package's explicit instruction not to build new fragile heuristics. Raw per-foul
provider fields (`kind`, `type`, `fouledOn`, `freeThrows`) remain available on the raw
action stream but are not wrapped into a new fact object in this pass; a future task
adding that would need its own bounded audit of how reliably a `foul` action links to a
specific resulting FT sequence beyond the and-1 case — reported here as excluded, not
silently built.

## 8. Assist / pass facts — official only, last-passer explicitly deferred

`official_assist: bool` (a linked `assist` action via `parentActionId`) is exposed as a
provider fact. Generic last-passer identity is **not** implemented —
`PROVENANCE_LAST_PASSER_UNAVAILABLE` documents why: the video track's own game-136 audit
found a linked passer for only 38/62 made FGA and 0/78 missed/blocked FGA. This is
intentionally excluded, not attempted.

## 9. Sample coverage (this validation run)

Ran end-to-end against cached game 136 (1 game, both teams): 140 shot facts, 152
possession facts, both `TeamScoutingSummary` objects built successfully with sane
numbers (home 70 FGA / 55.0% eFG%, away 70 FGA / 47.1% eFG%; home fast-break FGA rate
7.1%, matching CP2.4B's validated prevalence range). Underlying methods (geometry,
fastbreak, possession, and-1) each carry their own larger validation samples — see
`docs`/`artifacts/cp2/` — this pack's own test is 26 focused unit tests
(`tests/test_scouting_features.py`) covering identity, joins, provenance, and
aggregation arithmetic; it does not re-validate the underlying basketball logic.

## 10. Deterministic/video boundary

This pack implements **no video metrics**. It is designed to join cleanly with future
video observations by stable keys already present on every object: `game_id`,
`action_id` (shot facts), `possession_id` (possession facts), `team_id`. A future video
observation keyed by the same `game_id`+`action_id` (or `possession_id`) can be attached
alongside this pack's facts without any schema change here.

## 11. Fields intentionally excluded (report, not silent omission)

- Fine RA-vs-Paint shot-zone boundary — `DEFER_POST_MVP` (§5).
- General foul→FT-sequence causal linkage beyond and-1 — not built (§7).
- Generic last-passer identity — deferred, data does not support it reliably (§8).
- `secondary_transition` possession-type classification — deferred (§4).
- Drive detection, kick/dish, multi-drive video semantics — explicitly out of scope
  for this deterministic-only pack; the video track owns this separately.
- Player-level/lineup-level aggregation — out of scope for this work package
  (team-level only, per the MVP's current stage).

## 12. Example serialized objects

See `artifacts/scouting_feature_pack/example_output.json` for real, reproducible
output: 3 example `DeterministicShotFact`s, 3 example `DeterministicPossessionFact`s,
and both teams' complete `TeamScoutingSummary` objects for game 136.

## 13. Downstream join keys

| object | join key(s) |
|---|---|
| `DeterministicShotFact` | `game_id`, `action_id` (or combined `event_id`), `team_id` |
| `DeterministicPossessionFact` | `game_id`, `possession_id` (or `possession_index`), `offense_team_id`/`defense_team_id` |
| `TeamScoutingSummary` | `team_id`, `opponent_id`, `game_id` (or `None` for a multi-game scope) |

## 14. Reused vs. new

**Reused (imported, not reimplemented):** `pbp.geometry.build_shot_geometry`/
`classify_coarse_zone`/`is_rim_attempt`/`distance_eligibility`; `stats.fastbreak.
build_fastbreak_events`/`classify`; `stats.possession.find_and1_shot_ids`/
`parse_clock_mmss`/`Possession`; `stats.formulas.effective_fg_pct`/`three_point_rate`;
`stats.scoring_sources.FastBreakProfile`/`AssistedProfile`/`ShotScoringMix`/
`SecondChanceProfile`/`PointsOffTurnoversProfile` (referenced by a caller, not rebuilt).

**New in this pack:** identity/join-key assembly (`DeterministicShotFact`/
`DeterministicPossessionFact`), the `MetricProvenance`/`ValidationState` taxonomy,
`ShotZoneDistribution` and `TransitionShotFacts` (trivial groupby/rate arithmetic over
already-computed shot facts), `TeamScoutingSummary` (assembly only), and
`team_id_map_from_game_info` (a 4-line helper matching `engine.py`'s existing
convention).

**One small shared-utility change:** `possession.find_and1_shot_ids` was promoted from
a module-private helper (`_find_and1_shot_ids`) to a public one so this pack could reuse
the exact validated logic instead of reimplementing it — a pure rename, no behavior
change, confirmed by the full existing possession test suite passing unchanged.
