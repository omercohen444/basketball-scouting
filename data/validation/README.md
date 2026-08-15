# Validation fixtures

Small, tracked-in-Git files used to check that the video pipeline produces
sensible results. Everything here must stay small enough to commit.

`data/raw/` and `data/processed/` are git-ignored; **this directory is not.**
Locally generated run outputs belong in `data/validation/runs/` (ignored).

---

## `video_events_ground_truth.csv`

The Gate 1 fixture: ~20 shot events, manually labelled, used to check whether
the model's video classifications are usable at all.

**The file ships empty (header only) on purpose.** Do not add example rows to
it — a fabricated label that survives into a real labelling session silently
corrupts the Gate 1 result. Regenerate the header at any time:

```python
from pathlib import Path
from basketball_scout.video.ground_truth import write_template
write_template(Path("data/validation/video_events_ground_truth.csv"))
```

Columns are generated from the metric registry in
`src/basketball_scout/video/metrics.py`, so replacing a provisional metric means
regenerating this template rather than hand-editing columns.

### Columns

| Column | Meaning |
|---|---|
| `event_id` | Unique id for the event. Must be unique in the file. |
| `game_id` | Source game identifier. |
| `video_url` | Full-game video URL (e.g. the YouTube watch URL). |
| `event_seconds` | Video-clock moment of the shot. Accepts `754`, `754s`, `12:34` or `1:02:03`. |
| `start_seconds` | Optional explicit window start. Overrides `event_seconds`. |
| `end_seconds` | Optional explicit window end. Overrides `event_seconds`. |
| `period` | Quarter number, from PBP. |
| `game_clock` | Game clock at the event, from PBP. |
| `team` | Shooting team, from PBP. |
| `pbp_description` | Raw PBP text for the event. |
| `human_<metric>` | **Your** label. Fill this in before looking at the model output. |
| `model_<metric>` | The model's label, written by the spike. |
| `match_<metric>` | `true` / `false` / blank, derived — do not hand-edit. |
| `notes` | Anything worth remembering about this event. |

Either `event_seconds`, or both `start_seconds` and `end_seconds`, is required.
When only `event_seconds` is given, the window is derived as
`[event - 8s, event + 4s]` (pre-roll is larger because transition-vs-half-court
and catch-and-shoot-vs-off-dribble are decided by what happened *before* the
release).

A blank label means **not labelled yet**. It does not mean `uncertain` —
`uncertain` is a real label and must be written out explicitly.

### Valid labels

| Metric | Labels |
|---|---|
| `shot_contest` | `open`, `contested`, `uncertain` |
| `possession_type` | `transition`, `half_court`, `uncertain` |
| `shot_creation` | `catch_and_shoot`, `off_dribble`, `uncertain` |

Loading rejects any label outside these sets, so a typo fails loudly instead of
quietly skewing the agreement numbers.

### Illustrative row (do NOT paste into the CSV)

```text
event_id  = G001-E017
game_id   = G001
video_url = https://www.youtube.com/watch?v=XXXXXXXXXXX
event_seconds = 1:12:30
period = 3 | game_clock = 04:12 | team = <team name>
pbp_description = 3PT shot made
human_shot_contest = contested
human_possession_type = half_court
human_shot_creation = catch_and_shoot
```

### Labelling protocol for Gate 1

1. Pick ~20 shot events spread across quarters and both teams — include some
   deliberately awkward ones (bad camera angle, transition, late-clock).
2. Fill in `human_*` **first**, without looking at model output. Anchoring on
   the model's answer destroys the value of the comparison.
3. Run the spike to populate `model_*`.
4. Compute agreement:

```python
from pathlib import Path
from basketball_scout.video.ground_truth import load_rows, agreement
rows = load_rows(Path("data/validation/video_events_ground_truth.csv"))
for result in agreement(rows).values():
    print(result.summary_line())
```

Two rates are reported. `agreement` counts every comparable pair.
`decisive` excludes pairs where either side said `uncertain` — without it, a
model that answers `uncertain` for everything is impossible to read correctly.

~20 events establishes *feasibility*, not accuracy. Treat the number as
"obviously workable / obviously broken / unclear", not as a benchmark score.

---

## `segev_game136_full.json`

A verbatim, complete copy of the real Segev `getActions` response for
`game_id=136` (MACCABI TEL AVIV 95 - HAPOEL JERUSALEM 84, 2026-01-11 Winner
League) — all 867 actions. Used by the statistics layer's real-data
integration test (`tests/test_stats_integration_game136.py`), which needs
every action type (rebounds, turnovers, free throws, fouls), not just shots —
`segev_game136_trimmed.json` above is shot-only and too small for that.

Kept whole rather than re-trimmed: the statistics layer's box-score
reconciliation checks (final score, ORB/DRB, TOV, etc.) are only meaningful
against a complete game.
