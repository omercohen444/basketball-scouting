#!/usr/bin/env python
"""Enrichment layer: full 182-game validation sweep + 3-team human preview.

Rebuilds GameEnrichment for every already-ingested game (from the cached raw
PBP + the already-validated data/processed/stats/ TeamGameStats — no new
network calls), runs the invariant checks from the enrichment brief §21, then
selects three real teams (highest / median / lowest win%) and writes a
factual Markdown preview + machine-readable JSON profile for each under
artifacts/stats_enrichment/ (git-ignored by default; not auto-committed).

Usage:
    python scripts/enrichment_validate_and_preview.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.stats.enrichment import build_game_enrichment  # noqa: E402
from basketball_scout.stats.profile import build_team_profile, build_top_wl_differentiators  # noqa: E402
from basketball_scout.stats.store import load_all_games, load_game  # noqa: E402

REGULATION_MIN = 10.0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings()
    stats_dir = settings.data_dir / "processed" / "stats"
    all_games = load_all_games(stats_dir)
    print(f"Loaded {len(all_games)} team-game rows ({len(all_games)//2} games) from {stats_dir}")

    pairs_by_team: dict[str, list] = defaultdict(list)
    anomalies: list[str] = []
    games_processed = 0

    for path in sorted(stats_dir.glob("*.json")):
        home_stats, away_stats = load_game(path)
        source_game_id = home_stats.source_game_id
        raw_path = settings.raw_pbp_dir / f"segev_{source_game_id}.json"
        if not raw_path.is_file():
            anomalies.append(f"{source_game_id}: raw cache missing, cannot re-enrich")
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))

        home_e, away_e = build_game_enrichment(
            {"gameInfo": raw["gameInfo"], "actions": raw["actions"]},
            internal_game_id=home_stats.internal_game_id, team_id=home_stats.team_id,
            opponent_id=away_stats.team_id, team_side="home", win=home_stats.win,
            game_date=home_stats.game_date, regulation_periods=home_stats.regulation_periods,
            ot_periods=home_stats.ot_periods,
        )
        games_processed += 1

        # ---- Invariant checks (enrichment brief §21) ----
        gid = source_game_id

        # B/C: shot counts and scoring mix, from the already-validated TeamGameStats
        for side_stats in (home_stats, away_stats):
            c = side_stats.components_for
            if c.fg2m + c.fg3m != c.fgm or (c.fga - c.fg3a) + c.fg3a != c.fga:
                anomalies.append(f"{gid}: shot count mismatch for {side_stats.team_id}")
            expected_pts = 2 * c.fg2m + 3 * c.fg3m + c.ftm
            if expected_pts != c.points:
                anomalies.append(f"{gid}: scoring mix mismatch for {side_stats.team_id}")

        # D/E: quarter and half additive reconciliation (FGA as the probe stat)
        total_fga = home_stats.components_for.fga
        q_sum = sum(home_e.segment_samples[("quarter", q)]["fga_n"] for q in ("Q1", "Q2", "Q3", "Q4", "OT"))
        if q_sum != total_fga:
            anomalies.append(f"{gid}: quarter FGA sum {q_sum} != game FGA {total_fga} (home)")
        half_sum = sum(home_e.segment_samples[("half", h)]["fga_n"] for h in ("1H", "2H")) + \
            home_e.segment_samples[("quarter", "OT")]["fga_n"]
        if half_sum != total_fga:
            anomalies.append(f"{gid}: half+OT FGA sum {half_sum} != game FGA {total_fga} (home)")

        # F/G: exactly one win/loss row and one home/away row (already guaranteed by
        # engine.py's construction, re-checked here as a real invariant, not assumed).
        if home_stats.win == away_stats.win:
            anomalies.append(f"{gid}: both sides show win={home_stats.win}")
        if home_stats.is_home == away_stats.is_home:
            anomalies.append(f"{gid}: both sides show is_home={home_stats.is_home}")

        # I: clutch subset of late_close subset of close_score (possession counts)
        clutch_n = home_e.segment_samples[("clutch", "clutch")]["possessions_n"]
        late_n = home_e.segment_samples[("late_close", "late_close")]["possessions_n"]
        close_n = home_e.segment_samples[("close_score", "close_score")]["possessions_n"]
        if clutch_n > late_n:
            anomalies.append(f"{gid}: clutch possessions ({clutch_n}) > late_close ({late_n})")

        # J: assisted + unassisted == FGM (unresolved assists are a separate count,
        # not part of this reconciliation — see scoring_sources.py docstring)
        if home_e.assisted.assisted_fgm + home_e.assisted.unassisted_fgm != home_stats.components_for.fgm:
            anomalies.append(f"{gid}: assisted+unassisted != FGM for home")

        # K: second-chance points never exceed team's total points
        if home_e.second_chance.second_chance_points > home_stats.components_for.points:
            anomalies.append(f"{gid}: second-chance points exceed total points (home)")

        # M: largest run cannot exceed team's final points
        if home_e.runs.largest_scoring_run_for > home_stats.final_score_for:
            anomalies.append(f"{gid}: largest run exceeds final score (home)")

        pairs_by_team[home_stats.team_id].append((home_stats, home_e))
        pairs_by_team[away_stats.team_id].append((away_stats, away_e))

    print(f"\nProcessed {games_processed} games through the enrichment layer.")
    print(f"Anomalies found: {len(anomalies)}")
    for a in anomalies[:30]:
        print(" ", a)

    print(f"\nTeams with enrichment data: {len(pairs_by_team)}")
    for team_id, pairs in sorted(pairs_by_team.items()):
        print(f"  {team_id}: {len(pairs)} games")

    # ---- Select 3 preview teams: highest / median / lowest win% ----
    records = []
    for team_id, pairs in pairs_by_team.items():
        wins = sum(1 for s, _ in pairs if s.win)
        n = len(pairs)
        records.append((team_id, pairs[0][0].team_name, wins / n if n else 0.0, wins, n - wins, n))
    records.sort(key=lambda r: (-r[2], r[0]))  # deterministic tie-break: team_id

    highest = records[0]
    lowest = records[-1]
    median_idx = len(records) // 2
    median = records[median_idx]

    print("\nPreview team selection:")
    for label, rec in (("highest win%", highest), ("median win%", median), ("lowest win%", lowest)):
        print(f"  {label}: {rec[1]} ({rec[0]}) {rec[3]}-{rec[4]}, win%={rec[2]:.3f}")

    out_dir = settings.artifacts_dir / "stats_enrichment"
    out_dir.mkdir(parents=True, exist_ok=True)

    for label, (team_id, team_name, win_pct, wins, losses, n) in (
        ("highest_win_pct", highest), ("median_win_pct", median), ("lowest_win_pct", lowest)
    ):
        pairs = pairs_by_team[team_id]
        profile = build_team_profile(team_id, pairs, window="full_season")
        recent10 = build_team_profile(team_id, pairs, window="last_10")
        recent5 = build_team_profile(team_id, pairs, window="last_5")
        home_profile = build_team_profile(team_id, pairs, window="home")
        away_profile = build_team_profile(team_id, pairs, window="away")
        wl = build_top_wl_differentiators(pairs)

        json_payload = {
            "team_id": team_id, "team_name": team_name, "selection_reason": label,
            "season": profile, "last_10": recent10, "last_5": recent5,
            "home": home_profile, "away": away_profile, "top_wl_differentiators": wl,
        }
        json_path = out_dir / f"{label}_{team_id.replace(':', '_')}.json"
        json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

        md_path = out_dir / f"{label}_{team_id.replace(':', '_')}.md"
        md_path.write_text(
            render_markdown_preview(team_name, team_id, label, profile, recent10, recent5,
                                     home_profile, away_profile, wl),
            encoding="utf-8",
        )
        print(f"\nWrote {md_path}")
        print(f"Wrote {json_path}")

    return 1 if anomalies else 0


def _fmt(v, digits=3):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return str(v)
    return f"{v:.{digits}f}"


def _metrics_table_row(label: str, m: dict) -> str:
    return (
        f"| {label} | {_fmt(m['offensive_rating'],1)} | {_fmt(m['defensive_rating'],1)} | "
        f"{_fmt(m['net_rating'],1)} | {_fmt(m['pace'],1)} | {_fmt(m['efg_pct'])} | "
        f"{_fmt(m['tov_pct'])} | {_fmt(m['orb_pct'])} | {_fmt(m['ft_rate'])} | "
        f"{_fmt(m['fg3a_rate'])} | {_fmt(m['ast_to_ratio'])} |"
    )


def render_markdown_preview(team_name, team_id, label, season, recent10, recent5, home, away, wl) -> str:
    b = season["basic"]
    lines = []
    lines.append(f"# {team_name} ({team_id}) — Statistics Enrichment Preview")
    lines.append(f"\n_Selection reason: {label.replace('_', ' ')}. Facts only — no interpretation._\n")

    lines.append("## A. Basic Profile\n")
    lines.append(f"Record: {b['wins']}-{b['losses']} ({b['games_n']} games), win% = {_fmt(b['win_pct'])}\n")
    lines.append("| | ORtg | DRtg | Net | Pace | eFG% | TOV% | ORB% | FTR | 3PAr | AST/TO |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    lines.append(_metrics_table_row("Season", b["metrics"]))

    lines.append("\n## B. Game Flow\n")
    lines.append("| | ORtg | DRtg | Net | Pace | eFG% | TOV% | ORB% | FTR | 3PAr | AST/TO | possessions_n |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for q in ("Q1", "Q2", "Q3", "Q4", "OT"):
        s = season["game_flow"]["quarters"][q]
        row = _metrics_table_row(q, s["metrics"])
        lines.append(row[:-1] + f" {s['sample'].get('possessions_n', 0)} |")
    for h in ("1H", "2H"):
        s = season["game_flow"]["halves"][h]
        row = _metrics_table_row(h, s["metrics"])
        lines.append(row[:-1] + f" {s['sample'].get('possessions_n', 0)} |")

    lines.append("\n## C. Clutch\n")
    c = season["clutch"]
    lines.append(f"games_n (games with >=1 clutch possession): {c['games_n']}, "
                 f"possessions_n: {c['sample'].get('possessions_n', 0)}\n")
    lines.append("| | ORtg | DRtg | Net | Pace | eFG% | TOV% | ORB% | FTR | 3PAr | AST/TO |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    lines.append(_metrics_table_row("Clutch", c["metrics"]))

    lines.append("\n## D. Score State\n")
    lines.append("| bin | ORtg | DRtg | Net | eFG% | TOV% | ORB% | FTR | 3PAr | AST/TO | possessions_n |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for bin_name in ("ahead_6_plus", "ahead_1_5", "tied", "behind_1_5", "behind_6_plus"):
        s = season["score_state"][bin_name]
        m = s["metrics"]
        lines.append(
            f"| {bin_name} | {_fmt(m['offensive_rating'],1)} | {_fmt(m['defensive_rating'],1)} | "
            f"{_fmt(m['net_rating'],1)} | {_fmt(m['efg_pct'])} | {_fmt(m['tov_pct'])} | "
            f"{_fmt(m['orb_pct'])} | {_fmt(m['ft_rate'])} | {_fmt(m['fg3a_rate'])} | "
            f"{_fmt(m['ast_to_ratio'])} | {s['sample'].get('possessions_n', 0)} |"
        )

    lines.append("\n## E. Recent Form\n")
    lines.append("| window | games_n | W-L | ORtg | DRtg | Net |")
    lines.append("|---|---|---|---|---|---|")
    for label_, prof in (("full_season", season), ("last_10", recent10), ("last_5", recent5)):
        bb = prof["basic"]
        lines.append(
            f"| {label_} | {bb['games_n']} | {bb['wins']}-{bb['losses']} | "
            f"{_fmt(bb['metrics']['offensive_rating'],1)} | {_fmt(bb['metrics']['defensive_rating'],1)} | "
            f"{_fmt(bb['metrics']['net_rating'],1)} |"
        )

    lines.append("\n## F. Scoring Sources (season, FOR)\n")
    ss = season["scoring_sources"]
    pot, sc, fb = ss["points_off_turnovers"], ss["second_chance"], ss["fast_break"]
    lines.append(f"- Points off turnovers: {pot['points_off_turnovers']} total "
                 f"({_fmt(pot['points_off_turnovers']/pot['games_n'] if pot['games_n'] else None,1)}/game), "
                 f"opponent turnovers forced: {pot['opponent_turnovers']}")
    lines.append(f"- Second-chance points: {sc['second_chance_points']} total, "
                 f"from {sc['offensive_rebound_possessions']} OREB possessions")
    lines.append(f"- Provider-defined fast-break points: {fb['provider_fast_break_points']} total "
                 f"(source: segev_provider_flag)")

    lines.append("\n## G. Assisted / Unassisted\n")
    a = ss["assisted"]
    lines.append(f"- Overall: {a['assisted_fgm']} assisted / {a['unassisted_fgm']} unassisted made FG")
    lines.append(f"- 2PT: {a['assisted_2pm']} assisted / {a['unassisted_2pm']} unassisted")
    lines.append(f"- 3PT: {a['assisted_3pm']} assisted / {a['unassisted_3pm']} unassisted")
    lines.append(f"- Unresolved assist links (not attributed to any shot): {a['unresolved_assist_count']}")

    lines.append("\n## H. Shot / Scoring Mix\n")
    sm = season["shot_mix"]
    lines.append(f"- 2PA share: {_fmt(sm['fg2a_share'])}, 3PA share: {_fmt(sm['fg3a_share'])}")
    lines.append(f"- Scoring share — 2PT: {_fmt(sm['scoring_share_2pt'])}, "
                 f"3PT: {_fmt(sm['scoring_share_3pt'])}, FT: {_fmt(sm['scoring_share_ft'])}")

    lines.append("\n## I. Runs / Droughts (season)\n")
    rd = season["runs_droughts"]
    lines.append(f"- Average largest run FOR: {_fmt(rd['runs']['average_largest_run_for'],1)}, "
                 f"AGAINST: {_fmt(rd['runs']['average_largest_run_against'],1)}")
    lines.append(f"- Max run FOR: {rd['runs']['max_run_for']}, AGAINST: {rd['runs']['max_run_against']}")
    lines.append(f"- Games with an 8+ run FOR: {rd['runs']['games_with_8_plus_run_for']}, "
                 f"AGAINST: {rd['runs']['games_with_8_plus_run_against']}")
    lines.append(f"- 3:00+ scoring droughts/game: {_fmt(rd['droughts']['drought_count_3m_plus_per_game'])}, "
                 f"longest: {_fmt(rd['droughts']['longest_scoring_drought_seconds_max'],0)}s")
    lines.append(f"- 3:00+ FG droughts/game: {_fmt(rd['droughts']['fg_drought_count_3m_plus_per_game'])}, "
                 f"longest: {_fmt(rd['droughts']['longest_fg_drought_seconds_max'],0)}s")

    lines.append("\n## J. Score Dynamics (season)\n")
    dy = season["dynamics"]
    lines.append(f"- Times tied/game: {_fmt(dy['times_tied_avg'],2)}, lead changes/game: {_fmt(dy['lead_changes_avg'],2)}")
    lines.append(f"- Largest lead (any game): {dy['largest_lead_max']}, largest deficit (any game): {dy['largest_deficit_max']}")
    cb = dy["comeback"]
    lines.append(f"- Games trailing by 10+: {cb['games_trailing_10_plus']}, "
                 f"comeback wins: {cb['comeback_wins_from_10_plus']}, "
                 f"conversion rate: {_fmt(cb['comeback_conversion_rate'])}")
    lines.append(f"- Games leading by 10+: {cb['games_leading_10_plus']}, "
                 f"losses after leading 10+: {cb['losses_after_leading_10_plus']}, "
                 f"blown-lead rate: {_fmt(cb['blown_10_plus_lead_rate'])}")

    lines.append("\n## K. Top W/L Differentiators (actionable metrics, ranked by |effect size|)\n")
    if wl["top_ranked"]:
        lines.append("| metric | effect size | win avg | loss avg | n (W/L) |")
        lines.append("|---|---|---|---|---|")
        for s in wl["top_ranked"]:
            lines.append(
                f"| {s['metric']} | {_fmt(s['effect_size'])} | {_fmt(s['win_average'])} | "
                f"{_fmt(s['loss_average'])} | {s['sample_wins']}/{s['sample_losses']} |"
            )
    else:
        lines.append("_No signal met the agent-rankable sample threshold "
                      "(n_wins>=3, n_losses>=3, finite non-zero pooled variance) for this team/window._")

    lines.append("\n---\n_Generated by scripts/enrichment_validate_and_preview.py. "
                 "Deterministic PBP-derived facts only; no scout interpretation._")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
