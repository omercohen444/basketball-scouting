# BUILD_PLAN

Risk-first build strategy for the one-week window.

Read `PROJECT_SPEC.md` first for what is being built. This file is about the
**order** it gets built in, and the gates that decide whether to continue.

---

## Strategy

**Test the riskiest thing first.**

Video Analytics is the first major implementation stage, not because it is the
most valuable part of the product, but because it carries the highest technical
uncertainty. Everything else on the plan — PBP analytics, agents, the web app,
the PDF — is work we already know how to do. The video layer is the only piece
that could turn out to be impossible.

Discovering that on day 5 would be fatal. Discovering it on day 1 costs a
metric and nothing else, because the product is designed so the video layer can
be cut without taking the rest of the report with it.

Consequences of this ordering:

- **A working end-to-end flow beats a sophisticated component.** Once a
  component is good enough for the MVP, stop improving it.
- **Do not continue a failing approach because time has been invested in it.**
  Sunk cost is not evidence.
- **Preserve extensibility only when it is cheap.** No speculative infrastructure.

---

## Video Risk Day — gates

The next major execution run tests Video Analytics against real footage.
Gates run in order. A gate that fails is reported, not worked around.

### Gate 0 — Sources

Obtain and confirm:

- one real Premier League **full-game YouTube source**;
- the **corresponding usable PBP** for that same game.

Both are required. A video without matching PBP cannot be localized, and PBP
without video cannot be verified.

### Gate 1 — Model feasibility

Manually label roughly **20 representative shot events** as ground truth, then
compare Gemini's classifications against them for the three provisional metrics.

The purpose is **feasibility, not benchmark optimization.** The question is
"obviously workable / obviously broken / unclear", not a score to a decimal
place. ~20 events cannot establish accuracy.

Harness: `data/validation/video_events_ground_truth.csv` (+ its README for the
labelling protocol) and `scripts/spikes/gemini_video_event.py --agreement`.

Label the human column **before** looking at model output. Anchoring on the
model's answer destroys the value of the comparison.

### Gate 2 — First true end-to-end metric

Prove the whole chain on real data, for one metric:

```
PBP event → correct video window → Gemini classification
  → structured result → Python aggregation
```

"Correct video window" is the part most likely to break. Broadcast clocks and
PBP clocks do not agree by default.

### Gate 3 — Two reliable metrics

At least **two** metrics working through the pipeline.

A third metric is worth having **only if it can be added cheaply** — the
pipeline is designed so a metric is a registry entry, so this should be nearly
free. If it is not nearly free, skip it.

### Gate 4 — One full game

Process all relevant events from **one complete real game** and produce
team-level video metrics.

This is where cost, rate limits and runtime become real. Measure them here.

### Gate 5 — Decision

Decide whether the video approach is **viable**, should be **simplified**, or
should be **removed**.

> **This decision is not the implementer's to make.** The implementer's job is
> to provide clear technical evidence and report blockers honestly. The project
> team decides.

---

## Stage order after the video risk stage

The video outcome shapes what follows, so later stages are listed as sequence,
not schedule. **No dates are assigned here** — none have been agreed.

1. **Video Analytics risk stage** (gates 0–5 above) — closed, Gate 5 = REMOVE
2. PBP ingestion — ~91 games, one round-robin cycle
3. Team analytics — the ten core metrics
4. Wins-vs-losses signals — the main section of the report
5. Persistence — Supabase / PostgreSQL
6. CrewAI agents — Data Analysis, Video Analysis, Head Scout
7. FastAPI + web frontend
8. PDF scouting report

Stages 2–4 are the primary evidence layer and carry the report on their own.
That is deliberate: the product survives Gate 5 returning "remove".

---

## Current stage status

Last updated 2026-08-19 (Run 14). Detail for every line is in `WORKLOG.md`.

### Video Risk Day — closed

**Gate 5 outcome: REMOVE.** Management decision, recorded in `WORKLOG.md`
Run 13: the video layer was built and fresh-game validated, and failed
reliability gates. It is excluded from the MVP, and the report declares the
absence explicitly rather than implying capability.

What this branch's log actually documents about the gates:

| Gate | Recorded outcome (this repository) |
|---|---|
| Gate 0 — Sources | **PASS** — public full-game video + matching PBP for `game_id=136`, cross-checked against 3 other rounds |
| Gate 1 — Model feasibility | **PARTIAL** (CP1 verdict, Run 2). `gemini-2.5-flash` returned HTTP 404 for new users; `gemini-3.5-flash` pinned and verified. Clipping offsets confirmed honoured (exactly linear VIDEO-token scaling). Sync was falsified: the assumed slope of 1.0 was wrong (~0.946 measured), and 1 of 4 quarters held a genuine discontinuity |
| Gates 2–4 | Executed on the `fresh-video-eval` branch, **not logged here.** This worktree was created from `1aa2af5` on the stats track and carries no CP2–CP4 video record |
| Gate 5 — Decision | **REMOVE** |

The detailed video evidence trail lives on the `fresh-video-eval` branch and in
`docs/VIDEO_STAGE_PLAN.md`; do not infer CP2–CP4 outcomes from this file.

The strategy in this file worked exactly as intended: the riskiest component was
tested first, and removing it did not take the product with it.

### Stages after the video risk stage

| Stage | Status |
|---|---|
| 2. PBP ingestion | Complete — 297 games cached, 182 team-game records |
| 3. Team analytics | Complete — the ten core metrics, validated |
| 4. Wins-vs-losses signals | Complete |
| 5. Persistence — Supabase | Complete — schema applied, seeded, RLS verified |
| 6. CrewAI agents | Complete — Triage / Tactical Scout / Head Scout, live-verified |
| 7. FastAPI + web frontend | Complete — frontend is a functional placeholder by design |
| 8. PDF scouting report | Complete |
| Deployment (Railway) | Ready, not performed — see `docs/DEPLOYMENT.md` |
