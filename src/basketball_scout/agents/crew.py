"""The only CrewAI-aware module in the project.

Everything else in ``agents/`` is provider-agnostic, so if the CrewAI dependency
tree ever becomes a problem this file can be replaced with direct SDK calls and
no schema, prompt, validator or renderer changes.

CrewAI is used deliberately thinly: sequential process, no delegation, no memory,
no tools, structured output per task. This pipeline is a typed three-step chain —
agent autonomy is not what we want from it, and a manager agent would only add a
routing decision that does not exist.

Each stage runs as its own single-task Crew rather than one three-task Crew. That
is what lets ``pipeline.py`` validate at every boundary and offer a repair attempt
before the next agent sees anything — a bad signal set never reaches the Head Scout.
"""

from __future__ import annotations

import os
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import ConfigError, Settings, load_settings, require_gemini_api_key
from ..net import enable_system_trust_store
from . import prompts
from .schemas import EvidencePack, ScoutingReport, TacticalOutput, TriageOutput


def _silence_crewai_telemetry() -> None:
    """CrewAI ships opt-out telemetry that phones home on every kickoff.

    Must run before ``crewai`` is imported. Off by default here: this project
    sends nothing outbound that the user did not ask for, and the exporter's
    retry warnings otherwise bury real provider errors in the CLI output."""
    os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")


class ProviderError(RuntimeError):
    """A provider call failed for an external reason (auth, quota, billing,
    availability) rather than a modelling one. Surfaced with an actionable
    message instead of a raw SDK traceback."""

# CP1-C finding, still binding: gemini-2.5-flash is listed by the API but returns
# HTTP 404 "no longer available to new users". gemini-3.5-flash is the verified
# working pin. Do not "fix" a failure here by falling back to 2.5.
DEFAULT_MODEL = "gemini-3.5-flash"
PROVIDER_PREFIX = "gemini"

TRIAGE_TEMPERATURE = 0.1
TACTICAL_TEMPERATURE = 0.3
HEAD_SCOUT_TEMPERATURE = 0.3

T = TypeVar("T", bound=BaseModel)

# (substring to match in the provider error, actionable explanation)
_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("prepayment credits are depleted",
     "The Gemini API key has no remaining credit. Top up billing at "
     "https://ai.studio/projects, or run with --stub for the offline backend."),
    ("RESOURCE_EXHAUSTED",
     "The provider refused the request for quota/billing reasons. Check the key's "
     "quota and billing, or run with --stub."),
    ("API key not valid",
     "GEMINI_API_KEY is set but rejected. Check the value in .env."),
    ("no longer available",
     f"The pinned model was rejected as unavailable. Note CP1-C: gemini-2.5-flash "
     f"returns 404 for new users; the verified pin is {DEFAULT_MODEL}."),
    ("certificate", "TLS verification failed. enable_system_trust_store() must run before any provider call."),
)


def _as_provider_error(role: str, exc: Exception) -> Exception:
    """Turn an SDK traceback into one actionable line, without hiding the cause."""
    message = str(exc)
    for needle, hint in _PROVIDER_HINTS:
        if needle.lower() in message.lower():
            return ProviderError(f"{role}: {hint}\n  provider said: {message.strip()[:300]}")
    return exc

_EXPECTED_TRIAGE = (
    "A JSON object with a 'signals' array of 8-12 entries. Each entry: signal_id, "
    "signal_kind, headline, why_kept, evidence_refs (ids from the candidate list), "
    "priority_rank, caveats."
)
_EXPECTED_TACTICAL = (
    "A JSON object with an 'implications' array of 4-6 entries. Each entry: "
    "implication_id, tendency, proposed_claim_strength, claim_basis, signal_refs, "
    "supports_refs, limitation_refs, counter_evidence_refs, scope_caveat."
)
_EXPECTED_REPORT = (
    "A JSON scouting report: report_id, team_id, team_name, scope_note, "
    "executive_summary, offensive_identity, strengths, vulnerabilities, "
    "transition_notes, turnover_notes, recommendations (4-5 Keys to Win, each "
    "with objective, why_it_matters, confidence, implication_refs and 0-2 "
    "tactics — a tactic has method, mechanism and implication_refs), caveats. "
    "Every claim and recommendation carries implication_refs."
)


class CrewBackend:
    """Runs the three agents through CrewAI against a single provider."""

    name = "crewai"

    def __init__(self, *, settings: Settings | None = None, model: str | None = None):
        # This machine needs the OS trust store for every HTTPS client, and
        # truststore patches ssl globally so it covers LiteLLM's httpx too.
        enable_system_trust_store()
        _silence_crewai_telemetry()

        self.settings = settings or load_settings()
        self.model = model or DEFAULT_MODEL

        # Fail at the point of use with an actionable message, never at import.
        api_key = require_gemini_api_key(self.settings)
        os.environ.setdefault("GEMINI_API_KEY", api_key)

        try:
            from crewai import LLM  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ConfigError(
                "crewai is not installed. Install it into the project environment "
                "(pip install crewai) or run with --stub for the offline backend."
            ) from exc

        self._llm_cls = LLM
        self._llms: dict[float, Any] = {}

    # -- internals ------------------------------------------------------------

    def _llm(self, temperature: float):
        if temperature not in self._llms:
            self._llms[temperature] = self._llm_cls(
                model=f"{PROVIDER_PREFIX}/{self.model}", temperature=temperature
            )
        return self._llms[temperature]

    def _execute(
        self,
        *,
        role: str,
        goal: str,
        backstory: str,
        system_prompt: str,
        task_prompt: str,
        expected_output: str,
        output_model: type[T],
        temperature: float,
        feedback: list[str] | None,
    ) -> T:
        from crewai import Agent, Crew, Process, Task  # noqa: PLC0415

        description = task_prompt
        if feedback:
            # The repair attempt gets the actual findings verbatim. A bare retry
            # would just resample the same mistake.
            issues = "\n".join(f"- {item}" for item in feedback)
            description = (
                f"{task_prompt}\n\n"
                f"YOUR PREVIOUS ATTEMPT WAS REJECTED BY AUTOMATED VALIDATION.\n"
                f"Fix exactly these problems and return corrected output:\n{issues}\n"
            )

        agent = Agent(
            role=role,
            goal=goal,
            backstory=f"{backstory}\n\n{system_prompt}",
            llm=self._llm(temperature),
            allow_delegation=False,
            verbose=False,
        )
        task = Task(
            description=description,
            expected_output=expected_output,
            agent=agent,
            output_pydantic=output_model,
        )
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            memory=False,
            verbose=False,
        )
        try:
            crew.kickoff()
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
            raise _as_provider_error(role, exc) from exc

        parsed = task.output.pydantic if task.output else None
        if parsed is None:  # pragma: no cover - provider-dependent
            raise RuntimeError(
                f"{role}: model returned no schema-valid output "
                f"(raw: {getattr(task.output, 'raw', None)!r:.300})"
            )
        return parsed  # type: ignore[return-value]

    # -- AgentBackend protocol ------------------------------------------------

    def run_triage(self, pack: EvidencePack, feedback: list[str] | None = None) -> TriageOutput:
        return self._execute(
            role=prompts.TRIAGE_ROLE,
            goal=prompts.TRIAGE_GOAL,
            backstory=prompts.TRIAGE_BACKSTORY,
            system_prompt=prompts.triage_system_prompt(),
            task_prompt=prompts.triage_task_prompt(pack),
            expected_output=_EXPECTED_TRIAGE,
            output_model=TriageOutput,
            temperature=TRIAGE_TEMPERATURE,
            feedback=feedback,
        )

    def run_tactical(
        self, pack: EvidencePack, triage: TriageOutput, feedback: list[str] | None = None
    ) -> TacticalOutput:
        return self._execute(
            role=prompts.TACTICAL_ROLE,
            goal=prompts.TACTICAL_GOAL,
            backstory=prompts.TACTICAL_BACKSTORY,
            system_prompt=prompts.tactical_system_prompt(),
            task_prompt=prompts.tactical_task_prompt(pack, triage),
            expected_output=_EXPECTED_TACTICAL,
            output_model=TacticalOutput,
            temperature=TACTICAL_TEMPERATURE,
            feedback=feedback,
        )

    def run_head_scout(
        self,
        pack: EvidencePack,
        triage: TriageOutput,
        tactical: TacticalOutput,
        feedback: list[str] | None = None,
    ) -> ScoutingReport:
        return self._execute(
            role=prompts.HEAD_SCOUT_ROLE,
            goal=prompts.HEAD_SCOUT_GOAL,
            backstory=prompts.HEAD_SCOUT_BACKSTORY,
            system_prompt=prompts.head_scout_system_prompt(),
            task_prompt=prompts.head_scout_task_prompt(pack, triage, tactical),
            expected_output=_EXPECTED_REPORT,
            output_model=ScoutingReport,
            temperature=HEAD_SCOUT_TEMPERATURE,
            feedback=feedback,
        )
