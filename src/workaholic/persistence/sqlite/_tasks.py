"""Atomic attributable Task creation for the SQLite repository."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    IdempotencyConflictError,
    PermissionDeniedError,
    TaskCreationMutation,
)
from workaholic.domain import (
    INITIAL_TASK_VERSION,
    JsonScalar,
    ProjectRole,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskState,
    build_task_key,
)
from workaholic.persistence.sqlite._authorization import require_task_operator
from workaholic.persistence.sqlite._records import (
    canonical_json,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELD_SET,
    task_from_mapping,
    task_mapping,
    task_row,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

_CREATE_TASK_OPERATION: Final = "task.create"
_TASK_OUTCOME_KEYS: Final = frozenset(("event_id", "task"))


def create_task(database_path: Path, mutation: TaskCreationMutation) -> Task:
    """Atomically allocate, create, attribute, and optionally record one Task.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated semantic Task creation input.

    Returns:
        The new or idempotently replayed Task.

    Raises:
        AuthenticationRequiredError: If a Token-backed store omits the actor.
        IdempotencyConflictError: If a caller key has different semantic input.
        PermissionDeniedError: If the active Subject lacks Operator permission.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate_mutation: object = mutation
    if not isinstance(candidate_mutation, TaskCreationMutation):
        raise StorageUnavailableError
    request_fingerprint = _task_fingerprint(candidate_mutation)
    try:
        with open_write_transaction(database_path) as connection:
            return _create_task_in_transaction(
                connection,
                mutation=candidate_mutation,
                request_fingerprint=request_fingerprint,
            )
    except (
        IdempotencyConflictError,
        PermissionDeniedError,
        StorageUnavailableError,
    ):
        raise
    except (TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _create_task_in_transaction(
    connection: sqlite3.Connection,
    *,
    mutation: TaskCreationMutation,
    request_fingerprint: str,
) -> Task:
    """Execute Task creation semantics in one caller-owned transaction.

    Args:
        connection: Active validated write transaction.
        mutation: Validated Task creation mutation.
        request_fingerprint: Canonical semantic input digest.

    Returns:
        The new or idempotently replayed Task.

    """
    project_key, task_number = _require_active_owner(connection, mutation)
    replay = _read_idempotent_task(
        connection,
        mutation=mutation,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay

    allocation = connection.execute(
        """
        UPDATE projects
        SET next_task_number = next_task_number + 1
        WHERE id = ? AND next_task_number = ?
        """,
        (str(mutation.project_id), task_number),
    )
    if allocation.rowcount != 1:
        raise StorageUnavailableError

    task = Task(
        uid=mutation.task_id,
        project_id=mutation.project_id,
        number=task_number,
        key=build_task_key(project_key, task_number),
        title=mutation.title,
        objective=mutation.objective,
        state=TaskState.OPEN,
        priority=mutation.priority,
        available_at=mutation.available_at,
        approval=mutation.approval,
        acceptance=mutation.acceptance,
        context=mutation.context,
        version=INITIAL_TASK_VERSION,
        created_by=mutation.actor_subject_id,
        created_at=mutation.occurred_at,
        updated_at=mutation.occurred_at,
    )
    _insert_task(connection, task)
    event = _insert_task_event(connection, mutation=mutation, task=task)
    _record_idempotent_task(
        connection,
        mutation=mutation,
        request_fingerprint=request_fingerprint,
        task=task,
        event=event,
    )
    return task


def _require_active_owner(
    connection: sqlite3.Connection,
    mutation: TaskCreationMutation,
) -> tuple[str, int]:
    """Authorize the active Operator and return Project allocation state.

    Args:
        connection: Active validated write transaction.
        mutation: Task request carrying Project and actor identities.

    Returns:
        Immutable Project key and next task number.

    Raises:
        PermissionDeniedError: If Subject, Project, or Operator grant is unavailable.
        StorageUnavailableError: If allocation state is malformed.

    """
    authorized = require_task_operator(
        connection,
        actor=mutation.actor,
        actor_subject_id=mutation.actor_subject_id,
        project_id=mutation.project_id,
        occurred_at=mutation.occurred_at,
    )
    if authorized is not None:
        row = connection.execute(
            "SELECT key, next_task_number FROM projects WHERE id = ?",
            (str(authorized.project.id),),
        ).fetchone()
        if row is None:
            raise StorageUnavailableError
        return require_text(row[0]), require_integer(row[1])
    row = connection.execute(
        """
        SELECT p.key, p.next_task_number, s.kind, s.enabled, g.role
        FROM projects AS p
        LEFT JOIN subjects AS s ON s.id = ?
        LEFT JOIN project_grants AS g
          ON g.subject_id = s.id AND g.project_id = p.id
        WHERE p.id = ?
        """,
        (str(mutation.actor_subject_id), str(mutation.project_id)),
    ).fetchone()
    if (
        row is None
        or row[2] != SubjectKind.HUMAN.value
        or row[3] != 1
        or row[4] != ProjectRole.OWNER.value
    ):
        raise PermissionDeniedError
    return require_text(row[0]), require_integer(row[1])


def _task_fingerprint(mutation: TaskCreationMutation) -> str:
    """Hash caller-controlled semantic Task input only.

    Args:
        mutation: Validated mutation containing semantic and generated fields.

    Returns:
        Lowercase SHA-256 hexadecimal digest.

    """
    encoded = canonical_json(
        {
            "acceptance": [
                {"id": item.id, "required": item.required, "text": item.text}
                for item in mutation.acceptance
            ],
            "actor_subject_id": str(mutation.actor_subject_id),
            "approval": mutation.approval.value,
            "available_at": (
                None
                if mutation.available_at is None
                else serialize_timestamp(mutation.available_at)
            ),
            "context": [
                {"uri": item.uri, "version": item.version} for item in mutation.context
            ],
            "objective": mutation.objective,
            "priority": mutation.priority,
            "project_id": str(mutation.project_id),
            "title": mutation.title,
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_idempotent_task(
    connection: sqlite3.Connection,
    *,
    mutation: TaskCreationMutation,
    request_fingerprint: str,
) -> Task | None:
    """Return a recorded matching Task or reject conflicting key reuse.

    Args:
        connection: Active semantic write transaction.
        mutation: Validated Task creation mutation.
        request_fingerprint: Canonical semantic request digest.

    Returns:
        Original Task for a matching replay, or ``None``.

    Raises:
        IdempotencyConflictError: If the key was used for different input.
        StorageUnavailableError: If the durable outcome is malformed.

    """
    if mutation.idempotency_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (
            str(mutation.actor_subject_id),
            _CREATE_TASK_OPERATION,
            mutation.idempotency_key,
        ),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    event_id, task = _parse_task_outcome(require_text(row[1]))
    if (
        task.project_id != mutation.project_id
        or task.created_by != mutation.actor_subject_id
    ):
        raise StorageUnavailableError
    event = connection.execute(
        """
        SELECT
            e.task_uid, e.project_id, e.actor_subject_id, e.actor_kind,
            e.attempt_id, e.event_type
        FROM task_events AS e
        JOIN tasks AS t
          ON t.uid = e.task_uid AND t.project_id = e.project_id
        WHERE e.id = ?
        """,
        (str(event_id),),
    ).fetchone()
    if event != (
        str(task.uid),
        str(task.project_id),
        str(task.created_by),
        _actor_kind(mutation).value,
        None,
        TaskEventType.TASK_CREATED.value,
    ):
        raise StorageUnavailableError
    return task


def _insert_task(connection: sqlite3.Connection, task: Task) -> None:
    """Persist one validated Task row.

    Args:
        connection: Active semantic write transaction.
        task: Validated initial Task.

    """
    connection.execute(
        """
        INSERT INTO tasks (
            uid, project_id, number, key, title, objective, state, priority,
            available_at, approval, acceptance_json, context_json,
            blocking_reason, current_result_id, version, created_by,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        task_row(task),
    )


def _insert_task_event(
    connection: sqlite3.Connection,
    *,
    mutation: TaskCreationMutation,
    task: Task,
) -> TaskEvent:
    """Persist and validate the attributable Task-created event.

    Args:
        connection: Active semantic write transaction.
        mutation: Source identities and authoritative timestamp.
        task: Newly inserted Task.

    Returns:
        Validated TaskEvent including its allocated cursor.

    """
    payload: dict[str, JsonScalar] = {
        "key": task.key,
        "number": task.number,
        "objective": task.objective,
        "priority": task.priority,
        "state": task.state.value,
        "title": task.title,
        "version": task.version,
    }
    inserted = connection.execute(
        """
        INSERT INTO task_events (
            id, task_uid, project_id, actor_subject_id, actor_kind, attempt_id,
            request_id, event_type, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(mutation.event_id),
            str(task.uid),
            str(task.project_id),
            str(mutation.actor_subject_id),
            _actor_kind(mutation).value,
            None,
            str(mutation.request_id),
            TaskEventType.TASK_CREATED.value,
            serialize_timestamp(mutation.occurred_at),
            canonical_json(payload),
        ),
    )
    return TaskEvent(
        id=mutation.event_id,
        cursor=require_integer(inserted.lastrowid),
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=mutation.actor_subject_id,
        request_id=mutation.request_id,
        event_type=TaskEventType.TASK_CREATED,
        occurred_at=mutation.occurred_at,
        payload=payload,
    )


def _actor_kind(mutation: TaskCreationMutation) -> SubjectKind:
    """Return authenticated kind or the tokenless Phase 4 Human kind."""
    return SubjectKind.HUMAN if mutation.actor is None else mutation.actor.subject_kind


def _record_idempotent_task(
    connection: sqlite3.Connection,
    *,
    mutation: TaskCreationMutation,
    request_fingerprint: str,
    task: Task,
    event: TaskEvent,
) -> None:
    """Persist one Task replay outcome in the owning transaction.

    Args:
        connection: Active semantic write transaction.
        mutation: Task mutation containing optional caller key.
        request_fingerprint: Canonical semantic request digest.
        task: Original Task result.
        event: Attributable creation event.

    """
    if mutation.idempotency_key is None:
        return
    outcome = canonical_json(
        {
            "event_id": str(event.id),
            "task": task_mapping(task),
        }
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
            _CREATE_TASK_OPERATION,
            mutation.idempotency_key,
            request_fingerprint,
            outcome,
            serialize_timestamp(mutation.occurred_at),
        ),
    )


def _parse_task_outcome(value: str) -> tuple[TaskEventId, Task]:
    """Parse one exact Task creation replay outcome.

    Args:
        value: Persisted canonical JSON.

    Returns:
        Original event identity and Task result.

    Raises:
        StorageUnavailableError: If the outcome is malformed.

    """
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict) or set(decoded) != _TASK_OUTCOME_KEYS:
        raise StorageUnavailableError
    event_id = decoded.get("event_id")
    task_data = decoded.get("task")
    if not isinstance(event_id, str) or not isinstance(task_data, dict):
        raise StorageUnavailableError
    if set(task_data) != TASK_FIELD_SET:
        raise StorageUnavailableError
    task_mapping = cast("Mapping[str, object]", task_data)
    return TaskEventId(event_id), task_from_mapping(task_mapping)
