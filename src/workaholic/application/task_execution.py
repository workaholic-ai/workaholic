"""Application orchestration for Agent progress and Result submission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from workaholic.application.commands import (
    GetTask,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskResultInput,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import TaskProgressResult, TaskSubmissionResult
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
    ProjectId,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskEvent,
    TaskEventType,
    TaskId,
    TaskProgress,
    TaskResult,
)

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, ExecutionIdentifierFactory

_IDEMPOTENCY_KEY_MAX_LENGTH = 128


class _ExecutionRepository(Protocol):
    """Minimal query and semantic persistence surface for Agent execution."""

    def get_task(self, command: GetTask) -> Task:
        """Resolve one authorized scoped Task."""
        ...

    def report_task_progress(
        self,
        mutation: ReportTaskProgressMutation,
    ) -> TaskProgressResult:
        """Persist one current-Agent progress batch."""
        ...

    def submit_agent_result(
        self,
        mutation: SubmitAgentResultMutation,
    ) -> TaskSubmissionResult:
        """Persist one current-Agent Result submission."""
        ...


class TaskExecutionApplication:
    """Build Agent mutations from trusted Session ownership attribution."""

    def __init__(
        self,
        repository: _ExecutionRepository,
        clock: Clock,
        identifiers: ExecutionIdentifierFactory,
    ) -> None:
        """Initialize explicit Agent-execution dependencies.

        Args:
            repository: Authorized Task query and Agent mutation boundary.
            clock: Authoritative transaction clock.
            identifiers: Opaque Result, event, and request identity factory.

        Raises:
            TypeError: If a dependency lacks one required operation.

        """
        for method_name in (
            "get_task",
            "report_task_progress",
            "submit_agent_result",
        ):
            _require_callable(repository, method_name, "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_result_id", "new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def report_progress(  # noqa: PLR0913 - explicit Attempt attribution is required.
        self,
        *,
        project_id: ProjectId,
        subject_id: SubjectId,
        task: TaskId | str,
        attempt_id: AttemptId,
        progress: TaskProgress,
        idempotency_key: str | None = None,
    ) -> TaskProgressResult:
        """Append one structured progress report for a current Agent Attempt.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Subject identity.
            task: Canonical Task ID or stable Human key.
            attempt_id: Exact current Agent owner token from trusted Session input.
            progress: Validated caller-controlled structured progress.
            idempotency_key: Optional caller replay key.

        Returns:
            Unchanged Task and ownership plus ordered progress events.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Agent progress"
        task_uid = self._resolve_task_uid(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        )
        _validate_agent_input(
            attempt_id=attempt_id,
            payload=progress,
            payload_type=TaskProgress,
            idempotency_key=idempotency_key,
            operation=operation,
        )
        try:
            progress_event_id = self._identifiers.new_event_id()
            observation_event_ids = tuple(
                self._identifiers.new_event_id()
                for _observation in (progress.observations or ())
            )
            mutation = ReportTaskProgressMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                task_uid=task_uid,
                attempt_id=attempt_id,
                progress=progress,
                progress_reported_event_id=progress_event_id,
                observation_event_ids=observation_event_ids,
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.report_task_progress(mutation)
        if not isinstance(result, TaskProgressResult) or not _matches_progress(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def submit_result(  # noqa: PLR0913 - explicit optimistic owner contract.
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
        """Submit structured work through one exact current Agent Attempt.

        Args:
            project_id: Selected authorized Project identity.
            subject_id: Trusted bootstrap Subject identity.
            task: Canonical Task ID or stable Human key.
            attempt_id: Exact current Agent owner token from trusted Session input.
            expected_version: Optimistic Task version returned by Claim acquisition.
            result: Validated caller-controlled Result content without identities.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed Task, Result, terminal Attempt, and submission events.

        Raises:
            ApplicationError: If input, dependencies, persistence, or output fails.

        """
        operation = "Agent Result submission"
        resolved = self._resolve_task(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        )
        _validate_agent_input(
            attempt_id=attempt_id,
            payload=result,
            payload_type=TaskResultInput,
            idempotency_key=idempotency_key,
            operation=operation,
        )
        if type(expected_version) is not int or expected_version < 1:
            raise _invalid_input(operation)
        try:
            result_id = self._identifiers.new_result_id()
            submitted_event_id = self._identifiers.new_event_id()
            completed_event_id = (
                self._identifiers.new_event_id()
                if resolved.approval is ApprovalRequirement.NONE
                else None
            )
            mutation = SubmitAgentResultMutation(
                project_id=project_id,
                actor_subject_id=subject_id,
                task_uid=resolved.uid,
                attempt_id=attempt_id,
                expected_version=expected_version,
                result_id=result_id,
                result_submitted_event_id=submitted_event_id,
                task_completed_event_id=completed_event_id,
                result=result,
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                idempotency_key=idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        submitted: object = self._repository.submit_agent_result(mutation)
        if not isinstance(submitted, TaskSubmissionResult) or not _matches_submission(
            submitted,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return submitted

    def _resolve_task_uid(
        self,
        *,
        project_id: object,
        subject_id: object,
        task: object,
        operation: str,
    ) -> TaskId:
        """Resolve a Human key only when no canonical Task ID was supplied."""
        _validate_scope(project_id, subject_id, operation=operation)
        if isinstance(task, TaskId):
            return task
        return self._resolve_task(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            operation=operation,
        ).uid

    def _resolve_task(
        self,
        *,
        project_id: object,
        subject_id: object,
        task: object,
        operation: str,
    ) -> Task:
        """Resolve and validate one authoritative scoped Task exactly once."""
        _validate_scope(project_id, subject_id, operation=operation)
        try:
            query = GetTask(
                project_id=cast("ProjectId", project_id),
                subject_id=cast("SubjectId", subject_id),
                task=cast("TaskId | str", task),
            )
        except (TypeError, ValueError) as error:
            raise _invalid_input(operation) from error
        resolved: object = self._repository.get_task(query)
        selector_matches = isinstance(resolved, Task) and (
            resolved.uid == task if isinstance(task, TaskId) else resolved.key == task
        )
        if (
            not isinstance(resolved, Task)
            or resolved.project_id != project_id
            or not selector_matches
        ):
            resolution_operation = f"{operation} Task resolution"
            raise _invalid_result(resolution_operation)
        return resolved


def _matches_progress(
    result: TaskProgressResult,
    *,
    mutation: ReportTaskProgressMutation,
) -> bool:
    """Return whether persistence honored one exact structured progress contract."""
    events = result.events
    expected_ids = (
        mutation.progress_reported_event_id,
        *mutation.observation_event_ids,
    )
    expected_types = (
        TaskEventType.PROGRESS_REPORTED,
        *(TaskEventType.OBSERVATION_ADDED for _ in mutation.observation_event_ids),
    )
    expected_payloads = (
        _progress_payload(mutation.progress),
        *tuple(
            {
                "kind": cast("object", observation.kind.value),
                "text": cast("object", observation.text),
            }
            for observation in (mutation.progress.observations or ())
        ),
    )
    return (
        result.task.uid == mutation.task_uid
        and result.task.project_id == mutation.project_id
        and result.claim.subject_id == mutation.actor_subject_id
        and result.claim.attempt_id == mutation.attempt_id
        and result.attempt.id == mutation.attempt_id
        and result.attempt.status is AttemptStatus.ACTIVE
        and tuple(event.event_type for event in events) == expected_types
        and tuple(dict(event.payload) for event in events) == expected_payloads
        and all(
            event.actor_subject_id == mutation.actor_subject_id
            and event.attempt_id == mutation.attempt_id
            for event in events
        )
        and _matches_generated_events(
            events,
            expected_ids=expected_ids,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            idempotency_key=mutation.idempotency_key,
        )
    )


def _matches_submission(
    outcome: TaskSubmissionResult,
    *,
    mutation: SubmitAgentResultMutation,
) -> bool:
    """Return whether persistence honored one exact Agent submission contract."""
    task = outcome.task
    result = outcome.result
    events = outcome.events
    expected_types = (
        (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)
        if result.review.status is ResultReviewStatus.NOT_REQUIRED
        else (TaskEventType.RESULT_SUBMITTED,)
    )
    expected_ids = (
        (mutation.result_submitted_event_id, mutation.task_completed_event_id)
        if mutation.task_completed_event_id is not None
        else (mutation.result_submitted_event_id,)
    )
    expected_payloads = tuple(
        _submission_payload(event_type, task=task, result=result)
        for event_type in expected_types
    )
    return (
        task.uid == mutation.task_uid
        and task.project_id == mutation.project_id
        and task.version == mutation.expected_version + 1
        and result.task_uid == task.uid
        and result.submitted_by == mutation.actor_subject_id
        and result.attempt_id == mutation.attempt_id
        and result.comment is None
        and _result_content_matches(result, mutation.result)
        and (
            (
                result.review.status is ResultReviewStatus.NOT_REQUIRED
                and task.approval is ApprovalRequirement.NONE
            )
            or (
                result.review.status is ResultReviewStatus.PENDING
                and task.approval is ApprovalRequirement.HUMAN
            )
        )
        and outcome.attempt is not None
        and outcome.attempt.id == mutation.attempt_id
        and outcome.attempt.status is AttemptStatus.SUBMITTED
        and tuple(event.event_type for event in events) == expected_types
        and tuple(dict(event.payload) for event in events) == expected_payloads
        and all(
            event.actor_subject_id == mutation.actor_subject_id
            and event.attempt_id == mutation.attempt_id
            for event in events
        )
        and _matches_generated_events(
            events,
            expected_ids=expected_ids,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            idempotency_key=mutation.idempotency_key,
        )
        and (mutation.idempotency_key is not None or result.id == mutation.result_id)
    )


def _matches_generated_events(
    events: tuple[TaskEvent, ...],
    *,
    expected_ids: tuple[object, ...],
    request_id: object,
    occurred_at: object,
    idempotency_key: str | None,
) -> bool:
    """Return whether generated attribution is exact or safely replayed."""
    if len(events) != len(expected_ids):
        return False
    if idempotency_key is not None:
        return True
    return all(
        event.id == event_id
        and event.request_id == request_id
        and event.occurred_at == occurred_at
        for event, event_id in zip(events, expected_ids, strict=True)
    )


def _progress_payload(progress: TaskProgress) -> dict[str, object]:
    """Build the exact supplied progress-header payload."""
    payload: dict[str, object] = {}
    if progress.message is not None:
        payload["message"] = progress.message
    if progress.percent_complete is not None:
        payload["percent_complete"] = progress.percent_complete
    return payload


def _submission_payload(
    event_type: TaskEventType,
    *,
    task: Task,
    result: TaskResult,
) -> dict[str, object]:
    """Build the exact closed payload for one Agent submission event."""
    payload: dict[str, object] = {
        "result_id": str(result.id),
        "version": task.version,
    }
    if event_type is TaskEventType.RESULT_SUBMITTED:
        payload["review_status"] = result.review.status.value
    return payload


def _result_content_matches(result: TaskResult, expected: TaskResultInput) -> bool:
    """Compare caller-controlled Result content without generated identities."""
    return (
        result.summary == expected.summary
        and result.criteria == expected.criteria
        and result.artifacts == expected.artifacts
        and result.proposed_follow_ups == expected.proposed_follow_ups
    )


def _validate_agent_input(
    *,
    attempt_id: object,
    payload: object,
    payload_type: type[object],
    idempotency_key: object,
    operation: str,
) -> None:
    """Validate trusted Attempt attribution and one structured caller payload."""
    if not isinstance(attempt_id, AttemptId) or not isinstance(payload, payload_type):
        raise _invalid_input(operation)
    _validate_idempotency_key(idempotency_key, operation=operation)


def _validate_scope(
    project_id: object,
    subject_id: object,
    *,
    operation: str,
) -> None:
    """Require trusted typed Project and Subject attribution."""
    if not isinstance(project_id, ProjectId) or not isinstance(subject_id, SubjectId):
        raise _invalid_input(operation)


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
        message = f"Task execution {label} must provide {method_name}()."
        raise TypeError(message)


def _invalid_input(operation: str) -> ApplicationError:
    """Build one stable invalid Agent-execution input failure."""
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
