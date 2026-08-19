#!/usr/bin/env python
"""Cross-team content QA over the stored reports.

    python scripts/ops/qa_reports.py

Reads every saved report and flags prose that *looks like* one of the defect
classes ``agents/validation.py`` exists to prevent. Read-only: it touches
storage and nothing else, makes no provider call, and changes nothing.

**This is a triage tool, not a second validator.** It is deliberately
over-broad — it matches words, where the real rules match words *against the
cited evidence*. R9, for instance, permits "elite" precisely when the evidence
is extreme enough to earn it, and this script cannot tell the difference. Two
examples from a real run, both correct output that this script still flagged:

* Maccabi Tel Aviv's summary said "elite ball security" with a mean league
  extremity of 49.2/50 — rank 1 of 14 in most cited metrics. Earned.
* A caveat said "an extremely small sample of defeats", which describes the
  sample rather than the team. Not a claim about the opponent at all.

So a finding here is a question, not a verdict. Confirm it against the rule
before regenerating anything.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from basketball_scout.agents.pack_store import PackStore  # noqa: E402
from basketball_scout.config import load_settings  # noqa: E402
from basketball_scout.reports.contracts import PublicReport  # noqa: E402
from basketball_scout.web.context import build_repository  # noqa: E402

# (label, regex) — matched against agent-authored prose only.
CHECKS = [
    ("exaggerated adjective", r"\b(exceptional|elite|extremely|massive|dramatic|dominant|phenomenal|outstanding|explosive|overwhelming|severe)\w*\b"),
    ("absolute claim", r"\b(always|never|rarely)\b"),
    ("causal W/L", r"(causes? (them|their)|caused their|lead(s)? to a (win|loss)|is why they (win|lose)|the reason they (win|lose)|drives their (win|loss))"),
    ("stability claim", r"\b(remains? stable|stays stable|is stable|remains constant|unchanged|steady)\b"),
    ("half-court / possession type", r"(half[- ]court|set offense|in the half court)"),
    ("intentionality", r"(by design|designed to|coaching intent)"),
    ("shot contest / video", r"(shot contests?|perimeter defense|on-ball pressure|contest(ing|ed)? (their|every|the) shot)"),
    ("scheme / coverage", r"(drop coverage|zone defense|2-3 zone|1-3-1|man-to-man|switch(ing)? (everything|scheme)|hedge the|ice the ball screen|pick and roll coverage)"),
    ("player / personnel", r"(point guard|shooting guard|small forward|power forward|star player|best player|key player|leading scorer|top scorer|starting (five|lineup)|bench unit|roster|personnel)"),
    ("unmeasured construct", r"\b(rhythm|intensity|momentum)\b"),
    ("internal vocabulary", r"(an indicated |an established |a speculative |claim strength)"),
    ("uncomputed metric", r"(true shooting|usage rate|player efficiency rating|win shares|plus[- ]minus|deflections|closeout)"),
    ("technique in objective", None),  # handled structurally below
]

TECHNIQUE = re.compile(
    r"\b(contest|contesting|box(ing)? out|box-out|trap|trapping|double[- ]team|"
    r"full[- ]court press|hedge|hedging|clos(e|ing) out|closeout|blitz)\w*\b", re.I)


def coach_prose(r: PublicReport):
    """Only what a coach reads and an agent wrote. Excludes system boilerplate
    (unavailable_evidence reasons, metric labels) which legitimately contains
    words like 'half-court' and 'shot contest'."""
    out = [("executive_summary", r.executive_summary)]
    for k in r.recommendations:
        out.append((f"key{k.priority}.objective", k.objective))
        out.append((f"key{k.priority}.why", k.why_it_matters))
        for t in k.tactics:
            out.append((f"key{k.priority}.tactic.method", t.method))
            out.append((f"key{k.priority}.tactic.mechanism", t.mechanism))
    for _key, title, claims in r.sections.items():
        for i, c in enumerate(claims):
            out.append((f"{title}[{i}]", c.text))
    for i, c in enumerate(r.caveats):
        out.append((f"caveat[{i}]", c))
    return out


def scan(r: PublicReport):
    findings = []
    for where, text in coach_prose(r):
        low = text.lower()
        for label, pattern in CHECKS:
            if pattern is None:
                continue
            # scheme vocabulary is legal in advice-to-us text
            if label == "scheme / coverage" and (".objective" in where or ".tactic.method" in where):
                continue
            if label == "half-court / possession type" and (".objective" in where or ".tactic.method" in where):
                continue
            for m in re.finditer(pattern, low, re.I):
                findings.append((label, where, text[max(0, m.start() - 30):m.start() + 45]))
        if where.endswith(".objective") and (m := TECHNIQUE.search(text)):
            findings.append(("technique in objective", where, m.group(0)))
    # structural checks
    for k in r.recommendations:
        if len(k.tactics) > 2:
            findings.append(("more than 2 tactics", f"key{k.priority}", str(len(k.tactics))))
        for t in k.tactics:
            if not t.evidence:
                findings.append(("tactic without evidence", f"key{k.priority}", t.tactic_id))
    if not (4 <= len(r.recommendations) <= 5):
        findings.append(("key count out of band", "report", str(len(r.recommendations))))
    return findings


def main():
    repo = build_repository(load_settings())
    total = 0
    scanned = 0
    for tid in PackStore("data/evidence_packs").team_ids():
        stored = repo.get_latest_report(tid)
        if stored is None:
            print(f"{tid:12s} -- no report")
            continue
        r = PublicReport.model_validate(stored.report_json)
        f = scan(r)
        scanned += 1
        total += len(f)
        status = "CLEAN" if not f else f"{len(f)} FINDING(S)"
        print(f"{tid:12s} {r.team_name[:24]:24s} keys={len(r.recommendations)} "
              f"tactics={sum(len(k.tactics) for k in r.recommendations)} {status}")
        for label, where, snippet in f:
            print(f"    !! {label:28s} {where:28s} ...{snippet.strip()}...")
    print(f"\nscanned {scanned} reports, {total} finding(s)")


if __name__ == "__main__":
    main()
