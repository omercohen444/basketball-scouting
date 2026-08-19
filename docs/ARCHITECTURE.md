# Architecture

One sentence carries the whole design:

> **Deterministic code calculates. Agents interpret.**

Everything below is that sentence enforced structurally rather than by
convention.

---

## 1. The full path

```
  basket.co.il / SegevSport JSON-RPC          (public play-by-play)
        │
        │  scripts/fetch_pbp.py                       297 games cached, git-ignored
        ▼
  pbp/            canonical.py · geometry.py · segev.py
        │         source-specific parsing behind one canonical schema
        ▼
  stats/          possession engine · four factors · league context · stability
                  win/loss signals · segments · runs · turnover taxonomy
        │         182 team-game records · 14 teams × 26 games
        │         THE ONLY SOURCE OF A NUMBER IN THIS PRODUCT
        ▼
  agents/evidence_pack.py     25 evidence items per team, formatted once,
        │                     reliability tiers computed, effect sizes masked
        │                     when they fail the rankability gate,
        │                     20 pre-ranked candidates
        ▼
  agents/pack_store.py        ── serialize ──►  data/evidence_packs/*.json
        │                                        versioned · hash-checked · IN GIT
        │                                        (this is what a deployment reads)
        ▼
  ┌───────────────────────── agents/pipeline.py ─────────────────────────┐
  │  Evidence Triage  →  Tactical Scout  →  Head Scout                   │
  │  (CrewAI, sequential, no delegation, no memory, no tools)            │
  │  validate ────────── validate ──────── validate                      │
  │       ↑ one repair attempt per stage, findings handed back verbatim  │
  └──────────────────────────────────────────────────────────────────────┘
        │
        ▼
  agents/render.py            attaches every canonical number to agent prose
        │                     key_evidence is COMPUTED, never model-authored
        ▼
  reports/contracts.py        PublicReport — the typed, public-safe contract
        │
        ├──► reports/pdf.py        ReportLab, from a saved report only
        ├──► persistence/          Supabase (PostgREST) or in-memory
        │
        ▼
  web/                        FastAPI · Jinja frontend · admin-gated generation
```

---

## 2. Why the numbers cannot be wrong

Three structural choices, not three rules to police:

1. **Agents emit no numbers at all.** Their schemas have no numeric fields for
   values. They carry `evidence_refs`; `render.py` looks every figure up in the
   pack at render time. "Quoted numbers match the source" therefore needs no
   validator.

2. **The Head Scout cannot cite evidence.** It cites `implication_id`s only, and
   `ScoutingReport` deliberately has no `key_evidence` field. "Introduces no new
   evidence" is true by construction.

3. **Provenance is adjudicated in Python.** `reliability_tier` is computed from
   validation state, sample size and volatility. An agent *proposes* a claim
   strength; `resolve_claim_strength()` recomputes it and may only **lower** it.
   An over-claiming model is corrected; an under-claiming one is left alone.

What remains, honestly: an agent cannot state a wrong number, but it can
mischaracterise a number's *magnitude* qualitatively. That is a known residual
risk, and `W-dual-framing` plus the confidence-versus-reliability check surface
its most common shapes as warnings.

---

## 3. Layering rules

Dependencies point one way and are enforced by module boundaries:

```
config · net                       (no project dependencies)
   ▲
pbp                                (parsing only)
   ▲
stats                              (all analytics; frozen dataclasses)
   ▲
agents                             (contracts, prompts, validation, render; Pydantic)
   │  agents/crew.py is the ONLY provider-aware module in the project
   ▲
reports                            (public contract, PDF, generation service)
   ▲
web                                (HTTP only: routing, status codes, templates)
```

Consequences worth stating:

- `config` never imports `agents` — the verified model pin is duplicated as a
  literal with a comment rather than imported.
- `web` contains no basketball logic. Its only route into the domain is
  `ReportService`.
- Swapping CrewAI for direct SDK calls touches exactly one file.
- `reports/service.py` is the single generation path. The API endpoint and the
  batch CLI both call it, so there is no second, subtly different
  implementation.

---

## 4. Why evidence packs are serialized

`data/raw` and `data/processed` are git-ignored — they are large, reproducible,
and not source. But the agent pipeline needs the deterministic evidence, and a
deployment has neither directory.

Shipping the cache would be wrong (hundreds of files, hundreds of MB, and a
production dependency on a scratch directory). Recomputing at request time would
be worse. So the deterministic layer is **serialized once, offline, and
committed**: 14 artifacts, ~600 KB, each carrying

- the complete `EvidencePack`,
- a sha256 over a canonical serialization of it, recomputed and enforced at
  load,
- provenance the pack schema deliberately does not hold: source game ids, a
  source fingerprint, item counts, and version identifiers.

The pack schema is `extra="forbid"` because it is the agent contract; provenance
belongs in an envelope around it, not inside it.

`build_production_packs.py --check` rebuilds in memory and compares hashes
against what is committed, so "are the shipped artifacts still the truth?" is a
one-command question.

---

## 5. Persistence

Three tables, JSONB-first:

| Table | Holds |
|---|---|
| `teams` | The opponent allowlist, seeded from the shipped pack index |
| `scouting_reports` | `report_json` (the exact public payload the API returns) and `evidence_json` (the pack artifact it came from) |
| `generation_runs` | Every attempt, including the ones that produced no report |

No relational explosion of claims, evidence and recommendations: nothing queries
inside a report, the shape is versioned by `report_version`, and a normalised
model would become a second source of truth for a structure the agent layer
already owns.

`generation_runs` exists because a rejected report is never saved. Without it a
failed generation would leave no trace at all.

The repository is a five-method `Protocol` with two adapters — Supabase over
PostgREST, and in-memory. The in-memory one is what every offline test runs
against, which is what keeps the suite credential-free.

---

## 6. Cost and safety model

- **No public route can reach the provider.** Reads hit storage; the PDF is
  rendered from a stored report. Tested by wiring a backend factory that raises
  on construction and driving every public route.
- **Generation is one endpoint behind `X-Admin-Token`**, compared with
  `secrets.compare_digest`. An unset token disables generation rather than
  opening it.
- **There is no free-text input anywhere.** The only caller-supplied values in
  the entire surface are a team id from a fixed 14-entry allowlist and a
  boolean. `GenerateRequest` is `extra="forbid"`, which turns "no arbitrary
  text reaches a prompt" from an intention into an assertion.
- **An invalid report is never saved.** `run_pipeline` raises when a stage still
  fails validation after its repair attempt; the service refuses hard rejections
  again before writing, and records the failed attempt instead.

See [`SECURITY.md`](SECURITY.md) for the full threat model.

---

## 7. What is deliberately absent

- **Video analytics.** Prototyped extensively, calibrated against real
  broadcast footage, and evaluated on fresh games. Reliability and
  generalization were insufficient, so it was cut rather than shipped. The
  product declares this in `unavailable_evidence`, so a reader sees the absence
  rather than inferring capability.
- **Player-level and lineup analytics.** Team-level only, by locked product
  decision.
- **Scheme, coverage and play-call claims.** Play-by-play carries none of it.
  The validators reject that vocabulary outright.
- **Auth, Redis, queues, RAG, an ORM.** No user in this product needs any of
  them.
