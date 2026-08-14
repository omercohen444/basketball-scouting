# Video spike notes — SUPERSEDED

**This document is from the bootstrap preparation run (2026-08-14) and is
superseded by [`docs/VIDEO_STAGE_PLAN.md`](VIDEO_STAGE_PLAN.md).**

Everything in this file was written before the CP0 investigation. In
particular:

- §2.2 ("Whether a YouTube URL is accepted...") and §2.3 ("Whether
  `video_metadata` offsets are honoured...") are superseded by
  `VIDEO_STAGE_PLAN.md` §3 (A16/A17) and §6 — the evidence is now much
  stronger (and more concerning: official docs omit clipping entirely, and a
  Google staff member confirmed an escalated bug report) and the verification
  method is now a deterministic single-call token test (§6.4), not the "do
  the answers change" heuristic this file described.
- §3 ("PBP extraction method still unknown") is **resolved**. The exact
  endpoint, response envelope, and action schema are documented in
  `VIDEO_STAGE_PLAN.md` §5.1, with a working client at
  `src/basketball_scout/pbp/segev.py`.
- §3 ("Clock alignment... not necessarily constant") is **superseded by a
  materially better finding**: Segev PBP actions carry real wall-clock time
  (`userTime`), which makes the mapping a constant offset per quarter rather
  than an unknown nonlinear function of every stoppage. See
  `VIDEO_STAGE_PLAN.md` §7.

For current status of what has actually been verified against live systems
this stage, see `artifacts/cp1/cp1_report.md` (and later `cp2_report.md`,
`cp3_report.md`, `cp4_report.md` as those checkpoints execute).

This file is kept for history; do not treat any claim in it as current.
