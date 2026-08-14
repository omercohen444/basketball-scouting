# Basketball Analytics and AI Scouting System

A web-based analytics and AI scouting system for the Israeli Basketball Premier
League. A user selects an opponent and sees historical team analytics, league
rankings, wins-vs-losses signals and separately-presented video-derived metrics,
then generates an AI scouting report as a PDF.

```
League data + video → data processing → team analytics → wins/losses analysis
  → AI scouting workflow → website → PDF scouting report
```

**Status: preparation complete. Currently at the Video Analytics risk stage
(Gate 0).** Nothing downstream of the video spike is built yet.

| Document | What it covers |
|---|---|
| [`PROJECT_SPEC.md`](PROJECT_SPEC.md) | What is being built — locked, provisional, stretch, out of scope |
| [`BUILD_PLAN.md`](BUILD_PLAN.md) | Build order and the Video Risk Day gates |
| [`WORKLOG.md`](WORKLOG.md) | Running log of what each session did |
| [`CLAUDE.md`](CLAUDE.md) | Operating instructions for Claude Code sessions |
| [`docs/VIDEO_SPIKE_NOTES.md`](docs/VIDEO_SPIKE_NOTES.md) | Gemini integration: what is verified, what is not |

---

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env      # then fill in GEMINI_API_KEY
```

Dependencies are deliberately minimal. Later stages (CrewAI, FastAPI, Supabase,
PDF rendering) add their own when those stages begin.

```powershell
.venv\Scripts\python.exe -m pytest
```

Tests run with no credentials and no network.

---

## Layout

```
src/basketball_scout/   library code (config, video pipeline)
scripts/spikes/         diagnostic harnesses for the current risk stage
tests/                  offline smoke tests
data/raw/               source dumps            (git-ignored)
data/processed/         derived data            (git-ignored)
data/validation/        small fixtures          (tracked)
docs/                   design and verification notes
artifacts/              spike output            (git-ignored)
```

## Current tooling

```powershell
# Diagnose a play-by-play source URL
.venv\Scripts\python.exe scripts\spikes\probe_segevsport.py --url https://basket.co.il/

# Inspect a Gemini video request without spending a call
.venv\Scripts\python.exe scripts\spikes\gemini_video_event.py --dry-run `
    --url "https://www.youtube.com/watch?v=XXXX" --at 1:12:30
```

See [`scripts/spikes/README.md`](scripts/spikes/README.md) for the full harness
documentation.

---

Secrets live in `.env` (git-ignored). `.env.example` holds empty placeholders
only. Never commit a key.
