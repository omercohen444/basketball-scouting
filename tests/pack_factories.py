"""Evidence-pack helpers for tests.

Kept separate from ``product_factories`` (which wires an app and a repository)
so the pack-layer suite depends only on ``agents/``. That mirrors the source
layering: shipped packs are the input to the product, not part of it.
"""

from __future__ import annotations

from pathlib import Path

from agents_factories import make_item, make_pack

from basketball_scout.agents.pack_store import (
    build_artifact,
    build_index,
    write_artifact,
    write_index,
)
from basketball_scout.config import REPO_ROOT

#: The real, committed production artifacts. Used by the integrity tests.
PRODUCTION_PACKS_DIR = REPO_ROOT / "data" / "evidence_packs"

# The stub backend keeps 8-12 signals and validation rejects anything outside
# that band, so a synthetic pack needs at least 8 distinct candidates to be a
# realistic stand-in for a production one (25 items, 20 candidates).
_SYNTHETIC_METRICS = (
    ("EV.season.efg_pct", "higher_is_better"),
    ("EV.season.tov_pct", "lower_is_better"),
    ("EV.season.orb_pct", "higher_is_better"),
    ("EV.season.pace", "neutral"),
    ("EV.season.offensive_rating", "higher_is_better"),
    ("EV.season.defensive_rating", "lower_is_better"),
    ("EV.season.ft_rate", "neutral"),
    ("EV.season.fg3a_rate", "neutral"),
    ("EV.season.ast_to_ratio", "higher_is_better"),
    ("EV.clutch.efg_pct", "higher_is_better"),
    ("EV.clutch.tov_pct", "lower_is_better"),
    ("EV.Q4.net_rating", "higher_is_better"),
)


def synthetic_pack(team_id: str = "segev:4"):
    """A pack large enough to satisfy the agent layer's own validation rules."""
    items = [
        make_item(evidence_id, direction=direction)
        for evidence_id, direction in _SYNTHETIC_METRICS
    ]
    return make_pack(team_id=team_id, items=items)


def write_synthetic_packs(
    directory: Path,
    *,
    team_ids: tuple[str, ...] = ("segev:4", "segev:11"),
) -> Path:
    """Write a miniature pack store: fast, and independent of the real data."""
    artifacts = [
        build_artifact(synthetic_pack(team_id), [f"g{team_id}-1", f"g{team_id}-2"])
        for team_id in team_ids
    ]
    for artifact in artifacts:
        write_artifact(artifact, directory)
    write_index(build_index(artifacts, generated_at="2026-08-19T00:00:00Z"), directory)
    return directory
