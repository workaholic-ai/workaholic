"""Transport-neutral Phase 4 execution over one trusted local scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, Protocol

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    TaskClaimResult,
    TaskProgressResult,
    TaskResultInput,
    TaskSubmissionResult,
)
from workaholic.domain import (
    AttemptId,
    Project,
    ProjectId,
    SubjectId,
    TaskId,
    TaskProgress,
)
from workaholic.session.models import (
    AgentHeartbeatRequest,
    AgentProgressRequest,
    AgentReleaseRequest,
    AgentSubmitRequest,
    AgentTaskClaimRequest,
    HumanClaimReleaseRequest,
    HumanClaimRenewRequest,
    HumanTaskClaimRequest,
)

if TYPE_CHECKING:
    from datetime import timedelta


class PhaseFourClaimService(Protocol):
    """Claim application capabilities consumed by Phase 4 Sessions."""

    def claim_task(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        lease_duration: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Acquire one targeted Human Claim."""
        ...

    def claim_next_task(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        lease_duration: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Pull one ready Task for a new Agent Attempt."""
        ...

    def renew_claim(  # noqa: PLR0913 - explicit owner token is the contract.
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId | None,
        lease_duration: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Renew one exact Human or Agent owner token."""
        ...

    def release_claim(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId | None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Release one exact Human or Agent owner token."""
        ...


class PhaseFourExecutionService(Protocol):
    """Agent progress and Result capabilities consumed by Sessions."""

    def report_progress(  # noqa: PLR0913 - Attempt attribution is explicit.
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId,
        progress: TaskProgress,
        idempotency_key: str | None = None,
    ) -> TaskProgressResult:
        """Append progress for one exact Agent Attempt."""
        ...

    def submit_result(  # noqa: PLR0913 - optimistic owner contract is explicit.
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId,
        expected_version: int,
        result: TaskResultInput,
        idempotency_key: str | None = None,
    ) -> TaskSubmissionResult:
        """Submit one Result through an exact Agent Attempt."""
        ...


@dataclass(frozen=True, slots=True)
class PhaseFourScope:
    """One trusted bootstrap Subject, Project, and execution capability set."""

    subject_id: SubjectId
    project: Project
    claims: PhaseFourClaimService
    execution: PhaseFourExecutionService

    def __post_init__(self) -> None:
        """Validate the trusted scope and explicit capability surface."""
        subject_value: object = self.subject_id
        project_value: object = self.project
        if not isinstance(subject_value, SubjectId) or not isinstance(
            project_value,
            Project,
        ):
            message = "Phase 4 Session scope requires typed local entities."
            raise TypeError(message)
        for method_name in (
            "claim_task",
            "claim_next_task",
            "renew_claim",
            "release_claim",
        ):
            _require_callable(self.claims, method_name, "Claim service")
        for method_name in ("report_progress", "submit_result"):
            _require_callable(self.execution, method_name, "execution service")


class PhaseFourScopeResolver(Protocol):
    """Resolve one request into a trusted local Phase 4 Project scope."""

    def __call__(self, *, project: str | None) -> PhaseFourScope:
        """Resolve the bootstrap Subject, Project, and semantic services."""
        ...


class LocalExecutionOperations:
    """Implement explicit Human and Agent Session execution paths."""

    def __init__(self, resolve_scope: PhaseFourScopeResolver) -> None:
        """Initialize one trusted per-request scope resolver.

        Args:
            resolve_scope: Profile, bootstrap Subject, and Project resolver.

        Raises:
            TypeError: If the resolver is not callable.

        """
        resolver_value: object = resolve_scope
        if not callable(resolver_value):
            message = "Phase 4 Session scope resolver must be callable."
            raise TypeError(message)
        self._resolve_scope = resolve_scope

    def claim_task(self, request: HumanTaskClaimRequest) -> TaskClaimResult:
        """Acquire one targeted Claim for the bootstrap Human."""
        candidate = _require_request(request, HumanTaskClaimRequest, "Human Claim")
        scope = self._scope(candidate.project)
        result = scope.claims.claim_task(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            lease_duration=candidate.lease,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=False,
            released=False,
            expected_attempt=None,
            label="Human Claim",
        )

    def claim_next_task(self, request: AgentTaskClaimRequest) -> TaskClaimResult:
        """Pull one ready Project Task for a new Agent Attempt."""
        candidate = _require_request(request, AgentTaskClaimRequest, "Agent Claim")
        scope = self._scope(candidate.project)
        result = scope.claims.claim_next_task(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            lease_duration=candidate.lease,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=True,
            released=False,
            expected_attempt=None,
            label="Agent Claim",
        )

    def renew_claim(self, request: HumanClaimRenewRequest) -> TaskClaimResult:
        """Renew one targeted Claim owned by the bootstrap Human."""
        candidate = _require_request(
            request,
            HumanClaimRenewRequest,
            "Human Claim renewal",
        )
        scope = self._scope(candidate.project)
        result = scope.claims.renew_claim(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=None,
            lease_duration=candidate.lease,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=False,
            released=False,
            expected_attempt=None,
            label="Human Claim renewal",
        )

    def heartbeat_attempt(self, request: AgentHeartbeatRequest) -> TaskClaimResult:
        """Renew one exact active Agent Attempt's Claim."""
        candidate = _require_request(
            request,
            AgentHeartbeatRequest,
            "Agent heartbeat",
        )
        scope = self._scope(candidate.project)
        result = scope.claims.renew_claim(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=candidate.attempt,
            lease_duration=candidate.lease,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=True,
            released=False,
            expected_attempt=candidate.attempt,
            label="Agent heartbeat",
        )

    def release_claim(self, request: HumanClaimReleaseRequest) -> TaskClaimResult:
        """Release one targeted Claim owned by the bootstrap Human."""
        candidate = _require_request(
            request,
            HumanClaimReleaseRequest,
            "Human Claim release",
        )
        scope = self._scope(candidate.project)
        result = scope.claims.release_claim(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=None,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=False,
            released=True,
            expected_attempt=None,
            label="Human Claim release",
        )

    def release_attempt(self, request: AgentReleaseRequest) -> TaskClaimResult:
        """Release one exact active Agent Attempt and its Claim."""
        candidate = _require_request(request, AgentReleaseRequest, "Agent release")
        scope = self._scope(candidate.project)
        result = scope.claims.release_claim(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=candidate.attempt,
            idempotency_key=candidate.idempotency_key,
        )
        return _require_claim_result(
            result,
            scope=scope,
            agent=True,
            released=True,
            expected_attempt=candidate.attempt,
            label="Agent release",
        )

    def report_progress(self, request: AgentProgressRequest) -> TaskProgressResult:
        """Report structured progress for one exact active Agent Attempt."""
        candidate = _require_request(request, AgentProgressRequest, "Agent progress")
        scope = self._scope(candidate.project)
        result: object = scope.execution.report_progress(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=candidate.attempt,
            progress=candidate.progress,
            idempotency_key=candidate.idempotency_key,
        )
        if (
            not isinstance(result, TaskProgressResult)
            or result.task.project_id != scope.project.id
            or result.claim.subject_id != scope.subject_id
            or result.claim.attempt_id != candidate.attempt
            or result.attempt.id != candidate.attempt
            or result.attempt.subject_id != scope.subject_id
        ):
            _raise_internal_result("Agent progress")
        return result

    def submit_agent_result(
        self,
        request: AgentSubmitRequest,
    ) -> TaskSubmissionResult:
        """Submit one structured Result through an exact Agent Attempt."""
        candidate = _require_request(
            request,
            AgentSubmitRequest,
            "Agent Result submission",
        )
        scope = self._scope(candidate.project)
        result: object = scope.execution.submit_result(
            project_id=scope.project.id,
            subject_id=scope.subject_id,
            task=candidate.task,
            attempt_id=candidate.attempt,
            expected_version=candidate.expected_version,
            result=candidate.result,
            idempotency_key=candidate.idempotency_key,
        )
        if (
            not isinstance(result, TaskSubmissionResult)
            or result.task.project_id != scope.project.id
            or result.result.submitted_by != scope.subject_id
            or result.result.attempt_id != candidate.attempt
            or result.attempt is None
            or result.attempt.id != candidate.attempt
            or result.attempt.subject_id != scope.subject_id
        ):
            _raise_internal_result("Agent Result submission")
        return result

    def _scope(self, project: str | None) -> PhaseFourScope:
        """Resolve and validate one exact Project execution scope."""
        scope: object = self._resolve_scope(project=project)
        if not isinstance(scope, PhaseFourScope):
            _raise_internal_result("Phase 4 Session scope")
        return scope


def _require_request[T](value: object, request_type: type[T], label: str) -> T:
    """Require one exact validated request type without subclass dispatch."""
    if type(value) is not request_type:
        _raise_invalid_input(f"{label} Session request is invalid.")
    return value


def _require_claim_result(  # noqa: PLR0913 - result ownership is explicit.
    value: object,
    *,
    scope: PhaseFourScope,
    agent: bool,
    released: bool,
    expected_attempt: AttemptId | None,
    label: str,
) -> TaskClaimResult:
    """Require one Claim result matching the selected owner command path."""
    if (
        not isinstance(value, TaskClaimResult)
        or value.task.project_id != scope.project.id
    ):
        _raise_internal_result(label)
    if released:
        valid_owner = value.claim is None and (
            (
                agent
                and value.attempt is not None
                and value.attempt.subject_id == scope.subject_id
                and value.attempt.id == expected_attempt
            )
            or (not agent and value.attempt is None)
        )
    else:
        valid_owner = (
            value.claim is not None
            and value.claim.subject_id == scope.subject_id
            and ((value.attempt is not None) is agent)
            and ((value.claim.attempt_id is not None) is agent)
            and (
                not agent
                or (
                    value.attempt is not None
                    and value.attempt.subject_id == scope.subject_id
                    and value.claim.attempt_id == value.attempt.id
                    and (
                        expected_attempt is None or value.attempt.id == expected_attempt
                    )
                )
            )
        )
    if not valid_owner:
        _raise_internal_result(label)
    return value


def _require_callable(value: object, method_name: str, label: str) -> None:
    """Require one explicitly named callable dependency method."""
    if not callable(getattr(value, method_name, None)):
        message = f"Phase 4 Session {label} must provide {method_name}()."
        raise TypeError(message)


def _raise_invalid_input(message: str) -> Never:
    """Raise one stable invalid Session-input failure."""
    raise ApplicationError(ApplicationErrorCode.INVALID_INPUT, message)


def _raise_internal_result(label: str) -> Never:
    """Raise one stable malformed-dependency-result failure."""
    raise ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{label} returned an invalid result.",
    )
