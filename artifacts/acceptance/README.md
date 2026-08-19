# Live acceptance — product path, 2026-08-19

One real end-to-end run through the **production** path, not a rehearsal:

```
data/evidence_packs/pack_segev_4.json   (committed, hash-checked)
  → CrewAI: Evidence Triage → Tactical Scout → Head Scout   (gemini-3.5-flash)
  → deterministic validation
  → Supabase (scouting_reports + generation_runs)
  → FastAPI: JSON, HTML, PDF
```

The command is the same code path the admin API endpoint uses — both go through
`ReportService`, so there is no second implementation that could drift.

```powershell
python scripts\ops\generate_reports.py --team-id segev:4 --force --pdf-dir artifacts\acceptance
```

## Result

| | |
|---|---|
| Team | `segev:4` — HAPOEL JERUSALEM (18-8, 26 games, 2025-10-12 → 2026-05-27) |
| Report id | `93589481-4ac0-4053-bd57-5828b5951d5a` |
| Backend / model | CrewAI / `gemini-3.5-flash` |
| Provider calls | **3** (one per agent) |
| Repair retries | 0 |
| Transient retries | 0 |
| Hard rejections | **0** |
| Warnings | 1 |
| Duration | 140.5 s |
| Persisted | Supabase, `status = published` |
| PDF | `scouting-report-hapoel-jerusalem-2025-26.pdf`, 11,213 bytes |
| Evidence pack | `segev:4\|2025-26\|agents-v1`, `sha256:cb5c6614c5da8807d…` |
| Versions | report `report-v1` · evidence `packs-v1` · definitions `agents-v1` |

### The one warning

```
R8: confidence 'high' exceeds weakest supporting evidence reliability 'moderate'
```

Recommendation 3 claimed high confidence while resting on a
moderate-reliability clutch measure. This is the validator doing its job on live
output, and it is surfaced to the reader rather than silently dropped — the
report still shows `high` alongside the warning, because the deterministic layer
reports the discrepancy rather than rewriting the model's prose.

### Content check (spot values, all deterministic)

| Metric | Value | League rank | Sample | Reliability |
|---|---|---|---|---|
| Pace | 75.2 | 12 of 14 | 26 g | high |
| Share of Points from FT | 19.1% | 1 of 14 | 26 g | moderate |
| Offensive Rebound % | 33.8% | 2 of 14 | 26 g | high |
| Turnover Rate | 13.4% | 3 of 14 | 26 g | moderate |
| Effective FG% When Trailing 6+ | 69.7% | 2 of 14 | 26 g | moderate |

The executive summary describes exactly this profile — slow tempo, elite
offensive rebounding, low turnovers, free-throw-heavy scoring, a late-game
efficiency drop — with no number in the prose. Every figure above was attached
by `render.py` from the pack.

## Also verified live in the same session

- Supabase schema applied and seeded (14 teams); RLS on, no policies,
  `has_table_privilege('anon', 'public.scouting_reports', 'SELECT')` → `false`.
- Full write path exercised first with the deterministic stub backend (zero
  provider calls) to prove persistence before spending anything. That rehearsal
  row was removed afterwards; its `generation_runs` audit row survives with a
  null `report_id`, which is what the foreign key's `on delete set null` is for.
- `GET /api/teams` against live Supabase: 14 teams in **one** query, ~420 ms.
- `GET /api/reports/latest/segev:4`, `GET /api/reports/{id}`,
  `GET /api/reports/{id}/pdf`, `GET /`, `GET /teams/segev:4` — all 200 against
  the live database.

## Not done

Only one team was generated with a real provider. The remaining 13 are
deliberately left ungenerated: the capability is proven and batch generation is
built (`--all`, with a `--yes` gate), but spending 39 provider calls to
demonstrate a loop that already works would be waste. The site renders those
teams in its empty state, which is itself worth having tested.
