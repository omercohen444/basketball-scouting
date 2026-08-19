"""Deterministic analytics artifacts for the public website.

Separate from ``agents/`` on purpose. The evidence packs exist to feed three
agents a curated 25-item slice; this package exists to feed a browsable
analytics site the whole picture. Sharing one artifact between them would mean
every website need became a change to the agent contract — and a change to the
agent contract means regenerating fourteen scouting reports.

So: same proven shape as ``agents/pack_store.py`` (hash-verified, versioned,
per-team files behind an index, loaded once at startup), different contents,
independent lifecycle. Nothing here can move a ``pack_hash``.
"""

from .schema import (
    ANALYTICS_ARTIFACT_VERSION,
    OUTCOMES,
    SEGMENTS,
    AnalyticsArtifact,
    AnalyticsIndex,
    GameRow,
    SegmentCell,
    TeamAnalytics,
)
from .store import AnalyticsStore, AnalyticsArtifactError

__all__ = [
    "ANALYTICS_ARTIFACT_VERSION",
    "OUTCOMES",
    "SEGMENTS",
    "AnalyticsArtifact",
    "AnalyticsArtifactError",
    "AnalyticsIndex",
    "AnalyticsStore",
    "GameRow",
    "SegmentCell",
    "TeamAnalytics",
]
