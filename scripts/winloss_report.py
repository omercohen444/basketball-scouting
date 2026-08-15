#!/usr/bin/env python
"""Print the deterministic win-vs-loss signal report for one team.

    python scripts/winloss_report.py --team-id segev:2

Reads every processed game under ``--stats-dir`` (default
``data/processed/stats``), filters to the given ``team_id``
(provider-qualified, e.g. ``segev:2`` — see stats/engine.py for how it's
built), and prints:

1. the full ten-metric overview (win/loss averages, raw difference) —
   outcome/context metrics (ORtg/DRtg/Net Rating/Pace) included, for context;
2. the default scouting signal ranking — ACTIONABLE metrics only, ranked by
   standardized effect size, not raw difference. See stats/winloss.py's
   module docstring for why.

Pure deterministic Python; no LLM involved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.stats.store import load_all_games  # noqa: E402
from basketball_scout.stats.winloss import compute_signals, games_for_team, rank_actionable_signals  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team-id", required=True, help='Provider-qualified team id, e.g. "segev:2".')
    parser.add_argument("--stats-dir", default="data/processed/stats")
    return parser


def _fmt(value: float | None, fmt: str = ".2f") -> str:
    return format(value, fmt) if value is not None else "n/a"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings()
    raw_stats_dir = Path(args.stats_dir)
    stats_dir = raw_stats_dir if raw_stats_dir.is_absolute() else (settings.data_dir.parent / raw_stats_dir).resolve()

    all_games = load_all_games(stats_dir)
    team_games = games_for_team(all_games, args.team_id)
    if not team_games:
        print(f"No processed games found for team_id={args.team_id!r} under {stats_dir}", file=sys.stderr)
        return 1

    team_name = team_games[0].team_name
    wins = sum(1 for g in team_games if g.win)
    losses = len(team_games) - wins
    print(f"{team_name} ({args.team_id}) — {len(team_games)} games: {wins}-{losses}\n")

    all_signals = compute_signals(team_games)

    print("Overview — all ten metrics (win/loss averages, raw units):\n")
    header = f"{'metric':<18} {'category':<15} {'win avg':>10} {'loss avg':>10} {'raw diff':>10} {'W/L n':>8}"
    print(header)
    print("-" * len(header))
    for s in all_signals:
        print(
            f"{s.metric:<18} {s.category:<15} {_fmt(s.win_average):>10} {_fmt(s.loss_average):>10} "
            f"{_fmt(s.difference, '+.2f'):>10} {s.sample_wins:>3}/{s.sample_losses:<3}"
        )

    print(
        "\nDefault scouting signal ranking — ACTIONABLE factors only, ranked by "
        "|standardized effect| (not raw difference; outcome/context metrics like "
        "Net Rating are excluded — see winloss.py module docstring):\n"
    )
    ranked = rank_actionable_signals(team_games)
    header2 = f"{'metric':<14} {'effect':>8} {'raw diff':>10} {'win avg':>10} {'loss avg':>10} {'W/L n':>8}  sufficient  note"
    print(header2)
    print("-" * len(header2))
    for s in ranked:
        note = s.effect_note or ""
        print(
            f"{s.metric:<14} {_fmt(s.effect_size, '+.3f'):>8} {_fmt(s.difference, '+.3f'):>10} "
            f"{_fmt(s.win_average):>10} {_fmt(s.loss_average):>10} {s.sample_wins:>3}/{s.sample_losses:<3}  "
            f"{str(s.sample_sufficient):<10}  {note}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
