"""Atomic Human and Agent Task Claim acquisition for SQLite."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ApplicationError,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    IdempotencyConflictError,
    InvalidTransitionError,
    NoTaskAvailableError,
    ReleaseClaimMutation,
    RenewClaimMutation,
    TaskClaimResult,
    TaskLockedError,
)
from workaholic.domain import (
    AttemptStatus,
    DomainValidationError,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEventType,
    TaskId,
    is_task_claimable,
)
from workaholic.persistence.sqlite._authorization import (
    require_task_agent,
    require_task_operator,
)
from workaholic.persistence.sqlite._claim_records import (
    TASK_ATTEMPT_FIELD_SET,
    TASK_CLAIM_MAPPING_FIELD_SET,
    TaskAttemptRecord,
    TaskClaimRecord,
    task_attempt_record_from_mapping,
    task_attempt_record_mapping,
    task_attempt_row,
    task_claim_record_from_mapping,
    task_claim_record_mapping,
    task_claim_row,
)
from workaholic.persistence.sqlite._claim_state import (
    StoredClaimState,
    current_claim_state,
    load_claim_state,
    materialize_expired_claim,
    require_current_claim_owner,
)
from workaholic.persistence.sqlite._event_records import (
    TASK_EVENT_FIELDS,
    TaskEventRecord,
    insert_task_event,
    task_event_record_from_mapping,
    task_event_record_from_row,
    task_event_record_mapping,
)
from workaholic.persistence.sqlite._queries import (
    _load_task_dependencies,
    _require_authorized_project,
    _task_from_project_ordered_row,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    canonical_json,
    parse_json_object,
    parse_timestamp,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    _load_agent_task,
    _load_authorized_task,
)
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELD_SET,
    task_from_mapping,
    task_mapping,
)
from workaholic.persistence.sqlite._task_views import _load_prerequisite_tasks
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from workaholic.domain import AttemptId, Project

type _ClaimMutation = ClaimTaskMutation | ClaimNextTaskMutation
type _LeaseMutation = RenewClaimMutation | ReleaseClaimMutation
type _ClaimPersistenceMutation = _ClaimMutation | _LeaseMutation

_CLAIM_TASK_OPERATION: Final = "task.claim"
_CLAIM_NEXT_OPERATION: Final = "task.claim.next"
_RENEW_CLAIM_OPERATION: Final = "task.claim.renew"
_RELEASE_CLAIM_OPERATION: Final = "task.claim.release"
_CLAIM_OUTCOME_KEYS: Final = frozenset(("attempt", "claim", "events", "task"))
_MAX_CLAIM_EVENTS: Final = 2


def claim_task(
    database_path: Path,
    mutation: ClaimTaskMutation,
) -> TaskClaimResult:
    """Atomically acquire one explicit ready Task for a Human owner.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated targeted Human Claim mutation.

    Returns:
        New, current no-op, or idempotently replayed Human Claim outcome.

    Raises:
        ApplicationError: If authorization, readiness, locking, or replay fails.
        StorageUnavailableError: If persistence violates its closed contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, ClaimTaskMutation):
        raise StorageUnavailableError
    return _execute_claim(database_path, mutation=candidate)


def claim_next_task(
    database_path: Path,
    mutation: ClaimNextTaskMutation,
) -> TaskClaimResult:
    """Atomically pull the highest-ranked ready Task for an Agent Attempt.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated Project-scoped Agent pull mutation.

    Returns:
        New or idempotently replayed Agent Claim and Attempt outcome.

    Raises:
        ApplicationError: If authorization, availability, or replay fails.
        StorageUnavailableError: If persistence violates its closed contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, ClaimNextTaskMutation):
        raise StorageUnavailableError
    return _execute_claim(database_path, mutation=candidate)


def renew_claim(
    database_path: Path,
    mutation: RenewClaimMutation,
) -> TaskClaimResult:
    """Atomically renew a Human Claim or heartbeat an Agent Attempt.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated exact owner token and replacement Lease duration.

    Returns:
        Renewed or idempotently replayed Claim ownership state.

    Raises:
        ApplicationError: If authorization, ownership, Lease, or replay fails.
        StorageUnavailableError: If persistence violates its closed contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, RenewClaimMutation):
        raise StorageUnavailableError
    return _execute_lease_operation(database_path, mutation=candidate)


def release_claim(
    database_path: Path,
    mutation: ReleaseClaimMutation,
) -> TaskClaimResult:
    """Atomically release one exact current Human or Agent owner token.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated exact owner token and release event identity.

    Returns:
        Released ownership with a nullable terminal Agent Attempt.

    Raises:
        ApplicationError: If authorization, ownership, Lease, or replay fails.
        StorageUnavailableError: If persistence violates its closed contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, ReleaseClaimMutation):
        raise StorageUnavailableError
    return _execute_lease_operation(database_path, mutation=candidate)


def _execute_claim(
    database_path: Path,
    *,
    mutation: _ClaimMutation,
) -> TaskClaimResult:
    """Execute one Claim acquisition under a single immediate transaction."""
    operation = _claim_operation(mutation)
    request_fingerprint = _claim_fingerprint(mutation)
    try:
        with open_write_transaction(database_path) as connection:
            authorized = (
                require_task_operator(
                    connection,
                    actor=mutation.actor,
                    actor_subject_id=mutation.actor_subject_id,
                    project_id=mutation.project_id,
                    occurred_at=mutation.occurred_at,
                    required_kind=SubjectKind.HUMAN,
                )
                if isinstance(mutation, ClaimTaskMutation)
                else require_task_agent(
                    connection,
                    actor=mutation.actor,
                    actor_subject_id=mutation.actor_subject_id,
                    project_id=mutation.project_id,
                    occurred_at=mutation.occurred_at,
                )
            )
            project = (
                authorized.project
                if authorized is not None
                else _require_authorized_project(
                    connection,
                    project_id=mutation.project_id,
                    subject_id=mutation.actor_subject_id,
                )
            )
            replay = _read_idempotent_claim(
                connection,
                operation=operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                _require_matching_claim_result(replay, mutation=mutation)
                return replay

            task = _select_claim_task(
                connection,
                project=project,
                mutation=mutation,
            )
            stored_claim = load_claim_state(connection, task=task)
            current_claim = current_claim_state(
                stored_claim,
                now=mutation.occurred_at,
            )
            if current_claim is not None:
                if _is_owned_human_noop(current_claim, mutation=mutation):
                    result = TaskClaimResult(
                        task=task,
                        claim=current_claim.claim,
                        attempt=None,
                        events=(),
                    )
                    _require_matching_claim_result(result, mutation=mutation)
                    _record_idempotent_claim(
                        connection,
                        operation=operation,
                        mutation=mutation,
                        request_fingerprint=request_fingerprint,
                        result=result,
                        event_records=(),
                    )
                    return result
                if isinstance(mutation, ClaimTaskMutation):
                    raise TaskLockedError
                # Agent SQL excludes current Claims under the same write lock.
                raise NoTaskAvailableError  # pragma: no cover - defensive invariant.

            prerequisites = _load_prerequisite_tasks(connection, (task,))[task.uid]
            if not is_task_claimable(
                task=task,
                prerequisites=prerequisites,
                now=mutation.occurred_at,
                claim=None if stored_claim is None else stored_claim.claim,
            ):
                if isinstance(mutation, ClaimNextTaskMutation):
                    # Agent SQL already applies this exact readiness predicate.
                    raise NoTaskAvailableError  # pragma: no cover
                raise InvalidTransitionError
            _require_monotonic_claim_time(task, mutation=mutation)

            event_records: list[TaskEventRecord] = []
            if stored_claim is not None:
                event_records.append(
                    materialize_expired_claim(
                        connection,
                        task=task,
                        state=stored_claim,
                        actor_subject_id=mutation.actor_subject_id,
                        request_id=mutation.request_id,
                        event_id=mutation.claim_expired_event_id,
                        occurred_at=mutation.occurred_at,
                        actor_kind=_claim_actor_kind(mutation),
                    )
                )
            claim, attempt = _insert_claim_ownership(
                connection,
                task=task,
                mutation=mutation,
            )
            event_records.append(
                insert_task_event(
                    connection,
                    event_id=mutation.task_claimed_event_id,
                    task=task,
                    actor_subject_id=mutation.actor_subject_id,
                    request_id=mutation.request_id,
                    event_type=TaskEventType.TASK_CLAIMED,
                    occurred_at=mutation.occurred_at,
                    payload={
                        "lease_expires_at": serialize_timestamp(claim.lease_expires_at)
                    },
                    attempt_id=claim.attempt_id,
                    actor_kind=_claim_actor_kind(mutation),
                )
            )
            result = TaskClaimResult(
                task=task,
                claim=claim,
                attempt=attempt,
                events=tuple(record.event for record in event_records),
            )
            _require_matching_claim_result(result, mutation=mutation)
            _record_idempotent_claim(
                connection,
                operation=operation,
                mutation=mutation,
                request_fingerprint=request_fingerprint,
                result=result,
                event_records=tuple(event_records),
            )
            return result
    except ApplicationError:
        raise
    except (
        DomainValidationError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
    ) as error:
        raise StorageUnavailableError from error


def _execute_lease_operation(
    database_path: Path,
    *,
    mutation: _LeaseMutation,
) -> TaskClaimResult:
    """Execute renewal or release through one shared ownership transaction."""
    operation = _lease_operation(mutation)
    request_fingerprint = _lease_fingerprint(mutation)
    try:
        with open_write_transaction(database_path) as connection:
            task = (
                _load_authorized_task(
                    connection,
                    task_uid=mutation.task_uid,
                    project_id=str(mutation.project_id),
                    actor_subject_id=str(mutation.actor_subject_id),
                    actor=mutation.actor,
                    occurred_at=mutation.occurred_at,
                    required_kind=SubjectKind.HUMAN,
                )
                if mutation.attempt_id is None
                else _load_agent_task(
                    connection,
                    task_uid=mutation.task_uid,
                    project_id=mutation.project_id,
                    actor_subject_id=mutation.actor_subject_id,
                    actor=mutation.actor,
                    occurred_at=mutation.occurred_at,
                )
            )
            replay = _read_idempotent_claim(
                connection,
                operation=operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                _require_matching_lease_result(replay, mutation=mutation)
                return replay
            state = require_current_claim_owner(
                load_claim_state(connection, task=task),
                subject_id=mutation.actor_subject_id,
                attempt_id=mutation.attempt_id,
                now=mutation.occurred_at,
            )
            if mutation.occurred_at < task.updated_at:
                raise StorageUnavailableError
            if isinstance(mutation, RenewClaimMutation):
                claim, attempt = _renew_claim_ownership(
                    connection,
                    task=task,
                    state=state,
                    mutation=mutation,
                )
                event_type = TaskEventType.CLAIM_RENEWED
                event_id = mutation.claim_renewed_event_id
                payload_expiry = claim.lease_expires_at
            else:
                claim = None
                attempt = _release_claim_ownership(
                    connection,
                    task=task,
                    state=state,
                    mutation=mutation,
                )
                event_type = TaskEventType.CLAIM_RELEASED
                event_id = mutation.claim_released_event_id
                payload_expiry = state.claim.lease_expires_at
            event_record = insert_task_event(
                connection,
                event_id=event_id,
                task=task,
                actor_subject_id=mutation.actor_subject_id,
                request_id=mutation.request_id,
                event_type=event_type,
                occurred_at=mutation.occurred_at,
                payload={"lease_expires_at": serialize_timestamp(payload_expiry)},
                attempt_id=mutation.attempt_id,
                actor_kind=(
                    SubjectKind.HUMAN
                    if mutation.actor is None
                    else mutation.actor.subject_kind
                ),
            )
            result = TaskClaimResult(
                task=task,
                claim=claim,
                attempt=attempt,
                events=(event_record.event,),
            )
            _require_matching_lease_result(result, mutation=mutation)
            _record_idempotent_claim(
                connection,
                operation=operation,
                mutation=mutation,
                request_fingerprint=request_fingerprint,
                result=result,
                event_records=(event_record,),
            )
            return result
    except ApplicationError:
        raise
    except (
        DomainValidationError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
    ) as error:
        raise StorageUnavailableError from error


def _require_monotonic_claim_time(task: Task, *, mutation: _ClaimMutation) -> None:
    """Reject an authoritative Claim time older than stored Task state."""
    if mutation.occurred_at < task.updated_at:
        raise StorageUnavailableError


def _select_claim_task(
    connection: sqlite3.Connection,
    *,
    project: Project,
    mutation: _ClaimMutation,
) -> Task:
    """Load a Human target or atomically select the next ordered Agent Task."""
    if isinstance(mutation, ClaimTaskMutation):
        return _load_authorized_task(
            connection,
            task_uid=mutation.task_uid,
            project_id=str(project.id),
            actor_subject_id=str(mutation.actor_subject_id),
            actor=mutation.actor,
            occurred_at=mutation.occurred_at,
            required_kind=SubjectKind.HUMAN,
        )
    timestamp = serialize_timestamp(mutation.occurred_at)
    row = connection.execute(
        """
        SELECT
            p.key,
            t.uid, t.project_id, t.number, t.key, t.title, t.objective,
            t.state, t.priority, t.available_at, t.approval,
            t.acceptance_json, t.context_json, t.blocking_reason,
            t.current_result_id, t.version, t.created_by, t.created_at,
            t.updated_at
        FROM tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        WHERE t.project_id = ?
          AND t.state = 'open'
          AND (t.available_at IS NULL OR t.available_at <= ?)
          AND NOT EXISTS (
              SELECT 1
              FROM task_dependencies AS d
              JOIN tasks AS prerequisite
                ON prerequisite.uid = d.prerequisite_uid
              WHERE d.task_uid = t.uid
                AND d.project_id = t.project_id
                AND prerequisite.state != 'done'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM task_claims AS claim
              WHERE claim.task_uid = t.uid
                AND claim.lease_expires_at > ?
          )
        ORDER BY
            t.priority DESC,
            (t.available_at IS NOT NULL) ASC,
            t.available_at ASC,
            t.number ASC
        LIMIT 1
        """,
        (str(project.id), timestamp, timestamp),
    ).fetchone()
    if row is None:
        raise NoTaskAvailableError
    parsed_task_uid = TaskId(require_text(row[1]))
    # The dependency loader owns cross-row validation; Task construction below
    # then verifies the stable Project key and every persisted field.
    dependencies = _load_task_dependencies(
        connection,
        (parsed_task_uid,),
    )
    return _task_from_project_ordered_row(
        row,
        depends_on=dependencies[parsed_task_uid],
    )


def _is_owned_human_noop(
    state: StoredClaimState,
    *,
    mutation: _ClaimMutation,
) -> bool:
    """Return whether a targeted Human already owns the current Claim."""
    return (
        isinstance(mutation, ClaimTaskMutation)
        and state.claim.subject_id == mutation.actor_subject_id
        and state.claim.attempt_id is None
        and state.attempt is None
    )


def _insert_claim_ownership(
    connection: sqlite3.Connection,
    *,
    task: Task,
    mutation: _ClaimMutation,
) -> tuple[TaskClaim, TaskAttempt | None]:
    """Create one positive Lease and its nullable Agent Attempt."""
    lease_expires_at = mutation.occurred_at + timedelta(
        seconds=mutation.lease_duration_seconds
    )
    attempt: TaskAttempt | None = None
    attempt_id: AttemptId | None = None
    if isinstance(mutation, ClaimNextTaskMutation):
        attempt_id = mutation.attempt_id
        attempt = TaskAttempt(
            id=attempt_id,
            task_uid=task.uid,
            subject_id=mutation.actor_subject_id,
            status=AttemptStatus.ACTIVE,
            lease_expires_at=lease_expires_at,
            started_at=mutation.occurred_at,
            ended_at=None,
        )
        connection.execute(
            """
            INSERT INTO task_attempts (
                id, task_uid, project_id, subject_id, status, started_at,
                ended_at, lease_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            task_attempt_row(
                TaskAttemptRecord(project_id=task.project_id, attempt=attempt)
            ),
        )
    claim = TaskClaim(
        task_uid=task.uid,
        task_key=task.key,
        subject_id=mutation.actor_subject_id,
        attempt_id=attempt_id,
        claimed_at=mutation.occurred_at,
        lease_expires_at=lease_expires_at,
    )
    connection.execute(
        """
        INSERT INTO task_claims (
            task_uid, project_id, subject_id, attempt_id, claimed_at,
            lease_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        task_claim_row(TaskClaimRecord(project_id=task.project_id, claim=claim)),
    )
    return claim, attempt


def _renew_claim_ownership(
    connection: sqlite3.Connection,
    *,
    task: Task,
    state: StoredClaimState,
    mutation: RenewClaimMutation,
) -> tuple[TaskClaim, TaskAttempt | None]:
    """Replace one exact current owner Lease without changing Task state."""
    lease_expires_at = mutation.occurred_at + timedelta(
        seconds=mutation.lease_duration_seconds
    )
    renewed_attempt: TaskAttempt | None = None
    if state.attempt is not None:
        changed_attempt = connection.execute(
            """
            UPDATE task_attempts
            SET lease_expires_at = ?
            WHERE id = ? AND task_uid = ? AND project_id = ?
              AND subject_id = ? AND status = 'active' AND ended_at IS NULL
              AND lease_expires_at = ?
            """,
            (
                serialize_timestamp(lease_expires_at),
                str(state.attempt.id),
                str(task.uid),
                str(task.project_id),
                str(state.attempt.subject_id),
                serialize_timestamp(state.attempt.lease_expires_at),
            ),
        )
        if changed_attempt.rowcount != 1:
            raise StorageUnavailableError
        renewed_attempt = replace(
            state.attempt,
            lease_expires_at=lease_expires_at,
        )
    changed_claim = connection.execute(
        """
        UPDATE task_claims
        SET lease_expires_at = ?
        WHERE task_uid = ? AND project_id = ? AND subject_id = ?
          AND attempt_id IS ? AND claimed_at = ? AND lease_expires_at = ?
        """,
        (
            serialize_timestamp(lease_expires_at),
            str(task.uid),
            str(task.project_id),
            str(state.claim.subject_id),
            (None if state.claim.attempt_id is None else str(state.claim.attempt_id)),
            serialize_timestamp(state.claim.claimed_at),
            serialize_timestamp(state.claim.lease_expires_at),
        ),
    )
    if changed_claim.rowcount != 1:
        raise StorageUnavailableError
    return (
        replace(state.claim, lease_expires_at=lease_expires_at),
        renewed_attempt,
    )


def _release_claim_ownership(
    connection: sqlite3.Connection,
    *,
    task: Task,
    state: StoredClaimState,
    mutation: ReleaseClaimMutation,
) -> TaskAttempt | None:
    """Delete one current Claim and terminalize its nullable Agent Attempt."""
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
            (None if state.claim.attempt_id is None else str(state.claim.attempt_id)),
            serialize_timestamp(state.claim.claimed_at),
            serialize_timestamp(state.claim.lease_expires_at),
        ),
    )
    if deleted.rowcount != 1:
        raise StorageUnavailableError
    if state.attempt is None:
        return None
    changed_attempt = connection.execute(
        """
        UPDATE task_attempts
        SET status = 'released', ended_at = ?
        WHERE id = ? AND task_uid = ? AND project_id = ?
          AND subject_id = ? AND status = 'active' AND ended_at IS NULL
          AND lease_expires_at = ?
        """,
        (
            serialize_timestamp(mutation.occurred_at),
            str(state.attempt.id),
            str(task.uid),
            str(task.project_id),
            str(state.attempt.subject_id),
            serialize_timestamp(state.attempt.lease_expires_at),
        ),
    )
    if changed_attempt.rowcount != 1:
        raise StorageUnavailableError
    return replace(
        state.attempt,
        status=AttemptStatus.RELEASED,
        ended_at=mutation.occurred_at,
    )


def _claim_operation(mutation: _ClaimMutation) -> str:
    """Return the closed idempotency operation for one acquisition path."""
    return (
        _CLAIM_TASK_OPERATION
        if isinstance(mutation, ClaimTaskMutation)
        else _CLAIM_NEXT_OPERATION
    )


def _claim_actor_kind(mutation: _ClaimMutation) -> SubjectKind:
    """Return authenticated kind while preserving the tokenless build bridge.

    Args:
        mutation: Human target or Agent pull mutation.

    Returns:
        Real authenticated kind, or the Phase 4 bootstrap Human kind.

    """
    return SubjectKind.HUMAN if mutation.actor is None else mutation.actor.subject_kind


def _lease_operation(mutation: _LeaseMutation) -> str:
    """Return the closed idempotency operation for renewal or release."""
    return (
        _RENEW_CLAIM_OPERATION
        if isinstance(mutation, RenewClaimMutation)
        else _RELEASE_CLAIM_OPERATION
    )


def _claim_fingerprint(mutation: _ClaimMutation) -> str:
    """Hash caller-controlled Claim semantics and exclude generated identities."""
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "attempt_id": None,
            "lease_duration_seconds": mutation.lease_duration_seconds,
            "project_id": str(mutation.project_id),
            "task": (
                str(mutation.task_uid)
                if isinstance(mutation, ClaimTaskMutation)
                else None
            ),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lease_fingerprint(mutation: _LeaseMutation) -> str:
    """Hash exact caller-controlled Lease mutation semantics."""
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "attempt_id": (
                None if mutation.attempt_id is None else str(mutation.attempt_id)
            ),
            "lease_duration_seconds": (
                mutation.lease_duration_seconds
                if isinstance(mutation, RenewClaimMutation)
                else None
            ),
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_idempotent_claim(
    connection: sqlite3.Connection,
    *,
    operation: str,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
) -> TaskClaimResult | None:
    """Return a matching closed Claim outcome or reject conflicting reuse."""
    if caller_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (actor_subject_id, operation, caller_key),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    result, event_records = _parse_claim_outcome(require_text(row[1]))
    outcome_subject_id = (
        result.claim.subject_id
        if result.claim is not None
        else (
            result.attempt.subject_id
            if result.attempt is not None
            else result.events[-1].actor_subject_id
        )
    )
    if outcome_subject_id.value != actor_subject_id:
        raise StorageUnavailableError
    for event_record in event_records:
        actual = connection.execute(
            f"""
            SELECT {", ".join(TASK_EVENT_FIELDS)}
            FROM task_events
            WHERE id = ?
            """,  # noqa: S608 - fields are a closed module constant.
            (str(event_record.event.id),),
        ).fetchone()
        if (
            actual is None
            or task_event_record_from_row(actual) != event_record
            or event_record.event.actor_subject_id.value != actor_subject_id
        ):
            raise StorageUnavailableError
    return result


def _record_idempotent_claim(  # noqa: PLR0913 - exact durable record contract.
    connection: sqlite3.Connection,
    *,
    operation: str,
    mutation: _ClaimPersistenceMutation,
    request_fingerprint: str,
    result: TaskClaimResult,
    event_records: Sequence[TaskEventRecord],
) -> None:
    """Persist one canonical closed Claim outcome in its owning transaction."""
    if mutation.idempotency_key is None:
        return
    claim_record = (
        None
        if result.claim is None
        else TaskClaimRecord(project_id=result.task.project_id, claim=result.claim)
    )
    attempt_record = (
        None
        if result.attempt is None
        else TaskAttemptRecord(
            project_id=result.task.project_id,
            attempt=result.attempt,
        )
    )
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(mutation.actor_subject_id),
            operation,
            mutation.idempotency_key,
            request_fingerprint,
            canonical_json(
                {
                    "attempt": (
                        None
                        if attempt_record is None
                        else task_attempt_record_mapping(attempt_record)
                    ),
                    "claim": (
                        None
                        if claim_record is None
                        else task_claim_record_mapping(claim_record)
                    ),
                    "events": [
                        task_event_record_mapping(record) for record in event_records
                    ],
                    "task": task_mapping(result.task),
                }
            ),
            serialize_timestamp(mutation.occurred_at),
        ),
    )


def _parse_claim_outcome(
    value: str,
) -> tuple[TaskClaimResult, tuple[TaskEventRecord, ...]]:
    """Parse and validate one exact durable Claim replay outcome."""
    decoded = parse_json_object(
        value,
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(decoded) != _CLAIM_OUTCOME_KEYS:
        raise StorageUnavailableError
    task_value = decoded["task"]
    claim_value = decoded["claim"]
    attempt_value = decoded["attempt"]
    events_value = decoded["events"]
    if (
        not isinstance(task_value, dict)
        or set(task_value) != TASK_FIELD_SET
        or (
            claim_value is not None
            and (
                not isinstance(claim_value, dict)
                or set(claim_value) != TASK_CLAIM_MAPPING_FIELD_SET
            )
        )
        or (
            attempt_value is not None
            and (
                not isinstance(attempt_value, dict)
                or set(attempt_value) != TASK_ATTEMPT_FIELD_SET
            )
        )
        or not isinstance(events_value, list)
        or len(events_value) > _MAX_CLAIM_EVENTS
        or any(not isinstance(item, dict) for item in events_value)
    ):
        raise StorageUnavailableError
    task = task_from_mapping(cast("Mapping[str, object]", task_value))
    claim_record = (
        None
        if claim_value is None
        else task_claim_record_from_mapping(cast("Mapping[str, object]", claim_value))
    )
    attempt_record = (
        None
        if attempt_value is None
        else task_attempt_record_from_mapping(
            cast("Mapping[str, object]", attempt_value)
        )
    )
    event_records = tuple(
        task_event_record_from_mapping(cast("Mapping[str, object]", item))
        for item in events_value
    )
    if (claim_record is not None and claim_record.project_id != task.project_id) or (
        attempt_record is not None and attempt_record.project_id != task.project_id
    ):
        raise StorageUnavailableError
    try:
        result = TaskClaimResult(
            task=task,
            claim=None if claim_record is None else claim_record.claim,
            attempt=None if attempt_record is None else attempt_record.attempt,
            events=tuple(record.event for record in event_records),
        )
    except ValueError as error:
        raise StorageUnavailableError from error
    return result, event_records


def _require_matching_claim_result(
    result: TaskClaimResult,
    *,
    mutation: _ClaimMutation,
) -> None:
    """Validate a fresh or replayed Claim result against semantic input."""
    if (
        result.task.project_id != mutation.project_id
        or result.claim is None
        or result.claim.subject_id != mutation.actor_subject_id
        or result.claim.task_uid != result.task.uid
    ):
        raise StorageUnavailableError
    if isinstance(mutation, ClaimTaskMutation):
        if (
            result.task.uid != mutation.task_uid
            or result.claim.attempt_id is not None
            or result.attempt is not None
        ):
            raise StorageUnavailableError
    elif (
        result.claim.attempt_id is None
        or result.attempt is None
        or result.attempt.status is not AttemptStatus.ACTIVE
    ):
        raise StorageUnavailableError
    if not result.events:
        if not isinstance(mutation, ClaimTaskMutation):
            raise StorageUnavailableError
        return
    expected_types = (
        (TaskEventType.TASK_CLAIMED,)
        if len(result.events) == 1
        else (TaskEventType.CLAIM_EXPIRED, TaskEventType.TASK_CLAIMED)
    )
    if tuple(event.event_type for event in result.events) != expected_types:
        raise StorageUnavailableError
    claimed_event = result.events[-1]
    if (
        claimed_event.actor_subject_id != mutation.actor_subject_id
        or claimed_event.attempt_id != result.claim.attempt_id
        or claimed_event.occurred_at != result.claim.claimed_at
        or dict(claimed_event.payload)
        != {"lease_expires_at": serialize_timestamp(result.claim.lease_expires_at)}
        or result.claim.lease_expires_at - result.claim.claimed_at
        != timedelta(seconds=mutation.lease_duration_seconds)
    ):
        raise StorageUnavailableError
    if len(result.events) == _MAX_CLAIM_EVENTS:
        expired_event = result.events[0]
        expired_payload = dict(expired_event.payload)
        if (
            set(expired_payload) != {"lease_expires_at"}
            or not isinstance(expired_payload["lease_expires_at"], str)
            or parse_timestamp(expired_payload["lease_expires_at"])
            > result.claim.claimed_at
            or expired_event.occurred_at != claimed_event.occurred_at
            or expired_event.request_id != claimed_event.request_id
        ):
            raise StorageUnavailableError


def _require_matching_lease_result(
    result: TaskClaimResult,
    *,
    mutation: _LeaseMutation,
) -> None:
    """Validate one fresh or replayed renewal/release closed outcome."""
    if (
        result.task.uid != mutation.task_uid
        or result.task.project_id != mutation.project_id
        or len(result.events) != 1
    ):
        raise StorageUnavailableError
    event = result.events[0]
    if (
        event.actor_subject_id != mutation.actor_subject_id
        or event.attempt_id != mutation.attempt_id
        or set(event.payload) != {"lease_expires_at"}
        or not isinstance(event.payload["lease_expires_at"], str)
    ):
        raise StorageUnavailableError
    payload_expiry = parse_timestamp(event.payload["lease_expires_at"])
    if isinstance(mutation, RenewClaimMutation):
        if (
            event.event_type is not TaskEventType.CLAIM_RENEWED
            or result.claim is None
            or result.claim.subject_id != mutation.actor_subject_id
            or result.claim.attempt_id != mutation.attempt_id
            or result.claim.lease_expires_at != payload_expiry
            or result.claim.lease_expires_at
            != event.occurred_at + timedelta(seconds=mutation.lease_duration_seconds)
        ):
            raise StorageUnavailableError
        return
    if (
        event.event_type is not TaskEventType.CLAIM_RELEASED
        or result.claim is not None
        or payload_expiry <= event.occurred_at
    ):
        raise StorageUnavailableError
    if mutation.attempt_id is None:
        if result.attempt is not None:
            raise StorageUnavailableError
        return
    if (
        result.attempt is None
        or result.attempt.id != mutation.attempt_id
        or result.attempt.subject_id != mutation.actor_subject_id
        or result.attempt.status is not AttemptStatus.RELEASED
        or result.attempt.lease_expires_at != payload_expiry
        or result.attempt.ended_at != event.occurred_at
    ):
        raise StorageUnavailableError
