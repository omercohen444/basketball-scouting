# Frontend design handoff

Everything a designer needs to produce the final interface for this product, and
the small number of things the design is **not** allowed to change.

The current interface (`src/basketball_scout/web/templates/`) is a deliberate
placeholder: it exists so the product works end to end and can be
integration-tested. Replacing it is expected. The API contract below is not.

---

## 1. What this product is

A public, shareable **opponent scouting** site for the Israeli Basketball
Premier League. A visitor picks one of 14 opponents and reads a scouting report
built from a full season of official play-by-play, then downloads it as a PDF.

The one sentence that shapes every screen:

> **Deterministic code calculates. Agents interpret.**

Every number on every screen was computed in Python from play-by-play and
pre-formatted by the backend. A three-agent pipeline chose which evidence
mattered, explained it and prioritized it — it wrote no numbers. The interface
should make that division legible, because it is the product's actual claim to
trustworthiness.

---

## 2. The public flow

```
  ┌─────────────────┐      ┌──────────────────────┐      ┌───────────────┐
  │  1. Home        │      │  2. Report           │      │  3. PDF       │
  │  pick opponent  │ ───► │  read + inspect      │ ───► │  download     │
  │  (14 teams)     │      │  evidence            │      │               │
  └─────────────────┘      └──────────────────────┘      └───────────────┘
```

Three screens. There is no login, no search box, no free-text input anywhere,
and nothing a visitor does costs money — reports are generated in advance by an
administrator and read from storage.

---

## 3. Screens

### 3.1 Home — opponent selector

**Data:** `GET /api/teams` → [`example_teams.json`](example_teams.json)

Per team: `team_id`, `team_name`, `season`, `wins`, `losses`, `record`,
`games_n`, `has_report`, `latest_report_id`, `latest_generated_at`.

Needs to express:

- a list or grid of exactly 14 opponents, each with its season record;
- a clear difference between "report ready" and "no report yet"
  (`has_report`) — a team without one is still selectable, and lands on the
  empty state;
- one sentence of orientation: what this data is, and that opening a report is
  free.

### 3.2 Report

**Data:** `GET /api/reports/latest/{team_id}` → [`example_report.json`](example_report.json)

The real thing, live, for Hapoel Jerusalem. Design against this file.

Order matters more than styling. The current order is deliberate and worth
keeping: **the most actionable content comes first.**

| Block | Field | Notes |
|---|---|---|
| Header | `team_name`, `provenance.record`, `provenance.games_n`, `provenance.date_range`, `generated_at` | Also the natural home for the PDF action |
| Data-state banner | `provenance.pack_states` | Usually empty. When it contains `no_win_loss_evidence`, win/loss comparisons genuinely do not exist for this team and are absent everywhere below — say so, do not render blanks |
| Scope note | `scope_note` | One line stating what the report is built from |
| Executive summary | `executive_summary` | One paragraph. The thing a coach reads if they read nothing else |
| Game-plan priorities | `recommendations[]` | 3–5, ordered by `priority`. The most useful block on the page |
| Narrative sections | `sections.*` | `offensive_identity`, `strengths`, `vulnerabilities`, `transition_notes`, `turnover_notes`. **Any of these may be empty — render only what is present** |
| Key deterministic evidence | `key_evidence[]` | A table. This is the receipts block |
| Caveats | `caveats[]` | Plain list |
| Not available in this data | `unavailable_evidence[]` | Deliberately declared absences. Do not hide these |
| Automated validation | `validation` | Rejection/warning counts and the warning list |
| Provenance | `provenance`, `backend`, `model_name`, `report_version` | Small print, but present |

### 3.3 Empty, loading and error states

- **No report yet** (`404`, code `not_found` on the latest endpoint): the team
  exists and is selectable; no report has been generated. The copy must make
  clear that browsing does not trigger generation — that is an
  administrator-only action.
- **Loading**: reads are fast (a single database query). A skeleton or a simple
  progress indicator is enough. There is no long-running request in the public
  UI — generation is not reachable from the browser at all.
- **Errors**: see [`example_errors.json`](example_errors.json). Every `/api`
  failure has the same shape:
  ```json
  { "error": { "code": "not_found", "message": "Report not found" } }
  ```
  Codes worth distinguishing: `not_found`, `unknown_team`, `rate_limited`
  (a `Retry-After` header accompanies it), `unavailable` /
  `storage_not_initialised`, `internal_error`.
- **Storage unreachable**: the team list still works, because the allowlist
  comes from shipped data rather than the database. Show a banner, not a blank
  page.

---

## 4. Components

### 4.1 Evidence card

`key_evidence[]` and the `evidence[]` array inside every claim and every
recommendation use the same shape:

```json
{
  "evidence_id": "EV.season.orb_pct",
  "metric": "Offensive Rebound %",
  "scope": "season",
  "value": "33.8%",
  "league_rank": "2 of 14",
  "league_percentile": 92.3,
  "league_average": "30.3%",
  "sample_games": 26,
  "sample_possessions": null,
  "reliability": "high",
  "validation_state": "validated_deterministic",
  "direction": "higher_is_better",
  "win_loss": {
    "available": true,
    "in_wins": "33.9%",
    "in_losses": "32.7%",
    "effect_size": 0.14,
    "favorable_in_wins": true,
    "sample": "18W / 8L",
    "reason": null
  },
  "limitations": []
}
```

Design notes:

- `value`, `league_rank`, `league_average`, `in_wins` and `in_losses` are
  **already formatted strings**. Render them as-is.
- `reliability` (`high` / `moderate` / `low`) deserves visible treatment. It is
  the honest-uncertainty signal and it is computed, not guessed.
- `scope` is `season` unless the metric is situational (`clutch`, `Q4`, `1H`,
  `behind_6_plus`). A non-season scope should be visible — it changes what the
  number means.
- `win_loss.available` may be `false`. Then there is no split to show, and
  `reason` says why. Do not render zeros or dashes as if they were data.
- `direction: "neutral"` means high and low are **style, not quality**. Never
  colour a neutral metric as good or bad.
- `limitations[]` are codes whose full text appears in the report's `caveats`.

### 4.2 Claim

```json
{
  "text": "They establish an extremely slow and deliberate tempo, preferring to control the speed of the game.",
  "claim_strength": "established",
  "implication_refs": ["IMP_pace"],
  "evidence": [ /* EvidenceCard[] */ ]
}
```

`claim_strength` is `established` / `indicated` / `speculative`, resolved in
Python from data provenance — the model proposes, code may only lower it. Show
it. A claim without its evidence visible (or one interaction away) loses the
whole point of the product.

### 4.3 Recommendation

```json
{
  "recommendation_id": "REC_ONE",
  "priority": 1,
  "directive": "Execute a disciplined half-court defense that avoids fouling and forces them into contested two-point field goals.",
  "rationale": "Since they rely heavily on the free-throw line for scoring and rarely score from two-point range, keeping them off the line and forcing them to execute in the half-court will neutralize their primary scoring method.",
  "confidence": "moderate",
  "implication_refs": ["IMP_scoring_distribution", "IMP_pace"],
  "evidence": [ /* EvidenceCard[] */ ]
}
```

`recommendation_id` and `implication_refs` are model-authored labels, so treat
them as opaque strings — never parse or sort by them.

`directive` is the instruction; `rationale` is why. `confidence` is
`high`/`moderate`/`low`. Sort by `priority`.

### 4.4 PDF action

`GET /api/reports/{report_id}/pdf` returns `application/pdf` with a
`Content-Disposition: attachment` filename. A plain link is enough — no
client-side generation, no spinner state to manage, and no provider call.

---

## 5. API reference

Base URL is the deployment root. `openapi.json` in this folder is the generated
schema.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | — | Liveness + configuration snapshot |
| `GET` | `/api/teams` | — | The 14 supported opponents |
| `GET` | `/api/reports/latest/{team_id}` | — | Latest saved report for a team |
| `GET` | `/api/reports/{report_id}` | — | One saved report |
| `GET` | `/api/reports/{report_id}/pdf` | — | PDF of a saved report |
| `POST` | `/api/admin/reports/generate` | `X-Admin-Token` | Generate + validate + save |

`{team_id}` accepts both `segev:4` and the URL-friendly `segev_4`.

The admin endpoint is **not part of the public frontend** and must never appear
in browser-delivered code. Its only inputs are `team_id` and
`force_regenerate`; the request model rejects every other field.

---

## 6. Hard constraints

These are not stylistic preferences. Breaking one breaks the product's claim.

1. **The frontend calculates no authoritative metric.** No sums, no averages,
   no percentages, no rank recomputation, no unit conversion. Every figure
   arrives pre-formatted. Sorting and filtering the given values is fine.
2. **The frontend holds no secret.** No admin token, no Supabase key, no Gemini
   key, no service credential — not in JS, not in a data attribute, not in a
   build-time environment variable.
3. **The frontend invents no basketball fact.** No illustrative numbers, no
   placeholder stats that look real, no "typical" values in an empty state.
4. **No video, no player, no scheme content.** The product is team-level from
   play-by-play. Video analytics were prototyped, evaluated on fresh games,
   found insufficiently reliable, and deliberately excluded. There is no
   player-level, lineup, coverage or play-call data. Do not design a slot for
   any of it.
5. **The public UI never triggers generation.** No "generate report" button, no
   regenerate affordance, no retry that posts to the admin endpoint. Generation
   costs money and is a backend operation.
6. **Declared absences stay visible.** `unavailable_evidence` and `caveats` are
   content, not chrome. Collapsing them behind a toggle is fine; removing them
   is not.
7. **Reliability and claim strength stay visible.** A report that reads as
   uniformly confident misrepresents its own evidence.

---

## 7. Free to change

Everything else: layout, type, colour, spacing, iconography, motion, dark mode,
navigation shape, how evidence is progressively disclosed, how the team picker
works, mobile behaviour, and whether the report is one page or several. The
placeholder makes no claim on any of it.

---

## 8. Files here

| File | What it is |
|---|---|
| `example_report.json` | A real generated report (Hapoel Jerusalem, 2025-26), exactly as the API serves it |
| `example_teams.json` | A real `GET /api/teams` response |
| `example_errors.json` | Every error state a frontend must handle |
| `openapi.json` | Generated OpenAPI schema for the whole surface |
