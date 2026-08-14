# Video Analytics Stage Plan (CP0)

> **Status: APPROVED 2026-08-15.** This is the controlling technical design for the Video Analytics
> stage (CP1–CP4). Checkpoint governance: CP1, CP2, CP3, CP4 are separate execution packages. After
> every checkpoint — PASS, PARTIAL or FAIL — execution stops for mandatory management review. A PASS
> does not self-authorize the next checkpoint.
>
> This plan was produced from live read-only investigation that **materially changed three core
> assumptions** carried through every section below.

---

# 1. EXECUTIVE DESIGN SUMMARY

## 1.1 The three findings that reshaped this plan

**F1 — SegevSport PBP extraction is SOLVED, and it carries wall-clock time.**
The play-by-play is a public, unauthenticated JSON-RPC 2.0 API. Every action carries `userTime`
(real wall-clock, HH:MM:SS, UTC) *in addition to* `quarterTime` (game clock). Because broadcast
video also advances in real time, this **substantially simplifies the synchronization problem
from "piecewise-nonlinear with unknown stoppage durations" to a piecewise-affine mapping with
human calibration** — a game-level slope plus one offset per quarter, rather than a model of every
stoppage. **Revised after CP1 evidence (2026-08-15): this is not literally a "constant offset,
slope 1.0" model** — real broadcasts run at a measurably different rate than PBP real time (a
fitted slope, not 1.0), and an edited quarter can contain a genuine discontinuity. See §5, §7.

**F2 — Gemini video clipping is the critical unknown, and economics swing ~150× on it.**
The installed SDK exposes `VideoMetadata(start_offset, end_offset, fps)` and its own docstring says
"start and end offsets for **clipping**". But the official Gemini Developer API video docs do **not**
document clipping at all, and a Google staff member confirmed an escalated bug report that
`video_metadata` offsets were **not supported** on the Gemini API. If offsets are honored, the whole
matchday costs a few dollars. If ignored, each call ingests a 2-hour broadcast and the design is
non-viable. See §3, §6, §14, §21.

**F3 — Gate 0 (public full-game YouTube video) is NOT established and may fail.**
The league's official full-game VOD is on **winnerleague.tv** (its own Sportradar-backed OTT
platform), free worldwide. YouTube evidence found only highlights and player reels, not official
full games. The entire video stage assumes a public YouTube full-game URL that has not been proven
to exist. See §3, §5.4, §15, §21.

## 1.2 Recommended pipeline

```
[manual, 7×]  select game  ──►  game manifest entry (internal ids + Segev id + video URL)
                                        │
[automated]   Segev JSON-RPC getActions ──► raw cache (data/raw/pbp/)
                                        │
[automated]   canonical PbpEvent[]  (shots only; team/jersey/type/points/outcome + userTime)
                                        │
[manual, ~10min/game]  quarter anchors+checks ──► GameSync {game slope, per-quarter offset, excluded_quarters}
                                        │
[automated]   video_t = slope * userTime_s + offset_quarter   (game-level slope, fitted per §7)
                                        │
[automated]   VideoWindow [t−20s, t+8s]  (CP1-calibrated; tightened to ~20s in CP3)
                                        │
[automated]   ONE Gemini call per event → all surviving metrics at once
              (YouTube file_uri + video_metadata clip + response_schema + thinking OFF)
                                        │
[automated]   ClassifiedEvent → append-only JSONL (resume by request fingerprint)
                                        │
[automated]   deterministic Pandas/Python aggregation
                                        │
              video_team_metrics  (14 team-game rows, coverage + uncertainty + provenance)
```

## 1.3 Key design decisions

| Decision | Rationale |
|---|---|
| Anchor sync on `userTime`, not game clock | Real time already contains all stoppages (F1) |
| Piecewise-affine mapping: one fitted **game-level slope**, one **offset per quarter** | Absorbs any halftime/quarter cut in the upload; slope corrects for the broadcast running at a different rate than PBP real time (revised 2026-08-15 — see §7) |
| One Gemini call per event, all metrics | 3 metrics for the price of 1; ~950 calls not ~2,850 |
| `MEDIA_RESOLUTION_LOW` + `thinking_budget=0` | 100 vs 300 tok/s; classification needs no reasoning chain |
| Verify clipping via **VIDEO token count** | Deterministic single-call test, not a two-call heuristic |
| Withhold `made`/`fastBreak`/`assisted` from prompt | They leak the labels being measured |
| Use PBP `fastBreak`/`assisted`/`blocked` as **free** validation | Ground truth on ~950 events, not just 20 |
| Append-only JSONL + fingerprint resume | Event 73 failing must not cost the run |

---

# 2. REPOSITORY STATE AS FOUND

Verified by direct inspection this session. Branch `master`, **zero commits**, 34 untracked files,
152 KB. Nothing staged. `.venv` present (Python 3.13.9, `google-genai` 2.18.1). 104 offline tests pass.

## 2.1 Exists and is reusable as-is

| File | What it actually does |
|---|---|
| `src/basketball_scout/config.py` | Env-based `Settings`, never raises at import, `redacted()`, `with_overrides()` |
| `src/basketball_scout/net.py` | `enable_system_trust_store()` — **mandatory on this machine**, TLS fails otherwise |
| `src/basketball_scout/video/metrics.py` | `MetricDefinition` registry; schema/prompt/CLI/fixture all derive from it |
| `src/basketball_scout/video/schema.py` | Dynamic pydantic model from registry; `ClassifiedEvent`; tolerant JSON parsing |
| `src/basketball_scout/video/prompts.py` | Provider-agnostic prompt builder |
| `src/basketball_scout/video/ground_truth.py` | CSV fixture I/O + `agreement()` with **decisive** rate excluding `uncertain` |
| `src/basketball_scout/video/gemini_client.py` | `build_request()` (pure) + `GeminiVideoClassifier`; errors returned not raised |
| `scripts/spikes/probe_segevsport.py` | URL-driven HTTP probe; encoding sniffing; artifact dump |
| `scripts/spikes/gemini_video_event.py` | Single-event CLI: `--dry-run`, `--list-models`, `--agreement`, `--from-fixture` |
| `tests/` (104 tests) | Schema/label validation, config safety, socket-patched import tests |

## 2.2 Exists but MUST change for this stage

| File | Required change | Why |
|---|---|---|
| `video/events.py` | `DEFAULT_PRE_ROLL_SECONDS` 8 → 20, `DEFAULT_POST_ROLL_SECONDS` 4 → 8 | 12s window cannot absorb calibration + operator-lag error (§8.3) |
| `video/events.py` | `ShotEvent` gains `jersey`, `shot_type`, `points`, `expected_offset_s` | Needed to disambiguate *which* shot in a widened window |
| `video/gemini_client.py` | Capture `usage_metadata`, `finish_reason`; set `thinking_config` | Cost telemetry + the F2 clipping test depend on VIDEO token counts |
| `video/schema.py` | `ClassifiedEvent` gains provenance/usage/versioning fields | Auditability + resume (§10) |
| `video/prompts.py` | Add `PROMPT_VERSION`, disambiguation block | Reproducibility across CP2→CP4 |

## 2.3 Does NOT exist (all net-new)

`pbp/segev.py`, `pbp/canonical.py`, `video/sync.py`, `video/manifest.py`, `video/runner.py`,
`video/aggregate.py`, and four `scripts/` entry points. There is **no PBP code of any kind** in the
repo today — the bootstrap only produced an HTTP probe.

## 2.4 Conflicts between the briefing/docs and repository reality

| Claim | Reality | Action |
|---|---|---|
| Briefing: lead file `b-func.js` | **404 Not Found.** Real file is `pbp/js/new-func.js` | Corrected in §5 |
| `docs/VIDEO_SPIKE_NOTES.md`: "PBP extraction method still unknown" | **Resolved this session** — exact endpoint found | Rewrite that section at CP1 |
| Docs: "clock alignment... offset not necessarily constant" | True in spirit, but `userTime` makes it constant *per quarter* | Redesigned in §7 |
| Docs §3: window padding 8s/4s "if localization proves loose, widen it" | Proven insufficient *before* CP1 by error-budget analysis | Changed in §8.3 |
| `VIDEO_SPIKE_NOTES` §2.3: verify offsets by "do the answers change" | Weak heuristic; token counts are deterministic | Replaced in §6.4 |

---

# 3. VERIFIED / UNVERIFIED ASSUMPTIONS

Evidence levels: **[R]** repo/SDK inspection · **[D]** official vendor docs · **[L]** live public
source inspection performed this session · **[S]** secondary source · **[—]** untested.

| # | Assumption | Status | Evidence | Consequence if false | Resolved by |
|---|---|---|---|---|---|
| A1 | Segev PBP is publicly reachable, no auth | **VERIFIED** [L] | `GET stats.segevstats.com/realtimestat_heb/api/?method=getActions&game_id=58` → 200, JSON-RPC, 1020 actions | Whole PBP path changes | done |
| A2 | Only two API methods exist | **VERIFIED** [L] | Static analysis of `new-func.js`: `getActions`, `getBoxScore` only | — | done |
| A3 | Every action has `userTime` wall-clock | **VERIFIED** [L] | All 1020 actions in game 58 carry `userTime` | Sync design collapses to game-clock modelling | done |
| A4 | `userTime` is UTC and matches scheduled tip-off | **STRONG/PARTIAL** [L] | g58 scheduled `18:40` local; first clock action `15:40:25`; Israel = UTC+3 in Oct | Anchor offsets shift by a constant — harmless (we anchor empirically) | CP1 |
| A5 | Shot actions carry team, jersey, type, points, outcome, coords | **VERIFIED** [L] | `parameters{team,player,coordX,coordY,points,type,fastBreak,made,...}` | Disambiguation + attribution weaken | done |
| A6 | PBP already contains `fastBreak` | **VERIFIED** [L] | 15/132 (g58), 14/140 (g136) | — (this is an *opportunity*, §27) | done |
| A7 | Assists link to shots via `parentActionId` | **VERIFIED** [L] | `assist.parentActionId` → shot `id` | Lose free catch-and-shoot proxy | done |
| A8 | ~132–140 FGA per game (both teams) | **VERIFIED** [L] | g58 132, g136 140 | Cost/runtime model scales | done |
| A9 | `start-of-game` is an unreliable anchor | **VERIFIED** [L] | g136: `start-of-game` 17:52:13 vs Q1 `start-of-quarter` 18:51:12 → **59 min gap** | Would break all sync | done |
| A10 | `userTime` is only *mostly* monotonic | **VERIFIED** [L] | 60/1020 (g58), 48/867 (g136) non-monotonic steps | Must not assume sorted order | done |
| A11 | Overtime quarters occur (Q5) | **VERIFIED** [L] | g58 has Q5, `numberOfQuarters`=4 | Missing OT events | done |
| A12 | 2025-26 "Winner League" season is complete & available | **VERIFIED** [L] | comp=`Winner League`, gid 49 (2025-10-12) → 157+ (2026-02-08) | Wrong season selected | done |
| A13 | 2026-27 season has NOT started | **VERIFIED** [L] | `config.json` cYear 2027, Winner Cup fixtures dated 08/09/2026 | Must use 2025-26 | done |
| A14 | SDK exposes `VideoMetadata(start_offset,end_offset,fps)` | **VERIFIED** [R] | `model_fields`; docstring: "start and end offsets for **clipping**"; fps default 1.0, range (0.0,24.0] | — | done |
| A15 | Repo's `build_request()` matches the documented working shape | **VERIFIED** [R][S] | Dry-run emits `fileData.fileUri` + `videoMetadata.startOffset/endOffset`; matches published sample | — | done |
| A16 | **Offsets are actually HONORED for a YouTube URI** | **UNVERIFIED — CRITICAL** [—] | Official docs omit clipping entirely; Google staff confirmed escalated bug report (Jun 2025) that `video_metadata` was unsupported | **Cost ×150, design non-viable** | **CP1-A** |
| A17 | Public YouTube URL accepted as `file_data.file_uri` | **PARTIAL** [D] | Docs confirm YouTube URIs supported; public-only; ≤10 videos/req for 2.5+ | Must use Files API (ToS-dependent) | CP1-A |
| A18 | Video token cost ≈300 tok/s default, ≈100 tok/s low | **VERIFIED** [D] | Direct quote, video-understanding docs | Cost model shifts proportionally | CP1 measures |
| A19 | Free tier caps YouTube at 8h/day | **VERIFIED** [D] | Direct quote; paid tier no length limit | May allow only ~4 calls/day if clip counts as full length | **CP1-B** |
| A20 | A clipped request counts only the clip toward the 8h quota | **UNVERIFIED** [—] | Not documented | Free tier unusable → paid key required | **CP1-B** |
| A21 | **Public full-game YouTube VOD exists for a Premier League matchday** | **UNVERIFIED — GATE 0** [S] | Official VOD is winnerleague.tv (Sportradar OTT); YouTube shows highlights/player reels only | **Entire stage blocked** | **CP1-0** |
| A22 | A specific Gemini model id supports video + structured output | **UNVERIFIED** [—] | `gemini-2.5-flash` in `.env.example` is a placeholder | Wrong model → errors | CP1-C |
| A23 | Current Gemini pricing | **UNVERIFIED** [S] | Secondary sources mutually inconsistent; one claimed "$0.15/second of video" (implausible) | Cost estimate wrong | CP1 measures tokens; price applied later |
| A24 | Operator entry lag between event and `userTime` | **UNVERIFIED** [—] | Estimated 0–6s | Window mis-sized | **CP1-D** |
| A25 | YouTube upload advances at slope = 1.0 relative to PBP real time | **FALSIFIED** [L] | CP1-D, live 2026-08-15: 3 independently-fit quarters gave slope 0.943/0.957/0.937 (avg 0.9456) on game 136 — not 1.0. One quarter (Q2) additionally showed a genuine discontinuity a single slope could not explain | Naive slope=1.0 produced 60-70s localization errors; must fit slope per game, not assume it | resolved — see §7 |
| A26 | TLS requires `truststore` on this machine | **VERIFIED** [L] | Every HTTPS host failed until `enable_system_trust_store()` | All network code breaks | done |

## 3.1 Highest-risk dependency chain

```
A21 (YouTube full game exists)
   └─► A17 (Gemini accepts the URL)
         └─► A16 (offsets honored)   ◄── economics live or die here
               └─► A20 (quota counts the clip, not the video)
                     └─► A24/A25 (localization accurate enough)
                           └─► metric feasibility (CP2)
```

Every link is external and **none of the first four is verified**. CP1 must walk this chain in
exactly this order and stop at the first failure. All PBP work (A1–A13) is already de-risked and is
*not* on the critical path.

---

# 4. RECOMMENDED VIDEO ARCHITECTURE

## 4.1 Module boundaries

```
src/basketball_scout/
  config.py             [EXTEND]  + segev_api_url, video defaults, concurrency, budget caps
  net.py                [REUSE]   enable_system_trust_store() — call in every entry point
  ids.py                [NEW,~40] internal id minting/slugs (team_id, game_id)
  pbp/
    segev.py            [NEW,~180] JSON-RPC client, raw cache, error envelope handling
    canonical.py        [NEW,~200] PbpEvent, shot extraction, exclusion rules, parentActionId links
  video/
    metrics.py          [REUSE]   registry (definitions refined in §9)
    events.py            [MODIFY]  window defaults; ShotEvent disambiguation fields
    schema.py            [MODIFY]  ClassifiedEvent provenance/usage/versioning
    prompts.py            [MODIFY]  PROMPT_VERSION + disambiguation block
    sync.py              [NEW,~220] QuarterAnchor, GameSync, mapping, drift detection
    manifest.py          [NEW,~140] game manifest load/validate/save
    gemini_client.py     [MODIFY]  usage capture, thinking off, retry classification
    runner.py            [NEW,~260] batch execution, resume, cache, observability
    aggregate.py         [NEW,~180] deterministic team metrics
    ground_truth.py      [REUSE]   + PBP-proxy agreement helper
scripts/
  fetch_pbp.py           [NEW]  fetch + cache + summarize one game's PBP
  calibrate_game.py      [NEW]  interactive quarter-anchor calibration
  run_video_game.py      [NEW]  CP3 one-game pipeline
  run_matchday.py        [NEW]  CP4 seven-game batch
  spikes/*.py            [REUSE/EXTEND]
```

**Layering rule (already established in the repo, preserved):** only `gemini_client.py` imports
`google.genai`; only `pbp/segev.py` knows the Segev wire format. `sync.py`, `canonical.py`,
`aggregate.py` are pure and fully testable offline.

## 4.2 Data flow and artifacts

| Stage | Input | Output | Tracked in Git? |
|---|---|---|---|
| Manifest | manual | `data/manifest/matchday.json` | **yes** (small, hand-made) |
| PBP fetch | Segev id | `data/raw/pbp/segev_<gid>.json` | no (ignored) |
| Canonicalize | raw JSON | in-memory `PbpEvent[]` | no |
| Calibrate | PBP + video | `sync` block inside manifest | **yes** |
| Classify | events + sync | `data/processed/video/events/<game_id>.jsonl` | no |
| Aggregate | JSONL | `data/processed/video/video_team_metrics.csv` + `.json` | no |
| Validation | sample | `data/validation/video_events_ground_truth.csv` | **yes** |
| Run telemetry | runner | `artifacts/runs/<ts>/run_summary.json`, `run.log` | no |

One `.gitignore` line to add: `!data/manifest/`.

---

# 5. SOURCE & PBP DESIGN

## 5.1 What was directly verified this session

**Endpoint (exact):**
```
https://stats.segevstats.com/realtimestat_heb/api/?method=getActions&game_id=<SEGEV_ID>
https://stats.segevstats.com/realtimestat_heb/api/?method=getBoxScore&game_id=<SEGEV_ID>
```
Discovered by static analysis of `https://basket.co.il/pbp/js/new-func.js`
(`PATH_TO_STATS = "https://stats.segevstats.com/realtimestat_heb/api/"`). Public, no auth, no
cookie, no CORS obstacle server-side. **`b-func.js` from the briefing does not exist (404).**

**Response envelope** (JSON-RPC 2.0; note `Content-Type: text/html` despite JSON body):
```jsonc
{ "jsonrpc":"2.0", "id":"42", "error":null,
  "result": {
    "gameInfo": { "gameId","id","time","homeTeam","awayTeam","competition",
                  "numberOfQuarters","gameFinished","currentQuarter","currentQuarterTime" },
    "actions": [ ... ]        // ABSENT ENTIRELY when the game has no PBP
  } }
```
Not-found envelope (**still HTTP 200**):
```json
{"jsonrpc":"2.0","id":"1","result":{"success":false},
 "error":{"code":"-32000","message":"game not found"}}
```

**Action record (verified, game 58):**
```jsonc
{ "quarter":1, "id":580015, "parentActionId":0,
  "userTime":"15:40:46",     // real wall clock (UTC)  ◄── the sync key
  "quarterTime":"09:54",     // game clock remaining
  "type":"shot", "playerId":1076, "teamId":6,
  "parameters":{ "team":2, "player":"32", "coordX":405.0, "coordY":252.0,
                 "points":2, "type":"jump-shot", "fastBreak":false,
                 "secondChancePoints":false, "pointsFromTurnover":false,
                 "made":"blocked" } }
```

**Type distribution (game 58, 1020 actions):** `clock` 244 · `substitution` 214 · **`shot` 132** ·
`rebound` 82 · `freeThrow` 69 · `foul` 66 · `foul-drawn` 65 · `turnover` 50 · `assist` 38 ·
`steal` 19 · `deflection` 12 · `quarter` 10 · `block` 10 · `timeout` 7 · `game` 2.

**Sub-types:** `jump-shot` 83, `lay-up` 44, `dunk` 5; fouls `personal`/`technical`; rebounds
`offensive`/`defensive`; turnovers `bad-pass`/`travelling`/`24-seconds-violation`/etc.

**ID scheme:** Segev game ids are **small integers**, not the 18xxx range. Action id ≈
`gameId*10000 + sequence`. `game_id=1` exists but has **no `actions` key** (unplayed fixture) —
the loader must handle this.

**Season/competition mapping (verified by scanning gid 1→160, step 3):**
`Winner League` (= Premier League, the target), `Winner Cup`, `Preparation Games`, `Women`.
2025-26 Winner League games run from gid≈49 (2025-10-12) through 157+ (2026-02-08).
**The 2026-27 season has not started** (`config.json`: cYear 2027; fixtures dated 08/09/2026),
so **the MVP must use the completed 2025-26 season.**

## 5.2 Game identity (answers question A)

Internal record, one per game, hand-authored for 7 games:

```jsonc
{
  "game_id": "IBPL-2025-26-G070",            // internal, stable, minted by us
  "season": "2025-26",
  "competition": "Winner League",
  "date_utc": "2025-11-02",
  "source": { "segev_game_id": 70,           // provider id — never conflated with internal
              "basket_co_il_game_id": null },// optional, discovery convenience only
  "home": {"team_id":"IBPL-HAEMEK","segev_team_id":8,"name":"HAPOEL HAEMEK"},
  "away": {"team_id":"IBPL-BEERSHEVA","segev_team_id":10,"name":"BEER SHEVA"},
  "video": { "provider":"youtube", "url":"https://www.youtube.com/watch?v=...",
             "verified_full_game": true, "duration_s": 7412 },
  "sync": { "...": "see §7.4" }
}
```

**Automated:** PBP fetch, team/roster resolution, event extraction, windowing, classification,
aggregation, `games_analyzed`.
**Manual (7×):** choosing the game, finding+verifying the video URL, and the quarter anchors.
**Why manual:** there is no proven programmatic game→video mapping, and per A21 the video may not
be on YouTube at all. Building a discovery scraper for a 7-item problem is not justified;
7 lookups ≈ 30 min total.

**Team identity:** `segev_team_id` is stable within the season (verified: HAPOEL HAEMEK = 8,
BNEI HERZLIYA = 6). Internal `team_id` is a slug minted once and stored in the manifest — this is
the seam that later lets the PBP stage and the website share team identity without importing
Segev's numbering.

## 5.3 Canonical PBP event (answers question C)

Minimum representation for Video Analytics — deliberately **not** the full future PBP platform:

```python
@dataclass(frozen=True)
class PbpEvent:
    event_id: str          # f"{game_id}:{source_action_id}"  — stable, idempotency key
    game_id: str           # internal
    source_action_id: int
    quarter: int           # 1..N, includes OT
    quarter_time: str      # "09:54" as given
    user_time_s: float     # seconds since midnight UTC  ◄── sync input
    event_type: str        # "shot"
    team_side: str         # "home" | "away"   (from parameters.team 1|2)
    team_id: str           # internal
    source_team_id: int
    player_jersey: str     # parameters.player
    source_player_id: int
    shot_type: str         # jump-shot | lay-up | dunk
    points: int            # 2 | 3
    outcome: str            # made | missed | blocked
    fast_break: bool        # PBP-derived — NEVER sent to the model (label leakage)
    assisted: bool           # derived via parentActionId — NEVER sent to the model
    coord_x: float | None
    coord_y: float | None
    raw: dict               # complete source action, preserved
```

`raw` is preserved so the later PBP stage can re-derive richer features without re-ingesting.

**Parsing rules (implementation-ready):**
- `user_time_s`: `HH:MM:SS` → seconds. **Do not assume sorted** (A10) — sort by `(quarter, user_time_s)`
  after parsing, and record how many inversions were corrected.
- `assisted`: build `{a.parentActionId for a in actions if a.type=="assist"}`, test shot `id` membership.
- Missing `result.actions` → raise `PbpUnavailable(game_id)`; do not return an empty list silently.
- `error.message == "game not found"` despite HTTP 200 → raise `PbpNotFound`.
- Text is Hebrew UTF-8 served without a charset header → sniff encoding (the existing probe already
  does this; reuse the same approach).

## 5.4 Video source (F3 — Gate 0)

**Verified:** the league operates its own OTT platform, **winnerleague.tv**, built with Sportradar,
carrying full-game VOD free worldwide. `pbp/js/games.js` references `https://winnerleague.tv`.
**Not verified:** that any public **YouTube** full-game exists for a Premier League matchday.
Searches surfaced only highlights and individual-player compilations.

This matters because **Gemini can ingest a YouTube URL, but cannot ingest an arbitrary OTT URL.**
The only provider-supported inputs are a YouTube URL, an uploaded file (Files API), or a GCS URI.

CP1 therefore begins with a source check *before any API work* (§15, step 0), and the fallback tree
(§21) treats "no public YouTube full game" as a first-class branch requiring management decision —
not something to route around silently.

## 5.5 Bounded fallback for PBP

Extraction is already solved, so this is short:
1. **Primary:** `getActions` JSON-RPC (verified working).
2. If the endpoint starts refusing server-side calls (UA/Referer/rate): send browser-like headers
   (`Referer: https://basket.co.il/pbp/`) — already proven to work. **Timebox 30 min.**
3. If it is withdrawn entirely: parse the cached raw JSON already on disk (fetch all 7 games'
   PBP **early, at CP1**, precisely so later stages cannot be blocked by the source).
4. Only if 1–3 fail: `getBoxScore` gives team totals but **no temporal events** → video localization
   is impossible → escalate to management (video stage cannot proceed on PBP-assisted localization).

**Action:** fetch and cache all candidate games' PBP during CP1. It costs seconds and permanently
removes the source from the risk chain.

---

# 6. GEMINI INTEGRATION DESIGN

## 6.1 The exact interaction (already built, verified shape)

`build_request()` in the repo already emits precisely the shape published in working examples:

```jsonc
{ "model": "<pinned at CP1>",
  "contents": {"role":"user","parts":[
    {"fileData":{"fileUri":"https://www.youtube.com/watch?v=..."},
     "videoMetadata":{"startOffset":"4342s","endOffset":"4362s","fps":1.0}},
    {"text":"<prompt>"}]},
  "config": {"systemInstruction":"...","responseMimeType":"application/json",
             "responseSchema":"VideoEventClassification","temperature":0.0,
             "mediaResolution":"MEDIA_RESOLUTION_LOW"} }
```

**Changes required:**
- add `thinking_config=ThinkingConfig(thinking_budget=0)` — SDK confirms `0 is DISABLED`.
  Classification does not need a reasoning chain; this cuts output tokens and latency.
- capture `response.usage_metadata` (`prompt_token_count`, `candidates_token_count`,
  `thoughts_token_count`, `total_token_count`, and **`prompt_tokens_details` → per-modality counts**).
- capture `candidate.finish_reason` (SDK enum includes `SAFETY`, `RECITATION`, `MAX_TOKENS`,
  `PROHIBITED_CONTENT`) — a `SAFETY` block must be recorded distinctly from a parse failure.

## 6.2 Classification of every important claim

| Claim | Status |
|---|---|
| `VideoMetadata(start_offset,end_offset,fps)` exists; docstring says "for clipping"; fps default 1.0, range (0.0,24.0] | **VERIFIED FROM REPO/SDK** |
| `Part` accepts `file_data`+`video_metadata`; `GenerateContentConfig` accepts `response_schema`/`media_resolution`/`system_instruction`; response exposes `.parsed` and `.usage_metadata` | **VERIFIED FROM REPO/SDK** |
| Public YouTube URLs supported; public-only; free tier 8h/day; ≤10 videos/request (2.5+); ~300 tok/s default, ~100 tok/s low; 1M ctx ≈ 1h default / 3h low | **VERIFIED FROM OFFICIAL DOCS** |
| Official Gemini API video docs contain **no** clipping/`videoMetadata`/custom-FPS section | **VERIFIED FROM OFFICIAL DOCS** (confirmed against the raw `.md.txt`) |
| Google staff confirmed an escalated report that `video_metadata` offsets were unsupported on the Gemini API (Jun 2025); related reports of FPS not functioning | **SECONDARY (official forum)** |
| A published sample shows YouTube + `start_offset='1250s'`/`end_offset='1570s'` working | **SECONDARY** |
| Offsets are honored **for this key/tier/model** | **VERIFIED LIVE (CP1-A, 2026-08-15).** Three real clipped calls against a real YouTube video: 5s→455 VIDEO tokens, 20s→1820, 40s→3640 — a constant ~91 VIDEO tokens/s, and the 40s call cost exactly 2× the 20s call. Offsets are conclusively honored, not ignored. |
| A clipped call counts only clip seconds toward the free-tier 8h quota | **STILL OPEN (CP1-B, soft gap).** Investigated live: no 429s across 8 real calls, and `x-gemini-service-tier: standard` was observed in response headers, but the SDK response exposes no explicit quota-remaining field. Not resolved; not pursued further per management instruction. |
| Specific model id supporting video + structured output | **VERIFIED LIVE (CP1-C, 2026-08-15).** The plan's placeholder default (`gemini-2.5-flash`) returned HTTP 404 "no longer available to new users" on a real call despite being listed by `--list-models`. Pinned **`gemini-3.5-flash`** instead — confirmed working across CP1-A and CP1-E (multiple successful real calls, valid structured output, `finish_reason=STOP`). |
| Current pricing | **UNVERIFIED (secondary sources contradict; one implausible)** |

## 6.3 Model selection (answers question E)

**Required properties**, in order:
1. Accepts video input from a YouTube `file_uri`.
2. Honors `video_metadata` clipping (the decisive property).
3. Supports `response_schema` structured output.
4. Supports `thinking_budget=0`.
5. Low cost per input token (video dominates the token count).
6. Stable, not preview (a preview model that is withdrawn mid-week is a real one-week risk).
7. ≥1M context (only a safety margin if clipping fails).

**Recommendation:** pin the **cheapest current stable Flash-class model that passes the CP1-A
clipping test**. Do not pin from memory. `gemini-2.5-flash` in `.env.example` is an unverified
placeholder — the docs note clipping/FPS quality is "significantly higher from 2.5 series models",
so 2.5-or-later is the floor.

**Selection procedure (CP1-C, ~20 min):**
1. `gemini_video_event.py --list-models` → enumerate ids actually visible to the key.
2. Shortlist stable Flash-class ids ≥2.5 that advertise video input.
3. Run the CP1-A token test on the cheapest; if it fails, try the next; then a Pro-class id.
4. Pin the winner in `.env` as `GEMINI_VIDEO_MODEL` and record it in `run_summary.json`.
5. **Fallback:** if no Flash-class model honors clipping but a Pro-class one does, report the cost
   delta to management before adopting (§25).

Record for the pinned model: exact id, clipping honored (y/n), video tokens for a 20s clip,
p50 latency, structured-output path used (`.parsed` vs text fallback).

## 6.4 The decisive clipping test (CP1-A) — deterministic, one call

Replaces the weak "do the answers change" heuristic in `VIDEO_SPIKE_NOTES` §2.3.

> Issue **one** request against a full-game video with `start_offset`/`end_offset` spanning **20s**,
> `media_resolution=LOW`. Read `usage_metadata.prompt_tokens_details` and extract the
> **`VIDEO` modality token count**.

| Observed VIDEO tokens | Interpretation | Verdict |
|---|---|---|
| ≈ 2,000 (20s × ~100 tok/s) | Offsets **honored** | **PASS** |
| ≈ 720,000 (7,200s × ~100) | Offsets **ignored**, full broadcast ingested | **FAIL → §21 branch T3** |
| API error naming `video_metadata` | Offsets **rejected** | **FAIL → §21 branch T3** |
| Anything else | Inconclusive — repeat at 40s; tokens must roughly double | investigate |

**Why this is reliable:** it is a direct measurement of what was billed and processed, independent of
model behaviour, prompt quality, or whether the clip content happens to look similar. A second
confirmation at 40s (expect ~2× tokens) turns it from a threshold check into a linearity check.

**Run this before anything else that costs money.** It is one call and it decides the architecture.

---

# 7. PBP ↔ VIDEO SYNCHRONIZATION DESIGN

This is the section the briefing correctly identified as the largest engineering risk after API
feasibility. **Finding F1 changes its nature fundamentally — and CP1 live evidence (2026-08-15)
further revised the exact shape of that change. This section reflects the CP1-validated design;
history is kept visible so a later reader understands why it looks the way it does.**

## 7.1 Why the problem is smaller than assumed — and the exact form CP1 proved

The briefing framed it as: *PBP says "Q2 06:17 remaining", video says "00:47:32", and broadcasts
contain dead balls, timeouts, quarter breaks, halftime, replays and ads — so the mapping is
nonlinear and unknown.*

That framing is correct **if the only PBP time signal is the game clock.** It is not.

Every Segev action carries **`userTime` — real wall-clock time.** Real time and video time both
advance monotonically, and every stoppage the briefing lists (dead balls, timeouts, replays,
reviews, free-throw sequences) consumes real time *and* video time together. This is what makes
the mapping tractable at all:

```
game clock → video    : NONLINEAR, needs a model of every stoppage                    ✗
userTime   → video    : PIECEWISE-AFFINE, one fitted slope + one offset per quarter   ✓
```

**CP0 originally assumed the affine slope was exactly 1.0** (real time and video time advancing at
literally the same rate) and that a single anchor's offset would hold across an entire quarter.
**CP1-D falsified both of those specific sub-assumptions with live data — the broader insight
above did not fail, but its exact parameters were wrong:**

- **The slope is not 1.0.** On game 136 (2026-08-15), three independently-fit quarters gave
  slope 0.943 / 0.957 / 0.937 — averaging **0.9456**. The uploaded broadcast runs consistently
  ~5.4% "faster" than PBP real time (plausibly: compressed dead time in the edited VOD). A naive
  `slope=1.0` model produced 60-70s localization errors on this video; fitting the real slope
  brought 3 of 4 quarters to within tolerance immediately.
- **A single per-quarter offset is not always sufficient.** One quarter of four (Q2) contained a
  confirmed genuine discontinuity: a bisection check showed the residual **growing** with distance
  from the anchor rather than shrinking — the opposite of what a pure slope error produces, and the
  signature of an actual edit/cut inside the quarter.

**The validated design is therefore a genuine piecewise-affine model, not a pure constant-offset
one:** `video_time = slope * userTime + offset_quarter`, with **one slope fitted at the game
level** (not a fixed constant, and not fit separately per quarter unless evidence requires it —
§7.3), one offset per quarter, and the explicit possibility that an edited quarter breaks the
model entirely and must be excluded (§7.7). Do not describe this design elsewhere as a "constant
offset" or "slope 1.0" mapping — both phrases are now inaccurate.

Further supporting evidence for the underlying `userTime`-based approach (measured earlier this
session, still valid — this is about why `userTime` is usable at all, independent of the slope
finding above):

| | Game 58 | Game 136 |
|---|---|---|
| Q2 real elapsed | 26.4 min | 27.3 min |
| Q3 real elapsed | 33.3 min | 27.9 min |
| Q4 real elapsed | 40.2 min | 26.4 min |
| Halftime gap (Q2 end → Q3 start) | 9.1 min | 12.8 min |
| Non-monotonic `userTime` steps | 60 / 1020 | 48 / 867 |
| `start-of-game` → Q1 `start-of-quarter` | 2 s | **59 min** |

Three hard lessons now extracted: **never anchor on `start-of-game`** (A9), **never assume
`userTime` is sorted** (A10), and **never assume slope=1.0** (A25, falsified by CP1-D).

## 7.2 Alternatives considered

| # | Approach | Effort | Robust to breaks/replays | Manual/game | Expected accuracy | Scales 1→7 | Failure mode |
|---|---|---|---|---|---|---|---|
| 1 | Single global offset from tip-off, game-clock based | Low | **No** — needs stoppage model | ~1 min | ±60s+ | yes | Drifts badly by Q4 |
| 2 | Single global offset, **userTime** based | Low | Yes, unless upload cuts | ~1 min | ±5s if no cuts | yes | Breaks at halftime cut |
| 3 | **Piecewise-affine (game-level fitted slope + per-quarter offset), userTime based** ⭐ | Low-Med | Yes for slope error; no for genuine cuts (§7.7) | ~7-10 min | **±5s** (post-fit, per quarter) | yes | Needs 4-5 anchors + 1 check/quarter; an edited quarter must be excluded, not silently mismapped |
| 4 | Piecewise per-possession / dense anchors | High | Yes | ~30 min+ | ±2s | poor | Manual cost explodes |
| 5 | Automated scoreboard OCR from frames | **High** | Yes | ~0 after build | ±2s | yes | 4–8h build; new CV dependency; own failure modes |
| 6 | Ask Gemini to locate the event itself | Med | Partial | 0 | unknown | costly | Circular — needs the clip we're trying to find |
| 7 | Hybrid: #3 + automated residual check | Med | Yes | ~7 min | ±5s | yes | — |

**Selected: #3, with the #7 automated residual check.**

Rejected #5 (scoreboard OCR) explicitly: it is the "advanced CV infrastructure" trap. It would
consume 4–8 hours — a third of the entire stage budget — to replace ~70 minutes of manual work, and
it introduces a second unproven pipeline inside the highest-risk stage. Revisit only if the project
ever scales past ~20 games (§27, P6).

Rejected #4: manual cost scales linearly with events and destroys the 7-game budget.

## 7.3 Mapping logic

```python
video_t = (user_time_s - anchor.pbp_user_time_s) * slope + anchor.video_time_s
```

This is the piecewise-affine model: **one `slope`, fitted at the game level** (not assumed 1.0,
not fit separately per quarter as a default — see below), combined with **one offset per quarter**
via that quarter's anchor. This is exactly what `GameSync` in `src/basketball_scout/video/sync.py`
already implements (`slope: float` is a single game-level field; `_anchor_for(quarter)` supplies
the per-quarter offset) — **no code change was required by the CP1 finding**, only the
documentation of what `slope` means and how it is obtained changed.

**How `slope` is obtained (revised 2026-08-15, per CP1-D):**
1. Fit slope from one representative quarter's anchor + check pair (`fit_slope()`), or average
   across however many quarters have both points available.
2. Apply that **single game-level slope** to every quarter.
3. **Do not fit separate per-quarter slopes as a default.** Only if the game-level slope fails
   validation in a specific quarter (§7.5) and evidence specifically requires it should that
   quarter be investigated individually — and per §7.7, the MVP resolution for a quarter that
   still fails is to **exclude that quarter**, not to give it its own slope.

Quarter selection uses the event's own `quarter` field (handles OT naturally, A11).

**Guards:**
- `video_t` outside `[0, video_duration_s]` → mark event `sync_out_of_range`, skip, count it.
- No anchor for the event's quarter → `sync_missing_anchor`, skip, count it.
- A quarter confirmed to contain a discontinuity (§7.5/§7.7) → excluded entirely, not mapped.

## 7.4 Calibration data representation

Stored inside the manifest entry so calibration travels with game identity and is version-controlled:

```jsonc
"sync": {
  "video_duration_s": 7412,
  "slope": 0.9456,          // fitted at the game level (§7.3) — do NOT default this to 1.0
  "tolerance_s": 5.0,
  "anchors": [
    {"quarter":1, "source_action_id":580037, "pbp_user_time_s":56541,
     "video_time_s":412.0, "method":"manual",
     "note":"Q1 first made FG, #14 3PT"},
    {"quarter":2, "source_action_id":580311, "pbp_user_time_s":58180,
     "video_time_s":2051.0, "method":"manual"},
    {"quarter":3, "...": "..."},
    {"quarter":4, "...": "..."}
  ],
  "checks": [
    {"quarter":1, "source_action_id":580290, "predicted_video_s":1962.0,
     "observed_video_s":1964.0, "residual_s":2.0, "status":"ok"}
  ],
  "quality": "ok",                    // ok | degraded | failed — GAME-level rollup (§7.7)
  "excluded_quarters": [],            // quarters confirmed to contain a discontinuity (§7.7);
                                       // e.g. [2] on game 136 this session — that game's video
                                       // metrics are computed from Q1/Q3/Q4 only, never Q2
  "operator_lag_estimate_s": 3.0,     // measured at CP1, feeds window sizing
  "calibrated_by": "manual",
  "calibrated_at": "2026-08-15T09:12:00Z"
}
```

## 7.5 Calibration procedure (implementation-ready, revised 2026-08-15 per CP1-D)

`scripts/calibrate_game.py --game IBPL-2025-26-G070` does the following. **Two human observations
per quarter are now standard**, not an optional extra: one anchor (sets that quarter's offset) plus
one check (validates it). The check is **primarily a residual check, not automatically a second
fitted anchor** — it only becomes the basis for a slope fit when the residual pattern calls for it
(Step 5).

**Step 1 — propose anchors automatically.** For each quarter, select the **first made field goal**
and the **last made field goal** (`type=="shot"`, `made=="made"`). Made FGs are ideal anchors: the
scoreboard visibly changes, they are unambiguous, and they exist in every quarter.

**Step 2 — print a clickable seek link per anchor.** For quarter 1, the operator has no offset yet,
so the tool prints the raw YouTube URL and asks for the timestamp of the described shot
("Q1 first made FG — #14, 3PT jump shot"). For quarters 2+, the tool **predicts** using the
game-level slope fitted so far (initially the previous quarter's implied rate, or 1.0 before any
fit exists) and prints `https://youtu.be/<id>?t=<predicted>s`, so the operator clicks, confirms or
nudges.

**Step 3 — operator records the observed video timestamp** for each of the 4-5 anchors.

**Step 4 — automatic residual check.** For each quarter, predict the *last* made FG of that quarter
(the standard check point) and report `residual = observed − predicted`. Operator confirms the
predicted timestamp visually (one click).

**Step 5 — residual handling.** Thresholds are unchanged from the original design:

| Residual | Meaning | Action |
|---|---|---|
| \|r\| ≤ 5s | Good | accept, quarter `status="ok"` |
| 5 < \|r\| ≤ 15s | Slow drift, or the game-level slope has not yet been fit/applied | **Fit (or re-fit) the game-level slope** from the available anchor+check pairs (§7.3) and re-check under that slope before concluding drift is unresolved |
| \|r\| ≥ 15s | Possible discontinuity inside the quarter | **Targeted bisection**, not automatic segmentation: request one additional real timestamp for a PBP event roughly midway between the anchor and the check. Compare its residual to the check's residual (§7.5.1) |

**Step 5.1 — interpreting a bisection.** This is the diagnostic step CP1-D actually used and
validated live:
- If the midpoint's residual is **smaller** than the endpoint's and roughly proportional to its
  (shorter) distance from the anchor → the game-level slope was simply not yet applied or was
  mis-fit; re-fit and re-check. This is still ordinary drift, not a cut.
- If the midpoint's residual is **similar in size to or larger than** the endpoint's, despite being
  closer to the anchor → confirmed genuine discontinuity (an edit/cut). **Do not attempt to build a
  two-segment mapping for this quarter.** Per §7.7, the MVP resolution is to mark the quarter
  excluded and move on. Segmented (piecewise, multi-anchor-per-quarter) mapping remains a possible
  future fallback, adopted only if later evidence (across more games) shows that excluding whole
  quarters is materially damaging coverage — not implemented now.

**What NOT to do:** do not fit a separate slope per quarter as a first response to a bad residual
(§7.3) — always try the shared game-level slope first. Do not silently accept a quarter whose
bisection confirms a cut; exclude it explicitly (§7.7) and record why (§7.4's `excluded_quarters`).

**Effort:** Q1 anchor ≈ 2-3 min (searching blind); Q2-Q4 ≈ 45-60 s each (prediction lands close);
residual checks ≈ 15 s each; an occasional bisection (expect roughly 1 quarter in 4, per CP1-D
evidence) adds ≈ 1-2 min. **≈ 10 min/game → ≈ 70 min for 7 games.** Revised upward from the original
~7 min/game estimate now that bisection is accounted for as routine, not exceptional.

## 7.6 Operator lag — the residual error to measure

`userTime` is when the **scorer pressed the button**, not when the ball left the shooter's hand.
Expect a lag of roughly 0–6 s. This is *not* removed by calibration if the anchor is also a PBP
event — anchoring on a made FG means the offset **absorbs the average lag**, and only the
*variance* remains.

**CP1-D measures it:** take 8 shots spread across a game, record the true release timestamp in the
video, compare against mapped `userTime`. Report mean (absorbed by the anchor) and standard
deviation (must be covered by the window). Store `operator_lag_estimate_s` in the manifest.

If σ > 8 s, the window must widen and disambiguation becomes essential (§8.4).

## 7.7 Acceptable tolerance and low-confidence behaviour (revised 2026-08-15 per CP1-D)

Granularity matters here: quality is assessed **per quarter**, and the MVP default response to a
confirmed problem is to **exclude that quarter**, not the whole game. CP1-D's own evidence is the
reason for this: game 136 had 3 of 4 quarters (75%) calibrate cleanly once the game-level slope was
fitted, and only one confirmed discontinuity (Q2). Discarding the whole game over one bad quarter
would have thrown away three-quarters of good, usable data for no reason — the per-quarter grain
is what the architecture was already designed to support (§4, §7.1) and what CP1 confirmed is the
right grain to act on.

- **Target:** |localization error| ≤ 5 s post-calibration (after the game-level slope is applied);
  window absorbs up to ~19 s on the CP1/CP2 window sizing (§8.3).
- **Quarter `status="ok"`:** residual ≤5s after the game-level slope is applied. Process normally.
- **Quarter `status="degraded"`:** residual in the 5-15s drift band and not resolved by re-fitting
  the game-level slope. Still process, but stamp every event from that quarter with
  `sync_quality="degraded"`; aggregation must carry this flag through to the team-game row (§11).
- **Quarter `status="excluded"` (renamed from "failed" — see below):** a bisection check (§7.5.1)
  confirmed a genuine discontinuity. **This is the MVP default resolution for a confirmed cut: drop
  that quarter's events, keep the rest of the game.** Do not spend API calls on events in an
  excluded quarter. Do not attempt an automatic segmented/multi-anchor mapping to rescue it — that
  is deferred (§7.5, §21 T6) unless later evidence across more games shows quarter exclusion is
  materially damaging total coverage.
- **Game-level `quality` field** (`GameSync.quality()`) remains a useful rollup for logging/triage,
  but it is **not itself an accept/reject gate any more** — a game with one excluded quarter and
  three good ones is usable, and downstream aggregation (§11) must represent exactly that: which
  quarters contributed, not a single binary game-level verdict.
- A game where **most or all** quarters are excluded (not just one) is a materially different,
  worse finding — see §21 T7, unchanged: that indicates the source video itself is unusable for
  PBP-assisted localization, and stops for a different reason than a single edited quarter.

---

# 8. EVENT SELECTION & VIDEO WINDOW DESIGN

## 8.1 Inclusion rules (answers question G)

**Include:** `type == "shot"` — every field-goal attempt. ~132–140 per game (both teams, verified).

| Case | Decision | Reason |
|---|---|---|
| Made FG | include | core |
| Missed FG | include | core; contest rate is meaningless if only makes are sampled |
| **Blocked** (`made=="blocked"`) | **include** | real FGA; definitionally contested → free validation anchor (~10/game) |
| **Free throws** (`type=="freeThrow"`, 69/game) | **EXCLUDE** | uncontested by rule; "open vs contested" and "catch-and-shoot vs off-dribble" are undefined. Halves volume for zero information |
| Offensive fouls | excluded naturally | recorded as `turnover`, not `shot` |
| Team attribution missing (`teamId==0` or `parameters.team` ∉ {1,2}) | **EXCLUDE**, count as `dropped_no_team` | cannot attribute to a team; silently guessing corrupts team metrics |
| Missing/unparseable `userTime` | **EXCLUDE**, count as `dropped_no_time` | cannot localize |
| `quarter` with no sync anchor | **EXCLUDE**, count as `dropped_no_anchor` | §7.3 |
| Shots in a quarter confirmed `excluded` (discontinuity) | **EXCLUDE that quarter's shots**, count as `dropped_excluded_quarter`; rest of the game still processes | §7.7 (revised 2026-08-15 — was "exclude whole game," now quarter-level per CP1-D evidence) |

Every exclusion is **counted and reported** — they become the coverage denominator in §11.

**Volume:** ~136 shots/game → **~950 events across 7 games.**

## 8.2 One call per event, all metrics

One inference call returns all surviving metrics for that event. The video tokens dominate cost and
are paid once regardless of how many questions the prompt asks. Three separate calls would triple
cost and latency for no benefit. The repo's registry-driven schema already supports exactly this.

## 8.3 Window sizing — derived, not inherited

The repo default (8 s pre / 4 s post = 12 s) is **too tight**. Error budget:

```
t_release ≈ t_mapped − L        L = operator lag,  L ∈ [0, 6] s   (CP1-D)
                        ± ε      ε = calibration residual, ε ∈ [−5, +5] s
⇒  t_release ∈ [t_mapped − 11,  t_mapped + 5]

Need ≥ 8 s of lead-up before the release  (possession phase, catch vs dribble)
Need ≥ 3 s after the release              (outcome, contest confirmation)

window_start ≤ (t_mapped − 11) − 8 = t_mapped − 19
window_end   ≥ (t_mapped +  5) + 3 = t_mapped +  8
```

**CP1/CP2 window: `[t_mapped − 20 s, t_mapped + 8 s]` = 28 s.**
**CP3 target after CP1-D measures L and ε: `[t_mapped − 14 s, t_mapped + 6 s]` = 20 s.**

Cost impact is small — 28 s at LOW = 2,800 video tokens (~$0.001/event) — so buying robustness here
is cheap and correct. Tighten only on evidence.

Change `DEFAULT_PRE_ROLL_SECONDS = 20.0`, `DEFAULT_POST_ROLL_SECONDS = 8.0` in `video/events.py`.
Keep `MAX_WINDOW_SECONDS = 120` as the guard.

## 8.4 Disambiguation — which shot in the window?

A 28 s window may contain a previous possession or a replay. Solution: tell the model **which** shot,
using PBP metadata that identifies without labelling.

**Send:** shooter jersey number · shot type (`jump-shot`/`lay-up`/`dunk`) · `points` (2/3) ·
quarter + game clock · shooting team name · **approximate offset within the clip**
("the shot occurs about 20 seconds into this clip").

**Withhold — these leak the labels being measured:**

| Field | Leaks |
|---|---|
| `fastBreak` | directly answers `possession_type` |
| `made` / `outcome` | biases `shot_contest` (makes read as "open") |
| `assisted` | directly answers `shot_creation` |
| `coordX/coordY` | not needed; risks anchoring |

This is a hard rule and must be covered by a unit test asserting these substrings never appear in a
built prompt.

## 8.5 Replay contamination

A window may include a replay of the same shot from another angle, which can flip a contest
judgement. Mitigations: (a) the prompt states the shot occurs ~N s in and instructs the model to
classify the **live action**, not a replay; (b) `post_roll` is kept short (8 s → 6 s) since replays
follow the play; (c) CP2 reviews `*_evidence` strings for replay mentions and reports the rate.
If replays prove to be a systematic error source, shorten `post_roll` further — this is a cheap knob.

## 8.6 Boundary handling

- `window_start < 0` → clamp to 0 (already implemented in `window_around`).
- `window_end > video_duration_s` → clamp; if the clamped window is < 8 s, drop as
  `dropped_window_too_short`.
- Events near a quarter boundary may straddle a cut. Since anchors are per quarter, an event in the
  first ~20 s of a quarter may reach back into a cut region. Flag events whose window start precedes
  their quarter's anchor by more than 30 s as `window_crosses_boundary` and report the count.

## 8.7 Metric-specific window needs

| Metric | Needs before release | Needs after |
|---|---|---|
| `shot_contest` | ~1 s (defender at release) | ~1 s |
| `possession_type` | **~8 s** (possession start / defence retreating) | 0 |
| `shot_creation` | ~3 s (catch and dribbles) | 0 |

`possession_type` is the binding constraint. **One shared window serves all three** — confirming
§8.2's single-call design.

---

# 9. METRIC DEFINITIONS & CLASSIFICATION CONTRACT

The registry in `video/metrics.py` already encodes these; this section fixes the *operational*
meaning so a human annotator, Gemini, and validation code judge the same concept.

## 9.1 `shot_contest` — open vs contested

- **`contested`** — at the moment of release, a defender is within roughly one arm's length
  (~1 m) of the shooter, **or** a defender is actively closing out with a hand raised into the
  shooter's line of sight.
- **`open`** — no defender within ~1 m and no hand contesting the line of sight.
- **`uncertain`** — the release is off-camera, obscured, the angle does not show the nearest
  defender, or the clip does not contain the identified shot.

**Edge cases (must be stated identically to human and model):**
| Case | Rule |
|---|---|
| Defender close but flat-footed, hand down | `open` |
| Late closeout arriving after release | `open` (judge **at** release) |
| Shot blocked | `contested` (definitional) |
| Post-up with body contact | `contested` |
| Help defender arriving from the weak side | `contested` if within ~1 m at release |

**Note:** this is the **only genuinely video-only metric** — PBP offers no proxy (§27, P1).

## 9.2 `possession_type` — transition vs half-court

- **`transition`** — the shot comes early in the possession while the defence is still retreating
  or outnumbered (fast break / early offence before the defence is set).
- **`half_court`** — the defence is set in its half-court shape before the shot.
- **`uncertain`** — the possession start is not visible in the window.

**Edge cases:** secondary/drag screen immediately into a shot before the defence sets →
`transition`. After a made basket with a quick inbound but a set defence → `half_court`. Putback off
an offensive rebound → `half_court` unless the defence was still retreating.

**PBP proxy exists:** `fastBreak` boolean (verified, ~10–11 % of FGA). Used as free validation
(§12.4), **not** sent to the model.

## 9.3 `shot_creation` — catch-and-shoot vs off-dribble

- **`catch_and_shoot`** — the shooter receives a pass and rises without putting the ball on the
  floor (one settling hop still counts).
- **`off_dribble`** — at least one dribble immediately precedes the shot.
- **`uncertain`** — the catch or the dribbles before release are not visible.

**Edge cases:** dunks/lay-ups off a cut with no dribble → `catch_and_shoot`. Offensive-rebound
putback → `catch_and_shoot` (no dribble). One dribble to gather then rise → `off_dribble`.

**PBP partial proxy:** `assisted` (assists exist only on makes) — validates on made FGs (§12.4).

## 9.4 The `uncertain` contract

`uncertain` is a **valid, expected outcome**, not a failure. Operational rule, uniform across metrics:

> Answer `uncertain` when the specific visual evidence the definition requires is not observable in
> the clip. Do not infer from basketball priors, the score, or which teams are playing.

Confidence must reflect what was *seen*, not plausibility. The system instruction already enforces
this; keep it and add: *"An honest `uncertain` is more useful than a guess, because these labels are
aggregated into statistics."*

**Reporting rule:** `uncertain` is excluded from metric numerators and denominators but is **always
reported** as `uncertain_rate`. A metric whose `uncertain_rate > 0.35` is flagged `low_confidence`
and must not be presented as a clean rate.

## 9.5 Output contract

Per metric the model returns three fields (already generated by `build_classification_model`):
`<key>` (Literal incl. `uncertain`), `<key>_confidence` (0.0–1.0), `<key>_evidence` (one sentence).

Evidence strings are not decorative — CP2 uses them to diagnose *why* the model erred and to detect
replay contamination.

`PROMPT_VERSION` and `SCHEMA_VERSION` constants are stamped onto every result so CP2 labels are never
silently compared against CP4 output produced by a different prompt.

---

# 10. EVENT-LEVEL DATA / PROVENANCE CONTRACT

Extends the existing `ClassifiedEvent`. Every field earns its place; purpose stated.

```python
class ClassifiedEvent(BaseModel):
    # ---- identity / idempotency ----
    event_id: str                 # "<game_id>:<action_id>" — resume key
    game_id: str                  # internal
    request_fingerprint: str      # sha1(event_id|model|prompt_v|schema_v|win_start|win_end)
                                  #   → detects "same event, different config" on rerun

    # ---- basketball context (auditability, aggregation) ----
    team_id: str                  # internal; the aggregation grain
    team_side: str                # home|away
    quarter: int
    quarter_time: str             # "09:54" — lets a human find the play in the PBP
    shot_type: str; points: int   # stratification in CP2
    outcome: str                  # made|missed|blocked  (recorded, NOT prompted)
    fast_break: bool              # PBP proxy for validation (NOT prompted)
    assisted: bool                # PBP proxy for validation (NOT prompted)

    # ---- localization (the thing most likely to be wrong) ----
    user_time_s: float
    mapped_video_s: float
    window_start_s: float; window_end_s: float
    sync_quality: str             # ok|degraded|excluded — reflects THIS event's quarter (§7.7),
                                  # not a whole-game rollup; an excluded quarter's events are
                                  # never sent to the model at all (§7.3)
    sync_offset_s: float          # the quarter offset actually applied
    sync_anchor_action_id: int | None

    # ---- model answer ----
    outcomes: dict[str, MetricOutcome]   # label + confidence + evidence per metric

    # ---- provenance / reproducibility ----
    provider: str; model: str
    prompt_version: str; schema_version: str
    media_resolution: str | None; fps: float | None
    created_at: str

    # ---- telemetry / cost (feeds §14) ----
    latency_seconds: float | None
    usage: dict | None            # {prompt, video, output, thoughts, total} token counts
    finish_reason: str | None

    # ---- failure handling ----
    attempt: int                  # 1..3
    error: str | None
    error_kind: str | None        # transient|schema|safety|source|sync
    raw_text: str | None          # only retained when error is set (keeps files small)
```

**Why `usage` per event:** it is the only way the CP4 cost figure becomes evidence rather than an
estimate, and it is how the clipping regression (A16) would be caught if it silently changed.

**Storage:** append-only JSONL, one object per line,
`data/processed/video/events/<game_id>.jsonl`. Append-only is what makes resume trivial and makes a
crashed run lose at most one event.

---

# 11. AGGREGATION DESIGN

Pure deterministic Python/Pandas in `video/aggregate.py`. **No model involvement.**

## 11.1 Per metric

For a given `(game_id, team_id, metric)`, partition that team's classified shots:

```
n_open, n_contested, n_uncertain, n_failed, n_excluded
decided = n_open + n_contested
```

| Metric | `metric_name` | Numerator | Denominator |
|---|---|---|---|
| shot_contest | `contested_shot_rate` | `n_contested` | `n_open + n_contested` |
| possession_type | `transition_shot_rate` | `n_transition` | `n_transition + n_half_court` |
| shot_creation | `catch_and_shoot_rate` | `n_catch_and_shoot` | `n_catch_and_shoot + n_off_dribble` |

**Rules:**
- `uncertain` is **excluded from both** numerator and denominator, and reported separately.
- Failed/unclassified events are excluded from the denominator and reported as `events_failed`.
- If `decided == 0` → `metric_value = null`, `status = "no_data"`. Never emit 0.0.
- If `decided < 20` → `status = "insufficient_sample"` (value still stored, flagged).
- If `uncertain_rate > 0.35` → `status = "low_confidence"`.
- If any contributing event has `sync_quality == "degraded"` → `sync_flag = "degraded"`.

`min_decided = 20` is a deliberate MVP threshold: with ~68 FGA per team-game, a metric that cannot
decide 20 of them is not describing the team.

## 11.2 Output row (`video_team_metrics`)

CSV + JSON, one row per `(game_id, team_id, metric_name)` → **3 metrics × 14 team-games = up to 42 rows**.

| Column | Source |
|---|---|
| `game_id`, `team_id`, `team_name`, `opponent_team_id` | manifest |
| `metric_name`, `metric_value` | §11.1 |
| `event_count` | decided denominator |
| `events_total`, `events_classified`, `events_uncertain`, `events_failed`, `events_excluded` | runner counters |
| `coverage_rate` | `events_classified / events_total` |
| `uncertain_rate` | `n_uncertain / events_classified` |
| `quarters_usable`, `quarters_excluded` | **added 2026-08-15, mandatory, non-null.** e.g. `quarters_usable=[1,3,4]`, `quarters_excluded=[2]` for a game with one confirmed sync discontinuity (§7.7). A game/team-row missing this pair cannot be produced — an excluded quarter must never be silently absent from the record |
| `games_analyzed` | **computed** — `n_distinct(game_id)` for that team (never hard-coded) |
| `status`, `sync_flag` | §11.1 |
| `source`, `provider`, `model`, `prompt_version`, `schema_version` | provenance |
| `sample_scope` | literal `"single_game_video_snapshot"` |
| `created_at` | run timestamp |

## 11.3 Preserving the evidence hierarchy

`sample_scope = "single_game_video_snapshot"`, `games_analyzed`, and (added 2026-08-15)
`quarters_usable`/`quarters_excluded` are **mandatory, non-null columns on every row**. Together
they are the structural guarantee that a downstream consumer — including the future Video Analysis
Agent — cannot present a one-game observation as a season tendency, **and cannot present a
partial-quarter-coverage game as if it were a complete one**. `games_analyzed` is derived by
counting actual game records; `quarters_usable`/`quarters_excluded` are derived directly from each
game's `GameSync` state (§7.7), so neither can drift out of sync with reality.

Video metrics are written to a **separate table/file** from PBP analytics and are never joined into a
combined "team stats" table at this stage. Metric names are prefixed on export
(`video_contested_shot_rate`) so a naming collision with a PBP metric is impossible.

---

# 12. VALIDATION DESIGN

## 12.1 Sampling strategy (~20 events, CP2)

Sample from **one game** (the CP1 game) to keep calibration constant and isolate model quality from
sync quality.

Stratify deliberately — 20 events chosen randomly would mostly be routine mid-range jumpers:

| Stratum | Target n | Why |
|---|---|---|
| Quarter spread (Q1–Q4) | ≥4 per half | detects sync drift late in the game |
| Both teams | ≥8 each | detects team/jersey/camera-side bias |
| `jump-shot` | ~10 | dominant type (83/132) |
| `lay-up` / `dunk` | ~6 | rim shots — contest is visually different |
| 3-point (`points==3`) | ~6 | perimeter contest is the hardest call |
| `fastBreak==true` | ≥3 | otherwise transition is under-sampled (only ~11 %) |
| `made=="blocked"` | ≥2 | known-contested control |
| Deliberately awkward | ~4 | bad angle, late clock, crowded paint |

Selection is scripted and seeded (`--seed`) so the sample is reproducible.

## 12.2 Human ground-truth procedure

1. Generate the 20-row fixture with PBP context filled in and **all label columns blank**.
2. The human watches each window **in the video** and fills `human_*` **before seeing any model
   output**. The existing fixture design already separates `human_*` from `model_*` for exactly this.
3. Any event the human cannot judge → `uncertain` (same rule as the model — this is essential; a
   different standard makes agreement meaningless).
4. Then run the classifier with `--update-fixture` to populate `model_*`.
5. `--agreement` computes the metrics.

**Time:** ~20 events × ~2.5 min (locate, watch, judge 3 metrics, note) ≈ **50 min**. This is the
single largest human cost in CP2 and is budgeted as such.

## 12.3 Metrics computed (the repo already implements these)

- **Overall agreement** — over all comparable pairs.
- **Decisive agreement** — excluding pairs where *either* side said `uncertain`. Without this, a
  model answering `uncertain` everywhere is unreadable. Already implemented in `ground_truth.agreement()`.
- **Uncertain rate** — model and human separately.
- **Per-class error pattern** — the 2×2 confusion for each metric. With n≈20 this is indicative only;
  report counts, never percentages to a decimal.

## 12.4 Free PBP-derived validation (a bonus this design unlocks)

Because PBP carries `fastBreak`, `assisted` and `blocked`, we get a **second independent check across
all ~136 events of the game — not just the 20 labelled ones**, at zero labelling cost:

| Check | Comparison | Expectation |
|---|---|---|
| `possession_type` vs PBP `fastBreak` | model `transition` vs `fastBreak==true` | high agreement; PBP `fastBreak` is narrower than "transition", so expect model `transition` ⊇ `fastBreak` |
| `shot_creation` vs PBP `assisted` | on **made** FGs only | assisted ≈ `catch_and_shoot` (not exact — assisted drives exist) |
| `shot_contest` on `made=="blocked"` | model should say `contested` | near-100 %; a control that catches gross failure |

These are **not** substitutes for human ground truth (they measure different, narrower concepts), but
a large disagreement here is a strong early warning, and the blocked-shot control is essentially a
free correctness assertion. Report all three in the CP2 artifact.

## 12.5 Disagreement review and decision rules

Every disagreement is reviewed by reading the model's `*_evidence` string and re-watching the clip,
then classified:

| Cause | Meaning | Remedy |
|---|---|---|
| Wrong play analysed | localization/disambiguation failure | widen window / strengthen disambiguation — **not a metric problem** |
| Replay judged | contamination | shorten post-roll |
| Definition mismatch | model applied a different threshold | sharpen the definition, re-run |
| Genuinely ambiguous | human unsure too | should have been `uncertain` on both sides |
| Model simply wrong | real capability limit | counts against the metric |

**This classification is mandatory**, because "sync broke" and "the model can't see contests" produce
identical-looking disagreement numbers but have completely different remedies.

## 12.6 Decision thresholds (per management guidance)

| Decisive agreement | Decision |
|---|---|
| **≥ 90 %** | **KEEP** — good enough for MVP. **Stop optimizing.** |
| **80–89 %** | **KEEP and continue**, unless errors are *systematic* (§12.5) — then fix the definition once and re-run **once** |
| **65–79 %** | **MODIFY** — one definition revision + one re-run. If still <80 %, cut |
| **< 65 %** | **CUT** the metric (management-notified, not a silent decision) |
| `uncertain_rate > 0.5` | Metric unusable regardless of agreement → report |

**Explicit anti-goal:** do not spend time chasing 95 %. A systematic conceptual error on 3 events
matters more than 3 scattered mistakes.

**Stage outcome:** at least **two** metrics ≥80 % decisive agreement. A third is kept only if it
already passes — it costs nothing extra to run (§8.2).

## 12.7 CP4 bounded re-validation

Not a second full campaign: **10 events sampled from 3 *different* games** (2 games not used in CP2),
labelled the same way (~25 min). Purpose is to detect *game-specific* failure (a different arena,
camera position, or broadcast style), not to re-measure the model. If CP4 agreement drops >15 points
below CP2, stop and report.

---

# 13. FAILURE / RETRY / CACHE / RESUME DESIGN

## 13.1 Principles

Per-event durability, cheap idempotency, no silent data loss. Simple enough to build in ~1 hour.

## 13.2 Caching

| Layer | Key | Behaviour |
|---|---|---|
| Raw PBP | `segev_<gid>.json` | fetch once; `--refresh` to force. Removes the source from the risk chain |
| Classified events | `request_fingerprint` | never re-pay for an identical successful request |
| Calibration | manifest | hand-made; never auto-overwritten |

`request_fingerprint = sha1(event_id | model | prompt_version | schema_version | window_start | window_end)`.

Changing the prompt, schema, model, or window **changes the fingerprint**, so results from different
configurations can never be silently mixed — the exact failure mode that would corrupt a CP2→CP4
comparison.

## 13.3 Resume

On start, the runner reads the existing JSONL, builds `{fingerprint: event}` keeping the **last**
occurrence of each, and skips events whose fingerprint is present with `error is None`.
Events with errors are retried on the next run unless `--no-retry-failed`.
Append-only means a crash mid-write loses at most the final line; a malformed trailing line is
skipped with a warning rather than aborting the load.

## 13.4 Error taxonomy and handling

| `error_kind` | Trigger | Handling |
|---|---|---|
| `transient` | 429, 500, 503, timeout, connection reset | exponential backoff 2 s → 4 s → 8 s, **max 3 attempts**; then record and continue |
| `schema` | invalid label, malformed JSON, missing field | **one** re-ask, then record with `raw_text` for diagnosis |
| `safety` | `finish_reason` in {SAFETY, PROHIBITED_CONTENT, RECITATION} | **no retry** — record and continue |
| `source` | video unavailable, URI rejected, quota exhausted | **abort the game** — this is systemic, not per-event |
| `sync` | out-of-range / missing anchor | never sent to the API; recorded as excluded |

**Circuit breaker:** if 10 consecutive events fail, or the transient rate exceeds 30 % over 25
events, **stop the run** and report. Burning quota against a systemic failure is the expensive
mistake this prevents.

**Budget guard:** `--max-events` and `--max-estimated-tokens`; the runner refuses to start if the
projected token count exceeds the cap, and stops mid-run if actual usage exceeds it by 25 %.

## 13.5 Concurrency

Start **serial** in CP3. In CP4 use a small fixed thread pool (default **4**, `--concurrency`), with
retry/backoff per worker. No async framework, no queue system. If 429s appear, drop to 2 and report.

---

# 14. COST & RUNTIME MODEL

## 14.1 Formulas (use these; substitute measured values)

```
video_tokens/event   = window_seconds × tokens_per_second(media_resolution)
                       tokens_per_second: 100 (LOW) | 300 (default)      [VERIFIED, docs]
prompt_tokens/event  = video_tokens + text_tokens(~700)
output_tokens/event  ≈ 250            (3 metrics × label+confidence+evidence, thinking OFF)
events/game          ≈ 136            [VERIFIED: 132 (g58), 140 (g136)]
total_events         = events/game × games
cost = (Σ prompt_tokens × price_in) + (Σ output_tokens × price_out)
```

`price_in` / `price_out` are **UNVERIFIED** — secondary sources contradicted each other and one
claimed an implausible "$0.15 per second of video". **Do not use a remembered price.** CP1 records
measured token counts; management applies the then-current published price.

## 14.2 Scenario A — clipping works (the design as planned)

20 s window, LOW resolution, 950 events:

| Quantity | Value |
|---|---|
| Video tokens/event | 20 × 100 = **2,000** |
| Prompt tokens/event | ≈ 2,700 |
| Output tokens/event | ≈ 250 |
| **Total input** | ≈ **2.57 M** |
| **Total output** | ≈ **0.24 M** |
| Indicative cost @ $0.30/M in, $2.50/M out | **≈ $1.35** (**±** price uncertainty) |
| With 20 % retry/overhead | **< $2** |

**Conclusion: cost is negligible if clipping works.** Even a 10× pricing error keeps it under $20.

## 14.3 Scenario B — clipping ignored (offsets not honored)

Each call ingests the full ~2 h broadcast:

| Quantity | Value |
|---|---|
| Video tokens/event @ LOW | 7,200 × 100 = **720,000** |
| Total input, 950 events | **684 M tokens** |
| Indicative cost @ $0.30/M | **≈ $205** |
| At default resolution | 2.16 M tokens/call — **exceeds a 1 M context window** → hard failure |

**Conclusion: Scenario B is not viable** — ~150× the cost, plus latency and context-limit failures.
This is why CP1-A is the first gate and why §21 branch T3 escalates rather than adapts.

## 14.4 Free-tier quota (A19/A20)

Free tier: **8 h of YouTube video per day** [VERIFIED, docs].
- If a clipped call counts **20 s** → 950 events ≈ 5.3 h → fits in one day.
- If it counts the **full 2 h video** → **4 calls/day** → the stage is impossible on free tier.

**CP1-B must measure this.** If the pessimistic reading holds, a billing-enabled key is required —
a management decision (§25, D2).

## 14.5 Runtime

| | Estimate |
|---|---|
| Latency/event (LOW, thinking off) | 4–12 s — **CP1 measures** |
| Serial, 950 events @ 8 s | ≈ 2.1 h |
| Concurrency 4 | **≈ 35 min unattended** |
| PBP fetch, all games | < 1 min |
| Aggregation | seconds |

Unattended batch time is **not** counted against the 12–15 h active budget, but CP4 must be started
with enough wall-clock margin to absorb a re-run.

## 14.6 Telemetry CP1–CP3 must capture

So the CP4 estimate is evidence, not a guess:
1. VIDEO modality tokens per call vs window length (proves clipping; gives tokens/second).
2. Total prompt/output/thoughts tokens per event.
3. p50 / p95 latency.
4. Error and retry rates by `error_kind`.
5. Actual events per game after exclusions.
6. Observed quota consumption across a session (settles A20).

`run_summary.json` aggregates all six per run.

---

# 15. CP1 EXECUTION PLAN — SOURCE & API FEASIBILITY

**Objective:** prove the external dependency chain (§3.1) on one real game. Prove nothing else.

**Time budget: 3.0 h active. Hard stop 4.0 h.**

**Prerequisites:** `GEMINI_API_KEY` in `.env`; `.venv` active; note whether the key is free or billing-enabled.

## 15.1 Sequence

**Step 0 — GATE 0: video source (30 min, do this FIRST, costs nothing).**
Per F3 this is unproven and can invalidate the stage.
1. Search YouTube for a **full-game** Premier League (Winner League) 2025-26 broadcast.
   Search Hebrew (`ליגת ווינר`, `משחק מלא`) and English; check the league's channel and club channels.
2. Verify a candidate is a **complete** game: duration ≥ ~90 min, shows tip-off, public (not unlisted).
3. Record URL + duration.
4. Cross-check `basket.co.il/pbp/json/games_all.json` → `pbp_link` / `liveChannel` for finished
   games — these may point directly at the broadcast.
5. **If no public YouTube full game is found within 30 minutes → STOP.** Go to §21 branch T2 and
   report. Do not substitute a highlight reel; do not start downloading from winnerleague.tv.

**Step 1 — PBP extraction, all candidate games (20 min).**
Build `pbp/segev.py` + `scripts/fetch_pbp.py`. Fetch and cache the chosen game **and the rest of the
intended matchday** now (removes PBP from the risk chain permanently).
Evidence: raw JSON on disk; printed summary — action count, shot count, quarters incl. OT,
team ids/names, `gameFinished`.

**Step 2 — canonical events (25 min).**
Build `pbp/canonical.py`. Produce `PbpEvent[]` for the game.
Evidence: shot count ≈130–145; both teams attributed; exclusion counters printed; OT handled;
inversion count reported.

**Step 3 — CP1-C: pin the model (20 min).**
`gemini_video_event.py --list-models`; shortlist stable Flash-class ≥2.5 with video input.
Evidence: full model list saved; chosen id recorded.

**Step 4 — CP1-A: THE CLIPPING TEST (20 min). ⚠ THE DECISIVE GATE.**
One call, 20 s window on the full-game video, `media_resolution=LOW`. Read
`usage_metadata.prompt_tokens_details` → VIDEO token count. Repeat once at 40 s.
Evidence: both raw `usage_metadata` blocks saved verbatim.
Verdict per §6.4 table. **If FAIL → STOP, report, §21 branch T3.**

**Step 5 — CP1-B: quota accounting (10 min).**
Record quota/usage before and after a few clipped calls; determine whether consumption tracks clip
seconds or full video length. Evidence: observed deltas + tier (free/paid).

**Step 6 — CP1-D: sync calibration + lag measurement (55 min).**
Build `video/sync.py` + `scripts/calibrate_game.py`. Calibrate the game (§7.5).
Then measure operator lag: for **8 shots** spread across quarters, record true release time in the
video vs mapped `userTime`. Report mean and σ. Set `operator_lag_estimate_s`; adjust window per §8.3.
Evidence: manifest `sync` block with anchors, residuals, quality; lag table.

**Step 7 — CP1-E: one real end-to-end classification (20 min).**
Classify 3 events through the real path (PBP → sync → window → Gemini → validated schema).
Evidence: 3 `ClassifiedEvent` JSON objects with usage, latency, labels, evidence strings.

**Step 8 — record findings (20 min).**
Rewrite `docs/VIDEO_SPIKE_NOTES.md` (it currently says PBP extraction is unresolved — now false).
Update `WORKLOG.md`. Write `artifacts/cp1/cp1_report.md`.

## 15.2 Files affected

New: `src/basketball_scout/pbp/{__init__,segev,canonical}.py`, `src/basketball_scout/video/{sync,manifest}.py`,
`src/basketball_scout/ids.py`, `scripts/fetch_pbp.py`, `scripts/calibrate_game.py`,
`data/manifest/matchday.json`.
Modified: `video/events.py` (window defaults), `video/gemini_client.py` (usage/thinking),
`video/schema.py` (provenance), `config.py`, `.env.example`, `.gitignore` (`!data/manifest/`),
`docs/VIDEO_SPIKE_NOTES.md`, `WORKLOG.md`.

## 15.3 Tests added (CP1)

`test_segev_parsing.py` (envelope, missing `actions`, "game not found", encoding),
`test_canonical.py` (shot extraction, exclusions, `assisted` linkage, OT, inversions),
`test_sync.py` (userTime parsing, offset mapping, per-quarter selection, OT, out-of-range, drift).
Uses a **trimmed real cached response** as fixture (~30 actions, committed to `data/validation/`).

## 15.4 PASS / PARTIAL / FAIL (revised 2026-08-15 per CP1-D — now quarter-granular, not all-or-nothing)

Calibration is assessed **per quarter**, not as a single all-quarters-or-nothing gate — CP1-D
showed this granularity is what the evidence actually supports (3 of 4 quarters on game 136
calibrated cleanly; the 4th was a distinct, correctly-diagnosed, correctly-excluded finding, not a
reason to fail the whole checkpoint).

| Verdict | Criteria |
|---|---|
| **PASS** | Public full-game YouTube video confirmed; PBP cached + canonicalized; model pinned; **VIDEO tokens scale with window (clipping honored)**; quota accounting understood or explicitly logged as a soft gap; **≥50% of quarters `ok`, remainder `degraded` or explicitly `excluded` with a confirmed-cut diagnosis (§7.5.1) — not merely unresolved**; 3 events classified end-to-end with valid schema |
| **PARTIAL** | Everything above passes **except**: one or more quarters `degraded` without full slope-fit resolution, **or** a confirmed-excluded quarter whose exclusion has not yet been reflected in the controlling plan/aggregation design, **or** quota accounting unresolved (proceed on a paid key, flag) |
| **FAIL** | No public full-game YouTube video **or** clipping not honored **or** no model accepts YouTube video **or** the game-level slope fit itself fails to bring **any** quarter to `ok`/`degraded` (i.e. the whole video is unusable, not just one quarter) |

CP1's actual result against this revised table: Gate 0/model/clipping/E2E all PASS outright;
calibration itself (3/4 quarters ok, 1/4 confirmed-excluded) would satisfy the PASS bar above, but
the exclusion had not yet been formalized into this plan at the time CP1 concluded — which is
exactly the PARTIAL condition. This document's revision closes that gap for future checkpoints.

## 15.5 Stop conditions

- Gate 0 not met in 30 min → stop (§21 T2).
- CP1-A FAIL → stop immediately (§21 T3). Do **not** proceed to CP2.
- 4.0 h reached → stop and report regardless of progress.
- Any FAIL → **do not start the CP2 labelling campaign.** Labelling against a broken pipeline wastes
  the most expensive human hour in the stage.

## 15.6 Ordinary fallbacks (no escalation needed)

Segev 403 → add `Referer`/UA (proven). Encoding garbled → sniff (existing pattern). TLS error →
`enable_system_trust_store()`. Model rejects `thinking_config` → omit it, note it. `.parsed` empty →
text fallback already implemented.

## 15.7 Explicitly OUT of scope for CP1

The 20-event labelling campaign · aggregation · the runner/batch · more than one game classified ·
prompt optimization · any of the 7-game work · Supabase/agents/web/PDF.

---

# 16. CP2 EXECUTION PLAN — METRIC FEASIBILITY

**Objective:** decide keep / modify / replace / cut for each of the three provisional metrics.

**Time budget: 3.5 h active. Hard stop 4.5 h.**

**Prerequisite: CP1 PASS.** (PARTIAL is acceptable only if the residual is understood and the window widened.)

## 16.1 Sequence

1. **(20 min)** Build the stratified sampler (§12.1), seeded. Emit 20 rows into the existing fixture
   with PBP context and blank labels.
2. **(50 min)** **Human labelling first**, before any model output is visible.
3. **(20 min)** Run the classifier over the 20 events; `--update-fixture`.
4. **(10 min)** `--agreement` → overall, decisive, uncertain rates, per-class counts.
5. **(20 min)** **Free PBP-proxy validation** (§12.4) across **all** ~136 events of the game — one
   batch run, ~$0.15, no human labelling. Gives `fastBreak`/`assisted`/`blocked` agreement.
6. **(45 min)** Disagreement review — classify every disagreement by cause (§12.5). This is the step
   that produces the actual decision, not the raw percentage.
7. **(25 min)** If exactly one metric shows a *systematic definition* error: revise the definition
   **once**, re-run those events, re-measure. **One revision only.**
8. **(20 min)** Write `artifacts/cp2/cp2_report.md` + update `WORKLOG.md`.

## 16.2 Files affected

New: `scripts/sample_validation_events.py`, `artifacts/cp2/cp2_report.md`.
Modified: `data/validation/video_events_ground_truth.csv` (**real labels — becomes a tracked asset**),
`video/metrics.py` (only if step 7 fires), `video/prompts.py` (`PROMPT_VERSION` bump if revised),
`WORKLOG.md`.

## 16.3 Evidence required

Completed fixture (20 rows, human + model + match) · agreement table per metric · confusion counts ·
uncertain rates · PBP-proxy agreement table · disagreement review table with causes · a stated
keep/modify/cut decision **per metric with justification**.

## 16.4 PASS / PARTIAL / FAIL

| Verdict | Criteria |
|---|---|
| **PASS** | ≥2 metrics at ≥80 % decisive agreement with non-systematic errors; uncertain rate <35 % for those metrics |
| **PARTIAL** | Exactly 2 metrics pass but one has uncertain rate 35–50 %, or one passes only after the single permitted revision |
| **FAIL** | <2 metrics reach 80 %, **or** disagreements are dominated by "wrong play analysed" (a *sync* failure masquerading as a metric failure — fix sync, re-run, do not cut metrics) |

## 16.5 Stop conditions

- Two metrics at ≥90 % → **stop optimizing immediately**, proceed to CP3.
- <2 metrics viable → stop, report to management (§21 T8/T9). Continuation is **not** the
  implementer's decision.
- 4.5 h reached → stop and report.

## 16.6 Out of scope

Prompt A/B testing beyond the single permitted revision · expanding the sample beyond 20 (+ the free
full-game proxy run) · new metrics not in the registry · any 7-game work · chasing 95 %.

---

# 17. CP3 EXECUTION PLAN — ONE-GAME END-TO-END

**Objective:** produce real `video_team_metrics` for **both teams** of one real game through the
complete automated pipeline. This is the checkpoint that proves the architecture.

**Time budget: 5.0 h active. Hard stop 6.0 h.** *(Largest allocation — this is where the real
engineering is.)*

## 17.1 Sequence

1. **(60 min) `video/runner.py`** — load manifest → PBP → canonical → sync → windows → classify →
   append JSONL. Includes fingerprint resume, retry/backoff, error taxonomy, circuit breaker,
   budget guard (§13).
2. **(30 min) Observability** (§18) — per-event progress line, rolling counters, `run_summary.json`.
3. **(50 min) `video/aggregate.py`** — §11 exactly: denominators, uncertain exclusion, status flags,
   `games_analyzed` computed from records, provenance columns.
4. **(20 min) `scripts/run_video_game.py`** — CLI: `--game`, `--limit`, `--dry-run`, `--resume`,
   `--concurrency`, `--max-events`.
5. **(30 min) Dry run** — full pipeline with `--dry-run` (no API calls). Verify window count,
   exclusion counters, no sync gaps, projected token/cost estimate.
6. **(35 min) Real run** — all ~136 events, serial or concurrency 2. Watch the first 10 events, then
   let it run (~15–25 min wall clock, partly unattended).
7. **(45 min) Sanity checks** (§17.3).
8. **(40 min) Tests** — `test_aggregate.py`, `test_runner_resume.py` (§20).
9. **(30 min) Report** — `artifacts/cp3/cp3_report.md`, `WORKLOG.md`, `video_team_metrics.csv`.

## 17.2 Files affected

New: `video/runner.py`, `video/aggregate.py`, `scripts/run_video_game.py`,
`tests/test_aggregate.py`, `tests/test_runner_resume.py`.
Modified: `video/schema.py`, `video/prompts.py`, `config.py`, `WORKLOG.md`.
Produced: `data/processed/video/events/<game_id>.jsonl`, `data/processed/video/video_team_metrics.csv`,
`artifacts/runs/<ts>/run_summary.json`.

## 17.3 Sanity checks (must all pass)

| Check | Expectation |
|---|---|
| Event count | 130–145 shots; matches canonical count minus reported exclusions |
| Team split | both teams ~60–75 events; neither near zero |
| Coverage | `events_classified / events_total` ≥ 0.90 |
| Uncertain | < 35 % on surviving metrics |
| **`fastBreak` cross-check** | model `transition` rate ≥ PBP `fastBreak` rate (~11 %) and ≤ ~35 % |
| **Blocked control** | blocked shots classified `contested` ≥ 90 % |
| Metric plausibility | `contested_shot_rate` roughly 0.35–0.75; a value near 0 or 1 signals a broken prompt or wrong window |
| Sync spot-check | 3 random events re-watched — correct play in window |
| `games_analyzed` | equals 1, **computed** not hard-coded |
| Resume | kill mid-run, restart, verify zero duplicate API calls and no duplicate rows |

## 17.4 PASS / PARTIAL / FAIL

| Verdict | Criteria |
|---|---|
| **PASS** | Both teams get ≥2 metrics with `status="ok"`; coverage ≥90 %; all sanity checks pass; resume verified; output schema matches §11.2 |
| **PARTIAL** | Pipeline completes but one metric is `insufficient_sample`/`low_confidence`, or coverage 80–90 % |
| **FAIL** | Coverage <80 %, or a sanity check fails systematically (e.g. blocked control <70 %), or resume double-charges |

## 17.5 Stop conditions

- Blocked control <70 % → sync or disambiguation is broken. **Stop, diagnose, do not scale.**
- Cost/event exceeds the CP1 measurement by >3× → stop, investigate (possible clipping regression).
- 6.0 h reached → stop and report.

## 17.6 Out of scope

Any second game · concurrency tuning beyond a simple pool · web/DB/agent integration · re-opening
metric definitions (CP2 settled them) · performance optimization.

---

# 18. OBSERVABILITY

Minimum useful for a long batch. No monitoring stack.

**Per-event line (stdout + log file):**
```
[game 3/7 IBPL-2025-26-G070] [event 45/136] Q2 07:13 → v=0:41:22 (±5s, ok)
    contest=contested(0.81) phase=half_court(0.90) creation=uncertain(0.20)
    4.2s  2,714 tok
```

**Rolling counters every 25 events:**
```
  ok=42 uncertain=8 failed=1 retried=3 | elapsed 3m21s | eta 6m50s | tokens 118,split
```

**Per-game summary:** totals, exclusions by reason, coverage, uncertain rate, error breakdown by
`error_kind`, tokens, wall time, model + prompt/schema versions.

**`artifacts/runs/<ts>/run_summary.json`** — machine-readable version of the above, the input to §14.6.

**Log file** `artifacts/runs/<ts>/run.log` — same lines, UTF-8 (Hebrew team names; the existing
`reconfigure(encoding="utf-8")` pattern in the spikes must be reused).

**Diagnosability rule:** every failed event's record retains `raw_text` and `finish_reason`, so a
failure can be diagnosed without re-running the API call.

---

# 19. FINAL VIDEO-LAYER OUTPUT CONTRACT

What exists when CP4 completes, and what downstream layers may depend on.

## 19.1 Artifacts

| Path | Content | Tracked | Stability |
|---|---|---|---|
| `data/manifest/matchday.json` | 7 games: identity, team ids, video URLs, calibration | **yes** | **stable contract** |
| `data/raw/pbp/segev_<gid>.json` | raw provider PBP, unmodified | no | reproducible |
| `data/processed/video/events/<game_id>.jsonl` | ~950 `ClassifiedEvent` records | no | **stable contract** |
| `data/processed/video/video_team_metrics.csv` / `.json` | **the deliverable** — ≤42 rows | no | **stable contract** |
| `data/validation/video_events_ground_truth.csv` | 20 CP2 + 10 CP4 labelled events | **yes** | evidence |
| `artifacts/runs/<ts>/run_summary.json` | telemetry, cost, errors | no | evidence |
| `artifacts/cp{1,2,3,4}/cp*_report.md` | checkpoint evidence | **yes** | audit trail |

## 19.2 What downstream layers may rely on

**The DB layer** maps `video_team_metrics` rows 1:1 into a `video_team_metrics` table. All columns in
§11.2 are present and typed; `metric_value` is nullable (`status="no_data"`).

**The website** reads `video_team_metrics` and **must** render `games_analyzed` and `sample_scope`
alongside any value. Video metrics are displayed in a **separate section** from PBP analytics.

**The Video Analysis Agent** receives only rows with `status="ok"`, plus `event_count`,
`uncertain_rate`, `games_analyzed`, `sample_scope`. Because `sample_scope` is a non-null column on
every row, the agent physically cannot receive a video metric without its sample context — this is
the structural guarantee that a one-game snapshot is never described as a season trend.

**Nobody downstream needs to know** what Gemini, YouTube, SegevSport, `userTime` or a video window
is. That is the point of the contract.

## 19.3 What is explicitly NOT delivered

Player-level video metrics · per-possession data · video clips or thumbnails · season-wide video
trends · a live/streaming path · any database table · any API endpoint.

---

# 20. TEST STRATEGY

High-value only. The repo already has 104 offline tests; this stage adds **~20**, not a second suite.

| Test file | CP | What it protects | Why it earns its place |
|---|---|---|---|
| `test_sync.py` | CP1 | userTime parsing, per-quarter offset, OT, out-of-range, drift detection, slope fit | **Highest-value tests in the stage.** A sync bug produces confidently-wrong labels that look plausible — the one failure mode no human review reliably catches |
| `test_canonical.py` | CP1 | shot extraction, exclusion rules, `assisted` linkage, missing `actions`, "game not found", non-monotonic ordering | Parsing assumptions against a real trimmed fixture |
| `test_segev_parsing.py` | CP1 | JSON-RPC envelope, HTTP-200-with-error, encoding | The error envelope returns 200 — a naive client would treat failure as success |
| `test_aggregate.py` | CP3 | denominators, uncertain exclusion, `no_data` ≠ 0.0, `insufficient_sample`, **`games_analyzed` computed** | Silent aggregation errors are invisible in the output |
| `test_runner_resume.py` | CP3 | fingerprint stability, skip-completed, fingerprint changes with prompt/model/window, no duplicate rows | Prevents double-charging and mixing configurations |
| `test_prompt_leakage.py` | CP2 | built prompt never contains `made`/`fastBreak`/`assisted` values | Guards the validity of every measurement in §12 |

**Reused patterns:** the existing `no_network` socket-patching fixture and `isolated_env` conftest
fixture — **all tests run with no credentials and no network**. Any test needing a live call is
marked `@pytest.mark.network` and excluded from normal runs (marker already registered in
`pyproject.toml`).

**Deliberately NOT tested:** prompt wording quality, model accuracy (that's CP2's human validation),
YouTube availability, exact token counts, CLI arg permutations.

---

# 21. FALLBACK / DECISION TREE

| # | Branch | Trigger (evidence) | Cheapest next action | Preserved | Approval? |
|---|---|---|---|---|---|
| **T1** | Segev extraction fails | Non-200/refusal from `getActions` | Add `Referer`/UA (proven); else use already-cached raw JSON. **30 min timebox** | Everything — PBP cached at CP1 | No |
| **T2** | **No public full-game YouTube video** | Gate 0 search (30 min) finds none | **STOP.** Report options: (a) obtain rights/permission for a usable source; (b) request a course-provided game file → Files API; (c) reduce video scope; (d) cut video layer | PBP layer 100 % intact; it is the primary evidence layer and carries the report alone | **YES — D1** |
| **T3** | **Offsets not honored** (A16) | CP1-A: VIDEO tokens ≈ full video, or API error | **STOP.** Report §14.3 economics (~150× cost, context overflow). Options: (a) Files API with clipped uploads — **ToS-dependent, operationally awkward, +4–6 h**; (b) cut to 1–2 games; (c) cut video layer | Sync, PBP, schema, aggregation all provider-agnostic and fully reusable | **YES — D1** |
| **T4** | Free-tier quota blocks the run | CP1-B: quota tracks full video length | Request billing-enabled key (cost is ~$2, §14.2) | Everything | **YES — D2** |
| **T5** | Chosen model unavailable/inadequate | `--list-models` or CP1-A failure | Next model in the §6.3 shortlist. If only a Pro-class model works, report cost delta | Everything (model is one env var) | Only if cost materially rises |
| **T6** | Sync unreliable for **one quarter** | Bisection (§7.5.1) confirms a genuine discontinuity | **Revised 2026-08-15 (CP1-D):** MVP default is to **exclude that quarter only** (§7.7) — drop its events, process the other quarters normally, record it in `excluded_quarters`/`quarters_excluded` (§7.4/§11). Only drop the **whole game** if bisection shows the discontinuity is not confined to one quarter (e.g. it recurs, or the game-level slope fit itself never stabilizes) | Other quarters of that game, and all other games, unaffected | No — quarter exclusion is now an ordinary, expected outcome, not an escalation |
| **T7** | Sync unreliable for **most** games | ≥3 games fail calibration | **STOP.** Likely uploads are edited. Report; propose a different matchday | All code | **YES** |
| **T8** | One metric performs badly | CP2 <65 % decisive, or systematic | Cut that metric. Two remaining satisfies the stage target. **Zero cost** — it's one registry entry | Pipeline unchanged | Notify, not approval |
| **T9** | **Two metrics perform badly** | CP2 leaves <2 viable | **STOP.** Report per-metric evidence + error causes. Note `shot_contest` is the only video-only metric (§27 P1) | Pipeline; PBP proxies still yield transition/assisted deterministically | **YES — D3** |
| **T10** | Cost/latency unexpectedly high | CP1/CP3 telemetry >3× estimate | Reduce window to 14 s; confirm LOW resolution + thinking off; if still high, reduce to 4 games (still 8 teams) | Everything | Approval if game count drops |
| **T11** | Event errors too frequent | >20 % failures or circuit breaker trips | Inspect `error_kind`. `transient` → lower concurrency, re-run (resume is free). `schema` → one prompt fix. `safety` → record and accept | Completed events never re-paid | No |
| **T12** | Behind schedule at the 16 h stop | Budget exhausted | Ship what exists: if CP3 passed, deliver 2 team-game records + honest coverage note | All artifacts | **YES** |

**Cross-cutting rule:** every "STOP" branch produces evidence and a recommendation. **None of them is
a decision the implementer makes.** Branches T2, T3, T7, T9, T12 halt the stage; the rest are ordinary
engineering recovery.

---

# 22. SCOPE GUARDRAILS

**Do not build during this stage** — each is a plausible-sounding trap:

| Temptation | Why it's a trap |
|---|---|
| Player-level video metrics | PBP has `playerId` on every action, making this *look* trivial. It is explicitly out of MVP scope and triples validation |
| Lineup / on-off analytics | 214 substitution actions per game make it tempting. Not in scope |
| Scoreboard OCR for auto-sync | 4–8 h to replace ~70 min of manual work (§7.2 #5) |
| YOLO / OpenCV player tracking | Not needed for any surviving metric. "Course box" thinking |
| Custom model training / fine-tuning | Absurd for a one-week MVP |
| Full-season video (91 games) | 13× cost and runtime; MVP is one matchday |
| Automatic highlight generation | Fun, unrelated |
| Downloading full broadcasts as the *primary* path | Explicitly excluded; fallback only, ToS-dependent |
| Supabase tables in this stage | Stage ends at durable local artifacts (§19) |
| CrewAI agents / FastAPI / website / PDF | Later stages; §19 is the contract |
| Prompt optimization past the CP2 threshold | §12.6 — 90 % is enough, stop |
| Chasing 95 % agreement | Explicit anti-goal |
| Shot charts from `coordX/coordY` | Tempting (coords verified present) — belongs to the PBP stage |
| Building the 91-game PBP pipeline | CP1 fetches only the games the video stage needs |
| Retry/queue/orchestration frameworks | A thread pool and a JSONL file suffice |

---

# 23. IMPLEMENTATION SEQUENCE / DEPENDENCY MAP

```
CP1 ──┬─ [0] GATE 0: YouTube full game ......... BLOCKS EVERYTHING          ← do first, free
      ├─ [1] Segev fetch + cache ............... independent  ┐ can run in
      ├─ [2] canonical events .................. needs [1]    ┘ parallel with [3][4]
      ├─ [3] model pinning ..................... independent
      ├─ [4] ★ CLIPPING TOKEN TEST ............ needs [0],[3]  ← CRITICAL PATH
      ├─ [5] quota accounting ................. needs [4]
      ├─ [6] sync calibration + lag ........... needs [0],[2]
      └─ [7] 3 events end-to-end .............. needs [2],[4],[6]
                     │
CP2 ──┬─ [8] stratified sampler ............... needs [2]
      ├─ [9] HUMAN LABELLING (50 min) ......... needs [8],[6]   ← human critical path
      ├─ [10] model run + agreement ........... needs [9]
      ├─ [11] free PBP-proxy validation ....... needs [2]; parallel with [9]
      └─ [12] disagreement review + decision .. needs [10],[11]
                     │
CP3 ──┬─ [13] runner (resume/retry) ........... needs [7]
      ├─ [14] observability ................... needs [13]
      ├─ [15] aggregation ..................... needs [12] (metric set); parallel with [13]
      ├─ [16] dry run ......................... needs [13],[15]
      ├─ [17] real one-game run ............... needs [16]
      └─ [18] sanity checks + tests ........... needs [17]
                     │
CP4 ──┬─ [19] manifest × 7 + video URLs ....... needs [0]; ★ START EARLY, parallel with CP2/CP3
      ├─ [20] calibrate 6 more games .......... needs [19],[6]; ★ parallel with CP3
      ├─ [21] batch run ....................... needs [18],[20]
      ├─ [22] aggregate 14 team-games ......... needs [21]
      └─ [23] bounded re-validation + report .. needs [22]
```

**Critical path:** `[0] → [4] → [6] → [7] → [13] → [17] → [21] → [22]`.

**Genuine parallelism worth exploiting:**
- **[19]/[20] (finding and calibrating the other 6 games) can be done during CP2/CP3.** This is the
  most valuable parallelization: it is ~1 h of low-concentration manual work that would otherwise sit
  on the critical path in CP4, and it can be done while a batch runs.
- [11] (free PBP-proxy validation) runs while the human labels in [9].
- [15] (aggregation) can be written while [13] (runner) is being tested.

**Hard serializations:** [4] gates all spending. [9] must precede [10] (anchoring bias). [12] must
precede [15] (aggregation needs the final metric set). [18] must precede [21] (never scale an
unverified pipeline).

---

# 24. ACTIVE-TIME BUDGET

Target **12–15 h**, hard stop **16 h**.

| CP | Work | Active | Hard stop | Unattended |
|---|---|---|---|---|
| **CP1** | Gate 0, PBP, canonical, model, **clipping test**, quota, sync+lag, 3 events, report | **3.0 h** | 4.0 h | ~0 |
| **CP2** | Sampler, **50 min human labelling**, model run, PBP proxies, disagreement review, 1 revision, report | **3.5 h** | 4.5 h | ~10 min |
| **CP3** | Runner, observability, aggregation, CLI, dry run, real run, sanity checks, tests, report | **5.0 h** | 6.0 h | ~25 min |
| **CP4** | 6 manifests + calibration (~1 h, parallelizable), batch, aggregate, re-validation, report | **2.5 h** | 3.0 h | ~40 min |
| | **Subtotal** | **14.0 h** | | ~1.3 h |
| | Contingency (one T-branch recovery) | **1.0 h** | | |
| | **Total** | **15.0 h** | **16.0 h** | ~1.3 h |

**Where the time deliberately goes:** CP3 (5.0 h) is the largest because it is where the real
engineering lives — runner, resume, aggregation. CP1 (3.0 h) is second because it carries all the
external uncertainty. **CP4 is only 2.5 h and that is the point**: if CP4 needs more, CP3 was not a
real architecture.

**Not padded:** CP4's batch execution (it's a loop over CP3), aggregation of 7 games vs 1 (same code).

**Critical-path human bottleneck:** the 50-minute CP2 labelling session. It cannot be parallelized,
compressed, or delegated to the model without destroying the measurement. Protect it — and do not
start it until CP1 is green.

---

# 25. MANAGEMENT DECISIONS REQUIRED

Only genuine approval gates. Ordinary engineering choices are excluded.

**D1 — Video source / clipping viability (triggered by T2 or T3).**
If no public full-game YouTube video exists, or offsets are not honored, the approach as designed is
not viable. Options: (a) obtain a rights-cleared game file → Files API (+4–6 h, ToS/rights
dependent); (b) reduce video scope to 1–2 games; (c) cut the video layer.
*Implementer provides evidence + recommendation. Implementer does not choose.*
**Decide within CP1.**

**D2 — Billing-enabled Gemini key (triggered by T4, or pre-emptively).**
Free tier caps YouTube at 8 h/day; if that is charged at full video length the stage cannot run.
Estimated true cost is **under $5** for the whole matchday if clipping works.
**Recommendation: approve a billing-enabled key before CP1** — it removes an entire failure branch
for trivial cost. **Decide before CP1.**

**D3 — Fewer than two viable metrics (triggered by T9).**
CP2 may leave <2 metrics at ≥80 %. Continuation after a gate failure is explicitly not the
implementer's decision. **Decide at CP2.**

**D4 — Metric-set reallocation (see §27 P1).**
Evidence shows `possession_type` is largely derivable from PBP `fastBreak` deterministically, for
all 91 games rather than 7. Reallocating that video budget to a second genuinely video-only metric
would increase the layer's marginal value. **This is a product-scope decision and is NOT in the
primary plan.** **Decide only if management wishes; default is to proceed as specified.**

**D5 — Reduced game count (triggered by T10/T12).**
Dropping from 7 games to 4 halves runtime but leaves 6 of 14 teams without video. **Decide at CP4.**

---

# 26. OPEN QUESTIONS

Only genuinely unresolvable before execution.

| # | Question | Why unresolvable now | Resolution |
|---|---|---|---|
| Q1 | Are `video_metadata` offsets honored for a YouTube URI on this key/tier/model? | Requires a live billed call; official docs omit clipping and a staff-confirmed bug report exists | **CP1-A**, §6.4 token test. Binary, one call |
| Q2 | Does a public full-game YouTube VOD exist for a Premier League matchday? | Search evidence is suggestive (official VOD is on winnerleague.tv), not conclusive | **CP1 step 0**, 30-min timeboxed search |
| Q3 | Does a clipped call consume clip-seconds or full-video-length against the 8 h/day free quota? | Undocumented | **CP1-B**, observe quota deltas |
| Q4 | Which exact model id to pin? | Model availability is key/tier-specific and changes | **CP1-C**, `--list-models` + CP1-A |
| Q5 | Operator entry lag distribution (mean, σ) | Requires watching real video against real PBP | **CP1-D**, 8 shots. Sets window size |
| Q6 | ~~Is the YouTube upload continuous (slope = 1.0)?~~ **RESOLVED, negatively, by CP1-D (2026-08-15).** Slope is not 1.0 (measured 0.9456 on game 136); it must be fitted per game, not assumed. See §7.1/A25. | — | closed |
| Q7 | Current Gemini pricing | Secondary sources contradicted each other; one implausible | Management applies published price to CP1-measured token counts (§14.1) |
| Q8 | Do all 7 matchday games have usable video? | Depends on Q2 | **CP4 [19]**, done early in parallel |
| Q9 | Is `userTime` UTC for every game, or does it vary? | Verified consistent on 2 games only | Harmless — anchors are empirical, absorbing any constant. Monitored via residuals |
| Q10 | Which specific matchday? | Needs Q2/Q8 — pick the round with the best video coverage | CP1 step 0 / CP4 [19] |

---

# 27. IMPROVEMENT PROPOSALS

Recommendations only. **None is incorporated into the primary plan.**

### P1 — Reallocate `possession_type`; keep video for what only video can see
**Proposal.** PBP already contains `fastBreak` (verified: 15/132, 14/140). Transition rate can be
computed deterministically for **all ~91 games**, not 7 — broader, cheaper, and exact. Similarly
`assisted` (via `parentActionId`) is a strong proxy for catch-and-shoot on made shots.
**`shot_contest` is the only one of the three with no PBP proxy — it is the true video-only metric.**
Consider replacing `possession_type` with a second genuinely video-only metric (e.g. help-defence
presence, or shot contest *level* on a 3-point scale).
**Benefit.** Higher marginal value per API call; a stronger story ("video shows what the box score
cannot"); transition rate gains season-wide coverage.
**Cost.** One registry edit; a new metric needs its own CP2 validation (~40 min).
**One-week risk.** Slightly increases CP2 scope; reduces risk of the video layer looking redundant.
**Recommendation: consider later (management decision D4).** Not now — CP2 must first evaluate the
three metrics as specified.

### P2 — Use PBP proxies as a free, continuous quality monitor
**Proposal.** Beyond CP2, keep computing model-vs-`fastBreak`/`assisted`/`blocked` agreement on every
run and emit it in `run_summary.json`.
**Benefit.** A regression (model change, clipping silently breaking, sync drift) is caught
automatically across ~950 events with zero human labelling — far more sensitive than 20 labelled events.
**Cost.** ~20 lines in aggregation.
**One-week risk.** Negligible; reduces risk.
**Recommendation: ADOPT NOW.** Already folded into §12.4 and §17.3 as sanity checks — this is
validation of agreed metrics, not new scope.

### P3 — Fetch all matchday PBP during CP1
**Proposal.** Cache PBP for all 7 games at CP1, not CP4.
**Benefit.** Permanently removes the external PBP source from the risk chain; costs seconds.
**Cost.** None.
**Recommendation: ADOPT NOW.** Included in §15.1 step 1.

### P4 — Find and calibrate the other 6 games during CP2/CP3
**Proposal.** Treat manifest-building and calibration (~1 h manual) as background work done while
batches run and while CP3 code is being written.
**Benefit.** Removes ~1 h from the critical path; surfaces "game 5 has no video" while there is still
time to substitute.
**Cost.** None — same work, better scheduled.
**Recommendation: ADOPT NOW.** Reflected in §23 as parallel work.

### P5 — Anchor on made field goals rather than quarter starts
**Proposal.** Use the first *made FG* of each quarter as the anchor, not `start-of-quarter`.
**Benefit.** Quarter-start `userTime` proved unreliable (game 136: 59-minute gap). A made FG is
visually unambiguous (scoreboard changes) and easy to locate by scrubbing.
**Cost.** None.
**Recommendation: ADOPT NOW.** Included in §7.5.

### P6 — Defer scoreboard OCR auto-sync
**Proposal.** Do not build it.
**Benefit.** Saves 4–8 h — a third of the stage budget.
**Cost.** ~70 min manual calibration for 7 games (revised 2026-08-15 from ~50 min — CP1-D showed
two observations per quarter, including occasional bisection, is standard, not exceptional).
**One-week risk.** Building it is a serious threat to delivery.
**Recommendation: REJECT FOR MVP.** Revisit only beyond ~20 games.

### P7 — Preserve `coordX/coordY` now, use later
**Proposal.** Keep shot coordinates in `PbpEvent.raw` and the canonical record; do not build shot
charts.
**Benefit.** The later PBP stage gets shot-zone analysis for free; CP2 can stratify by location.
**Cost.** Zero (already preserved via `raw`).
**Recommendation: ADOPT NOW** (storage only) — building anything on it is §22's trap.

### P8 — Risk the project plan may have missed: season availability
**Proposal.** Note that **the 2026-27 season has not started** (verified: fixtures dated 08/09/2026).
All PBP and video work must target the **completed 2025-26 season**. The ~91-game round-robin the
project needs exists there.
**Benefit.** Prevents a late discovery that the "current season" has no data.
**Recommendation: ADOPT NOW** as a documented constraint (§5.1). No scope change.

### P9 — Disable thinking explicitly
**Proposal.** Set `thinking_budget=0`.
**Benefit.** Lower cost and latency across ~950 calls; classification needs no reasoning chain.
**Cost.** One config line; verify the pinned model accepts it (CP1).
**Recommendation: ADOPT NOW.** In §6.1.

---

# 28. PLAN SELF-REVIEW

> **Historical CP0 snapshot, retained for auditability.** This section is the plan's own
> self-critique as written *before* CP1 executed, and is deliberately left unedited by hindsight —
> updating it after the fact would blur the audit trail rather than clarify it. For what CP1
> actually found (including the synchronization assumption this section did not anticipate),
> see `artifacts/cp1/cp1_report.md` and the revised §7/§15.4 above.

**What is the weakest assumption?**
A16 — that `video_metadata` offsets are honored for a YouTube URI. Official docs omit clipping
entirely and a Google staff member confirmed an escalated report that it was unsupported. The SDK and
a published sample say otherwise. This is a genuine, unresolved contradiction. *Mitigation:* it is
CP1's very first paid action, tested deterministically via VIDEO token counts, with a pre-agreed
escalation (T3) rather than an improvised workaround.

**Where is the most likely hidden time sink?**
Gate 0 — finding a *genuine, complete, public* YouTube broadcast for **seven** games. Finding one is
plausible; finding seven from a single matchday may not be. *Mitigation:* 30-minute timebox in CP1,
and P4 moves the other six searches early so a shortfall surfaces while substitution is still cheap.
Second candidate: the CP2 disagreement review, if disagreements turn out to be sync-caused (§12.5
exists precisely to detect that quickly).

**What is most likely to fail on real broadcast footage?**
Identifying *which* shot to judge inside a 28-second window — replays, camera cuts, and a preceding
possession all compete for attention. *Mitigation:* PBP-driven disambiguation (jersey, shot type,
points, expected offset) plus the blocked-shot control, which catches gross mis-localization
immediately.

**Does any component exceed a one-week MVP?**
The runner (resume, fingerprinting, circuit breaker, budget guard) is the most elaborate piece at
~260 lines. It is justified: ~950 paid calls without resume risks re-paying for the whole matchday
after one crash. I deliberately rejected the heavier options — scoreboard OCR, an orchestration
framework, a queue. Aggregation is plain Pandas.

**Is any checkpoint too large to audit?**
CP3 at 5.0 h is the largest and could have been split (runner / aggregation). I kept it whole because
its value is the *end-to-end proof*; splitting would produce a checkpoint that proves nothing on its
own. It is auditable through §17.3's ten concrete sanity checks plus a machine-readable
`run_summary.json`.

**Is any checkpoint too small?**
CP4 at 2.5 h is small by design — if scaling from 1 to 7 games needed more, CP3 would not have been a
real architecture. That is the intended test, not an oversight.

**Could CP3 be executed from this document without redesigning the layer?**
Yes. §4.1 fixes module boundaries; §5.3 and §10 give exact dataclass fields; §7.3 gives the mapping
formula; §8 gives inclusion rules and window arithmetic; §11 gives numerators, denominators and
status rules; §13 gives the fingerprint definition and error taxonomy; §17 gives the step sequence,
files and pass criteria. The open items are empirical (§26), each with a named checkpoint and test.

**Does CP4 genuinely scale CP3?**
Yes. CP3 already runs the manifest-driven loop; CP4 supplies 7 manifest entries instead of 1 and adds
a thread pool. No new component. The honest caveat: CP4 needs **6 more calibrations** (~45 min manual)
— real work, but manual data entry, not architecture. P4 moves it off the critical path.

**Are any claims presented as facts without evidence?**
I do not believe so. §3 labels every assumption [R]/[D]/[L]/[S]/[—]. Specifically flagged as
unverified: pricing (contradictory secondary sources — I refused to propagate the implausible
"$0.15/second"), clipping behaviour, quota accounting, model ids, operator lag, and YouTube
availability. Everything marked VERIFIED was executed in this session and its output is in the
transcript.

**Is the historical-vs-video evidence distinction preserved?**
Yes, structurally rather than by convention: `sample_scope="single_game_video_snapshot"` and a
**computed** `games_analyzed` are mandatory non-null columns on every output row (§11.2, §11.3);
video metrics are written to a separate artifact, never joined into team stats; metric names are
prefixed `video_*` on export; and §19.2 requires the agent and website to receive sample context with
every value. A one-game observation cannot reach a consumer stripped of its sample size.

**Is a practical fallback preserved without expanding scope?**
Yes. §21 has 12 branches; the ones that halt (T2, T3, T7, T9, T12) escalate to management rather than
silently adopting a heavier architecture. The download-and-upload path is named honestly as
ToS-dependent, operationally awkward, and +4–6 h — explicitly a fallback, never promoted to the
primary design. Critically, **every escalation preserves the PBP layer entirely**, which is the
primary evidence layer and carries the scouting report on its own.

**Fixed during self-review:** (1) window sizing was initially inherited at 8s/4s — replaced with the
derived error budget in §8.3; (2) the offsets test was a two-call heuristic — replaced with the
deterministic single-call token test in §6.4; (3) label-leaking PBP fields were originally going to be
sent for disambiguation — now an explicit withhold-list plus a unit test (§8.4, §20).
