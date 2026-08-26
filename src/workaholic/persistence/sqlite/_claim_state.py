"""Strict read-side hydration for current or expired SQLite Claim state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from workaholic.application import LeaseLostError, TaskLockedError
from workaholic.domain import (
    AttemptId,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEventId,
    TaskEventType,
    TaskId,
    is_lease_current,
    validate_claim_attempt_consistency,
    validate_utc_timestamp,
)
from workaholic.persistence.sqlite._claim_records import (
    TASK_ATTEMPT_FIELDS,
    TASK_CLAIM_FIELDS,
    task_attempt_record_from_row,
    task_claim_record_from_row,
)
from workaholic.persistence.sqlite._event_records import (
    TaskEventRecord,
    insert_task_event,
)
from workaholic.persistence.sqlite._records import require_text, serialize_timestamp
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

_CLAIM_FIELD_COUNT: Final = len(TASK_CLAIM_FIELDS)


@dataclass(frozen=True, slots=True)
class StoredClaimState:
    """One stored Claim and its nullable current Agent Attempt projection."""

    project_id: ProjectId
    claim: TaskClaim
    attempt: TaskAttempt | None

    def __post_init__(self) -> None:
        """Validate exact runtime types and Human/Agent ownership coupling."""
        project_id: object = self.project_id
        claim: object = self.claim
        attempt: object = self.attempt
        if not isinstance(project_id, ProjectId) or not isinstance(claim, TaskClaim):
            raise StorageUnavailableError
        if attempt is not None and not isinstance(attempt, TaskAttempt):
            raise StorageUnavailableError
        try:
            validate_claim_attempt_consistency(claim=claim, attempt=attempt)
        except ValueError as error:
            raise StorageUnavailableError from error


def load_claim_state(
    connection: sqlite3.Connection,
    *,
    task: Task,
) -> StoredClaimState | None:
    """Load one stored Claim projection in the caller's read snapshot.

    Args:
        connection: Active SQLite read or write transaction.
        task: Complete owning Task with stable Human key.

    Returns:
        Stored Claim state, including expired state, or ``None``.

    Raises:
        StorageUnavailableError: If stored Claim ownership is malformed.

    """
    candidate: object = task
    if not isinstance(candidate, Task):
        raise StorageUnavailableError
    return load_claim_states(connection, tasks=(candidate,))[candidate.uid]


def load_claim_states(
    connection: sqlite3.Connection,
    *,
    tasks: Sequence[Task],
) -> dict[TaskId, StoredClaimState | None]:
    """Batch-load stored Claim and Attempt projections for complete Tasks.

    Args:
        connection: Active SQLite read or write transaction.
        tasks: Complete Tasks whose Claim rows may exist.

    Returns:
        Every requested Task identity mapped to stored state or ``None``.

    Raises:
        StorageUnavailableError: If inputs or persisted ownership are malformed.

    """
    candidate_tasks: object = tasks
    if not isinstance(candidate_tasks, Sequence) or isinstance(
        candidate_tasks,
        (str, bytes),
    ):
        raise StorageUnavailableError
    selected = tuple(candidate_tasks)
    if any(not isinstance(task, Task) for task in selected):
        raise StorageUnavailableError
    task_by_id = {task.uid: task for task in selected}
    if len(task_by_id) != len(selected):
        raise StorageUnavailableError
    result: dict[TaskId, StoredClaimState | None] = dict.fromkeys(task_by_id)
    if not selected:
        return result

    claim_fields = ", ".join(f"c.{field}" for field in TASK_CLAIM_FIELDS)
    attempt_fields = ", ".join(f"a.{field}" for field in TASK_ATTEMPT_FIELDS)
    placeholders = ", ".join("?" for _task in selected)
    rows = connection.execute(
        f"""
        SELECT {claim_fields}, {attempt_fields}
        FROM task_claims AS c
        LEFT JOIN task_attempts AS a ON a.id = c.attempt_id
        WHERE c.task_uid IN ({placeholders})
        ORDER BY c.task_uid
        """,  # noqa: S608 - fields and placeholders are closed module values.
        tuple(str(task.uid) for task in selected),
    ).fetchall()
    for row in rows:
        claim_values = row[:_CLAIM_FIELD_COUNT]
        attempt_values = row[_CLAIM_FIELD_COUNT:]
        try:
            task_uid = TaskId(require_text(claim_values[0]))
        except ValueError as error:
            raise StorageUnavailableError from error
        task = task_by_id.get(task_uid)
        if task is None or result[task_uid] is not None:
            raise StorageUnavailableError
        claim_record = task_claim_record_from_row(
            claim_values,
            task_key=task.key,
        )
        if (
            claim_record.project_id != task.project_id
            or claim_record.claim.task_uid != task.uid
        ):
            raise StorageUnavailableError
        attempt: TaskAttempt | None
        if claim_record.claim.attempt_id is None:
            if any(value is not None for value in attempt_values):
                raise StorageUnavailableError
            attempt = None
        else:
            attempt_record = task_attempt_record_from_row(attempt_values)
            if attempt_record.project_id != task.project_id:
                raise StorageUnavailableError
            attempt = attempt_record.attempt
        result[task_uid] = StoredClaimState(
            project_id=task.project_id,
            claim=claim_record.claim,
            attempt=attempt,
        )
    return result


def current_claim_state(
    state: StoredClaimState | None,
    *,
    now: datetime,
) -> StoredClaimState | None:
    """Project stored state as current only within its half-open Lease.

    Args:
        state: Stored current-or-expired Claim state.
        now: Authoritative UTC query time.

    Returns:
        The same state while ``now < lease_expires_at``, otherwise ``None``.

    Raises:
        StorageUnavailableError: If state or time violates the trusted contract.

    """
    if state is not None and not isinstance(state, StoredClaimState):
        raise StorageUnavailableError
    try:
        current_time = validate_utc_timestamp(now, label="Claim query time")
        if state is None or not is_lease_current(
            lease_expires_at=state.claim.lease_expires_at,
            now=current_time,
        ):
            return None
    except ValueError as error:
        raise StorageUnavailableError from error
    return state


def require_current_claim_owner(
    state: StoredClaimState | None,
    *,
    subject_id: SubjectId,
    attempt_id: AttemptId | None,
    now: datetime,
) -> StoredClaimState:
    """Require the exact current Human or Agent Claim owner token.

    Args:
        state: Stored Claim state, including a potentially expired Lease.
        subject_id: Authenticated bootstrap Subject identity.
        attempt_id: Null Human token or exact Agent Attempt identity.
        now: Authoritative UTC transaction time.

    Returns:
        Current Claim state owned by ``(subject_id, attempt_id)``.

    Raises:
        LeaseLostError: If an Agent token is missing, stale, or superseded.
        TaskLockedError: If a Human path encounters another current owner token.
        StorageUnavailableError: If trusted inputs violate their runtime contract.

    """
    candidate_subject: object = subject_id
    candidate_attempt: object = attempt_id
    if not isinstance(candidate_subject, SubjectId) or (
        candidate_attempt is not None and not isinstance(candidate_attempt, AttemptId)
    ):
        raise StorageUnavailableError
    current = current_claim_state(state, now=now)
    if current is None:
        raise LeaseLostError
    claim = current.claim
    if candidate_attempt is None:
        if claim.subject_id != candidate_subject or claim.attempt_id is not None:
            raise TaskLockedError
        return current
    if (
        claim.subject_id != candidate_subject
        or claim.attempt_id != candidate_attempt
        or current.attempt is None
        or current.attempt.id != candidate_attempt
    ):
        raise LeaseLostError
    return current


def guard_human_task_mutation(  # noqa: PLR0913 - explicit attribution boundary.
    connection: sqlite3.Connection,
    *,
    task: Task,
    actor_subject_id: SubjectId,
    request_id: RequestId,
    occurred_at: datetime,
    claim_expired_event_id: TaskEventId,
) -> tuple[StoredClaimState | None, tuple[TaskEventRecord, ...]]:
    """Authorize a Human Task mutation and lazily materialize stale ownership.

    Args:
        connection: Active SQLite write transaction.
        task: Authorized Task selected by the Human mutation.
        actor_subject_id: Authenticated bootstrap Human Subject.
        request_id: Current logical request identity.
        occurred_at: Authoritative transaction time.
        claim_expired_event_id: Candidate event identity used only for expiry.

    Returns:
        The retained current Human owner, if any, and an optional expiry prefix.

    Raises:
        TaskLockedError: If another current Human or Agent Claim owns the Task.
        StorageUnavailableError: If inputs or persisted ownership are malformed.

    """
    candidate_task: object = task
    candidate_actor: object = actor_subject_id
    candidate_request: object = request_id
    candidate_event: object = claim_expired_event_id
    if (
        not isinstance(candidate_task, Task)
        or not isinstance(candidate_actor, SubjectId)
        or not isinstance(candidate_request, RequestId)
        or not isinstance(candidate_event, TaskEventId)
    ):
        raise StorageUnavailableError
    state = load_claim_state(connection, task=candidate_task)
    current = current_claim_state(state, now=occurred_at)
    if current is not None:
        if (
            current.claim.subject_id != candidate_actor
            or current.claim.attempt_id is not None
            or current.attempt is not None
        ):
            raise TaskLockedError
        return current, ()
    if state is None:
        return None, ()
    expired = materialize_expired_claim(
        connection,
        task=candidate_task,
        state=state,
        actor_subject_id=candidate_actor,
        request_id=candidate_request,
        event_id=candidate_event,
        occurred_at=occurred_at,
    )
    return None, (expired,)


def materialize_expired_claim(  # noqa: PLR0913 - explicit event attribution.
    connection: sqlite3.Connection,
    *,
    task: Task,
    state: StoredClaimState,
    actor_subject_id: SubjectId,
    request_id: RequestId,
    event_id: TaskEventId,
    occurred_at: datetime,
) -> TaskEventRecord:
    """Remove one stale Claim, end its Agent Attempt, and append expiry.

    Args:
        connection: Active SQLite write transaction.
        task: Complete Task owning the stale Claim.
        state: Exact stored Claim and nullable Attempt snapshot.
        actor_subject_id: Authenticated Subject observing the expiry.
        request_id: Logical request that materializes the expiry.
        event_id: Globally unique candidate expiry-event identity.
        occurred_at: Authoritative transaction time at or after expiry.

    Returns:
        Persisted ``claim_expired`` event record.

    Raises:
        StorageUnavailableError: If the Claim is current or changed concurrently.

    """
    if current_claim_state(state, now=occurred_at) is not None:
        raise StorageUnavailableError
    deleted = connection.execute(
        """
        DELETE FROM task_claims
        WHERE task_uid = ? AND project_id = ? AND subject_id = ?
          AND attempt_id IS ? AND claimed_at = ? AND lease_expires_at = ?
        """,
        (
            str(task.uid),
            str(task.project_id),
            str(state.claim.subject_id),
            None if state.claim.attempt_id is None else str(state.claim.attempt_id),
            serialize_timestamp(state.claim.claimed_at),
            serialize_timestamp(state.claim.lease_expires_at),
        ),
    )
    if deleted.rowcount != 1:
        raise StorageUnavailableError
    if state.attempt is not None:
        changed = connection.execute(
            """
            UPDATE task_attempts
            SET status = 'expired', ended_at = lease_expires_at
            WHERE id = ? AND task_uid = ? AND project_id = ?
              AND subject_id = ? AND status = 'active' AND ended_at IS NULL
              AND lease_expires_at = ?
            """,
            (
                str(state.attempt.id),
                str(task.uid),
                str(task.project_id),
                str(state.attempt.subject_id),
                serialize_timestamp(state.attempt.lease_expires_at),
            ),
        )
        if changed.rowcount != 1:
            raise StorageUnavailableError
    return insert_task_event(
        connection,
        event_id=event_id,
        task=task,
        actor_subject_id=actor_subject_id,
        request_id=request_id,
        event_type=TaskEventType.CLAIM_EXPIRED,
        occurred_at=occurred_at,
        payload={"lease_expires_at": serialize_timestamp(state.claim.lease_expires_at)},
        attempt_id=state.claim.attempt_id,
    )


def end_human_claim(
    connection: sqlite3.Connection,
    *,
    task: Task,
    state: StoredClaimState | None,
    actor_subject_id: SubjectId,
) -> None:
    """Delete a retained current Human Claim during submit or cancellation.

    Args:
        connection: Active SQLite write transaction.
        task: Complete Task owning the nullable Claim.
        state: Current Human owner returned by ``guard_human_task_mutation``.
        actor_subject_id: Authenticated Human owner identity.

    Raises:
        StorageUnavailableError: If ownership changed or is not exactly Human.

    """
    if state is None:
        return
    if (
        state.claim.subject_id != actor_subject_id
        or state.claim.attempt_id is not None
        or state.attempt is not None
    ):
        raise StorageUnavailableError
    deleted = connection.execute(
        """
        DELETE FROM task_claims
        WHERE task_uid = ? AND project_id = ? AND subject_id = ?
          AND attempt_id IS NULL AND claimed_at = ? AND lease_expires_at = ?
        """,
        (
            str(task.uid),
            str(task.project_id),
            str(actor_subject_id),
            serialize_timestamp(state.claim.claimed_at),
            serialize_timestamp(state.claim.lease_expires_at),
        ),
    )
    if deleted.rowcount != 1:
        raise StorageUnavailableError
