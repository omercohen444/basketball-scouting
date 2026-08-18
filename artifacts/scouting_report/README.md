# Agent Layer — Scouting Report Artifacts

Date: 2026-08-19
Branch: `no-video-mvp` (worktree `C:\AI_DEV10\basketball_analytics_mvp`)
Implementation: `src/basketball_scout/agents/`

Reproduce (offline, no API key, no network):

```powershell
python scripts\scouting_report\build_pack.py --team-id segev:4 --out-dir artifacts\scouting_report
python scripts\scouting_report\generate_report.py --team-id segev:4 --stub
```

## 1. What this is

The agent layer turns the deterministic stats layer into a structured scouting
report. Three agents, strictly sequential:

```
deterministic EvidencePack -> Evidence Triage -> Tactical Scout -> Head Scout -> deterministic render
```

The binding principle is that **an LLM never produces a number**. Agents emit
qualitative prose plus evidence references; `render.py` looks up every figure
from the pack at render time. This is enforced structurally, not by inspection.

## 2. Files here

| File | What it is |
|---|---|
| `pack_segev_4.json` | A complete `EvidencePack` — the deterministic agent input contract. Useful on its own: the later FastAPI/UI stage can consume this with no agent involved. |
| `report_segev_4_stub.{json,md}` | Primary demo — HAPOEL JERUSALEM (18-8), full win/loss evidence available. |
| `report_segev_11_stub.{json,md}` | Regression — BEER SHEVA (10-16). |
| `report_segev_2_stub.{json,md}` | **Degenerate-case gate** — MACCABI TEL AVIV (24-2). Demonstrates the `no_win_loss_evidence` path. |

All were produced by the **deterministic stub backend** (`--stub`), which makes
zero provider calls. The prose is deliberately flat placeholder text; **every
number, rank, sample size and reliability tier in them is real**. See §6 for why
no live-model artifact is present.

## 3. Why Maccabi Tel Aviv is the edge case, not the showcase

The league's best team (24-2) has **zero agent-rankable win/loss signals**. With
only 2 losses it fails `stats.winloss.AGENT_RANKABLE_MIN_LOSSES = 3`, so no
metric can be compared between wins and losses at a meaningful sample.

Since wins-vs-losses is the *main section* of the report per `PROJECT_SPEC.md`,
this had to be handled rather than discovered at demo time. The pack raises
`pack_states = ["no_win_loss_evidence"]`, every `win_loss` block is masked, the
prompts switch to an explicit prohibition, and validation rule R6 rejects any
outcome framing. The report degrades to league-relative identity claims and says
so, rather than fabricating a comparison.

Verified directly: `segev:2` is the only one of the 14 teams in this state.

## 4. Two defects this work found and fixed

**`effect_size` leaked past the rankability gate.** On `segev:2`, 13 of 15
enrichment-v2 evidence objects carried a numeric `effect_size` (e.g.
`net_rating` = 2.4358) while `win_loss_signal` was `None`. An agent handed that
would quote it. The pack now emits `effect_size: null` plus an explicit
`effect_status` whenever `is_agent_rankable()` is false — masked, not merely
flagged. `build_pack.py --all` fails loudly if any item leaks.

**`reliability_tier` mislabelled net rating.** Coefficient of variation is
`std / |mean|`, and net rating sits naturally near zero, so CV explodes without
bound — the league leader's net rating was being marked "low reliability".
Metrics that legitimately cross zero now opt out via `MetricSpec.cv_applicable`.

## 5. Evidence contract

`EvidencePack` carries 25 `EvidenceItem`s per team, each with a stable readable
id (`EV.{scope}.{metric}`), a pre-formatted `display_value`, league rank and
percentile, sample size, `validation_state`, a deterministic `reliability_tier`,
stability, win/loss and screening flags.

Composition: the 10 core `PROJECT_SPEC` metrics (volume-weighted canonical
aggregate), 5 situational cuts (clutch eFG%/TOV%, Q4 net rating, eFG% when
trailing 6+, first-half net rating), and 10 profile-shape metrics (scoring
shares, points off turnovers, second chance, fast break, assisted share, runs).

**No new analytics were written.** Every value comes from
`stats.evidence.build_evidence`, which was already accepted; this layer adds
only ids, formatting, tiering, masking and screening.

`screening.candidate_ids` is a deterministic pre-ranked pool of 20. The Triage
agent may only drop and reorder within it — it cannot introduce an id Python did
not select, so it structurally cannot miss a top signal.

`unavailable_evidence` names what this dataset genuinely cannot see (season shot
zone / rim share, shot distance, half-court possession type, last-passer
identity, player-level, video, scheme). Declaring gaps explicitly suppresses
gap-filling from world knowledge far better than silence, and gives a claim a
legitimate way to *acknowledge* a limit without citing it as support.

## 6. Live-model run: blocked, not skipped

The three agents are fully wired (`agents/crew.py`, CrewAI 1.15.16, sequential,
no delegation, no memory, no tools, `output_pydantic` per task). The live run
**could not be executed**: the Gemini API key returns

```
429 RESOURCE_EXHAUSTED — "Your prepayment credits are depleted"
```

This is a billing state, not a rate limit and not transient. Two things were
verified despite it:

- **TLS works through the CrewAI/LiteLLM/httpx path**, not just `google-genai`.
  The request reached Google and returned an application-level API error rather
  than a certificate failure, confirming `net.enable_system_trust_store()`'s
  global `ssl` patch covers httpx. The plan explicitly said not to assume this.
- **The failure surfaces as one actionable line**, not an SDK traceback
  (`generate_report.py` exits 3 with a top-up hint and a `--stub` suggestion).

Everything except the model's prose is proven: contracts, validation, claim
resolution, rendering, and the full three-stage chain with its repair-retry.

## 7. Known limitations

- **No live-model artifact** — blocked on provider credit (§6). Prose quality is
  therefore unproven; the pipeline around it is not.
- **No rim / shot-zone share.** Deliberately cut: it is
  `provisional_deterministic` (weakest tier) and would have required a second
  full 182-game play-by-play walk. Declared in `unavailable_evidence`. 3PT
  tendency is still covered via `fg3a_rate` and the scoring shares; transition
  via fast-break points.
- **Segment values are unweighted per-game means**, so a game with few segment
  possessions counts as much as one with many. Flagged per item via
  `unweighted_segment_mean` and reflected in `reliability_tier`.
- **Prose denylists are deliberately narrow.** They catch unambiguous
  multi-word phrases only. An earlier draft listed `"per "` for Player Efficiency
  Rating and immediately false-positived on the label "Points Off Turnovers (per
  game)" — a denylist that blocks valid reports is worse than a missing one,
  since the numbers are attached deterministically and cannot be wrong.
- **`W-thin` fires on the stub reports** (6 distinct evidence items). That is the
  stub's flat structure, not a pack deficiency — a real model spreading claims
  across more implications would cite more.
