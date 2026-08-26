"""Strict read-side hydration for current or expired SQLite Claim state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from workaholic.domain import (
    ProjectId,
    Task,
    TaskAttempt,
    TaskClaim,
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
from workaholic.persistence.sqlite._records import require_text
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
