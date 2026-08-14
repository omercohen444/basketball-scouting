"""Prompt construction, derived from the metric registry.

Provider-agnostic on purpose: this module returns plain strings. Swapping the
multimodal provider should not require rewriting the basketball wording, and
replacing a metric should not require touching the provider client.
"""

from __future__ import annotations

from .events import ShotEvent
from .metrics import DEFAULT_METRICS, UNCERTAIN, MetricDefinition
from .schema import CONFIDENCE_FIELD_SUFFIX, EVIDENCE_FIELD_SUFFIX

# Bump whenever SYSTEM_INSTRUCTION or build_classification_prompt's wording
# changes materially. Stamped onto every ClassifiedEvent (docs/VIDEO_STAGE_PLAN.md
# §9.5) so CP2 labels are never silently compared against output from a
# different prompt.
PROMPT_VERSION = "v2-cp1"

SYSTEM_INSTRUCTION = (
    "You are a basketball video analyst reviewing a short clip from a broadcast "
    "of a professional game. You classify one specific shot attempt.\n"
    "\n"
    "Rules:\n"
    "- Judge only what is visible in the clip. Do not infer from the score, the "
    "teams involved, or basketball priors.\n"
    f"- If the clip does not show what you need, answer '{UNCERTAIN}'. An honest "
    f"'{UNCERTAIN}' is more useful than a confident guess, and guessing corrupts "
    "the aggregate statistics this feeds.\n"
    "- Confidence must reflect what you actually saw, not how plausible the "
    "answer sounds.\n"
    "- Return only the requested JSON object."
)


def _metric_block(metric: MetricDefinition, index: int) -> str:
    lines = [f"{index}. {metric.title} -> JSON field \"{metric.key}\"", f"   {metric.question}"]
    for label in metric.labels:
        hint = metric.guidance.get(label, "")
        lines.append(f'   - "{label}": {hint}' if hint else f'   - "{label}"')
    uncertain_hint = metric.uncertain_when or "You cannot determine this from the clip."
    lines.append(f'   - "{UNCERTAIN}": {uncertain_hint}')
    lines.append(
        f'   Also set "{metric.key}{CONFIDENCE_FIELD_SUFFIX}" (0.0-1.0) and '
        f'"{metric.key}{EVIDENCE_FIELD_SUFFIX}" (one short sentence of visual evidence).'
    )
    return "\n".join(lines)


def build_classification_prompt(
    event: ShotEvent,
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
) -> str:
    """Build the user prompt for one localized shot event."""
    if not metrics:
        raise ValueError("at least one metric is required")

    window = event.window
    blocks = "\n\n".join(_metric_block(m, i) for i, m in enumerate(metrics, start=1))
    approx_offset = round(window.duration_seconds - 8.0) if window.duration_seconds > 8 else round(
        window.duration_seconds * 0.7
    )

    return (
        "This clip covers a single shot attempt from a professional basketball game.\n"
        f"Clip window: {window.label()} of the full broadcast.\n"
        f"Game context: {event.context_line()}\n"
        "\n"
        f"The shot attempt occurs approximately {approx_offset} seconds into this "
        f"{window.duration_seconds:.0f}-second clip; the earlier seconds are included "
        "so you can see how the possession developed. If the clip shows more than one "
        "shot attempt, classify the one matching the game context above (shooter "
        "jersey / attempt type / point value), not an earlier or later one.\n"
        "\n"
        "Classify that shot attempt on each of the following:\n"
        "\n"
        f"{blocks}\n"
        "\n"
        "If you cannot identify a shot attempt in this clip at all, answer "
        f'"{UNCERTAIN}" for every field with confidence 0.0 and say so in the '
        "evidence fields."
    )


def expected_field_names(metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS) -> list[str]:
    """Field names the provider is expected to return — handy for debugging."""
    names: list[str] = []
    for metric in metrics:
        names += [
            metric.key,
            f"{metric.key}{CONFIDENCE_FIELD_SUFFIX}",
            f"{metric.key}{EVIDENCE_FIELD_SUFFIX}",
        ]
    return names
