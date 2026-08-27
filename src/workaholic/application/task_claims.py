"""Application orchestration for Human Claims and Agent Claim acquisition."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from workaholic.application.commands import (
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetTask,
    ReleaseClaimMutation,
    RenewClaimMutation,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import TaskClaimResult
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    DomainValidationError,
    ProjectId,
    SubjectId,
    Task,
    TaskEvent,
    TaskEventType,
    TaskId,
    parse_rfc3339_utc_timestamp,
    resolve_lease_duration,
)

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, ExecutionIdentifierFactory

type _ClaimMutation = ClaimTaskMutation | ClaimNextTaskMutation
type _LeaseMutation = RenewClaimMutation | ReleaseClaimMutation

_IDEMPOTENCY_KEY_MAX_LENGTH = 128


class _ClaimRepository(Protocol):
    """Minimal query and semantic persistence surface for Claim services."""

    def get_task(self, command: GetTask) -> Task:
        """Resolve one authorized scoped Task."""
        ...

    def claim_task(self, mutation: ClaimTaskMutation) -> TaskClaimResult:
        """Persist one targeted Human Claim operation."""
        ...

    def claim_next_task(self, mutation: ClaimNextTaskMutation) -> TaskClaimResult:
        """Persist one Project-scoped Agent pull operation."""
        ...

    def renew_claim(self, mutation: RenewClaimMutation) -> TaskClaimResult:
        """Persist one exact-owner Lease renewal."""
        ...

    def release_claim(self, mutation: ReleaseClaimMutation) -> TaskClaimResult:
        """Persist one exact-owner Claim release."""
        ...


class TaskClaimApplication:
    """Build Claim mutations from trusted Session attribution and caller intent."""

    def __init__(
        self,
        repository: _ClaimRepository,
        clock: Clock,
        identifiers: ExecutionIdentifierFactory,
    ) -> None:
        """Initialize explicit Claim-service dependencies.

        Args:
            repository: Authorized Task query and Claim mutation boundary.
            clock: Authoritative transaction clock.
            identifiers: Opaque Attempt, event, and request identity factory.

        Raises:
            TypeError: If a dependency lacks one required operation.

        """
        for method_name in (
            "get_task",
            "claim_task",
            "claim_next_task",
            "renew_claim",
            "release_claim",
        ):
            _require_callable(repository, method_name, "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_attempt_id", "new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def claim_task(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        lease_duration: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Acquire one explicit ready Task for the trusted Human Subject.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Human Subject identity.
            task: Canonical Task ID or stable Human key.
            lease_duration: Optional Human Lease duration; null uses ``8h``.
            idempotency_key: Optional caller replay key.

        Returns:
            New, current no-op, or idempotently replayed Human Claim.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Human Claim"
        task_uid = self._resolve_task_uid(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        )
        duration_seconds = _resolve_duration_seconds(
            lease_duration,
            attempt_id=None,
            operation=operation,
        )
        _validate_idempotency_key(idempotency_key, operation=operation)
        try:
            mutation = ClaimTaskMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                task_uid=task_uid,
                lease_duration_seconds=duration_seconds,
                task_claimed_event_id=self._identifiers.new_event_id(),
                claim_expired_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.claim_task(mutation)
        if not isinstance(result, TaskClaimResult) or not _matches_acquisition(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def claim_next_task(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        lease_duration: timedelta | None = None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Pull the highest-ranked ready Task for one new Agent Attempt.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Subject identity.
            lease_duration: Optional Agent Lease duration; null uses ``15m``.
            idempotency_key: Optional caller replay key.

        Returns:
            Selected Task with current Agent Claim and active Attempt.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Agent Claim"
        _validate_scope(project_id, subject_id, operation=operation)
        _validate_idempotency_key(idempotency_key, operation=operation)
        try:
            attempt_id = _require_generated_attempt(self._identifiers.new_attempt_id())
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        duration_seconds = _resolve_duration_seconds(
            lease_duration,
            attempt_id=attempt_id,
            operation=operation,
        )
        try:
            mutation = ClaimNextTaskMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                attempt_id=attempt_id,
                lease_duration_seconds=duration_seconds,
                task_claimed_event_id=self._identifiers.new_event_id(),
                claim_expired_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.claim_next_task(mutation)
        if not isinstance(result, TaskClaimResult) or not _matches_acquisition(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

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
        """Renew a Human Claim or heartbeat one exact Agent Attempt.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Subject identity.
            task: Canonical Task ID or stable Human key.
            attempt_id: Null for Human renewal or the exact Agent owner token.
            lease_duration: Optional owner-specific duration; null uses its default.
            idempotency_key: Optional caller replay key.

        Returns:
            Renewed current Claim and nullable active Attempt.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Claim renewal"
        task_uid = self._resolve_task_uid(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        )
        _validate_attempt_id(attempt_id, operation=operation)
        duration_seconds = _resolve_duration_seconds(
            lease_duration,
            attempt_id=attempt_id,
            operation=operation,
        )
        _validate_idempotency_key(idempotency_key, operation=operation)
        try:
            mutation = RenewClaimMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                task_uid=task_uid,
                attempt_id=attempt_id,
                lease_duration_seconds=duration_seconds,
                claim_renewed_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.renew_claim(mutation)
        if not isinstance(result, TaskClaimResult) or not _matches_lease_operation(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def release_claim(
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId | None,
        idempotency_key: str | None = None,
    ) -> TaskClaimResult:
        """Release one exact current Human or Agent Claim owner token.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Subject identity.
            task: Canonical Task ID or stable Human key.
            attempt_id: Null for Human release or the exact Agent owner token.
            idempotency_key: Optional caller replay key.

        Returns:
            Released ownership and nullable terminal Agent Attempt.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Claim release"
        task_uid = self._resolve_task_uid(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        )
        _validate_attempt_id(attempt_id, operation=operation)
        _validate_idempotency_key(idempotency_key, operation=operation)
        try:
            mutation = ReleaseClaimMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                task_uid=task_uid,
                attempt_id=attempt_id,
                claim_released_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.release_claim(mutation)
        if not isinstance(result, TaskClaimResult) or not _matches_lease_operation(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def _resolve_task_uid(
        self,
        *,
        project_id: object,
        subject_id: object,
        task: object,
        operation: str,
    ) -> TaskId:
        """Validate one scope and resolve a Human Task key exactly once.

        Args:
            project_id: Candidate Project identity.
            subject_id: Candidate trusted Subject identity.
            task: Candidate canonical or Human Task selector.
            operation: Safe operation label for failures.

        Returns:
            Canonical Task identity.

        Raises:
            ApplicationError: If input or query output violates its contract.

        """
        _validate_scope(project_id, subject_id, operation=operation)
        if isinstance(task, TaskId):
            return task
        try:
            query = GetTask(
                project_id=cast("ProjectId", project_id),
                subject_id=cast("SubjectId", subject_id),
                task=cast("TaskId | str", task),
            )
        except (TypeError, ValueError) as error:
            raise _invalid_input(operation) from error
        resolved: object = self._repository.get_task(query)
        if (
            not isinstance(resolved, Task)
            or resolved.project_id != project_id
            or resolved.key != task
        ):
            resolution_operation = f"{operation} Task resolution"
            raise _invalid_result(resolution_operation)
        return resolved.uid


def _matches_acquisition(  # noqa: PLR0911 - closed outcome shapes fail fast.
    result: TaskClaimResult,
    *,
    mutation: _ClaimMutation,
) -> bool:
    """Return whether persistence honored one exact Claim acquisition contract."""
    claim = result.claim
    if (
        claim is None
        or result.task.project_id != mutation.project_id
        or claim.task_uid != result.task.uid
        or claim.subject_id != mutation.actor_subject_id
    ):
        return False
    if isinstance(mutation, ClaimTaskMutation):
        is_human = True
        if (
            result.task.uid != mutation.task_uid
            or claim.attempt_id is not None
            or result.attempt is not None
        ):
            return False
    else:
        is_human = False
        if (
            claim.attempt_id is None
            or result.attempt is None
            or result.attempt.status is not AttemptStatus.ACTIVE
            or (
                mutation.idempotency_key is None
                and result.attempt.id != mutation.attempt_id
            )
        ):
            return False
    if not result.events:
        return is_human
    expected_types = (
        (TaskEventType.TASK_CLAIMED,)
        if len(result.events) == 1
        else (TaskEventType.CLAIM_EXPIRED, TaskEventType.TASK_CLAIMED)
    )
    if tuple(event.event_type for event in result.events) != expected_types:
        return False
    claimed_event = result.events[-1]
    if (
        claimed_event.actor_subject_id != mutation.actor_subject_id
        or claimed_event.attempt_id != claim.attempt_id
        or claimed_event.occurred_at != claim.claimed_at
        or claim.lease_expires_at - claim.claimed_at
        != timedelta(seconds=mutation.lease_duration_seconds)
        or not _payload_matches_expiry(claimed_event, claim.lease_expires_at)
        or not _matches_fresh_event(
            claimed_event,
            event_id=mutation.task_claimed_event_id,
            mutation=mutation,
        )
    ):
        return False
    if len(result.events) == 1:
        return True
    expired = result.events[0]
    try:
        expired_at = _payload_expiry(expired)
    except DomainValidationError:
        return False
    return (
        expired_at <= claim.claimed_at
        and expired.occurred_at == claimed_event.occurred_at
        and expired.request_id == claimed_event.request_id
        and _matches_fresh_event(
            expired,
            event_id=mutation.claim_expired_event_id,
            mutation=mutation,
        )
    )


def _matches_lease_operation(  # noqa: PLR0911 - closed outcomes fail fast.
    result: TaskClaimResult,
    *,
    mutation: _LeaseMutation,
) -> bool:
    """Return whether persistence honored one exact renewal or release contract."""
    if (
        result.task.uid != mutation.task_uid
        or result.task.project_id != mutation.project_id
        or len(result.events) != 1
    ):
        return False
    event = result.events[0]
    try:
        payload_expiry = _payload_expiry(event)
    except DomainValidationError:
        return False
    expected_event_id = (
        mutation.claim_renewed_event_id
        if isinstance(mutation, RenewClaimMutation)
        else mutation.claim_released_event_id
    )
    if (
        event.actor_subject_id != mutation.actor_subject_id
        or event.attempt_id != mutation.attempt_id
        or not _matches_fresh_event(
            event,
            event_id=expected_event_id,
            mutation=mutation,
        )
    ):
        return False
    if isinstance(mutation, RenewClaimMutation):
        claim = result.claim
        return (
            event.event_type is TaskEventType.CLAIM_RENEWED
            and claim is not None
            and claim.subject_id == mutation.actor_subject_id
            and claim.attempt_id == mutation.attempt_id
            and claim.lease_expires_at == payload_expiry
            and claim.lease_expires_at
            == event.occurred_at + timedelta(seconds=mutation.lease_duration_seconds)
        )
    if (
        event.event_type is not TaskEventType.CLAIM_RELEASED
        or result.claim is not None
        or payload_expiry <= event.occurred_at
    ):
        return False
    if mutation.attempt_id is None:
        return result.attempt is None
    return (
        result.attempt is not None
        and result.attempt.id == mutation.attempt_id
        and result.attempt.subject_id == mutation.actor_subject_id
        and result.attempt.status is AttemptStatus.RELEASED
        and result.attempt.lease_expires_at == payload_expiry
        and result.attempt.ended_at == event.occurred_at
    )


def _matches_fresh_event(
    event: TaskEvent,
    *,
    event_id: object,
    mutation: _ClaimMutation | _LeaseMutation,
) -> bool:
    """Return whether generated event attribution is exact or replay-safe."""
    return mutation.idempotency_key is not None or (
        event.id == event_id
        and event.request_id == mutation.request_id
        and event.occurred_at == mutation.occurred_at
    )


def _payload_matches_expiry(event: TaskEvent, expected: object) -> bool:
    """Return whether one closed Lease payload contains the expected timestamp."""
    try:
        return _payload_expiry(event) == expected
    except DomainValidationError:
        return False


def _payload_expiry(event: TaskEvent) -> datetime:
    """Parse one exact Claim-event Lease expiry payload."""
    if set(event.payload) != {"lease_expires_at"}:
        message = "Claim event Lease payload is invalid."
        raise DomainValidationError(message)
    return parse_rfc3339_utc_timestamp(
        event.payload["lease_expires_at"],
        label="Claim event Lease expiry",
    )


def _resolve_duration_seconds(
    duration: object,
    *,
    attempt_id: AttemptId | None,
    operation: str,
) -> int:
    """Resolve an owner-specific duration to exact whole seconds."""
    try:
        resolved = resolve_lease_duration(duration, attempt_id=attempt_id)
    except (TypeError, ValueError) as error:
        raise _invalid_input(operation) from error
    seconds = resolved.total_seconds()
    if not seconds.is_integer():
        raise _invalid_input(operation)
    return int(seconds)


def _validate_scope(
    project_id: object,
    subject_id: object,
    *,
    operation: str,
) -> None:
    """Require trusted typed Project and Subject attribution."""
    if not isinstance(project_id, ProjectId) or not isinstance(subject_id, SubjectId):
        raise _invalid_input(operation)


def _validate_attempt_id(value: object, *, operation: str) -> None:
    """Require a null Human token or one typed Agent Attempt identity."""
    if value is not None and not isinstance(value, AttemptId):
        raise _invalid_input(operation)


def _require_generated_attempt(value: object) -> AttemptId:
    """Return one typed generated Attempt or reject the dependency value."""
    if not isinstance(value, AttemptId):
        message = "Agent Claim identifier factory returned an invalid Attempt."
        raise TypeError(message)
    return value


def _validate_idempotency_key(value: object, *, operation: str) -> None:
    """Validate one optional bounded opaque replay key before side effects."""
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _IDEMPOTENCY_KEY_MAX_LENGTH
        or any(
            character.isspace() or not character.isprintable() for character in value
        )
    ):
        raise _invalid_input(operation)


def _require_callable(value: object, method_name: str, label: str) -> None:
    """Require one explicitly named callable dependency method."""
    if not callable(getattr(value, method_name, None)):
        message = f"Task Claim {label} must provide {method_name}()."
        raise TypeError(message)


def _invalid_input(operation: str) -> ApplicationError:
    """Build one stable invalid Claim-service input failure."""
    return ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        f"{operation} input is invalid.",
    )


def _invalid_dependencies(operation: str) -> ApplicationError:
    """Build one safe generated-dependency failure."""
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{operation} dependencies returned invalid values.",
    )


def _invalid_result(operation: str) -> ApplicationError:
    """Build one safe malformed-persistence-result failure."""
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{operation} persistence returned an invalid result.",
    )
