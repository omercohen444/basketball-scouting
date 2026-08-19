# PROJECT_SPEC

Source of truth for the currently agreed product design. Compact on purpose.

**Status legend**

| Section | Meaning |
|---|---|
| **Locked** | Agreed. Changing it is a product decision, not an engineering one. |
| **Provisional** | Current best guess. Expected to change as evidence arrives. |
| **Stretch** | Only if it becomes almost free. Never at the cost of the core flow. |
| **Out of scope** | Deliberately excluded from the course MVP. |

---

## 0. Amendments (recorded, not decided here)

Sections 1–7 below are the originally agreed design and are left intact as the
audit trail. Four decisions have since been taken and are recorded here so this
document does not mislead a reader — each cites where the decision was made. An
implementer may not change this list; it only records what already happened.

| # | Amendment | Decided | Recorded in |
|---|---|---|---|
| A1 | **Video analytics removed from the MVP.** Gate 5 returned REMOVE after fresh-game evaluation found reliability and generalization insufficient. §2's locked "one complete matchday — 7 games" and §3's three video metrics no longer apply. The absence is declared explicitly in every report (`unavailable_evidence`). | Management, 2026-08-19 | `WORKLOG.md` Run 13; `BUILD_PLAN.md` §"Current stage status" |
| A2 | **The three agents are Evidence Triage → Tactical Scout → Head Scout.** §2's locked "exactly three CrewAI agents" still holds; the Video Analysis Agent was replaced by Evidence Triage, a genuinely distinct role, rather than dropping to two. | Management, 2026-08-19 | `WORKLOG.md` Run 13 |
| A3 | **Dataset is a full 14-team × 26-game season** (182 team-game records from 297 cached games), not the ~91-game single round-robin cycle in §2. This is §5's stretch goal, reached. | Stats track, 2026-08-15/16 | `WORKLOG.md` Runs 3–5 |
| A4 | **Deployment is now in scope.** §6 lists it as out of scope; the product is now built to deploy (Railway), with deployment itself deferred rather than excluded. | Project owner, Run 14 brief | `docs/DEPLOYMENT.md`; `WORKLOG.md` Run 14 |

Unchanged and still binding: team-level analysis only (no player analytics), the
ten core team metrics, deterministic code computes every number, agents only
interpret verified evidence, and no authentication.

---

## 1. Product

A web-based **Basketball Analytics and AI Scouting System** for the Israeli
Basketball Premier League.

A user selects an opponent and sees historical team analytics, league rankings,
wins-vs-losses signals and separately-presented video-derived metrics, then can
generate an AI scouting report as a PDF.

**Hard constraint: a one-week implementation window.** This is a serious
end-to-end MVP, not a complete commercial basketball platform.

---

## 2. Locked decisions

### Scope

- **Team-level analysis only.** No player analytics in the course MVP.
- Core dataset: **one complete Premier League round-robin cycle, ~91 games.**
- Video: **one complete matchday — 7 games, one analyzed game per team (14 teams).**
- Historical PBP is the **primary evidence layer**.
- Video is **contextual evidence from a much smaller sample** and must never be
  presented as a season-long trend. It is always labelled separately.

### Product flow

```
League data + video → data processing → team analytics → wins/losses analysis
  → AI scouting workflow → website → PDF scouting report
```

### The ten core team metrics

1. Offensive Rating   2. Defensive Rating   3. Net Rating   4. Pace
5. eFG%   6. TOV%   7. ORB%   8. Free Throw Rate   9. 3PA Rate
10. AST/TO Ratio

### Scouting report structure

1. Short team overview — **historical PBP data only**.
2. Notable league extremes (Top 3 / Bottom 3).
3. **Main section:** strongest differences between wins and losses.
4. A **separately labelled** video snapshot.
5. ~3 evidence-backed game-plan priorities.

### Agent architecture — exactly three CrewAI agents

| Agent | Input |
|---|---|
| Data Analysis Agent | Verified historical team analytics and W/L signals |
| Video Analysis Agent | Verified video metrics, **preserving sample-size limits** |
| Head Scout Agent | Combines both streams into the final structured report |

**Statistical calculations come from deterministic analytics code.** Agents
interpret, prioritize and explain verified evidence. Agents never invent
statistical calculations.

### Engineering

- Risk is tested early. Video Analytics is the first major build stage because
  it carries the highest technical uncertainty.
- Raw source data is preserved outside the core normalized product tables, so
  player-level analysis can be added later without re-ingesting everything.
- Internal IDs are distinguished from provider/source IDs.
- Secrets never enter Git. Raw data and large generated artifacts never enter Git.

---

## 3. Provisional assumptions

Everything here is expected to change on contact with evidence.

### Video approach

```
PBP event → localize to a video time window → Gemini multimodal analysis
  → structured classification → Python aggregation → team-level video metrics
```

PBP-assisted temporal localization is preferred over asking a model to watch a
full two-hour broadcast and invent aggregate statistics.

- **Video source:** public full-game YouTube video.
- **Model:** Gemini multimodal. OpenCV / YOLO remain possible alternatives *if
  genuinely required* — not a default.

### The three candidate video metrics

| Metric key | Labels |
|---|---|
| `shot_contest` | `open`, `contested`, `uncertain` |
| `possession_type` | `transition`, `half_court`, `uncertain` |
| `shot_creation` | `catch_and_shoot`, `off_dribble`, `uncertain` |

**These exact labels are provisional.** They live in one registry
(`src/basketball_scout/video/metrics.py`) from which the schema, prompt, CLI and
validation fixture are all derived, so any of them can be replaced cheaply.
The goal is to reuse one localized shot-event pipeline across several metrics.

Every metric always offers `uncertain`. Forcing a binary choice manufactures
false confidence, which corrupts the aggregates downstream.

### PBP source

- **SegevSport** is the current candidate source, reached via `basket.co.il`.
- Extraction method is **not yet determined**. See `WORKLOG.md` for the current
  lead and `scripts/spikes/probe_segevsport.py` for the diagnostic harness.

### Technology direction

Python · Pandas · Gemini multimodal · Supabase/PostgreSQL · CrewAI · FastAPI ·
HTML/CSS/JS frontend · PDF from an HTML/CSS template via browser rendering ·
Git/GitHub.

**Not all of this is installed.** Each stage adds its own dependencies when that
stage begins.

### Conceptual data entities

`teams` · `games` · `team_game_stats` · `pbp_features` · `video_team_metrics` ·
`team_signals`

Raw source data is kept alongside, outside these normalized product tables.

---

## 4. PBP deep dives (planned, provisional)

- Quarter splits.
- Clutch / close-game performance.
- Shot-type profile — **only if the source data supports sufficient detail.**

---

## 5. Stretch ideas

Attempt only if scaling from the delivered core is close to trivial.

- A second round-robin cycle (~182 games instead of ~91).
- A third video metric, if it rides the existing pipeline at near-zero cost.

---

## 6. Explicitly out of scope

Not in the course MVP:

- Player-level analytics.
- Authentication.
- Deployment.
- A multi-league platform.
- Commercial-grade basketball features beyond the agreed report.

Future source expansion (another league, another video provider) should be
possible through **source-specific adapters feeding canonical internal schemas**.
Build the adapter seam when it is cheap; do not build the platform now.

---

## 7. Decision authority

An implementer may make ordinary engineering decisions freely.

An implementer may **not** silently: expand product scope, add major
frameworks, remove agreed deliverables, reinterpret requirements, or make a
continuation/cut decision after a failed risk gate.

Those are reported, and work stops at the appropriate boundary.
