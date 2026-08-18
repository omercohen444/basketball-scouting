"""Prompt construction — provider-agnostic by design.

Mirrors the ``video/prompts.py`` precedent: nothing here knows about CrewAI,
LiteLLM or Gemini, so swapping the backend touches ``crew.py`` only.

Split follows the usual static/dynamic line: the *system* prompt carries the
role and the hard prohibitions (identical every run, cacheable), the *task*
prompt carries this team's evidence and the specific ask.

The prohibitions are stated in the prompt **and** enforced in ``validation.py``.
That duplication is deliberate — the prompt is how we get good output, the
validator is how we guarantee it.
"""

from __future__ import annotations

import json
from typing import Any

from .schemas import (
    PACK_STATE_NO_WIN_LOSS,
    EvidenceItem,
    EvidencePack,
    TacticalOutput,
    TriageOutput,
)

# ---- shared rules -----------------------------------------------------------

_HARD_RULES = """
NON-NEGOTIABLE RULES (violating any of these invalidates your entire output):

1. NEVER state a number. Not a percentage, not a rank, not a count, not a score.
   Numbers are attached automatically afterwards from the evidence you cite.
   Write "a top-three share of their attempts", never "38.5% of their attempts".
2. NEVER invent, rename, or reference a statistic that is not in the evidence you
   were given. If it is not there, you do not know it.
3. NEVER make a claim about an individual player, a lineup, or personnel. This
   dataset is team-level only.
4. NEVER describe a defensive scheme, coverage, or coaching intent (switching,
   drop coverage, zone, "they want to..."). Play-by-play carries no such
   information.
5. NEVER refer to video, film, or footage. None exists for this opponent.
6. Items listed under UNAVAILABLE are things this dataset genuinely cannot see.
   You may acknowledge them as limitations. You may never use them as support,
   and you may never fill the gap from general basketball knowledge.
7. Every claim must cite the evidence it rests on, by id.
""".strip()

_NEUTRAL_RULE = """
A metric marked direction=neutral (pace, free throw rate, three-point attempt
rate, scoring shares) has NO inherently good or bad end. Describe it as style,
never as a strength or a weakness.
""".strip()

_NO_WL_RULE = """
CRITICAL — THIS TEAM HAS NO USABLE WIN/LOSS EVIDENCE. Their record is too
lopsided for a statistically meaningful wins-versus-losses comparison, so all
such evidence has been withheld. You must NOT write anything of the form "better
in wins", "in their losses", "what separates their wins", or any equivalent.
Describe them relative to the LEAGUE instead.
""".strip()


def _item_brief(item: EvidenceItem) -> dict[str, Any]:
    """Compact serialization — nulls dropped, limitations referenced by code.

    Keeps the pack inside a sane prompt budget without hiding anything the agent
    needs to judge weight."""
    brief: dict[str, Any] = {
        "id": item.evidence_id,
        "metric": item.metric_label,
        "scope": item.scope,
        "value": item.display_value,
        "direction": item.direction,
        "reliability": item.reliability_tier,
        "sample_games": item.sample_games,
    }
    if item.league_rank is not None:
        brief["league_rank"] = f"{item.league_rank} of {item.eligible_teams}"
    if item.league_percentile is not None:
        brief["league_percentile"] = round(item.league_percentile)
    if item.league_mean_display:
        brief["league_average"] = item.league_mean_display
    if item.win_loss.agent_rankable:
        brief["win_loss"] = {
            "in_wins": item.win_loss.win_average_display,
            "in_losses": item.win_loss.loss_average_display,
            "effect_size": round(item.win_loss.effect_size, 2) if item.win_loss.effect_size else None,
            "favorable_in_wins": item.win_loss.favorable_in_wins,
        }
    flags = {k: v for k, v in item.flags.model_dump().items() if v}
    if flags:
        brief["flags"] = sorted(flags)
    if item.limitation_codes:
        brief["limitations"] = item.limitation_codes
    if item.validation_state != "validated_deterministic":
        brief["validation_state"] = item.validation_state
    return brief


def _unavailable_brief(pack: EvidencePack) -> list[dict[str, str]]:
    return [{"id": u.evidence_id, "label": u.label, "reason": u.reason} for u in pack.unavailable_evidence]


def _team_header(pack: EvidencePack) -> str:
    lines = [
        f"OPPONENT: {pack.team_name} ({pack.team_id})",
        f"SEASON: {pack.season} · RECORD: {pack.wins}-{pack.losses} · GAMES: {pack.games_n}",
        "SOURCE: deterministic play-by-play analytics. No video.",
    ]
    if PACK_STATE_NO_WIN_LOSS in pack.pack_states:
        lines.append("")
        lines.append(_NO_WL_RULE)
    return "\n".join(lines)


def _json_block(label: str, payload: Any) -> str:
    return f"{label}:\n```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```"


# ---- agent 1: evidence triage ----------------------------------------------

TRIAGE_ROLE = "Basketball Evidence Triage Analyst"
TRIAGE_GOAL = (
    "Narrow a pre-ranked set of deterministic statistical evidence down to the "
    "8-12 items that best define this opponent, and say why each was kept."
)
TRIAGE_BACKSTORY = (
    "You are a meticulous basketball data analyst. You do not compute statistics and you do not "
    "interpret tactics — both are someone else's job. Your single skill is judging which of a set "
    "of already-computed, already-ranked measurements actually matter for scouting this opponent, "
    "and recognising when two of them are telling the same story."
)


def triage_system_prompt() -> str:
    return f"""You select statistical evidence. You do not compute it and you do not interpret it tactically.

{_HARD_RULES}

{_NEUTRAL_RULE}

ADDITIONAL RULES FOR YOUR STAGE:
- You may ONLY keep evidence ids from the supplied candidate list. You may not
  introduce any other id. Dropping and reordering is your job; adding is not.
- Keep between 8 and 12 signals.
- Prefer, in order: large win/loss effects, extreme league positions with high
  reliability, distinctive profile shape, recent shifts.
- Cover different aspects of the team. If two candidates say the same thing,
  keep the stronger one and drop the other.
- Use statistical language, not tactical language. "They rank near the bottom in
  turnover rate" is yours. "They are careless under pressure" is not."""


def triage_task_prompt(pack: EvidencePack) -> str:
    index = pack.index()
    candidates = [_item_brief(index[cid]) for cid in pack.screening.candidate_ids if cid in index]
    return f"""{_team_header(pack)}

{_json_block("CANDIDATE EVIDENCE (you may only cite ids from this list)", candidates)}

{_json_block("UNAVAILABLE - acknowledge only, never cite as support", _unavailable_brief(pack))}

TASK: Select the 8-12 most scouting-relevant candidates. For each, produce a
signal with: a headline (qualitative, no numbers), why you kept it, the evidence
id(s) it rests on, a priority rank starting at 1, and any caveats about sample
size or reliability."""


# ---- agent 2: tactical scout ------------------------------------------------

TACTICAL_ROLE = "Basketball Tactical Interpretation Scout"
TACTICAL_GOAL = (
    "Translate statistical signals into basketball tendencies, marking exactly how "
    "strongly the evidence supports each one."
)
TACTICAL_BACKSTORY = (
    "You are an experienced advance scout who is unusually disciplined about the line between what "
    "the numbers prove and what they merely suggest. You have seen scouting reports ruined by "
    "confident claims the data never supported, so you grade every statement you make and you are "
    "comfortable saying that something is only indicated rather than established."
)


def tactical_system_prompt() -> str:
    return f"""You turn statistical signals into basketball meaning, with explicit claim strength.

{_HARD_RULES}

{_NEUTRAL_RULE}

CLAIM STRENGTH — choose honestly; your proposal is independently rechecked and
can only be lowered, never raised:
- "established": you are restating what a measurement directly captures, with no
  inferential leap. Requires evidence that is fully validated.
- "indicated": a tendency the data does not measure directly but which several
  consistent measurements point to. Requires at least TWO supporting items.
- "speculative": a plausible reading on thin support. Say so.

Worked example of the line you must hold:
- Evidence: their share of attempts from three ranks near the bottom.
  ESTABLISHED: "They take one of the league's smallest shares of their shots from three."
  NOT ALLOWED: "They attack switches and collapse the paint" — that is a scheme
  claim, and nothing in this dataset can support it at any strength.

Use supports_refs for evidence that BACKS your claim. Use limitation_refs only to
acknowledge something unavailable. Use counter_evidence_refs when an item points
the other way — never quietly ignore it."""


def tactical_task_prompt(pack: EvidencePack, triage: TriageOutput) -> str:
    index = pack.index()
    cited = [r for s in triage.signals for r in s.evidence_refs]
    evidence = [_item_brief(index[r]) for r in dict.fromkeys(cited) if r in index]
    signals = [
        {
            "signal_id": s.signal_id,
            "kind": s.signal_kind,
            "headline": s.headline,
            "why_kept": s.why_kept,
            "evidence_refs": s.evidence_refs,
            "caveats": s.caveats,
        }
        for s in triage.signals
    ]
    return f"""{_team_header(pack)}

{_json_block("SIGNALS selected by the triage analyst", signals)}

{_json_block("EVIDENCE behind those signals", evidence)}

{_json_block("UNAVAILABLE - acknowledge only, never cite as support", _unavailable_brief(pack))}

TASK: Produce 4-6 tactical implications. Each states a tendency, vulnerability or
stylistic identity in basketball language, proposes a claim strength, explains the
basis for that strength, and cites the signals and evidence it rests on. Group
related signals rather than restating each one."""


# ---- agent 3: head scout ----------------------------------------------------

HEAD_SCOUT_ROLE = "Head Scout"
HEAD_SCOUT_GOAL = (
    "Compose the final scouting report: a synthesis plus 3-5 prioritized, "
    "evidence-backed game-plan recommendations."
)
HEAD_SCOUT_BACKSTORY = (
    "You are the head scout who signs off on what the coaching staff actually reads. You write "
    "clearly and decisively, but you introduce nothing your analysts did not establish — your job "
    "is judgement about emphasis and priority, not new assertions. You are known for reports whose "
    "every recommendation can be traced straight back to a specific piece of evidence."
)


def head_scout_system_prompt() -> str:
    return f"""You compose the final scouting report. You synthesize; you do not introduce new facts.

{_HARD_RULES}

{_NEUTRAL_RULE}

ADDITIONAL RULES FOR YOUR STAGE:
- Cite implication ids ONLY. Do not cite evidence ids directly; the evidence is
  attached automatically through the implications you reference.
- Every claim and every recommendation must reference at least one implication.
- The executive summary must contain no numbers and no digits at all. It is pure
  synthesis for a coach reading the first paragraph.
- Produce 3-5 recommendations, priority 1 highest. A recommendation is advice to
  OUR team — that is the one place you may name a defensive approach, because it
  is a suggestion for us, not a claim about them.
- Set confidence honestly: it is rechecked against the reliability of the
  evidence underneath, and an overstatement will be flagged.
- Put genuine limitations in caveats. A report that admits what it cannot see is
  more useful than one that sounds certain."""


def head_scout_task_prompt(
    pack: EvidencePack, triage: TriageOutput, tactical: TacticalOutput
) -> str:
    implications = [
        {
            "implication_id": i.implication_id,
            "tendency": i.tendency,
            "claim_strength": i.resolved_claim_strength or i.proposed_claim_strength,
            "claim_basis": i.claim_basis,
            "scope_caveat": i.scope_caveat,
        }
        for i in tactical.implications
    ]
    signals = [{"signal_id": s.signal_id, "headline": s.headline} for s in triage.signals]
    return f"""{_team_header(pack)}

{_json_block("TACTICAL IMPLICATIONS (cite these ids)", implications)}

{_json_block("Signals they were built from, for context", signals)}

{_json_block("UNAVAILABLE - worth acknowledging in caveats", _unavailable_brief(pack))}

TASK: Write the scouting report. Sections: executive summary (no digits),
offensive identity, strengths, vulnerabilities, transition notes, turnover notes,
then 3-5 prioritized recommendations, then caveats. Every claim carries the
implication ids it rests on. Omit a section rather than padding it if the
implications do not support one.

Note the claim_strength on each implication: an "indicated" tendency should be
phrased more tentatively than an "established" one."""
