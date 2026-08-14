"""Gemini multimodal provider layer — the only module that knows google-genai.

Everything provider-specific lives here. ``metrics``, ``schema``, ``prompts``
and ``events`` stay clean, so swapping provider (or dropping to OpenCV/YOLO if
the risk stage says so) touches one file.

Verified against **google-genai 2.18.1** by SDK introspection:

* ``types.VideoMetadata`` has ``start_offset: str | None``, ``end_offset:
  str | None``, ``fps: float | None`` (aliases ``startOffset`` / ``endOffset``);
* ``types.FileData`` has ``file_uri`` and ``mime_type``;
* ``types.Part`` accepts ``file_data`` and ``video_metadata`` together;
* ``GenerateContentConfig`` accepts ``system_instruction``, ``response_mime_type``,
  ``response_schema``, ``temperature``, ``media_resolution``;
* ``GenerateContentResponse`` exposes ``.parsed``.

NOT yet verified — requires a live key, see docs/VIDEO_SPIKE_NOTES.md:

* that a YouTube watch URL is accepted as ``file_data.file_uri`` for this
  API tier and model;
* that ``video_metadata`` offsets are honoured for a YouTube-hosted video
  rather than being ignored (i.e. that the model really sees only the window);
* the exact model id to use (run ``--list-models``).

Use ``--dry-run`` to inspect the outgoing request without spending a call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..config import Settings, require_gemini_api_key
from .events import ShotEvent
from .metrics import DEFAULT_METRICS, MetricDefinition
from .prompts import PROMPT_VERSION, SYSTEM_INSTRUCTION, build_classification_prompt
from .schema import (
    ClassificationSchemaError,
    ClassifiedEvent,
    classification_model_for,
    outcomes_from_response,
    parse_json_payload,
)

PROVIDER_NAME = "gemini"
SCHEMA_VERSION = "v1"

# YouTube URIs are passed without an explicit mime type (the API resolves the
# source itself). Kept configurable because this is one of the live-unverified
# points above.
YOUTUBE_MIME_TYPE: str | None = None


class ProviderError(RuntimeError):
    """Raised when the provider SDK is unavailable or the call fails."""


def _genai():
    """Import google-genai lazily, with an actionable error if it is missing.

    Lazy so that the schema/metric layer and the test suite stay importable on
    an environment without the SDK installed.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ProviderError(
            "google-genai is not installed. Run: "
            "python -m pip install -r requirements.txt"
        ) from exc
    return genai, types


def _media_resolution(types_mod: Any, value: str | None) -> Any:
    if not value:
        return None
    return getattr(types_mod.MediaResolution, f"MEDIA_RESOLUTION_{value.upper()}")


@dataclass(frozen=True)
class GeminiRequest:
    """A fully-built request, separated from sending it so it can be inspected."""

    model: str
    contents: Any
    config: Any
    prompt: str

    def debug_dict(self) -> dict[str, Any]:
        """JSON-safe view of the request for dry-runs and debug artifacts.

        Contains no credentials — the API key lives on the client, not here.
        """

        def dump(obj: Any, exclude: set[str] | None = None) -> Any:
            if hasattr(obj, "model_dump"):
                return obj.model_dump(
                    mode="json", exclude_none=True, by_alias=True, exclude=exclude
                )
            return obj

        # `response_schema` holds a pydantic *class*, which is not JSON
        # serializable, and the schema is derivable from the metric registry
        # anyway. Record only its name.
        config = dump(self.config, exclude={"response_schema"})
        schema_cls = getattr(self.config, "response_schema", None)
        if isinstance(config, dict) and schema_cls is not None:
            config["response_schema"] = getattr(schema_cls, "__name__", str(schema_cls))
        return {"model": self.model, "contents": dump(self.contents), "config": config}


def build_request(
    event: ShotEvent,
    settings: Settings,
    metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
    *,
    mime_type: str | None = YOUTUBE_MIME_TYPE,
    disable_thinking: bool = True,
) -> GeminiRequest:
    """Build the generate_content request for one localized event.

    Pure: no network, no API key required. This is what ``--dry-run`` shows and
    what the offline shape test asserts on.

    ``disable_thinking``: classification needs no reasoning chain, so
    ``thinking_budget=0`` is requested by default (docs/VIDEO_STAGE_PLAN.md
    §6.1/§27 P9) to cut cost and latency. Set False if a model rejects it.
    """
    _, types = _genai()
    window = event.window
    prompt = build_classification_prompt(event, metrics)

    video_part = types.Part(
        file_data=types.FileData(file_uri=window.video_url, mime_type=mime_type),
        video_metadata=types.VideoMetadata(
            start_offset=window.start_offset,
            end_offset=window.end_offset,
            fps=settings.gemini_video_fps,
        ),
    )
    contents = types.Content(role="user", parts=[video_part, types.Part(text=prompt)])

    thinking_config = types.ThinkingConfig(thinking_budget=0) if disable_thinking else None
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=classification_model_for(metrics),
        temperature=0.0,
        media_resolution=_media_resolution(types, settings.gemini_media_resolution),
        thinking_config=thinking_config,
    )
    return GeminiRequest(
        model=settings.gemini_video_model, contents=contents, config=config, prompt=prompt
    )


class GeminiVideoClassifier:
    """Classifies one localized shot event at a time.

    Deliberately single-event: the risk stage needs to inspect, label and argue
    about individual events before anything is run in bulk.
    """

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.settings = settings
        if client is not None:
            self._client = client
        else:
            genai, _ = _genai()
            self._client = genai.Client(api_key=require_gemini_api_key(settings))

    def list_models(self) -> list[str]:
        """Model ids visible to this key — used to verify the configured model."""
        return [m.name or "" for m in self._client.models.list()]

    def classify(
        self,
        event: ShotEvent,
        metrics: tuple[MetricDefinition, ...] = DEFAULT_METRICS,
        *,
        mime_type: str | None = YOUTUBE_MIME_TYPE,
        disable_thinking: bool = True,
    ) -> ClassifiedEvent:
        """Classify one event. Failures are returned, not raised.

        A single bad event must never abort a labelling session, so the error is
        recorded on the result and the caller decides what to do with it.
        """
        request = build_request(
            event, self.settings, metrics, mime_type=mime_type, disable_thinking=disable_thinking
        )
        base = dict(
            event_id=event.event_id,
            game_id=event.game_id,
            video_url=event.window.video_url,
            start_seconds=event.window.start_seconds,
            end_seconds=event.window.end_seconds,
            provider=PROVIDER_NAME,
            model=request.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            media_resolution=self.settings.gemini_media_resolution,
            fps=self.settings.gemini_video_fps,
        )

        started = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=request.model, contents=request.contents, config=request.config
            )
        except Exception as exc:
            return ClassifiedEvent(
                **base,
                latency_seconds=round(time.monotonic() - started, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
        latency = round(time.monotonic() - started, 3)
        usage = _usage_from_response(response)
        finish_reason = _finish_reason_from_response(response)

        raw_text = getattr(response, "text", None)
        try:
            payload = _payload_from_response(response, raw_text)
            outcomes = outcomes_from_response(payload, metrics)
        except ClassificationSchemaError as exc:
            return ClassifiedEvent(
                **base, latency_seconds=latency, raw_text=raw_text, error=str(exc),
                usage=usage, finish_reason=finish_reason,
            )

        return ClassifiedEvent(
            **base, latency_seconds=latency, raw_text=raw_text, outcomes=outcomes,
            usage=usage, finish_reason=finish_reason,
        )


def _usage_from_response(response: Any) -> dict[str, Any] | None:
    """Extract token usage, including the per-modality VIDEO count.

    The VIDEO modality count is the CP1-A evidence (docs/VIDEO_STAGE_PLAN.md
    §6.4): if it scales with the requested clip length, offsets are honoured;
    if it matches the full video's duration, they are being ignored.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    video_tokens: int | None = None
    details = getattr(usage, "prompt_tokens_details", None) or []
    for entry in details:
        modality = getattr(entry, "modality", None)
        modality_name = getattr(modality, "name", str(modality))
        if modality_name == "VIDEO":
            video_tokens = getattr(entry, "token_count", None)
            break

    return {
        "prompt": getattr(usage, "prompt_token_count", None),
        "video": video_tokens,
        "output": getattr(usage, "candidates_token_count", None),
        "thoughts": getattr(usage, "thoughts_token_count", None),
        "total": getattr(usage, "total_token_count", None),
    }


def _finish_reason_from_response(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    return getattr(reason, "name", str(reason))


def _payload_from_response(response: Any, raw_text: str | None) -> dict[str, Any]:
    """Prefer the SDK's parsed object, fall back to parsing the raw text.

    ``response_schema`` should populate ``.parsed``, but the risk stage should
    not fail because of an SDK parsing quirk when valid JSON is sitting in
    ``.text``.
    """
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        if isinstance(parsed, dict):
            return parsed
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump()
    if raw_text is None:
        raise ClassificationSchemaError(
            "response had neither .parsed nor .text — inspect the raw response "
            "(it may have been blocked or truncated)"
        )
    return parse_json_payload(raw_text)
