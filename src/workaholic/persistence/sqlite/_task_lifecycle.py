"""Atomic optimistic Task lifecycle operations for the SQLite repository."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ApplicationError,
    IdempotencyConflictError,
    InvalidInputError,
    InvalidTransitionError,
    PermissionDeniedError,
    TaskMutationResult,
    TaskNotFoundError,
    TaskUpdateMutation,
    VersionConflictError,
)
from workaholic.domain import (
    DomainValidationError,
    ProjectRole,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventType,
    TaskId,
    TaskState,
    TaskTransition,
    build_task_key,
    transition_task_state,
)
from workaholic.persistence.sqlite._event_records import (
    TASK_EVENT_FIELDS,
    TaskEventRecord,
    task_event_record_from_mapping,
    task_event_record_from_row,
    task_event_record_mapping,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    canonical_json,
    parse_json_object,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELD_SET,
    task_from_mapping,
    task_from_row,
    task_mapping,
    task_row,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from workaholic.domain import (
        AcceptanceCriterion,
        ApprovalRequirement,
        ContextReference,
        JsonValue,
    )

_UPDATE_TASK_OPERATION: Final = "task.update"
_MUTATION_OUTCOME_KEYS: Final = frozenset(("event", "task"))


def update_task_if_version(
    database_path: Path,
    mutation: TaskUpdateMutation,
) -> TaskMutationResult:
    """Atomically update editable Task fields at one expected version.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic Task update mutation.

    Returns:
        The committed Task and its attributable ``task_updated`` event.

    Raises:
        IdempotencyConflictError: If a caller key has different semantic input.
        InvalidInputError: If supplied fields do not change the Task.
        InvalidTransitionError: If the Task state does not allow definition edits.
        PermissionDeniedError: If the actor is not an enabled Human Owner.
        TaskNotFoundError: If the scoped Task does not exist.
        VersionConflictError: If the expected version is stale.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, TaskUpdateMutation):
        raise StorageUnavailableError
    request_fingerprint = _update_fingerprint(candidate)
    try:
        with open_write_transaction(database_path) as connection:
            current = _load_authorized_task(
                connection,
                task_uid=candidate.task_uid,
                project_id=str(candidate.project_id),
                actor_subject_id=str(candidate.actor_subject_id),
            )
            replay = _read_idempotent_mutation(
                connection,
                operation=_UPDATE_TASK_OPERATION,
                actor_subject_id=str(candidate.actor_subject_id),
                caller_key=candidate.idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                _require_matching_update(replay, mutation=candidate)
                return replay
            if current.version != candidate.expected_version:
                raise VersionConflictError
            try:
                transition_task_state(
                    current.state,
                    TaskTransition.UPDATE,
                    approval=current.approval,
                )
            except DomainValidationError as error:
                raise InvalidTransitionError from error
            updated = _apply_update(current, mutation=candidate)
            _write_task_if_version(
                connection,
                previous=current,
                updated=updated,
            )
            event = _insert_task_event(
                connection,
                mutation=candidate,
                task=updated,
                event_type=TaskEventType.TASK_UPDATED,
                payload={
                    "changes": tuple(sorted(candidate.patch.model_fields_set)),
                    "version": updated.version,
                },
            )
            result = TaskMutationResult(task=updated, events=(event.event,))
            _require_matching_update(result, mutation=candidate)
            _record_idempotent_mutation(
                connection,
                operation=_UPDATE_TASK_OPERATION,
                actor_subject_id=str(candidate.actor_subject_id),
                caller_key=candidate.idempotency_key,
                request_fingerprint=request_fingerprint,
                occurred_at=candidate.occurred_at,
                result=result,
                event_record=event,
            )
            return result
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _apply_update(current: Task, *, mutation: TaskUpdateMutation) -> Task:
    """Apply one nonempty semantic field patch to a validated Task.

    Args:
        current: Authoritative Task snapshot locked by the transaction.
        mutation: Validated update mutation.

    Returns:
        Updated Task with one version increment and authoritative timestamp.

    Raises:
        InvalidInputError: If every supplied value equals current state.
        StorageUnavailableError: If the authoritative timestamp is inconsistent.

    """
    changes = {
        field_name: getattr(mutation.patch, field_name)
        for field_name in mutation.patch.model_fields_set
    }
    if all(getattr(current, name) == value for name, value in changes.items()):
        raise InvalidInputError
    if mutation.occurred_at < current.updated_at:
        raise StorageUnavailableError
    try:
        return replace(
            current,
            **changes,
            version=current.version + 1,
            updated_at=mutation.occurred_at,
        )
    except DomainValidationError as error:
        raise StorageUnavailableError from error


def _update_fingerprint(mutation: TaskUpdateMutation) -> str:
    """Hash exact caller-controlled update semantics, including omission.

    Args:
        mutation: Validated update mutation.

    Returns:
        Lowercase SHA-256 digest of canonical semantic input.

    """
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "expected_version": mutation.expected_version,
            "patch": _patch_mapping(mutation),
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _patch_mapping(mutation: TaskUpdateMutation) -> dict[str, object]:
    """Serialize only explicitly supplied patch fields for fingerprinting.

    Args:
        mutation: Validated update mutation.

    Returns:
        Canonical JSON-compatible patch mapping.

    """
    result: dict[str, object] = {}
    for field_name in sorted(mutation.patch.model_fields_set):
        value = getattr(mutation.patch, field_name)
        if field_name == "available_at":
            result[field_name] = (
                None if value is None else serialize_timestamp(cast("datetime", value))
            )
        elif field_name == "approval":
            result[field_name] = cast("ApprovalRequirement", value).value
        elif field_name == "acceptance":
            result[field_name] = _acceptance_mapping(
                cast("Sequence[AcceptanceCriterion]", value)
            )
        elif field_name == "context":
            result[field_name] = _context_mapping(
                cast("Sequence[ContextReference]", value)
            )
        else:
            result[field_name] = value
    return result


def _acceptance_mapping(
    values: Sequence[AcceptanceCriterion],
) -> list[dict[str, object]]:
    """Serialize ordered acceptance criteria for a semantic fingerprint."""
    return [
        {"id": item.id, "required": item.required, "text": item.text} for item in values
    ]


def _context_mapping(values: Sequence[ContextReference]) -> list[dict[str, object]]:
    """Serialize ordered context references for a semantic fingerprint."""
    return [{"uri": item.uri, "version": item.version} for item in values]


def _load_authorized_task(
    connection: sqlite3.Connection,
    *,
    task_uid: TaskId,
    project_id: str,
    actor_subject_id: str,
) -> Task:
    """Authorize one Human Owner and hydrate the complete scoped Task.

    Args:
        connection: Active validated write transaction.
        task_uid: Canonical Task identity.
        project_id: Canonical Project identity text.
        actor_subject_id: Authenticated Subject identity text.

    Returns:
        Complete persisted Task including ordered dependencies.

    Raises:
        PermissionDeniedError: If Project authorization is absent or disabled.
        TaskNotFoundError: If the authorized scoped Task does not exist.

    """
    row = connection.execute(
        """
        SELECT
            p.key, s.kind, s.enabled, g.role,
            t.uid, t.project_id, t.number, t.key, t.title, t.objective,
            t.state, t.priority, t.available_at, t.approval,
            t.acceptance_json, t.context_json, t.blocking_reason,
            t.current_result_id, t.version, t.created_by, t.created_at,
            t.updated_at
        FROM projects AS p
        LEFT JOIN subjects AS s ON s.id = ?
        LEFT JOIN project_grants AS g
          ON g.subject_id = s.id AND g.project_id = p.id
        LEFT JOIN tasks AS t
          ON t.project_id = p.id AND t.uid = ?
        WHERE p.id = ?
        """,
        (actor_subject_id, str(task_uid), project_id),
    ).fetchone()
    if (
        row is None
        or row[1] != SubjectKind.HUMAN.value
        or row[2] != 1
        or row[3] != ProjectRole.OWNER.value
    ):
        raise PermissionDeniedError
    if row[4] is None:
        raise TaskNotFoundError
    dependencies = _load_dependencies(
        connection,
        task_uid=task_uid,
        project_id=project_id,
    )
    task = task_from_row(row[4:], depends_on=dependencies)
    if task.key != build_task_key(require_text(row[0]), task.number):
        raise StorageUnavailableError
    return task


def _load_dependencies(
    connection: sqlite3.Connection,
    *,
    task_uid: TaskId,
    project_id: str,
) -> tuple[TaskId, ...]:
    """Load one Task's prerequisites in immutable Human-key order."""
    rows = connection.execute(
        """
        SELECT d.prerequisite_uid, prerequisite.key
        FROM task_dependencies AS d
        LEFT JOIN tasks AS prerequisite
          ON prerequisite.uid = d.prerequisite_uid
         AND prerequisite.project_id = d.project_id
        WHERE d.task_uid = ? AND d.project_id = ?
        ORDER BY prerequisite.key ASC
        """,
        (str(task_uid), project_id),
    ).fetchall()
    dependencies: list[TaskId] = []
    for row in rows:
        require_text(row[1])
        dependencies.append(TaskId(require_text(row[0])))
    return tuple(dependencies)


def _write_task_if_version(
    connection: sqlite3.Connection,
    *,
    previous: Task,
    updated: Task,
) -> None:
    """Write all mutable Task columns under the optimistic precondition."""
    values = task_row(updated)
    changed = connection.execute(
        """
        UPDATE tasks
        SET
            title = ?, objective = ?, state = ?, priority = ?,
            available_at = ?, approval = ?, acceptance_json = ?,
            context_json = ?, blocking_reason = ?, current_result_id = ?,
            version = ?, updated_at = ?
        WHERE uid = ? AND project_id = ? AND version = ?
        """,
        (
            values[4],
            values[5],
            values[6],
            values[7],
            values[8],
            values[9],
            values[10],
            values[11],
            values[12],
            values[13],
            values[14],
            values[17],
            values[0],
            values[1],
            previous.version,
        ),
    )
    if changed.rowcount != 1:
        raise VersionConflictError


def _insert_task_event(
    connection: sqlite3.Connection,
    *,
    mutation: TaskUpdateMutation,
    task: Task,
    event_type: TaskEventType,
    payload: Mapping[str, JsonValue],
) -> TaskEventRecord:
    """Append one Human-attributed TaskEvent inside the owning transaction."""
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
            SubjectKind.HUMAN.value,
            None,
            str(mutation.request_id),
            event_type.value,
            serialize_timestamp(mutation.occurred_at),
            canonical_json(payload),
        ),
    )
    return TaskEventRecord(
        event=TaskEvent(
            id=mutation.event_id,
            cursor=require_integer(inserted.lastrowid),
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=mutation.actor_subject_id,
            request_id=mutation.request_id,
            event_type=event_type,
            occurred_at=mutation.occurred_at,
            payload=payload,
        ),
        actor_kind=SubjectKind.HUMAN,
        attempt_id=None,
    )


def _read_idempotent_mutation(
    connection: sqlite3.Connection,
    *,
    operation: str,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
) -> TaskMutationResult | None:
    """Return one matching historic mutation or reject conflicting key reuse."""
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
    result, event_record = _parse_mutation_outcome(require_text(row[1]))
    actual_event_row = connection.execute(
        f"""
        SELECT {", ".join(TASK_EVENT_FIELDS)}
        FROM task_events
        WHERE id = ?
        """,  # noqa: S608 - field names are a closed module constant.
        (str(event_record.event.id),),
    ).fetchone()
    if (
        actual_event_row is None
        or task_event_record_from_row(actual_event_row) != event_record
        or event_record.event.actor_subject_id.value != actor_subject_id
    ):
        raise StorageUnavailableError
    return result


def _record_idempotent_mutation(  # noqa: PLR0913 - exact durable record contract.
    connection: sqlite3.Connection,
    *,
    operation: str,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
    occurred_at: datetime,
    result: TaskMutationResult,
    event_record: TaskEventRecord,
) -> None:
    """Persist one canonical replay outcome inside the owning transaction."""
    if caller_key is None:
        return
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor_subject_id,
            operation,
            caller_key,
            request_fingerprint,
            canonical_json(
                {
                    "event": task_event_record_mapping(event_record),
                    "task": task_mapping(result.task),
                }
            ),
            serialize_timestamp(occurred_at),
        ),
    )


def _parse_mutation_outcome(value: str) -> tuple[TaskMutationResult, TaskEventRecord]:
    """Parse and validate one exact canonical mutation replay outcome."""
    decoded = parse_json_object(
        value,
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(decoded) != _MUTATION_OUTCOME_KEYS:
        raise StorageUnavailableError
    task_value = decoded["task"]
    event_value = decoded["event"]
    if (
        not isinstance(task_value, dict)
        or set(task_value) != TASK_FIELD_SET
        or not isinstance(event_value, dict)
    ):
        raise StorageUnavailableError
    task = task_from_mapping(cast("Mapping[str, object]", task_value))
    event_record = task_event_record_from_mapping(
        cast("Mapping[str, object]", event_value)
    )
    try:
        result = TaskMutationResult(task=task, events=(event_record.event,))
    except ValueError as error:
        raise StorageUnavailableError from error
    return result, event_record


def _require_matching_update(
    result: TaskMutationResult,
    *,
    mutation: TaskUpdateMutation,
) -> None:
    """Validate one fresh or replayed update result against its mutation."""
    task = result.task
    event = result.events[0]
    expected_changes = tuple(sorted(mutation.patch.model_fields_set))
    if (
        task.uid != mutation.task_uid
        or task.project_id != mutation.project_id
        or task.version != mutation.expected_version + 1
        or task.state not in (TaskState.OPEN, TaskState.BLOCKED)
        or event.event_type is not TaskEventType.TASK_UPDATED
        or event.actor_subject_id != mutation.actor_subject_id
        or event.occurred_at != task.updated_at
        or dict(event.payload) != {"changes": expected_changes, "version": task.version}
        or any(
            getattr(task, name) != getattr(mutation.patch, name)
            for name in mutation.patch.model_fields_set
        )
    ):
        raise StorageUnavailableError
