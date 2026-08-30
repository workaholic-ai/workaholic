"""Atomic Human Result submission and review operations for SQLite."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from workaholic.application import (
    ApplicationError,
    ApproveResultMutation,
    InvalidTransitionError,
    RejectResultMutation,
    ResultInvalidError,
    SubmitAgentResultMutation,
    SubmitHumanResultMutation,
    TaskSubmissionResult,
    UnsatisfiableDependencyError,
    VersionConflictError,
)
from workaholic.domain import (
    ApprovalRequirement,
    AuthenticatedActor,
    DomainValidationError,
    ResultReview,
    ResultReviewStatus,
    SubjectKind,
    Task,
    TaskEventType,
    TaskResult,
    TaskState,
    TaskTransition,
    transition_task_state,
    validate_agent_submission,
    validate_human_submission,
    validate_task_result_consistency,
)
from workaholic.persistence.sqlite._claim_state import (
    StoredClaimState,
    end_agent_claim_as_submitted,
    end_human_claim,
    guard_human_task_mutation,
    load_claim_state,
    require_current_claim_owner,
)
from workaholic.persistence.sqlite._event_records import (
    insert_task_event as _insert_task_event,
)
from workaholic.persistence.sqlite._records import canonical_json, parse_timestamp
from workaholic.persistence.sqlite._result_records import (
    TASK_RESULT_FIELDS,
    read_idempotent_result_outcome,
    record_idempotent_result_outcome,
    task_result_from_row,
    task_result_row,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    _load_agent_task,
    _load_authorized_task,
    _load_dependencies,
    _write_task_if_version,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from workaholic.application import TaskResultInput
    from workaholic.domain import (
        JsonValue,
        SubjectId,
        TaskEventId,
    )
    from workaholic.persistence.sqlite._event_records import TaskEventRecord

type _SubmissionMutation = SubmitHumanResultMutation | SubmitAgentResultMutation
type _ResultMutation = (
    _SubmissionMutation | ApproveResultMutation | RejectResultMutation
)

_SUBMIT_OPERATION: Final = "task.result.submit"
_SUBMIT_AGENT_OPERATION: Final = "task.result.submit.agent"
_APPROVE_OPERATION: Final = "task.result.approve"
_REJECT_OPERATION: Final = "task.result.reject"


@dataclass(frozen=True, slots=True)
class _ResultPlan:
    """Closed operation and transition constants for one Result mutation."""

    operation: str
    transition: TaskTransition


_SUBMIT_PLAN: Final = _ResultPlan(
    operation=_SUBMIT_OPERATION,
    transition=TaskTransition.SUBMIT,
)
_SUBMIT_AGENT_PLAN: Final = _ResultPlan(
    operation=_SUBMIT_AGENT_OPERATION,
    transition=TaskTransition.SUBMIT,
)
_APPROVE_PLAN: Final = _ResultPlan(
    operation=_APPROVE_OPERATION,
    transition=TaskTransition.APPROVE,
)
_REJECT_PLAN: Final = _ResultPlan(
    operation=_REJECT_OPERATION,
    transition=TaskTransition.REJECT,
)


def submit_human_result(
    database_path: Path,
    mutation: SubmitHumanResultMutation,
) -> TaskSubmissionResult:
    """Atomically store a Human Result and transition its open Task.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic Human submission mutation.

    Returns:
        Committed Task, retained Result, and ordered semantic events.

    Raises:
        ApplicationError: If authorization, replay, version, or semantics fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not SubmitHumanResultMutation:
        raise StorageUnavailableError
    return _execute_result_mutation(
        database_path,
        mutation=candidate,
        plan=_SUBMIT_PLAN,
    )


def submit_agent_result(
    database_path: Path,
    mutation: SubmitAgentResultMutation,
) -> TaskSubmissionResult:
    """Atomically store an Agent Result and end its exact current Attempt.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic Agent submission mutation.

    Returns:
        Committed Task, Result, submitted Attempt, and ordered events.

    Raises:
        ApplicationError: If authorization, Lease, version, or semantics fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not SubmitAgentResultMutation:
        raise StorageUnavailableError
    return _execute_result_mutation(
        database_path,
        mutation=candidate,
        plan=_SUBMIT_AGENT_PLAN,
    )


def approve_result(
    database_path: Path,
    mutation: ApproveResultMutation,
) -> TaskSubmissionResult:
    """Atomically approve a pending Result and complete its Task.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic approval mutation.

    Returns:
        Committed Task, approved Result, and ordered semantic events.

    Raises:
        ApplicationError: If authorization, replay, version, or semantics fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not ApproveResultMutation:
        raise StorageUnavailableError
    return _execute_result_mutation(
        database_path,
        mutation=candidate,
        plan=_APPROVE_PLAN,
    )


def reject_result(
    database_path: Path,
    mutation: RejectResultMutation,
) -> TaskSubmissionResult:
    """Atomically reject, retain, and deselect a pending Result.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic rejection mutation.

    Returns:
        Reopened Task, rejected Result, and attributable rejection event.

    Raises:
        ApplicationError: If authorization, replay, version, or semantics fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not RejectResultMutation:
        raise StorageUnavailableError
    return _execute_result_mutation(
        database_path,
        mutation=candidate,
        plan=_REJECT_PLAN,
    )


def _execute_result_mutation(
    database_path: Path,
    *,
    mutation: _ResultMutation,
    plan: _ResultPlan,
) -> TaskSubmissionResult:
    """Execute one Result mutation in a single immediate transaction.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated Result mutation.
        plan: Closed operation and domain transition semantics.

    Returns:
        Fresh or idempotently replayed semantic result.

    Raises:
        ApplicationError: If a stable semantic precondition fails.
        StorageUnavailableError: If storage contains invalid data.

    """
    fingerprint = _mutation_fingerprint(mutation)
    try:
        with open_write_transaction(database_path) as connection:
            current = (
                _load_agent_task(
                    connection,
                    task_uid=mutation.task_uid,
                    project_id=mutation.project_id,
                    actor_subject_id=mutation.actor_subject_id,
                    actor=mutation.actor,
                    occurred_at=mutation.occurred_at,
                )
                if isinstance(mutation, SubmitAgentResultMutation)
                else _load_authorized_task(
                    connection,
                    task_uid=mutation.task_uid,
                    project_id=str(mutation.project_id),
                    actor_subject_id=str(mutation.actor_subject_id),
                    actor=mutation.actor,
                    occurred_at=mutation.occurred_at,
                )
            )
            replay = read_idempotent_result_outcome(
                connection,
                operation=plan.operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                _require_matching_result(replay, mutation=mutation, fresh=False)
                return replay
            human_owner_state: StoredClaimState | None = None
            agent_owner_state: StoredClaimState | None = None
            expiry_records: tuple[TaskEventRecord, ...] = ()
            if isinstance(mutation, SubmitHumanResultMutation):
                human_owner_state, expiry_records = guard_human_task_mutation(
                    connection,
                    task=current,
                    actor_subject_id=mutation.actor_subject_id,
                    request_id=mutation.request_id,
                    occurred_at=mutation.occurred_at,
                    claim_expired_event_id=mutation.claim_expired_event_id,
                    actor_kind=(
                        SubjectKind.HUMAN
                        if mutation.actor is None
                        else mutation.actor.subject_kind
                    ),
                )
            elif isinstance(mutation, SubmitAgentResultMutation):
                agent_owner_state = require_current_claim_owner(
                    load_claim_state(connection, task=current),
                    subject_id=mutation.actor_subject_id,
                    attempt_id=mutation.attempt_id,
                    now=mutation.occurred_at,
                )
            if current.version != mutation.expected_version:
                raise VersionConflictError
            _require_monotonic_time(current, mutation=mutation)
            submitted_attempt = None
            if isinstance(
                mutation,
                (SubmitHumanResultMutation, SubmitAgentResultMutation),
            ):
                result, target_state = _prepare_submission(
                    connection,
                    current=current,
                    mutation=mutation,
                )
                _insert_result(connection, result=result)
                if isinstance(mutation, SubmitHumanResultMutation):
                    end_human_claim(
                        connection,
                        task=current,
                        state=human_owner_state,
                        actor_subject_id=mutation.actor_subject_id,
                    )
                else:
                    submitted_attempt = end_agent_claim_as_submitted(
                        connection,
                        task=current,
                        state=_require_agent_owner_state(agent_owner_state),
                        actor_subject_id=mutation.actor_subject_id,
                        attempt_id=mutation.attempt_id,
                        occurred_at=mutation.occurred_at,
                    )
            else:
                result, target_state = _prepare_review(
                    connection,
                    current=current,
                    mutation=mutation,
                    transition=plan.transition,
                )
                _write_review(
                    connection,
                    previous_id=current.current_result_id,
                    result=result,
                )
            updated = replace(
                current,
                state=target_state,
                current_result_id=(
                    None if isinstance(mutation, RejectResultMutation) else result.id
                ),
                version=current.version + 1,
                updated_at=mutation.occurred_at,
            )
            _write_task_if_version(connection, previous=current, updated=updated)
            operation_records = _append_events(
                connection,
                mutation=mutation,
                task=updated,
                result=result,
            )
            event_records = (*expiry_records, *operation_records)
            outcome = TaskSubmissionResult(
                task=updated,
                result=result,
                events=tuple(record.event for record in event_records),
                attempt=submitted_attempt,
            )
            _require_matching_result(outcome, mutation=mutation, fresh=True)
            record_idempotent_result_outcome(
                connection,
                operation=plan.operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=fingerprint,
                occurred_at=mutation.occurred_at,
                result=outcome,
                event_records=event_records,
            )
            return outcome
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _require_agent_owner_state(
    state: StoredClaimState | None,
) -> StoredClaimState:
    """Return the Agent owner state guaranteed by submission dispatch.

    Args:
        state: Agent owner state resolved before optimistic version validation.

    Returns:
        Exact current Agent Claim and Attempt state.

    Raises:
        StorageUnavailableError: If dispatch lost the required state.

    """
    if state is None:
        raise StorageUnavailableError
    return state


def _prepare_submission(
    connection: sqlite3.Connection,
    *,
    current: Task,
    mutation: _SubmissionMutation,
) -> tuple[TaskResult, TaskState]:
    """Build and validate a Result against current Task dependencies.

    Args:
        connection: Active validated write transaction.
        current: Authoritative open Task.
        mutation: Validated Human or Agent submission mutation.

    Returns:
        New immutable Result and approval-dependent target state.

    Raises:
        InvalidTransitionError: If state or an unfinished dependency blocks submit.
        ResultInvalidError: If Result content conflicts with the Task definition.
        UnsatisfiableDependencyError: If a prerequisite is cancelled.

    """
    if current.state is not TaskState.OPEN:
        raise InvalidTransitionError
    if current.current_result_id is not None:
        raise ResultInvalidError
    prerequisites = _load_prerequisite_tasks(
        connection,
        task=current,
        actor_subject_id=mutation.actor_subject_id,
        actor=mutation.actor,
        occurred_at=mutation.occurred_at,
        agent_execution=isinstance(mutation, SubmitAgentResultMutation),
    )
    if any(item.state is TaskState.CANCELLED for item in prerequisites):
        raise UnsatisfiableDependencyError
    if any(item.state is not TaskState.DONE for item in prerequisites):
        raise InvalidTransitionError
    review_status = (
        ResultReviewStatus.NOT_REQUIRED
        if current.approval is ApprovalRequirement.NONE
        else ResultReviewStatus.PENDING
    )
    try:
        result = TaskResult(
            id=mutation.result_id,
            task_uid=current.uid,
            submitted_by=mutation.actor_subject_id,
            attempt_id=(
                mutation.attempt_id
                if isinstance(mutation, SubmitAgentResultMutation)
                else None
            ),
            submitted_at=mutation.occurred_at,
            comment=(
                mutation.comment
                if isinstance(mutation, SubmitHumanResultMutation)
                else None
            ),
            summary=mutation.result.summary,
            criteria=mutation.result.criteria,
            artifacts=mutation.result.artifacts,
            proposed_follow_ups=mutation.result.proposed_follow_ups,
            review=ResultReview(status=review_status),
        )
        if isinstance(mutation, SubmitHumanResultMutation):
            target_state = validate_human_submission(
                task=current,
                prerequisites=prerequisites,
                result=result,
            )
        else:
            target_state = validate_agent_submission(
                task=current,
                prerequisites=prerequisites,
                result=result,
            )
    except DomainValidationError as error:
        raise ResultInvalidError from error
    if (mutation.task_completed_event_id is not None) != (
        target_state is TaskState.DONE
    ):
        raise ResultInvalidError
    return result, target_state


def _prepare_review(
    connection: sqlite3.Connection,
    *,
    current: Task,
    mutation: ApproveResultMutation | RejectResultMutation,
    transition: TaskTransition,
) -> tuple[TaskResult, TaskState]:
    """Load and disposition the current pending Human or Agent Result.

    Args:
        connection: Active validated write transaction.
        current: Authoritative review Task.
        mutation: Validated approval or rejection mutation.
        transition: Exact domain transition requested.

    Returns:
        Reviewed immutable Result and target Task state.

    Raises:
        InvalidTransitionError: If the Task is not awaiting review.
        ResultInvalidError: If no valid pending Result is selected.
        StorageUnavailableError: If authoritative time regresses.

    """
    try:
        target_state = transition_task_state(
            current.state,
            transition,
            approval=current.approval,
        )
    except DomainValidationError as error:
        raise InvalidTransitionError from error
    if current.current_result_id is None:
        raise ResultInvalidError
    result = _load_result(
        connection,
        result_id=str(current.current_result_id),
        task_uid=str(current.uid),
    )
    if result.review.status is not ResultReviewStatus.PENDING or (
        mutation.occurred_at < result.submitted_at
    ):
        if mutation.occurred_at < result.submitted_at:
            raise StorageUnavailableError
        raise ResultInvalidError
    try:
        validate_task_result_consistency(
            task=current,
            result=result,
            human_submission=result.attempt_id is None,
        )
        if isinstance(mutation, ApproveResultMutation):
            review = ResultReview(
                status=ResultReviewStatus.APPROVED,
                reviewed_by=mutation.actor_subject_id,
                reviewed_at=mutation.occurred_at,
                comment=mutation.comment,
            )
        else:
            review = ResultReview(
                status=ResultReviewStatus.REJECTED,
                reviewed_by=mutation.actor_subject_id,
                reviewed_at=mutation.occurred_at,
                reason=mutation.reason,
            )
        reviewed = replace(result, review=review)
    except DomainValidationError as error:
        raise ResultInvalidError from error
    return reviewed, target_state


def _load_prerequisite_tasks(  # noqa: PLR0913 - explicit authorization boundary.
    connection: sqlite3.Connection,
    *,
    task: Task,
    actor_subject_id: SubjectId,
    actor: AuthenticatedActor | None = None,
    occurred_at: datetime | None = None,
    agent_execution: bool = False,
) -> tuple[Task, ...]:
    """Hydrate the exact dependency projection under the same authorization.

    Args:
        connection: Active write transaction.
        task: Dependant Task with ordered prerequisite identities.
        actor_subject_id: Authenticated mutation identity.
        actor: Authenticated actor context, or the tokenless build bridge.
        occurred_at: Authoritative authentication time when ``actor`` is set.
        agent_execution: Whether Agent rather than Operator permission applies.

    Returns:
        Complete prerequisites in stable Human-key order.

    """
    identities = _load_dependencies(
        connection,
        task_uid=task.uid,
        project_id=str(task.project_id),
    )
    if identities != task.depends_on:
        raise StorageUnavailableError
    if agent_execution:
        if occurred_at is None:
            raise StorageUnavailableError
        return tuple(
            _load_agent_task(
                connection,
                task_uid=identity,
                project_id=task.project_id,
                actor_subject_id=actor_subject_id,
                actor=actor,
                occurred_at=occurred_at,
            )
            for identity in identities
        )
    return tuple(
        _load_authorized_task(
            connection,
            task_uid=identity,
            project_id=str(task.project_id),
            actor_subject_id=str(actor_subject_id),
            actor=actor,
            occurred_at=occurred_at,
        )
        for identity in identities
    )


def _insert_result(connection: sqlite3.Connection, *, result: TaskResult) -> None:
    """Insert one already validated immutable Human or Agent Result row."""
    values = task_result_row(result)
    placeholders = ", ".join("?" for _field in TASK_RESULT_FIELDS)
    connection.execute(
        f"""
        INSERT INTO task_results ({", ".join(TASK_RESULT_FIELDS)})
        VALUES ({placeholders})
        """,  # noqa: S608 - field names are a closed module constant.
        values,
    )


def _load_result(
    connection: sqlite3.Connection,
    *,
    result_id: str,
    task_uid: str,
) -> TaskResult:
    """Load one exact Task-owned Result or reject invalid selection."""
    row = connection.execute(
        f"""
        SELECT {", ".join(TASK_RESULT_FIELDS)}
        FROM task_results
        WHERE id = ? AND task_uid = ?
        """,  # noqa: S608 - field names are a closed module constant.
        (result_id, task_uid),
    ).fetchone()
    if row is None:
        raise ResultInvalidError
    return task_result_from_row(row)


def _write_review(
    connection: sqlite3.Connection,
    *,
    previous_id: object,
    result: TaskResult,
) -> None:
    """Update only pending review fields for one immutable Result body."""
    if previous_id != result.id:
        raise StorageUnavailableError
    values = task_result_row(result)
    changed = connection.execute(
        """
        UPDATE task_results
        SET
            review_status = ?, reviewed_by = ?, reviewed_at = ?,
            review_comment = ?, rejection_reason = ?
        WHERE id = ? AND task_uid = ? AND review_status = 'pending'
        """,
        (
            values[10],
            values[11],
            values[12],
            values[13],
            values[14],
            values[0],
            values[1],
        ),
    )
    if changed.rowcount != 1:
        raise ResultInvalidError


def _append_events(
    connection: sqlite3.Connection,
    *,
    mutation: _ResultMutation,
    task: Task,
    result: TaskResult,
) -> tuple[TaskEventRecord, ...]:
    """Append the exact ordered events for one Result mutation."""
    specs = _event_specs(mutation)
    attempt_id = (
        mutation.attempt_id if isinstance(mutation, SubmitAgentResultMutation) else None
    )
    records: list[TaskEventRecord] = []
    for event_id, event_type in specs:
        records.append(
            _insert_task_event(
                connection,
                event_id=event_id,
                task=task,
                actor_subject_id=mutation.actor_subject_id,
                request_id=mutation.request_id,
                event_type=event_type,
                occurred_at=mutation.occurred_at,
                payload=_event_payload(event_type, task=task, result=result),
                attempt_id=attempt_id,
                actor_kind=(
                    SubjectKind.HUMAN
                    if mutation.actor is None
                    else mutation.actor.subject_kind
                ),
            )
        )
    return tuple(records)


def _event_specs(
    mutation: _ResultMutation,
) -> tuple[tuple[TaskEventId, TaskEventType], ...]:
    """Return exact generated identities and types in append order."""
    if isinstance(
        mutation,
        (SubmitHumanResultMutation, SubmitAgentResultMutation),
    ):
        first = (
            mutation.result_submitted_event_id,
            TaskEventType.RESULT_SUBMITTED,
        )
        if mutation.task_completed_event_id is None:
            return (first,)
        return (
            first,
            (mutation.task_completed_event_id, TaskEventType.TASK_COMPLETED),
        )
    if isinstance(mutation, ApproveResultMutation):
        return (
            (mutation.review_approved_event_id, TaskEventType.REVIEW_APPROVED),
            (mutation.task_completed_event_id, TaskEventType.TASK_COMPLETED),
        )
    return ((mutation.review_rejected_event_id, TaskEventType.REVIEW_REJECTED),)


def _event_payload(
    event_type: TaskEventType,
    *,
    task: Task,
    result: TaskResult,
) -> dict[str, JsonValue]:
    """Build one closed bounded Result-event payload."""
    payload: dict[str, JsonValue] = {
        "result_id": str(result.id),
        "version": task.version,
    }
    if event_type is TaskEventType.RESULT_SUBMITTED:
        payload["review_status"] = result.review.status.value
    elif event_type is TaskEventType.REVIEW_APPROVED:
        payload["comment"] = result.review.comment
    elif event_type is TaskEventType.REVIEW_REJECTED:
        payload["reason"] = result.review.reason
    return payload


def _mutation_fingerprint(mutation: _ResultMutation) -> str:
    """Hash exact caller-controlled Result-operation semantics."""
    if isinstance(mutation, SubmitHumanResultMutation):
        operation_input: object = {
            "comment": mutation.comment,
            "result": _result_input_mapping(mutation.result),
        }
    elif isinstance(mutation, SubmitAgentResultMutation):
        operation_input = {"result": _result_input_mapping(mutation.result)}
    elif isinstance(mutation, ApproveResultMutation):
        operation_input = {"comment": mutation.comment}
    else:
        operation_input = {"reason": mutation.reason}
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "attempt_id": (
                str(mutation.attempt_id)
                if isinstance(mutation, SubmitAgentResultMutation)
                else None
            ),
            "expected_version": mutation.expected_version,
            "input": operation_input,
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result_input_mapping(value: TaskResultInput) -> dict[str, object]:
    """Serialize caller-controlled Result content for a semantic fingerprint."""
    return {
        "artifacts": [
            {
                "media_type": item.media_type,
                "sha256": item.sha256,
                "uri": item.uri,
            }
            for item in value.artifacts
        ],
        "criteria": [
            {
                "criterion_id": item.criterion_id,
                "evidence": item.evidence,
                "status": item.status.value,
            }
            for item in value.criteria
        ],
        "proposed_follow_ups": [
            {"title": item.title} for item in value.proposed_follow_ups
        ],
        "summary": value.summary,
    }


def _require_matching_result(
    result: TaskSubmissionResult,
    *,
    mutation: _ResultMutation,
    fresh: bool,
) -> None:
    """Validate one fresh or replayed Result outcome against its mutation."""
    task = result.task
    stored_result = result.result
    records = result.events
    has_expiry_prefix = (
        isinstance(mutation, SubmitHumanResultMutation)
        and records[0].event_type is TaskEventType.CLAIM_EXPIRED
    )
    operation_events = records[1:] if has_expiry_prefix else records
    expected_specs = _event_specs(mutation)
    is_agent_submission = isinstance(mutation, SubmitAgentResultMutation)
    is_human_submission = isinstance(mutation, SubmitHumanResultMutation)
    expected_event_attempt = (
        mutation.attempt_id if isinstance(mutation, SubmitAgentResultMutation) else None
    )
    if (
        task.uid != mutation.task_uid
        or task.project_id != mutation.project_id
        or task.version != mutation.expected_version + 1
        or stored_result.task_uid != task.uid
        or (is_agent_submission and stored_result.attempt_id != expected_event_attempt)
        or (is_human_submission and stored_result.attempt_id is not None)
        or tuple(event.event_type for event in operation_events)
        != tuple(event_type for _event_id, event_type in expected_specs)
        or any(event.actor_subject_id != mutation.actor_subject_id for event in records)
        or any(event.attempt_id != expected_event_attempt for event in operation_events)
        or any(
            dict(event.payload)
            != _event_payload(event.event_type, task=task, result=stored_result)
            for event in operation_events
        )
    ):
        raise StorageUnavailableError
    if fresh and any(
        event.id != event_id
        or event.request_id != mutation.request_id
        or event.occurred_at != mutation.occurred_at
        for event, (event_id, _event_type) in zip(
            operation_events,
            expected_specs,
            strict=True,
        )
    ):
        raise StorageUnavailableError
    if has_expiry_prefix:
        if not isinstance(mutation, SubmitHumanResultMutation):
            raise StorageUnavailableError
        expired = records[0]
        payload = expired.payload
        if (
            expired.actor_subject_id != mutation.actor_subject_id
            or (
                fresh
                and (
                    expired.id != mutation.claim_expired_event_id
                    or expired.request_id != mutation.request_id
                    or expired.occurred_at != mutation.occurred_at
                )
            )
            or set(payload) != {"lease_expires_at"}
            or not isinstance(payload["lease_expires_at"], str)
            or parse_timestamp(payload["lease_expires_at"]) > expired.occurred_at
        ):
            raise StorageUnavailableError
    if isinstance(mutation, SubmitHumanResultMutation):
        valid_semantics = (
            stored_result.submitted_by == mutation.actor_subject_id
            and stored_result.comment == mutation.comment
            and _result_body_matches(stored_result, mutation.result)
            and (
                (
                    stored_result.review.status is ResultReviewStatus.NOT_REQUIRED
                    and task.state is TaskState.DONE
                )
                or (
                    stored_result.review.status is ResultReviewStatus.PENDING
                    and task.state is TaskState.REVIEW
                )
            )
            and task.current_result_id == stored_result.id
        )
        valid_fresh_identity = stored_result.id == mutation.result_id
    elif isinstance(mutation, SubmitAgentResultMutation):
        valid_semantics = (
            stored_result.submitted_by == mutation.actor_subject_id
            and stored_result.attempt_id == mutation.attempt_id
            and stored_result.comment is None
            and _result_body_matches(stored_result, mutation.result)
            and (
                (
                    stored_result.review.status is ResultReviewStatus.NOT_REQUIRED
                    and task.state is TaskState.DONE
                )
                or (
                    stored_result.review.status is ResultReviewStatus.PENDING
                    and task.state is TaskState.REVIEW
                )
            )
            and task.current_result_id == stored_result.id
            and result.attempt is not None
            and result.attempt.id == mutation.attempt_id
        )
        valid_fresh_identity = stored_result.id == mutation.result_id
    elif isinstance(mutation, ApproveResultMutation):
        valid_semantics = (
            stored_result.review.status is ResultReviewStatus.APPROVED
            and stored_result.review.reviewed_by == mutation.actor_subject_id
            and stored_result.review.comment == mutation.comment
            and task.state is TaskState.DONE
            and task.current_result_id == stored_result.id
        )
        valid_fresh_identity = True
    else:
        valid_semantics = (
            stored_result.review.status is ResultReviewStatus.REJECTED
            and stored_result.review.reviewed_by == mutation.actor_subject_id
            and stored_result.review.reason == mutation.reason
            and task.state is TaskState.OPEN
            and task.current_result_id is None
        )
        valid_fresh_identity = True
    if not valid_semantics:
        raise StorageUnavailableError
    if fresh and (
        not valid_fresh_identity
        or tuple(event.id for event in operation_events)
        != tuple(event_id for event_id, _event_type in expected_specs)
        or any(event.request_id != mutation.request_id for event in records)
        or any(event.occurred_at != mutation.occurred_at for event in records)
    ):
        raise StorageUnavailableError


def _result_body_matches(result: TaskResult, expected: TaskResultInput) -> bool:
    """Compare immutable caller-controlled Result content."""
    return (
        result.summary == expected.summary
        and result.criteria == expected.criteria
        and result.artifacts == expected.artifacts
        and result.proposed_follow_ups == expected.proposed_follow_ups
    )


def _require_monotonic_time(task: Task, *, mutation: _ResultMutation) -> None:
    """Require authoritative Result-operation time not before Task state."""
    if mutation.occurred_at < task.updated_at:
        raise StorageUnavailableError
