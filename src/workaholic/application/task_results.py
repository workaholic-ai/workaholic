"""Application orchestration for attributable Human Result transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from workaholic.application.commands import (
    ApproveResultInput,
    ApproveResultMutation,
    GetTask,
    RejectResultInput,
    RejectResultMutation,
    SubmitHumanResultInput,
    SubmitHumanResultMutation,
    TaskResultInput,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import TaskSubmissionResult
from workaholic.domain import (
    ApprovalRequirement,
    ResultReviewStatus,
    Task,
    TaskEvent,
    TaskEventType,
    TaskId,
    TaskResult,
    TaskState,
)

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, ResultIdentifierFactory

type _ResultIntent = SubmitHumanResultInput | ApproveResultInput | RejectResultInput
type _ResultMutation = (
    SubmitHumanResultMutation | ApproveResultMutation | RejectResultMutation
)


class _ResultRepository(Protocol):
    """Minimal semantic persistence surface for Human Result operations."""

    def get_task(self, command: GetTask) -> Task:
        """Resolve one authorized scoped Task."""
        ...

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Persist one atomic Human submission."""
        ...

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Persist one atomic Result approval."""
        ...

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Persist one atomic Result rejection."""
        ...


class TaskResultApplication:
    """Construct Human Result mutations without accepting trusted identities."""

    def __init__(
        self,
        repository: _ResultRepository,
        clock: Clock,
        identifiers: ResultIdentifierFactory,
    ) -> None:
        """Initialize explicit Result-operation dependencies.

        Args:
            repository: Semantic Task query and Result mutation boundary.
            clock: Authoritative transaction clock.
            identifiers: Opaque Result, event, and request identity factory.

        Raises:
            TypeError: If a dependency lacks a required method.

        """
        for method_name in (
            "get_task",
            "submit_human_result",
            "approve_result",
            "reject_result",
        ):
            _require_callable(repository, method_name, "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_result_id", "new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def submit(self, command: SubmitHumanResultInput) -> TaskSubmissionResult:
        """Submit manual Human work without creating an Agent Attempt.

        Args:
            command: Validated Human submission intent.

        Returns:
            Committed Task, immutable Result, and ordered semantic events.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        operation = "submission"
        candidate: object = command
        if not isinstance(candidate, SubmitHumanResultInput):
            raise _invalid_input(operation)
        task = self._resolve_task(candidate)
        try:
            mutation = SubmitHumanResultMutation(
                task_uid=task.uid,
                project_id=candidate.project_id,
                actor_subject_id=candidate.subject_id,
                result_id=self._identifiers.new_result_id(),
                result_submitted_event_id=self._identifiers.new_event_id(),
                task_completed_event_id=(
                    self._identifiers.new_event_id()
                    if task.approval is ApprovalRequirement.NONE
                    else None
                ),
                claim_expired_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                comment=candidate.comment,
                result=candidate.result,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.submit_human_result(mutation)
        if not isinstance(result, TaskSubmissionResult) or not _matches_result(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def approve(self, command: ApproveResultInput) -> TaskSubmissionResult:
        """Approve the current pending Result and complete its Task.

        Args:
            command: Validated Human approval intent.

        Returns:
            Committed Task, approved Result, and ordered semantic events.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        operation = "approval"
        candidate: object = command
        if not isinstance(candidate, ApproveResultInput):
            raise _invalid_input(operation)
        task = self._resolve_task(candidate)
        try:
            mutation = ApproveResultMutation(
                task_uid=task.uid,
                project_id=candidate.project_id,
                actor_subject_id=candidate.subject_id,
                review_approved_event_id=self._identifiers.new_event_id(),
                task_completed_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                comment=candidate.comment,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.approve_result(mutation)
        if not isinstance(result, TaskSubmissionResult) or not _matches_result(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def reject(self, command: RejectResultInput) -> TaskSubmissionResult:
        """Reject and deselect the current Result while retaining its audit row.

        Args:
            command: Validated Human rejection intent.

        Returns:
            Reopened Task, rejected Result, and attributable rejection event.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        operation = "rejection"
        candidate: object = command
        if not isinstance(candidate, RejectResultInput):
            raise _invalid_input(operation)
        task = self._resolve_task(candidate)
        try:
            mutation = RejectResultMutation(
                task_uid=task.uid,
                project_id=candidate.project_id,
                actor_subject_id=candidate.subject_id,
                review_rejected_event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                reason=candidate.reason,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_dependencies(operation) from error
        result: object = self._repository.reject_result(mutation)
        if not isinstance(result, TaskSubmissionResult) or not _matches_result(
            result,
            mutation=mutation,
        ):
            raise _invalid_result(operation)
        return result

    def _resolve_task(self, command: _ResultIntent) -> Task:
        """Resolve and validate the authoritative Task for one Result intent.

        Args:
            command: Validated Project-scoped Result intent.

        Returns:
            Authoritative Task selected by canonical ID or Human key.

        Raises:
            ApplicationError: If persistence returns an inconsistent Task.

        """
        result: object = self._repository.get_task(
            GetTask(
                project_id=command.project_id,
                subject_id=command.subject_id,
                task=command.task,
            )
        )
        expected_selector = command.task
        selector_matches = isinstance(result, Task) and (
            result.uid == expected_selector
            if isinstance(expected_selector, TaskId)
            else result.key == expected_selector
        )
        if (
            not isinstance(result, Task)
            or result.project_id != command.project_id
            or not selector_matches
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task Result resolution returned an invalid result.",
            )
        return result


def _matches_result(
    result: TaskSubmissionResult,
    *,
    mutation: _ResultMutation,
) -> bool:
    """Return whether persistence honored one exact Result mutation contract.

    Args:
        result: Candidate persistence result.
        mutation: Mutation sent to persistence.

    Returns:
        Whether state, Result content, attribution, and events match.

    """
    task = result.task
    events = result.events
    has_expiry_prefix = (
        isinstance(mutation, SubmitHumanResultMutation)
        and events[0].event_type is TaskEventType.CLAIM_EXPIRED
    )
    operation_events = events[1:] if has_expiry_prefix else events
    common = (
        task.uid == mutation.task_uid
        and task.project_id == mutation.project_id
        and task.version == mutation.expected_version + 1
        and all(event.actor_subject_id == mutation.actor_subject_id for event in events)
        and tuple(event.event_type for event in operation_events)
        == _expected_event_types(result)
        and all(
            dict(event.payload) == _expected_event_payload(event, result=result)
            for event in operation_events
        )
        and _matches_expiry_prefix(
            result,
            mutation=mutation,
            has_expiry_prefix=has_expiry_prefix,
        )
    )
    if not common:
        return False
    if isinstance(mutation, SubmitHumanResultMutation):
        content_matches = (
            result.result.submitted_by == mutation.actor_subject_id
            and result.result.attempt_id is None
            and result.result.comment == mutation.comment
            and _result_content_matches(result.result, mutation.result)
            and (result.result.review.status is ResultReviewStatus.NOT_REQUIRED)
            == (task.state is TaskState.DONE)
        )
        fresh_ids = (
            result.result.id == mutation.result_id
            and operation_events[0].id == mutation.result_submitted_event_id
            and (
                mutation.task_completed_event_id is None
                or operation_events[-1].id == mutation.task_completed_event_id
            )
        )
    elif isinstance(mutation, ApproveResultMutation):
        review = result.result.review
        content_matches = (
            task.state is TaskState.DONE
            and review.status is ResultReviewStatus.APPROVED
            and review.reviewed_by == mutation.actor_subject_id
            and review.comment == mutation.comment
        )
        fresh_ids = (
            events[0].id == mutation.review_approved_event_id
            and events[1].id == mutation.task_completed_event_id
        )
    else:
        review = result.result.review
        content_matches = (
            task.state is TaskState.OPEN
            and task.current_result_id is None
            and review.status is ResultReviewStatus.REJECTED
            and review.reviewed_by == mutation.actor_subject_id
            and review.reason == mutation.reason
        )
        fresh_ids = events[0].id == mutation.review_rejected_event_id
    if not content_matches:
        return False
    if mutation.idempotency_key is not None:
        return True
    return (
        fresh_ids
        and all(event.request_id == mutation.request_id for event in operation_events)
        and all(event.occurred_at == mutation.occurred_at for event in operation_events)
    )


def _matches_expiry_prefix(
    result: TaskSubmissionResult,
    *,
    mutation: _ResultMutation,
    has_expiry_prefix: bool,
) -> bool:
    """Return whether Human submission has a valid nullable expiry prefix."""
    if not has_expiry_prefix:
        return True
    if not isinstance(mutation, SubmitHumanResultMutation):
        return False
    expired = result.events[0]
    return expired.actor_subject_id == mutation.actor_subject_id and (
        mutation.idempotency_key is not None
        or (
            expired.id == mutation.claim_expired_event_id
            and expired.request_id == mutation.request_id
            and expired.occurred_at == mutation.occurred_at
        )
    )


def _expected_event_types(
    result: TaskSubmissionResult,
) -> tuple[TaskEventType, ...]:
    """Return the exact event sequence implied by a Result disposition."""
    status = result.result.review.status
    if status is ResultReviewStatus.NOT_REQUIRED:
        return (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)
    if status is ResultReviewStatus.PENDING:
        return (TaskEventType.RESULT_SUBMITTED,)
    if status is ResultReviewStatus.APPROVED:
        return (TaskEventType.REVIEW_APPROVED, TaskEventType.TASK_COMPLETED)
    return (TaskEventType.REVIEW_REJECTED,)


def _expected_event_payload(
    event: TaskEvent,
    *,
    result: TaskSubmissionResult,
) -> dict[str, object]:
    """Build the closed payload expected for one Result transition event."""
    payload: dict[str, object] = {
        "result_id": str(result.result.id),
        "version": result.task.version,
    }
    if event.event_type is TaskEventType.RESULT_SUBMITTED:
        payload["review_status"] = result.result.review.status.value
    elif event.event_type is TaskEventType.REVIEW_APPROVED:
        payload["comment"] = result.result.review.comment
    elif event.event_type is TaskEventType.REVIEW_REJECTED:
        payload["reason"] = result.result.review.reason
    return payload


def _result_content_matches(result: TaskResult, expected: TaskResultInput) -> bool:
    """Compare caller-controlled Result content without generated identities."""
    return (
        result.summary == expected.summary
        and result.criteria == expected.criteria
        and result.artifacts == expected.artifacts
        and result.proposed_follow_ups == expected.proposed_follow_ups
    )


def _require_callable(value: object, method_name: str, label: str) -> None:
    """Require one explicitly named callable dependency method."""
    if not callable(getattr(value, method_name, None)):
        message = f"Task Result {label} must provide {method_name}()."
        raise TypeError(message)


def _invalid_input(operation: str) -> ApplicationError:
    """Build one safe invalid-input application failure."""
    return ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        f"Task Result {operation} input is invalid.",
    )


def _invalid_dependencies(operation: str) -> ApplicationError:
    """Build one safe invalid-dependency application failure."""
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"Task Result {operation} dependencies returned invalid values.",
    )


def _invalid_result(operation: str) -> ApplicationError:
    """Build one safe invalid-persistence-result application failure."""
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"Task Result {operation} persistence returned an invalid result.",
    )
