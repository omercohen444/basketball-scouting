# Scouting Report — Maccabi Tel Aviv

*Deterministic play-by-play analytics for the 2025-26 season.*

**Season** 2025-26 · **Record** 24-2 · **Games** 26 · **Source** segev (deterministic play-by-play)

> **Pack state:** no_win_loss_evidence

## Executive Summary

Maccabi Tel Aviv represents the most dominant overall force in the league, powered by an elite offense that combines high-volume perimeter shooting with exceptional ball security. They excel at protecting the ball and generating high-quality looks, while also showing a strong tendency to extend possessions on the glass. To compete, our game plan must focus on disrupting their perimeter comfort and securing defensive rebounds.

## Offensive Identity

- The opponent establishes a highly perimeter-oriented offensive style, heavily prioritizing attempts from beyond the arc over two-point field goals.  _(established)_
    - **3PA Rate** 46.3% · rank 1 of 14 · league avg 40.3% · n=26g, high reliability
    - **Share of Points from 3PT** 40.1% · rank 1 of 14 · league avg 32.5% · n=26g, high reliability
    - **Share of Points from 2PT** 42.7% · rank 14 of 14 · league avg 50.7% · n=26g, high reliability

## Strengths

- They possess an exceptionally efficient offense driven by elite effective field goal shooting.  _(established)_
    - **Offensive Rating** 130.1 · rank 1 of 14 · league avg 110.4 · n=26g, high reliability
    - **Effective FG%** 58.4% · rank 1 of 14 · league avg 53.1% · n=26g, high reliability
- They maintain exceptional control of possessions by minimizing turnovers and maintaining highly efficient ball movement.  _(established)_
    - **Turnover Rate** 12.4% · rank 1 of 14 · league avg 15.8% · n=26g, high reliability
    - **Assist/Turnover Ratio** 2.05 · rank 1 of 14 · league avg 1.40 · n=26g, high reliability
- They are highly effective at extending possessions through offensive rebounding and converting those extra opportunities into points.  _(indicated)_
    - **Offensive Rebound %** 36.1% · rank 1 of 14 · league avg 30.3% · n=26g, high reliability
    - **Points per Second-Chance Possession** 1.09 · rank 2 of 14 · league avg 0.92 · n=26g, moderate reliability
- They are the most dominant team in the league overall, driven by their league-leading net rating.  _(established)_
    - **Net Rating** 23.1 · rank 1 of 14 · league avg 0.1 · n=26g, high reliability

## Turnovers

- They maintain exceptional control of possessions by minimizing turnovers and maintaining highly efficient ball movement.  _(established)_
    - **Turnover Rate** 12.4% · rank 1 of 14 · league avg 15.8% · n=26g, high reliability
    - **Assist/Turnover Ratio** 2.05 · rank 1 of 14 · league avg 1.40 · n=26g, high reliability

## Game-Plan Priorities

**1. Implement a hard-hedging or switching defensive scheme on the perimeter to run them off the three-point line.**  _(confidence: high)_

The opponent heavily prioritizes three-point attempts over two-point field goals. Forcing them inside their preferred perimeter zone will disrupt their primary offensive identity.

- **3PA Rate** 46.3% · rank 1 of 14 · league avg 40.3% · n=26g, high reliability
- **Share of Points from 3PT** 40.1% · rank 1 of 14 · league avg 32.5% · n=26g, high reliability
- **Share of Points from 2PT** 42.7% · rank 14 of 14 · league avg 50.7% · n=26g, high reliability
- **Offensive Rating** 130.1 · rank 1 of 14 · league avg 110.4 · n=26g, high reliability
- **Effective FG%** 58.4% · rank 1 of 14 · league avg 53.1% · n=26g, high reliability

**2. Deploy a high-pressure defensive game plan to disrupt their passing lanes and force turnovers.**  _(confidence: high)_

They excel at ball security and ball movement, leading the league in assist-to-turnover ratio. We must pressure their ball handlers to disrupt this control.

- **Turnover Rate** 12.4% · rank 1 of 14 · league avg 15.8% · n=26g, high reliability
- **Assist/Turnover Ratio** 2.05 · rank 1 of 14 · league avg 1.40 · n=26g, high reliability

**3. Commit all players to defensive rebounding block-outs to limit second-chance opportunities.**  _(confidence: moderate)_

They show a strong tendency to extend possessions through offensive rebounding and scoring on second chances. We must secure the defensive glass to prevent these extra possessions.

- **Offensive Rebound %** 36.1% · rank 1 of 14 · league avg 30.3% · n=26g, high reliability
- **Points per Second-Chance Possession** 1.09 · rank 2 of 14 · league avg 0.92 · n=26g, moderate reliability

## Key Evidence

| Metric | Scope | Value | League Rank | Sample | Reliability |
|---|---|---|---|---|---|
| 3PA Rate | season | 46.3% | 1 of 14 | 26g | high |
| Share of Points from 3PT | season | 40.1% | 1 of 14 | 26g | high |
| Share of Points from 2PT | season | 42.7% | 14 of 14 | 26g | high |
| Offensive Rating | season | 130.1 | 1 of 14 | 26g | high |
| Effective FG% | season | 58.4% | 1 of 14 | 26g | high |
| Turnover Rate | season | 12.4% | 1 of 14 | 26g | high |
| Assist/Turnover Ratio | season | 2.05 | 1 of 14 | 26g | high |
| Offensive Rebound % | season | 36.1% | 1 of 14 | 26g | high |
| Points per Second-Chance Possession | season | 1.09 | 2 of 14 | 26g | moderate |
| Net Rating | season | 23.1 | 1 of 14 | 26g | high |

## Caveats

- We lack data on season-scope rim and shot-zone shares.
- Shot distance metrics are unavailable due to high uncertainty at the season scope.
- We cannot classify half-court or set-offense possession types.
- We cannot identify primary playmakers or pass origins due to missing last-passer data.
- This report is strictly team-level; no player-level or lineup analytics are available.
- No video-derived metrics, such as shot contests or on-ball pressure, are available.
- We cannot analyze the opponent's defensive schemes, coverages, or coaching intent.
- The provider fast-break flag is one-directional: a False (or missing) value means only 'the provider did not classify this as a fast break'. It never means half-court, set offense, or secondary transition.
- This metric has no inherently good or bad direction. A high or low league percentile describes style, not quality, and must not be phrased as a strength or weakness.
- Segment values are the unweighted mean of per-game segment values, so a game with very few segment possessions counts as much as a game with many.

## Not Available In This Data

- **Rim / shot-zone share (season)** — Coarse shot-zone geometry is validated only at game scope and is still provisional_deterministic pending fresh blind human labelling. Season-scope aggregation was deliberately not built for this MVP checkpoint.
- **Shot distance** — Distance carries +/-1m uncertainty (validation_state 'partial') and is not aggregated at season scope.
- **Half-court / set-offense possession type** — No half-court classification exists. The provider fast-break flag is one-directional: its absence never implies half-court or set offense.
- **Last-passer identity / pass origin** — Audit found a linked passer for only 38/62 made and 0/78 missed field-goal attempts. Not implemented.
- **Player-level and lineup analytics** — This MVP is team-level only by locked product decision. No per-player claim is supportable.
- **Video-derived metrics (shot contest, creation, on-ball pressure)** — The video layer failed reliability validation and was removed from the MVP. No video evidence exists.
- **Defensive scheme / coverage / play-calling** — Play-by-play carries no scheme, coverage, or personnel information. Any claim about switching, drop coverage, zone, or coaching intent is unsupportable.

## Validation

- Hard rejections: **0**
- Warnings: **3**

    - W-sample: rests on evidence with insufficient W/L sample: ['EV.season.efg_pct', 'EV.season.fg3a_rate', 'EV.season.offensive_rating', 'EV.season.scoring_share_2pt', 'EV.season.scoring_share_3pt']
    - W-sample: rests on evidence with insufficient W/L sample: ['EV.season.ast_to_ratio', 'EV.season.tov_pct']
    - W-sample: rests on evidence with insufficient W/L sample: ['EV.season.orb_pct', 'EV.season.points_per_second_chance_possession']

---

_Every figure above is computed deterministically from play-by-play. Agents selected, interpreted and prioritized the evidence; they did not compute it._