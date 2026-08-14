# Spikes

Diagnostic harnesses for the current risk stage. Their job is to make the
**next** investigation fast — they are not product code and are not on the
product's critical path.

Both write to `artifacts/` or `data/validation/runs/`, which are git-ignored.
Both import from `src/` directly, so no install step is needed.

---

## `probe_segevsport.py` — what does this PBP URL actually return?

Makes one HTTP GET and reports status, content type, size and timing, then
surfaces links, script sources, inline JSON blocks, iframes and endpoint-ish
strings that hint at where structured play-by-play really lives.

It hard-codes no site — pass any URL.

```powershell
.venv\Scripts\python.exe scripts\spikes\probe_segevsport.py --url https://basket.co.il/
.venv\Scripts\python.exe scripts\spikes\probe_segevsport.py --url <url> --filter pbp
.venv\Scripts\python.exe scripts\spikes\probe_segevsport.py --url <url> --json --no-save
```

Saves `response.html` and `probe.json` under `artifacts/probe/<slug>/`.

It deliberately stops at *clues*. It does not extract play-by-play and does not
reverse-engineer anything.

**Note:** the source is Hebrew and serves UTF-8 without a charset header. The
probe sniffs the encoding — if you write a new fetcher, do the same or every
team name will mojibake.

---

## `gemini_video_event.py` — classify one localized shot event

The Gate 1 / Gate 2 harness. One event at a time, so each result can be
inspected before anything runs in bulk. Fixture mode is capped by `--limit`
(default **1**) so a bulk run must be asked for explicitly.

```powershell
# Inspect the outgoing request. No API key needed, no call made, costs nothing.
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py --dry-run `
    --url "https://www.youtube.com/watch?v=XXXX" --at 1:12:30

# Confirm which model ids this key can actually use. Do this first.
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py --list-models

# Classify one ad-hoc event
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py `
    --url "https://www.youtube.com/watch?v=XXXX" --at 1:12:30 `
    --event-id G001-E017 --period 3 --clock 04:12 --team "Team A" --save

# Classify a fixture row and write the model's labels back into the CSV
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py `
    --from-fixture --event-id G001-E017 --update-fixture

# Gate 1 agreement. Reads the fixture only — no API calls.
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py --agreement
```

Useful flags: `--metrics shot_contest,possession_type` to test a subset,
`--start`/`--end` for an explicit window, `--pre-roll`/`--post-roll` to adjust
padding, `--fps` and `--media-resolution LOW` to trade detail for cost.

Exit codes: `0` ok · `1` a classification failed · `2` bad usage or config.

**Before trusting any output from this, read `docs/VIDEO_SPIKE_NOTES.md` §2** —
several integration points have not been verified against a live key, including
whether the time-window offsets are honoured at all.
