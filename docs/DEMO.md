# A 3–5 minute walkthrough

Everything below runs on the live site. Nothing needs to be installed, and no
credential is required.

**https://web-production-82a60.up.railway.app**

The through-line to keep in view: **deterministic code calculates, agents interpret.**
Each stop shows one half of that.

---

### 0 · The one-line framing (15s)

> Israeli Basketball Premier League, 2025-26 regular season. 182 games of official
> play-by-play, parsed into possessions, turned into team analytics — and then a
> three-agent pipeline writes an opponent scouting report on top of it without ever
> being allowed to produce a number.

---

### 1 · League (45s) — `/`

Land on the league table.

- The **scatter** is the fastest read in the product: offense across, defense down,
  inverted so up-and-right is good. Crosshairs are the league average.
- The **advanced table** sorts on any column. Click *Net* — the standings order and the
  efficiency order are not the same, which is the whole reason for the site.

> Say: every number here is computed in Python from play-by-play. There is no model
> anywhere in this page.

---

### 2 · Team → Overview (60s) — pick **Hapoel Jerusalem**

- **Both halves of the Four Factors.** The defensive four are the opponent's own factors
  run through the same functions, so the two sides are directly comparable — which is
  not true of most public sources.
- **Net rating by quarter**, per 100 possessions.
- **What changes between wins and losses** — dumbbells on descriptive factors only.
  Ratings are excluded on purpose: "they outscored people in the games they won" is a
  tautology, not a finding.

Then hit **Consistency** and point at Net Rating carrying no label — the coefficient of
variation is meaningless for a metric that crosses zero, so the product says nothing
rather than something wrong.

---

### 3 · Team → Profile (45s)

- **Shot profile** carries an `EXPERIMENTAL` badge and a caveat that names the exact
  validation state. It is never ranked and never coloured.
- **Where the points come from** — one stacked bar for the 2PT/3PT/FT partition, which
  genuinely sums to 100%, and separate rows for the contextual sources that overlap.
  A fast-break lay-up off a steal is three of those at once, so they are never stacked.

> This is the honesty layer: the product distinguishes what it knows well from what it
> knows provisionally, on the page rather than in a footnote.

---

### 4 · Compare (30s) — `/compare`

Two teams aligned metric by metric, each value beside its league position. Shareable URL.

---

### 5 · Scouting report (75s) — `/scouting/segev:4`

The centrepiece. Scroll to **Keys to Win**.

- Every interpreted claim sits above the **measured evidence** it rests on: the value,
  the league average, the rank and the sample size.
- The prose is model-written. **The numbers are not** — no agent schema has a numeric
  field, so the agents structurally cannot produce one. Values are re-attached by the
  renderer from the evidence pack after the agents finish.
- Eighteen deterministic rules run over the agent output as pure functions. They reject
  half-court claims, causal language about a correlation, degree words the evidence
  doesn't reach, and constructs like "momentum" that nothing here measures.

Download the **PDF** if there is time.

> Say: a public page view never calls Gemini. Reports are generated snapshots, produced
> once through an admin-only endpoint.

---

### 6 · Methodology (30s) — `/methodology`

Every formula, written the way the code computes it — and a test evaluates that same
expression against the executing code, so the page cannot drift from the product.

Close on **Limitations**, which states what the product cannot do before anyone has to
discover it.

---

## If asked

| Question | Answer |
|---|---|
| *Why no video?* | It was built first, evaluated, and cut: Gate 5 returned REMOVE after a fresh-game evaluation found reliability and generalisation insufficient. `PROJECT_SPEC.md` amendment A1. |
| *Why no player stats?* | The source is team-level play-by-play. Not a missing feature — a property of the data. |
| *What stops the model inventing a number?* | Its schemas have no numeric field, and the validator rejects metrics this pipeline doesn't compute. |
| *How is it tested?* | 1,205 tests, every one of them offline — no credentials, no network. Plus completeness guards, hash-verified artifacts, and a test that no public route can construct an agent backend. |
| *Can I run it?* | `pip install -r requirements.txt` then `python main.py`. The whole analytics site works with zero configuration. |
