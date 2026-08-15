#!/usr/bin/env python
"""Stats Enrichment v2: league-context/stability/recent-delta/evidence
validation sweep + 3-team preview + league QA summary.

Rebuilds enrichment for every already-ingested game (from cache, no new
network calls), builds each team's league-relative evidence for a bounded,
high-value (metric, segment) set, checks v2 invariants across all 182
games/14 teams, and writes JSON + Markdown previews for the same
deterministic three-team selection as v1 under
artifacts/stats_enrichment_v2/ (git-ignored).

Usage:
    python scripts/enrichment_v2_validate_and_preview.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.stats.enrichment import build_game_enrichment  # noqa: E402
from basketball_scout.stats.evidence import build_evidence, compute_signal_flags  # noqa: E402
from basketball_scout.stats.possession import build_possessions  # noqa: E402
from basketball_scout.stats.possession_context import (  # noqa: E402
    build_after_own_timeout_profile,
    build_run_response_profile,
)
from basketball_scout.stats.profile import select_window  # noqa: E402
from basketball_scout.stats.scoring_timeline import build_scoring_timeline  # noqa: E402
from basketball_scout.stats.store import load_game  # noqa: E402
from basketball_scout.stats.turnover_taxonomy import build_turnover_taxonomy  # noqa: E402

# Bounded (metric, segment_type, segment_value, direction) set — high-value,
# not a Cartesian explosion. Season-level ten metrics + a handful of the
# most scouting-relevant segment cuts.
_SEASON_METRICS = [
    ("offensive_rating", "higher_is_better"), ("defensive_rating", "lower_is_better"),
    ("net_rating", "higher_is_better"), ("pace", "neutral"), ("efg_pct", "higher_is_better"),
    ("tov_pct", "lower_is_better"), ("orb_pct", "higher_is_better"), ("ft_rate", "neutral"),
    ("fg3a_rate", "neutral"), ("ast_to_ratio", "higher_is_better"),
]
_SEGMENT_CUTS = [
    ("clutch", "clutch", "efg_pct", "higher_is_better"),
    ("clutch", "clutch", "tov_pct", "lower_is_better"),
    ("quarter", "Q4", "net_rating", "higher_is_better"),
    ("score_state", "behind_6_plus", "efg_pct", "higher_is_better"),
    ("half", "1H", "net_rating", "higher_is_better"),
]


def season_extractor(field):
    return lambda s, e: getattr(s.metrics, field)


def segment_extractor(seg_type, seg_value, field):
    def _extract(s, e):
        m = e.segment_metrics.get((seg_type, seg_value))
        sample = e.segment_samples.get((seg_type, seg_value), {})
        if m is None or sample.get("possessions_n", 0) == 0:
            return None
        return getattr(m, field)
    return _extract


def build_all_evidence(team_id: str, league_pairs: dict) -> list[dict]:
    out = []
    for field, direction in _SEASON_METRICS:
        ev = build_evidence(team_id, field, "season", "season", "for",
                             season_extractor(field), league_pairs, direction=direction,
                             use_canonical_aggregate=True)
        flags = compute_signal_flags(ev)
        out.append({"evidence": ev.to_dict(), "flags": flags.to_dict()})
    for seg_type, seg_value, field, direction in _SEGMENT_CUTS:
        ev = build_evidence(team_id, field, seg_type, seg_value, "for",
                             segment_extractor(seg_type, seg_value, field), league_pairs, direction=direction)
        flags = compute_signal_flags(ev)
        out.append({"evidence": ev.to_dict(), "flags": flags.to_dict()})
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings()
    stats_dir = settings.data_dir / "processed" / "stats"

    league_pairs: dict[str, list] = defaultdict(list)
    extra_by_team: dict[str, dict] = defaultdict(lambda: {"run_response": [], "after_timeout": [], "tov_taxonomy": defaultdict(int)})

    for path in sorted(stats_dir.glob("*.json")):
        home_stats, away_stats = load_game(path)
        raw_path = settings.raw_pbp_dir / f"segev_{home_stats.source_game_id}.json"
        if not raw_path.is_file():
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        actions = raw["actions"]

        home_e, away_e = build_game_enrichment(
            {"gameInfo": raw["gameInfo"], "actions": actions},
            internal_game_id=home_stats.internal_game_id, team_id=home_stats.team_id,
            opponent_id=away_stats.team_id, team_side="home", win=home_stats.win,
            game_date=home_stats.game_date, regulation_periods=home_stats.regulation_periods,
            ot_periods=home_stats.ot_periods,
        )
        league_pairs[home_stats.team_id].append((home_stats, home_e))
        league_pairs[away_stats.team_id].append((away_stats, away_e))

        poss_result = build_possessions(actions, regulation_periods=home_stats.regulation_periods)
        plays = build_scoring_timeline(actions)
        home_poss = [p for p in poss_result.possessions if p.offense_team == "home"]
        away_poss = [p for p in poss_result.possessions if p.offense_team == "away"]

        extra_by_team[home_stats.team_id]["run_response"].append(
            build_run_response_profile(home_poss, plays, team_side="home"))
        extra_by_team[away_stats.team_id]["run_response"].append(
            build_run_response_profile(away_poss, plays, team_side="away"))
        extra_by_team[home_stats.team_id]["after_timeout"].append(
            build_after_own_timeout_profile(actions, poss_result.possessions, team_side="home"))
        extra_by_team[away_stats.team_id]["after_timeout"].append(
            build_after_own_timeout_profile(actions, poss_result.possessions, team_side="away"))

        home_tov, away_tov = build_turnover_taxonomy(actions)
        for k, v in home_tov.items():
            extra_by_team[home_stats.team_id]["tov_taxonomy"][k] += v
        for k, v in away_tov.items():
            extra_by_team[away_stats.team_id]["tov_taxonomy"][k] += v

    print(f"Built league data for {len(league_pairs)} teams.")

    # ---- v2 invariant checks -----------------------------------------
    anomalies: list[str] = []
    for field, direction in _SEASON_METRICS:
        for team_id, pairs in league_pairs.items():
            ev = build_evidence(team_id, field, "season", "season", "for",
                                 season_extractor(field), league_pairs, direction=direction,
                                 use_canonical_aggregate=True)
            if ev.league_context is not None:
                p = ev.league_context.percentile
                if p is not None and not (0.0 <= p <= 100.0):
                    anomalies.append(f"{team_id}/{field}: percentile {p} out of [0,100]")
                r = ev.league_context.rank
                if r is not None and not (1 <= r <= ev.league_context.eligible_teams):
                    anomalies.append(f"{team_id}/{field}: rank {r} out of [1,{ev.league_context.eligible_teams}]")
            for label, val in (("value", ev.value), ("std", ev.stability.std),
                               ("last5_minus_season", ev.recent.last5_minus_season)):
                if val is not None and (math.isnan(val) or math.isinf(val)):
                    anomalies.append(f"{team_id}/{field}: {label} is NaN/Inf")
            if ev.recent.last5_minus_season is not None:
                expected = ev.recent.last_5_value - ev.recent.season_value
                if abs(ev.recent.last5_minus_season - expected) > 1e-9:
                    anomalies.append(f"{team_id}/{field}: delta does not equal component difference")
            if ev.stability.games != len(select_window(pairs, "full_season")):
                # Some games can be excluded (None value) -- must be <=, not necessarily ==
                if ev.stability.games > len(pairs):
                    anomalies.append(f"{team_id}/{field}: stability n exceeds total games")

    # OREB consequence totals bounded by possession totals; response/timeout no double count.
    for team_id, pairs in league_pairs.items():
        for stats, e in pairs:
            sc = e.second_chance
            if sc.fga_after_oreb > stats.components_for.fga:
                anomalies.append(f"{team_id}/{stats.internal_game_id}: fga_after_oreb exceeds game FGA")
            if sc.second_chance_points > stats.components_for.points:
                anomalies.append(f"{team_id}/{stats.internal_game_id}: second_chance_points exceeds game points")

    for team_id, extra in extra_by_team.items():
        rr_total_responses = sum(r.responses_found for r in extra["run_response"])
        rr_total_crossings = sum(r.opponent_runs_conceded for r in extra["run_response"])
        if rr_total_responses > rr_total_crossings:
            anomalies.append(f"{team_id}: run-response found more responses than crossings")
        ato_total = sum(a.after_timeout_possessions_found for a in extra["after_timeout"])
        ato_timeouts = sum(a.own_timeouts_with_team for a in extra["after_timeout"])
        if ato_total > ato_timeouts:
            anomalies.append(f"{team_id}: after-timeout possessions exceed own timeouts with team")

    print(f"v2 invariant anomalies: {len(anomalies)}")
    for a in anomalies[:30]:
        print(" ", a)

    # ---- Preview team selection (identical rule to v1) ------------------
    records = []
    for team_id, pairs in league_pairs.items():
        wins = sum(1 for s, _ in pairs if s.win)
        n = len(pairs)
        records.append((team_id, pairs[0][0].team_name, wins / n if n else 0.0, wins, n - wins, n))
    records.sort(key=lambda r: (-r[2], r[0]))
    highest, lowest = records[0], records[-1]
    median = records[len(records) // 2]

    print("\nPreview selection:")
    for label, rec in (("highest", highest), ("median-ish", median), ("lowest", lowest)):
        print(f"  {label}: {rec[1]} ({rec[0]}) {rec[3]}-{rec[4]}")

    out_dir = settings.artifacts_dir / "stats_enrichment_v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    league_qa = {
        "teams": len(league_pairs),
        "games_per_team": {tid: len(p) for tid, p in league_pairs.items()},
        "invariant_anomalies": len(anomalies),
        "anomaly_samples": anomalies[:20],
    }
    (out_dir / "league_qa_summary.json").write_text(json.dumps(league_qa, indent=2), encoding="utf-8")
    print(f"\nWrote {out_dir / 'league_qa_summary.json'}")

    for label, (team_id, team_name, win_pct, wins, losses, n) in (
        ("highest_win_pct", highest), ("median_win_pct", median), ("lowest_win_pct", lowest)
    ):
        evidence_list = build_all_evidence(team_id, league_pairs)
        extra = extra_by_team[team_id]
        rr = extra["run_response"]
        ato = extra["after_timeout"]
        rr_summary = {
            "opponent_runs_conceded": sum(r.opponent_runs_conceded for r in rr),
            "responses_found": sum(r.responses_found for r in rr),
            "response_points": sum(r.response_points for r in rr),
        }
        ato_summary = {
            "own_timeouts_with_team": sum(a.own_timeouts_with_team for a in ato),
            "after_timeout_possessions_found": sum(a.after_timeout_possessions_found for a in ato),
            "points": sum(a.points for a in ato),
            "fga": sum(a.fga for a in ato),
            "fgm": sum(a.fgm for a in ato),
        }
        tov_taxonomy = dict(extra["tov_taxonomy"])

        json_payload = {
            "team_id": team_id, "team_name": team_name, "selection_reason": label,
            "record": f"{wins}-{losses}", "win_pct": win_pct,
            "evidence": evidence_list,
            "run_response_summary": rr_summary,
            "after_timeout_summary": ato_summary,
            "turnover_taxonomy": tov_taxonomy,
        }
        json_path = out_dir / f"{label}_{team_id.replace(':', '_')}_v2.json"
        json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

        md_lines = [f"# {team_name} ({team_id}) — v2 Evidence Preview\n",
                    f"_Selection: {label.replace('_', ' ')}. Record {wins}-{losses}. Facts only._\n"]
        md_lines.append("## League-Relative Context (season metrics)\n")
        md_lines.append("_`value` = canonical volume-weighted season aggregate "
                         "(sum of raw components, not a per-game average) — see Stability below "
                         "for the separate per-game-mean distribution statistic._\n")
        md_lines.append("| metric | value | league mean | rank | percentile | direction |")
        md_lines.append("|---|---|---|---|---|---|")
        for item in evidence_list[:len(_SEASON_METRICS)]:
            ev = item["evidence"]
            lc = ev["league_context"] or {}
            md_lines.append(
                f"| {ev['metric']} | {ev['value']:.3f} | {lc.get('league_mean', float('nan')):.3f} | "
                f"{lc.get('rank','n/a')}/{lc.get('eligible_teams','n/a')} | "
                f"{lc.get('percentile', float('nan')):.1f} | {lc.get('direction')} |"
            )
        md_lines.append("\n## Stability & Recent Shift (season metrics)\n")
        md_lines.append("_`mean` here = unweighted average of each game's own ratio "
                         "(distribution statistic, not the season aggregate above)._\n")
        md_lines.append("| metric | mean | std | last5 | last5-season | recent_shift flag |")
        md_lines.append("|---|---|---|---|---|---|")
        for item in evidence_list[:len(_SEASON_METRICS)]:
            ev, flags = item["evidence"], item["flags"]
            st, rc = ev["stability"], ev["recent"]
            std_s = f"{st['std']:.3f}" if st["std"] is not None else "n/a"
            l5 = f"{rc['last_5_value']:.3f}" if rc["last_5_value"] is not None else "n/a"
            d5 = f"{rc['last5_minus_season']:+.3f}" if rc["last5_minus_season"] is not None else "n/a"
            md_lines.append(f"| {ev['metric']} | {st['mean']:.3f} | {std_s} | {l5} | {d5} | {flags['recent_shift']} |")
        md_lines.append("\n## Segment Cuts (clutch / Q4 / behind_6+ / 1H)\n")
        md_lines.append("| segment | metric | value | percentile | win avg | loss avg | effect | win_loss flag |")
        md_lines.append("|---|---|---|---|---|---|---|---|")
        for item in evidence_list[len(_SEASON_METRICS):]:
            ev, flags = item["evidence"], item["flags"]
            lc = ev["league_context"] or {}
            wl = ev["win_loss"]
            pct = f"{lc.get('percentile'):.1f}" if lc.get("percentile") is not None else "n/a"
            wa = f"{wl['win_average']:.3f}" if wl["win_average"] is not None else "n/a"
            la = f"{wl['loss_average']:.3f}" if wl["loss_average"] is not None else "n/a"
            eff = f"{wl['effect_size']:.3f}" if wl["effect_size"] is not None else "n/a"
            md_lines.append(
                f"| {ev['segment_type']}:{ev['segment_value']} | {ev['metric']} | {ev['value']:.3f} | "
                f"{pct} | {wa} | {la} | {eff} | {flags['win_loss_signal']} |"
            )
        md_lines.append("\n## Run-Response (after opponent 8+ run)\n")
        md_lines.append(f"- Opponent qualifying runs conceded: {rr_summary['opponent_runs_conceded']}")
        md_lines.append(f"- Responses found (possession-level, truncation excluded): {rr_summary['responses_found']}")
        md_lines.append(f"- Points scored on those response possessions: {rr_summary['response_points']}")
        md_lines.append("\n## After Own Timeout\n")
        md_lines.append(f"- Own timeouts with team attribution: {ato_summary['own_timeouts_with_team']}")
        md_lines.append(f"- Matched offensive possessions: {ato_summary['after_timeout_possessions_found']}")
        md_lines.append(f"- Points / FGA / FGM on those possessions: {ato_summary['points']} / {ato_summary['fga']} / {ato_summary['fgm']}")
        md_lines.append("\n## Turnover Taxonomy (season, provider categories verbatim)\n")
        for k, v in sorted(tov_taxonomy.items(), key=lambda kv: -kv[1]):
            md_lines.append(f"- {k}: {v}")
        md_lines.append("\n---\n_Generated by scripts/enrichment_v2_validate_and_preview.py. Facts only._")

        md_path = out_dir / f"{label}_{team_id.replace(':', '_')}_v2.md"
        md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        print(f"Wrote {md_path}")
        print(f"Wrote {json_path}")

    return 1 if anomalies else 0


if __name__ == "__main__":
    raise SystemExit(main())
