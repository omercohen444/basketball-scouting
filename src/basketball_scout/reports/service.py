"""The one path that produces a scouting report.

Both the admin API endpoint and the batch CLI go through :class:`ReportService`,
so there is exactly one implementation of "deterministic evidence -> agents ->
validation -> persistence" and no second, subtly different one.

Three properties this module exists to guarantee:

1. **Public reads never generate.** ``get_latest`` / ``get_report`` only touch
   storage. Generation is a separate, admin-only method.
2. **An invalid report is never saved.** ``run_pipeline`` raises on a stage that
   fails validation twice; a report that somehow arrives with hard rejections is
   refused here as well, and the attempt is recorded as a failed run instead.
3. **Provider flakiness is not application failure.** Transient 503/overloaded
   responses get a bounded retry with backoff; auth/quota/validation problems do
   not, because retrying them cannot help.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..agents.pack_store import PackArtifact, PackArtifactError, PackStore
from ..agents.pipeline import AgentBackend, PipelineError, run_pipeline
from ..persistence.models import (
    RUN_STATUS_ERROR,
    RUN_STATUS_REJECTED,
    RUN_STATUS_SKIPPED,
    RUN_STATUS_SUCCEEDED,
    GenerationRun,
    StoredReport,
    TeamRecord,
)
from ..persistence.repository import ReportRepository, RepositoryError
from .contracts import (
    REPORT_CONTRACT_VERSION,
    PublicReport,
    TeamSummary,
    build_public_report,
)

log = logging.getLogger(__name__)

# Bounded on purpose. Three attempts covers the "high demand" blips observed
# against Gemini without turning a real outage into a ten-minute request.
MAX_TRANSIENT_ATTEMPTS = 3
BACKOFF_SECONDS = (4.0, 12.0)

# Substrings that mark a provider failure as worth retrying. Deliberately
# specific: a depleted-credit 429 is *not* transient and must fail immediately.
_TRANSIENT_MARKERS = (
    "503",
    "unavailable",
    "overloaded",
    "high demand",
    "try again later",
    "temporarily",
    "timeout",
    "timed out",
    "connection reset",
    "bad gateway",
    "502",
    "504",
)
_NON_TRANSIENT_MARKERS = (
    "prepayment credits",
    "api key not valid",
    "permission denied",
    "billing",
    "no longer available",
)


class UnknownTeamError(ValueError):
    """The requested team is not in the shipped production allowlist."""


# Shape gate applied *before* an id is ever echoed back or used in a query.
# Anything that does not match is rejected without being reflected to the caller.
TEAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.\-]{0,39}$")


class GenerationUnavailableError(RuntimeError):
    """The agent backend cannot run here (missing dependency or credentials)."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_transient(exc: BaseException) -> bool:
    message = str(exc).lower()
    if any(marker in message for marker in _NON_TRANSIENT_MARKERS):
        return False
    return any(marker in message for marker in _TRANSIENT_MARKERS)


@dataclass
class GenerationOutcome:
    """Everything the caller (API, CLI, audit row) needs about one attempt."""

    status: str
    team_id: str
    backend: str
    duration_ms: int
    report: PublicReport | None = None
    stored_report_id: str | None = None
    persisted: bool = False
    provider_calls: int = 0
    stage_attempts: dict[str, int] = field(default_factory=dict)
    transient_retries: int = 0
    rejects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_name: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (RUN_STATUS_SUCCEEDED, RUN_STATUS_SKIPPED)


def default_backend_factory(settings, model: str | None = None) -> Callable[[], AgentBackend]:
    """Build the real CrewAI backend lazily.

    Imported inside the closure so that a deployment (or CI) without the CrewAI
    dependency tree still serves saved reports perfectly — only generation is
    unavailable, and it says so.
    """

    def factory() -> AgentBackend:
        try:
            from ..agents.crew import CrewBackend
        except ImportError as exc:
            raise GenerationUnavailableError(
                "the CrewAI agent backend is not installed in this environment; "
                "saved reports are still served, but generation is unavailable"
            ) from exc
        return CrewBackend(settings=settings, model=model)

    return factory


class ReportService:
    """Reads saved reports; generates new ones on explicit admin request."""

    def __init__(
        self,
        *,
        pack_store: PackStore,
        repository: ReportRepository,
        backend_factory: Callable[[], AgentBackend] | None = None,
        model_name: str | None = None,
        clock: Callable[[], str] = utc_now_iso,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.pack_store = pack_store
        self.repository = repository
        self.backend_factory = backend_factory
        self.model_name = model_name
        self.clock = clock
        self.sleep = sleep

    # -- allowlist -----------------------------------------------------------

    def allowed_team_ids(self) -> list[str]:
        """The fixed allowlist, from the shipped packs. No network, no DB."""
        if not self.pack_store.available:
            return []
        return self.pack_store.team_ids()

    def resolve_team_id(self, raw: str) -> str:
        """Validate a caller-supplied team id against the fixed allowlist.

        Accepts both the canonical ``segev:4`` and its URL-friendly slug
        ``segev_4``. Anything failing the shape gate is rejected without being
        echoed back, so a hostile id never reaches a log line, a template or a
        query string.
        """
        if not isinstance(raw, str) or not TEAM_ID_PATTERN.match(raw):
            raise UnknownTeamError("unknown team")
        allowed = self.allowed_team_ids()
        if raw in allowed:
            return raw
        alternative = raw.replace("_", ":")
        if alternative in allowed:
            return alternative
        raise UnknownTeamError(f"unknown team {raw!r}")

    def require_team(self, team_id: str) -> str:
        return self.resolve_team_id(team_id)

    def team_records(self) -> list[TeamRecord]:
        """Team rows derived from the shipped pack index — the seed for storage."""
        if not self.pack_store.available:
            return []
        return [
            TeamRecord(
                team_id=entry.team_id,
                team_name=entry.team_name,
                season=entry.season,
                games_n=entry.games_n,
                wins=entry.wins,
                losses=entry.losses,
                active=True,
            )
            for entry in self.pack_store.entries()
        ]

    # -- reads ---------------------------------------------------------------

    def list_teams(self) -> list[TeamSummary]:
        """Every selectable opponent, annotated with whether a report exists.

        The allowlist comes from the shipped packs, so the endpoint works even
        when storage is empty or unreachable; storage only decorates it.
        """
        try:
            refs = self.repository.latest_report_refs()
        except RepositoryError as exc:
            # An unreachable database degrades the list rather than emptying it:
            # the allowlist comes from the shipped packs and is always available.
            log.warning("storage unavailable while listing teams: %s", exc)
            refs = {}

        summaries = [
            TeamSummary(
                team_id=record.team_id,
                team_name=record.team_name,
                season=record.season,
                games_n=record.games_n,
                wins=record.wins,
                losses=record.losses,
                record=record.record,
                has_report=record.team_id in refs,
                latest_report_id=refs[record.team_id].report_id if record.team_id in refs else None,
                latest_generated_at=(
                    refs[record.team_id].generated_at if record.team_id in refs else None
                ),
            )
            for record in self.team_records()
        ]
        return sorted(summaries, key=lambda t: t.team_name)

    def get_latest(self, team_id: str) -> PublicReport | None:
        team_id = self.resolve_team_id(team_id)
        stored = self.repository.get_latest_report(team_id)
        return _to_public(stored) if stored else None

    def get_report(self, report_id: str) -> PublicReport | None:
        stored = self.repository.get_report(report_id)
        return _to_public(stored) if stored else None

    # -- generation ----------------------------------------------------------

    def generate(
        self,
        team_id: str,
        *,
        force_regenerate: bool = False,
        backend: AgentBackend | None = None,
    ) -> GenerationOutcome:
        """Run the full product path for one team.

        ``backend`` is injectable so tests exercise this method end to end with a
        deterministic stub and zero provider calls.
        """
        team_id = self.resolve_team_id(team_id)
        started_at = self.clock()
        started = time.monotonic()

        if not force_regenerate:
            existing = self.repository.get_latest_report(team_id)
            if existing is not None:
                outcome = GenerationOutcome(
                    status=RUN_STATUS_SKIPPED,
                    team_id=team_id,
                    backend="none",
                    duration_ms=_elapsed_ms(started),
                    report=_to_public(existing),
                    stored_report_id=existing.report_id,
                    persisted=False,
                )
                self._record(outcome, started_at)
                return outcome

        try:
            artifact = self.pack_store.get(team_id)
        except PackArtifactError as exc:
            outcome = GenerationOutcome(
                status=RUN_STATUS_ERROR,
                team_id=team_id,
                backend="none",
                duration_ms=_elapsed_ms(started),
                error=str(exc),
            )
            self._record(outcome, started_at)
            return outcome

        try:
            agent_backend = backend or self._build_backend()
        except (GenerationUnavailableError, Exception) as exc:  # noqa: BLE001
            outcome = GenerationOutcome(
                status=RUN_STATUS_ERROR,
                team_id=team_id,
                backend="unavailable",
                duration_ms=_elapsed_ms(started),
                error=str(exc),
            )
            self._record(outcome, started_at)
            return outcome

        backend_name = getattr(agent_backend, "name", "unknown")
        transient_retries = 0
        result = None
        failure: Exception | None = None

        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                result = run_pipeline(artifact.pack, agent_backend)
                break
            except PipelineError as exc:
                failure = exc
                break  # a validation failure will not fix itself on a resample
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                failure = exc
                if not is_transient(exc) or attempt == MAX_TRANSIENT_ATTEMPTS - 1:
                    break
                transient_retries += 1
                delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                log.warning(
                    "transient provider failure for %s (attempt %d/%d), retrying in %.0fs: %s",
                    team_id, attempt + 1, MAX_TRANSIENT_ATTEMPTS, delay, exc,
                )
                self.sleep(delay)

        if result is None:
            status = RUN_STATUS_REJECTED if isinstance(failure, PipelineError) else RUN_STATUS_ERROR
            outcome = GenerationOutcome(
                status=status,
                team_id=team_id,
                backend=backend_name,
                duration_ms=_elapsed_ms(started),
                transient_retries=transient_retries,
                model_name=self.model_name,
                rejects=[str(failure)] if isinstance(failure, PipelineError) else [],
                error=str(failure) if failure else "generation produced no result",
            )
            self._record(outcome, started_at)
            return outcome

        validation = result.validation
        rejects = [f.message for f in validation.rejects]
        warnings = [f"{f.rule}: {f.message}" for f in validation.warnings]

        # Belt and braces: run_pipeline already raises on a rejecting stage, but
        # "an invalid report is never saved" is important enough to assert here
        # rather than to trust an upstream invariant.
        if rejects:
            outcome = GenerationOutcome(
                status=RUN_STATUS_REJECTED,
                team_id=team_id,
                backend=backend_name,
                duration_ms=_elapsed_ms(started),
                provider_calls=result.provider_calls,
                stage_attempts=dict(result.stage_attempts),
                transient_retries=transient_retries,
                rejects=rejects,
                warnings=warnings,
                model_name=self.model_name,
                error="report failed deterministic validation and was not saved",
            )
            self._record(outcome, started_at)
            return outcome

        report_id = str(uuid.uuid4())
        generated_at = self.clock()
        public = build_public_report(
            result.rendered,
            report_id=report_id,
            generated_at=generated_at,
            backend=backend_name,
            model_name=self.model_name,
            pack_hash=artifact.pack_hash,
        )
        stored = _to_stored(public, artifact, result.provider_calls)

        persisted = False
        save_error: str | None = None
        try:
            self.repository.save_report(stored)
            persisted = True
        except RepositoryError as exc:
            save_error = str(exc)
            log.error("failed to persist report for %s: %s", team_id, exc)

        outcome = GenerationOutcome(
            status=RUN_STATUS_SUCCEEDED,
            team_id=team_id,
            backend=backend_name,
            duration_ms=_elapsed_ms(started),
            report=public,
            stored_report_id=report_id,
            persisted=persisted,
            provider_calls=result.provider_calls,
            stage_attempts=dict(result.stage_attempts),
            transient_retries=transient_retries,
            warnings=warnings,
            model_name=self.model_name,
            error=save_error,
        )
        self._record(outcome, started_at)
        return outcome

    # -- internals -----------------------------------------------------------

    def _build_backend(self) -> AgentBackend:
        if self.backend_factory is None:
            raise GenerationUnavailableError(
                "no agent backend is configured for this instance"
            )
        return self.backend_factory()

    def _record(self, outcome: GenerationOutcome, started_at: str) -> None:
        """Append the audit row. Never allowed to fail a successful generation."""
        run = GenerationRun(
            run_id=str(uuid.uuid4()),
            team_id=outcome.team_id,
            started_at=started_at,
            finished_at=self.clock(),
            duration_ms=outcome.duration_ms,
            status=outcome.status,
            backend=outcome.backend,
            model_name=outcome.model_name,
            provider_calls=outcome.provider_calls,
            stage_attempts=dict(outcome.stage_attempts),
            rejects_n=len(outcome.rejects),
            warnings_n=len(outcome.warnings),
            report_id=outcome.stored_report_id if outcome.persisted else None,
            error_message=(outcome.error or None),
        )
        try:
            self.repository.record_generation_run(run)
        except Exception as exc:  # noqa: BLE001 - audit is best-effort by contract
            log.warning("could not record generation run for %s: %s", outcome.team_id, exc)


# ---- mapping helpers --------------------------------------------------------


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _to_public(stored: StoredReport) -> PublicReport:
    """Storage row -> public contract, tolerating an older stored shape."""
    return PublicReport.model_validate(stored.report_json)


def _to_stored(
    report: PublicReport, artifact: PackArtifact, provider_calls: int
) -> StoredReport:
    validation_summary: dict[str, Any] = {
        "ok": report.validation.ok,
        "rejects_n": report.validation.rejects_n,
        "warnings_n": report.validation.warnings_n,
        "warnings": [note.model_dump(mode="json") for note in report.validation.warnings],
        "provider_calls": provider_calls,
    }
    return StoredReport(
        report_id=report.report_id,
        team_id=report.team_id,
        team_name=report.team_name,
        season=report.season,
        generated_at=report.generated_at,
        report_version=REPORT_CONTRACT_VERSION,
        evidence_version=artifact.artifact_version,
        definition_version=artifact.pack.definition_version,
        backend=report.backend,
        model_name=report.model_name,
        pack_hash=artifact.pack_hash,
        report_json=report.model_dump(mode="json"),
        evidence_json=artifact.model_dump(mode="json"),
        validation_summary=validation_summary,
    )
