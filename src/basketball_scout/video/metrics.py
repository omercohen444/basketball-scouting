"""Definitions of the video metrics a model is asked to classify.

These three metrics are PROVISIONAL. The whole point of this module is that a
metric can be replaced, dropped or added by editing the registry below — the
schema, the prompt and the spike CLI all derive from it, so nothing else needs
to change.

Every metric implicitly gains an ``uncertain`` label. A model that cannot see
enough must be able to say so; forcing a binary choice manufactures false
confidence, which is worse than a gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

UNCERTAIN = "uncertain"


class MetricError(ValueError):
    """Raised for a malformed or unknown metric definition."""


@dataclass(frozen=True)
class MetricDefinition:
    """One question the model answers about a single shot event."""

    key: str
    """Stable snake_case identifier. Becomes the JSON field name."""

    title: str
    """Short human-readable name for reports and CLI output."""

    question: str
    """The decision the model must make, phrased for the prompt."""

    labels: tuple[str, ...]
    """Decision labels, excluding ``uncertain`` (added automatically)."""

    guidance: dict[str, str] = field(default_factory=dict, compare=False)
    """label -> what that label looks like on broadcast video.

    Excluded from equality/hashing so the definition stays hashable (a dict
    field would otherwise break it). Correct here too: the response schema
    depends on ``key`` and ``labels``, never on the wording of the guidance.
    """

    uncertain_when: str = ""
    """Explicit description of when the model should answer ``uncertain``."""

    def __post_init__(self) -> None:
        if not self.key or not self.key.replace("_", "").isalnum():
            raise MetricError(f"metric key must be snake_case alphanumeric, got {self.key!r}")
        if len(self.labels) < 2:
            raise MetricError(f"metric {self.key!r} needs at least 2 labels")
        if UNCERTAIN in self.labels:
            raise MetricError(
                f"metric {self.key!r} must not list {UNCERTAIN!r} explicitly; it is always added"
            )
        if len(set(self.labels)) != len(self.labels):
            raise MetricError(f"metric {self.key!r} has duplicate labels")
        unknown = set(self.guidance) - set(self.labels)
        if unknown:
            raise MetricError(f"metric {self.key!r} has guidance for unknown labels: {sorted(unknown)}")

    @property
    def all_labels(self) -> tuple[str, ...]:
        """Decision labels plus ``uncertain`` — the full permitted answer set."""
        return (*self.labels, UNCERTAIN)

    def is_valid_label(self, label: str) -> bool:
        return label in self.all_labels


# --------------------------------------------------------------------------
# Provisional metric registry (see PROJECT_SPEC.md § Provisional assumptions)
# --------------------------------------------------------------------------

SHOT_CONTEST = MetricDefinition(
    key="shot_contest",
    title="Open vs Contested",
    question="At the moment the shot is released, was the shooter open or contested?",
    labels=("open", "contested"),
    guidance={
        "open": "No defender within roughly an arm's length of the shooter, or the "
        "closest defender is not closing out with a raised hand at release.",
        "contested": "A defender is within roughly an arm's length at release, or is "
        "actively closing out with a hand up in the shooter's line of sight.",
    },
    uncertain_when="The release is off-camera, obscured, or the broadcast angle does "
    "not show the closest defender at the moment of release.",
)

POSSESSION_TYPE = MetricDefinition(
    key="possession_type",
    title="Transition vs Half-Court",
    question="Was the shot taken in transition or out of a set half-court possession?",
    labels=("transition", "half_court"),
    guidance={
        "transition": "The shot comes early in the possession while the defence is "
        "still retreating or outnumbered — fast break or early offence before the "
        "defence is set.",
        "half_court": "The defence is set in its half-court shape before the shot, "
        "typically after the offence has initiated a set or reset the ball.",
    },
    uncertain_when="The possession start is not visible in the window, so the time "
    "between the change of possession and the shot cannot be judged.",
)

SHOT_CREATION = MetricDefinition(
    key="shot_creation",
    title="Catch-and-Shoot vs Off-Dribble",
    question="Did the shooter catch and shoot, or create the shot off the dribble?",
    labels=("catch_and_shoot", "off_dribble"),
    guidance={
        "catch_and_shoot": "The shooter receives a pass and rises into the shot "
        "without putting the ball on the floor (a single settling hop still counts).",
        "off_dribble": "The shooter takes at least one dribble immediately before the "
        "shot — pull-up, step-back, drive or isolation.",
    },
    uncertain_when="The catch or the dribbles before the release are not visible in "
    "the window.",
)

DEFAULT_METRICS: tuple[MetricDefinition, ...] = (
    SHOT_CONTEST,
    POSSESSION_TYPE,
    SHOT_CREATION,
)


def metrics_by_key(
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> dict[str, MetricDefinition]:
    return {m.key: m for m in metrics}


def select_metrics(
    keys: list[str] | tuple[str, ...] | None,
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> tuple[MetricDefinition, ...]:
    """Return the requested subset of metrics, preserving the requested order.

    ``None`` or an empty selection means "all of them" — the common case when
    classifying one event against every candidate metric at once.
    """
    if not keys:
        return metrics
    available = metrics_by_key(metrics)
    unknown = [k for k in keys if k not in available]
    if unknown:
        raise MetricError(
            f"unknown metric(s): {unknown}. Available: {sorted(available)}"
        )
    return tuple(available[k] for k in keys)
