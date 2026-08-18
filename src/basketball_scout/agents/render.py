"""Deterministic rendering — attaches the canonical numbers to agent prose.

This is the module that makes "the LLM performs no arithmetic" literally true.
Agents emit qualitative prose plus references; everything numeric in the final
report is looked up here, from the pack, at render time.

``key_evidence`` is *computed*, never model-authored: it is the expansion of
every ``implication_ref`` the report cites. That is why the report schema has no
``key_evidence`` field for a model to fill in incorrectly.
"""

from __future__ import annotations

from typing import Any

from .evidence_pack import format_rank
from .schemas import (
    EvidenceItem,
    EvidencePack,
    Recommendation,
    ReportClaim,
    ScoutingReport,
    TacticalOutput,
    TriageOutput,
    ValidationResult,
)


def evidence_summary(item: EvidenceItem) -> dict[str, Any]:
    """The display form of one evidence item — every number pre-formatted."""
    summary: dict[str, Any] = {
        "evidence_id": item.evidence_id,
        "metric": item.metric_label,
        "scope": item.scope,
        "value": item.display_value,
        "league_rank": format_rank(item.league_rank, item.eligible_teams),
        "league_percentile": round(item.league_percentile, 1) if item.league_percentile is not None else None,
        "league_average": item.league_mean_display,
        "sample_games": item.sample_games,
        "reliability": item.reliability_tier,
        "validation_state": item.validation_state,
        "direction": item.direction,
    }
    if item.sample_possessions is not None:
        summary["sample_possessions"] = item.sample_possessions
    if item.win_loss.agent_rankable:
        summary["win_loss"] = {
            "in_wins": item.win_loss.win_average_display,
            "in_losses": item.win_loss.loss_average_display,
            "effect_size": round(item.win_loss.effect_size, 2) if item.win_loss.effect_size is not None else None,
            "favorable_in_wins": item.win_loss.favorable_in_wins,
            "sample": f"{item.win_loss.sample_wins}W / {item.win_loss.sample_losses}L",
        }
    else:
        summary["win_loss"] = {"available": False, "reason": item.win_loss.effect_status}
    if item.limitation_codes:
        summary["limitations"] = item.limitation_codes
    return summary


def _expand(
    implication_refs: list[str], pack: EvidencePack, tactical: TacticalOutput
) -> tuple[list[dict[str, Any]], list[str]]:
    """implication ids -> (evidence summaries, resolved claim strengths)."""
    index = pack.index()
    by_implication = {i.implication_id: i for i in tactical.implications}
    seen: list[str] = []
    summaries: list[dict[str, Any]] = []
    strengths: list[str] = []

    for ref in implication_refs:
        imp = by_implication.get(ref)
        if imp is None:
            continue
        strengths.append(imp.resolved_claim_strength or imp.proposed_claim_strength)
        for eid in imp.supports_refs:
            item = index.get(eid)
            if item is not None and eid not in seen:
                seen.append(eid)
                summaries.append(evidence_summary(item))
    return summaries, strengths


def _render_claim(claim: ReportClaim, pack: EvidencePack, tactical: TacticalOutput) -> dict[str, Any]:
    evidence, strengths = _expand(claim.implication_refs, pack, tactical)
    return {
        "text": claim.text,
        "claim_strength": min(strengths, key=_strength_rank) if strengths else "speculative",
        "implication_refs": claim.implication_refs,
        "evidence": evidence,
    }


def _strength_rank(strength: str) -> int:
    return {"speculative": 0, "indicated": 1, "established": 2}.get(strength, 0)


def _render_recommendation(
    rec: Recommendation, pack: EvidencePack, tactical: TacticalOutput
) -> dict[str, Any]:
    evidence, _ = _expand(rec.implication_refs, pack, tactical)
    return {
        "recommendation_id": rec.recommendation_id,
        "priority": rec.priority,
        "directive": rec.directive,
        "rationale": rec.rationale,
        "confidence": rec.confidence,
        "implication_refs": rec.implication_refs,
        "evidence": evidence,
    }


def render_report(
    pack: EvidencePack,
    triage: TriageOutput,
    tactical: TacticalOutput,
    report: ScoutingReport,
    validation: ValidationResult,
) -> dict[str, Any]:
    """The final machine-readable artifact — the thing FastAPI/PDF will consume."""
    sections = {
        name: [_render_claim(c, pack, tactical) for c in claims]
        for name, claims in (
            ("offensive_identity", report.offensive_identity),
            ("strengths", report.strengths),
            ("vulnerabilities", report.vulnerabilities),
            ("transition_notes", report.transition_notes),
            ("turnover_notes", report.turnover_notes),
        )
    }

    cited: list[str] = []
    for claim in report.all_claims():
        cited.extend(claim.implication_refs)
    for rec in report.recommendations:
        cited.extend(rec.implication_refs)
    key_evidence, _ = _expand(list(dict.fromkeys(cited)), pack, tactical)

    caveats = list(report.caveats)
    for code in sorted({c for i in pack.evidence for c in i.limitation_codes}):
        text = pack.limitation_legend.get(code)
        if text and text not in caveats:
            caveats.append(text)

    return {
        "report_id": report.report_id,
        "team_id": report.team_id,
        "team_name": report.team_name,
        "scope_note": report.scope_note,
        "generated_from": {
            "pack_id": pack.pack_id,
            "season": pack.season,
            "record": f"{pack.wins}-{pack.losses}",
            "games_n": pack.games_n,
            "date_range": pack.date_range,
            "definition_version": pack.definition_version,
            "pack_states": pack.pack_states,
            "source": pack.source,
        },
        "executive_summary": report.executive_summary,
        "sections": sections,
        "recommendations": [_render_recommendation(r, pack, tactical) for r in report.recommendations],
        "key_evidence": key_evidence,
        "caveats": caveats,
        "unavailable_evidence": [
            {"id": u.evidence_id, "label": u.label, "reason": u.reason}
            for u in pack.unavailable_evidence
        ],
        "validation": {
            "ok": validation.ok,
            "rejects": [f.model_dump(mode="json") for f in validation.rejects],
            "warnings": [f.model_dump(mode="json") for f in validation.warnings],
        },
    }


# ---- markdown ---------------------------------------------------------------

_SECTION_TITLES = {
    "offensive_identity": "Offensive Identity",
    "strengths": "Strengths",
    "vulnerabilities": "Vulnerabilities",
    "transition_notes": "Transition",
    "turnover_notes": "Turnovers",
}


def _evidence_line(ev: dict[str, Any]) -> str:
    bits = [f"**{ev['metric']}** {ev['value']}"]
    if ev.get("league_rank"):
        bits.append(f"rank {ev['league_rank']}")
    if ev.get("league_average"):
        bits.append(f"league avg {ev['league_average']}")
    wl = ev.get("win_loss") or {}
    if wl.get("in_wins"):
        bits.append(f"W {wl['in_wins']} / L {wl['in_losses']} (d={wl['effect_size']})")
    bits.append(f"n={ev['sample_games']}g, {ev['reliability']} reliability")
    return " · ".join(bits)


def render_markdown(rendered: dict[str, Any]) -> str:
    """Human-readable form. Deliberately plain Markdown — it is the bridge to the
    later PDF stage, which will restyle rather than restructure it."""
    src = rendered["generated_from"]
    out: list[str] = [
        f"# Scouting Report — {rendered['team_name']}",
        "",
        f"*{rendered['scope_note']}*",
        "",
        f"**Season** {src['season']} · **Record** {src['record']} · "
        f"**Games** {src['games_n']} · **Source** {src['source']} (deterministic play-by-play)",
        "",
    ]
    if src["pack_states"]:
        out += [f"> **Pack state:** {', '.join(src['pack_states'])}", ""]

    out += ["## Executive Summary", "", rendered["executive_summary"], ""]

    for key, title in _SECTION_TITLES.items():
        claims = rendered["sections"].get(key) or []
        if not claims:
            continue
        out += [f"## {title}", ""]
        for claim in claims:
            out.append(f"- {claim['text']}  _({claim['claim_strength']})_")
            for ev in claim["evidence"]:
                out.append(f"    - {_evidence_line(ev)}")
        out.append("")

    out += ["## Game-Plan Priorities", ""]
    for rec in sorted(rendered["recommendations"], key=lambda r: r["priority"]):
        out += [
            f"**{rec['priority']}. {rec['directive']}**  _(confidence: {rec['confidence']})_",
            "",
            f"{rec['rationale']}",
            "",
        ]
        for ev in rec["evidence"]:
            out.append(f"- {_evidence_line(ev)}")
        out.append("")

    out += ["## Key Evidence", "", "| Metric | Scope | Value | League Rank | Sample | Reliability |", "|---|---|---|---|---|---|"]
    for ev in rendered["key_evidence"]:
        out.append(
            f"| {ev['metric']} | {ev['scope']} | {ev['value']} | {ev['league_rank'] or '—'} "
            f"| {ev['sample_games']}g | {ev['reliability']} |"
        )
    out.append("")

    if rendered["caveats"]:
        out += ["## Caveats", ""] + [f"- {c}" for c in rendered["caveats"]] + [""]

    out += ["## Not Available In This Data", ""]
    for na in rendered["unavailable_evidence"]:
        out.append(f"- **{na['label']}** — {na['reason']}")
    out.append("")

    val = rendered["validation"]
    out += [
        "## Validation",
        "",
        f"- Hard rejections: **{len(val['rejects'])}**",
        f"- Warnings: **{len(val['warnings'])}**",
        "",
    ]
    for w in val["warnings"]:
        out.append(f"    - {w['rule']}: {w['message']}")
    out += ["", "---", "", "_Every figure above is computed deterministically from play-by-play. "
            "Agents selected, interpreted and prioritized the evidence; they did not compute it._"]
    return "\n".join(out)
