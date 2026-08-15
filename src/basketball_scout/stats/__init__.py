"""Deterministic team-game statistics layer.

Provider-independent team-game data model, the ten core PBP-derived team
metrics, and a win/loss comparison engine — see PROJECT_SPEC.md "The ten core
team metrics" and BUILD_PLAN.md stage 3/4.

Layering (mirrors ``pbp/`` and ``video/``):

* ``boxscore.py``  — the only module that knows the Segev action wire format;
  a statistics-side adapter, deliberately separate from ``pbp/canonical.py``
  (which is the video pipeline's shot-only contract and is out of bounds here).
* ``models.py``    — provider-independent dataclasses: raw components, derived
  metrics, one team's full game record.
* ``formulas.py``  — pure, provider-agnostic arithmetic. No LLM ever computes
  any of these numbers.
* ``engine.py``    — orchestrates boxscore -> formulas -> models for one game.
* ``winloss.py``   — historical win-vs-loss comparison and signal ranking.
* ``store.py``     — flat-file JSON persistence (no database yet).
* ``schedule.py``  — bounded multi-game discovery adapter; documents the gap
  where clean discovery is not yet possible.
"""
