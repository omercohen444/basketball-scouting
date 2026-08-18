"""Agent layer — deterministic evidence in, structured scouting report out.

Layering (strict, one direction only)::

    stats/                    deterministic analytics — the ONLY source of numbers
      |
      v
    agents/evidence_pack.py   flattens stats output into the agent-facing contract
      |                       (ids, formatting, reliability tiers, masking). NO LLM.
      v
    agents/prompts.py         provider-agnostic prompt construction
      |
      v
    agents/crew.py            the ONLY CrewAI/provider-aware module
      |
      v
    agents/validation.py      deterministic post-agent checks — pure, no I/O
      |
      v
    agents/render.py          attaches canonical numbers back onto agent prose

``agents/pipeline.py`` orchestrates the above and also ships deterministic stub
agents, so the entire chain is exercisable offline with zero provider calls.

Two invariants this package exists to enforce:

1. **Agents never produce a number.** They emit qualitative prose plus
   ``evidence_refs``; :mod:`render` attaches the canonical values from the pack.
2. **Agents never adjudicate provenance.** ``reliability_tier`` and the final
   ``claim_strength`` are computed in Python; an agent only ever *proposes* a
   strength, and :func:`validation.resolve_claim_strength` may downgrade it —
   never upgrade it.
"""
