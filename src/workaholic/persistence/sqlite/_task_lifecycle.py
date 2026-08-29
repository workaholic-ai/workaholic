"""Atomic optimistic Task lifecycle operations for the SQLite repository."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol, cast

from workaholic.application import (
    ApplicationError,
    IdempotencyConflictError,
    InvalidInputError,
    InvalidTransitionError,
    PermissionDeniedError,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskMutationResult,
    TaskNotFoundError,
    TaskUnblockMutation,
    TaskUpdateMutation,
    VersionConflictError,
)
from workaholic.domain import (
    DomainValidationError,
    ProjectId,
    ProjectRole,
    SubjectId,
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
from workaholic.persistence.sqlite._authorization import require_task_operator
from workaholic.persistence.sqlite._claim_state import (
    end_human_claim,
    guard_human_task_mutation,
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
    parse_timestamp,
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
        AuthenticatedActor,
        ContextReference,
        JsonValue,
        RequestId,
        TaskEventId,
    )

_MUTATION_OUTCOME_KEYS: Final = frozenset(("events", "task"))

type _LifecycleMutation = (
    TaskUpdateMutation | TaskBlockMutation | TaskUnblockMutation | TaskCancelMutation
)


class _TaskEventMutation(Protocol):
    """Attribution fields shared by semantic Task-event mutations."""

    event_id: TaskEventId
    actor_subject_id: SubjectId
    actor: AuthenticatedActor | None
    request_id: RequestId
    occurred_at: datetime


class _ClaimGuardedMutation(Protocol):
    """Attribution and conditional-expiry identity for Operator Task writes."""

    claim_expired_event_id: TaskEventId
    actor_subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class _MutationPlan:
    """Closed semantic constants for one optimistic lifecycle operation."""

    operation: str
    transition: TaskTransition
    event_type: TaskEventType


_UPDATE_PLAN: Final = _MutationPlan(
    operation="task.update",
    transition=TaskTransition.UPDATE,
    event_type=TaskEventType.TASK_UPDATED,
)
_BLOCK_PLAN: Final = _MutationPlan(
    operation="task.block",
    transition=TaskTransition.BLOCK,
    event_type=TaskEventType.TASK_BLOCKED,
)
_UNBLOCK_PLAN: Final = _MutationPlan(
    operation="task.unblock",
    transition=TaskTransition.UNBLOCK,
    event_type=TaskEventType.TASK_UNBLOCKED,
)
_CANCEL_PLAN: Final = _MutationPlan(
    operation="task.cancel",
    transition=TaskTransition.CANCEL,
    event_type=TaskEventType.TASK_CANCELLED,
)


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
    return _execute_mutation(database_path, mutation=candidate, plan=_UPDATE_PLAN)


def block_task(
    database_path: Path,
    mutation: TaskBlockMutation,
) -> TaskMutationResult:
    """Atomically move one open Task to blocked.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic blocking mutation.

    Returns:
        The committed blocked Task and its attributable event.

    Raises:
        ApplicationError: If authorization, version, transition, or replay fails.
        StorageUnavailableError: If storage violates its contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, TaskBlockMutation):
        raise StorageUnavailableError
    return _execute_mutation(database_path, mutation=candidate, plan=_BLOCK_PLAN)


def unblock_task(
    database_path: Path,
    mutation: TaskUnblockMutation,
) -> TaskMutationResult:
    """Atomically return one blocked Task to open.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic unblocking mutation.

    Returns:
        The committed open Task and its attributable event.

    Raises:
        ApplicationError: If authorization, version, transition, or replay fails.
        StorageUnavailableError: If storage violates its contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, TaskUnblockMutation):
        raise StorageUnavailableError
    return _execute_mutation(database_path, mutation=candidate, plan=_UNBLOCK_PLAN)


def cancel_task(
    database_path: Path,
    mutation: TaskCancelMutation,
) -> TaskMutationResult:
    """Atomically cancel one mutable Task.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic cancellation mutation.

    Returns:
        The committed cancelled Task and its attributable event.

    Raises:
        ApplicationError: If authorization, version, transition, or replay fails.
        StorageUnavailableError: If storage violates its contract.

    """
    candidate: object = mutation
    if not isinstance(candidate, TaskCancelMutation):
        raise StorageUnavailableError
    return _execute_mutation(database_path, mutation=candidate, plan=_CANCEL_PLAN)


def _execute_mutation(
    database_path: Path,
    *,
    mutation: _LifecycleMutation,
    plan: _MutationPlan,
) -> TaskMutationResult:
    """Execute one lifecycle mutation through the shared optimistic core.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated lifecycle mutation.
        plan: Closed operation, transition, and event semantics.

    Returns:
        The committed or idempotently replayed mutation result.

    Raises:
        ApplicationError: If a stable semantic operation fails.
        StorageUnavailableError: If persisted state violates its contract.

    """
    request_fingerprint = _mutation_fingerprint(mutation)
    try:
        with open_write_transaction(database_path) as connection:
            current = _load_authorized_task(
                connection,
                task_uid=mutation.task_uid,
                project_id=str(mutation.project_id),
                actor_subject_id=str(mutation.actor_subject_id),
                actor=mutation.actor,
                occurred_at=mutation.occurred_at,
            )
            replay = _read_idempotent_mutation(
                connection,
                operation=plan.operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                _require_matching_mutation(
                    replay,
                    mutation=mutation,
                    plan=plan,
                    fresh=False,
                )
                return replay
            owner_state, expiry_records = guard_human_task_mutation(
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
            if current.version != mutation.expected_version:
                raise VersionConflictError
            try:
                next_state = transition_task_state(
                    current.state,
                    plan.transition,
                    approval=current.approval,
                )
            except DomainValidationError as error:
                raise InvalidTransitionError from error
            updated = _apply_mutation(
                current,
                mutation=mutation,
                next_state=next_state,
            )
            if isinstance(mutation, TaskCancelMutation):
                end_human_claim(
                    connection,
                    task=current,
                    state=owner_state,
                    actor_subject_id=mutation.actor_subject_id,
                )
            _write_task_if_version(
                connection,
                previous=current,
                updated=updated,
            )
            event = _insert_task_event(
                connection,
                mutation=mutation,
                task=updated,
                event_type=plan.event_type,
                payload=_event_payload(mutation, task=updated),
            )
            event_records = (*expiry_records, event)
            result = TaskMutationResult(
                task=updated,
                events=tuple(record.event for record in event_records),
            )
            _require_matching_mutation(
                result,
                mutation=mutation,
                plan=plan,
                fresh=True,
            )
            _record_idempotent_mutation(
                connection,
                operation=plan.operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=request_fingerprint,
                occurred_at=mutation.occurred_at,
                result=result,
                event_records=event_records,
            )
            return result
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _apply_mutation(
    current: Task,
    *,
    mutation: _LifecycleMutation,
    next_state: TaskState,
) -> Task:
    """Apply one validated lifecycle operation to an authoritative Task.

    Args:
        current: Authoritative Task snapshot locked by the transaction.
        mutation: Validated lifecycle mutation.
        next_state: Domain-authorized post-operation state.

    Returns:
        Updated Task with one version increment and authoritative timestamp.

    Raises:
        InvalidInputError: If an update patch changes no definition field.
        StorageUnavailableError: If the authoritative timestamp is inconsistent.

    """
    if isinstance(mutation, TaskUpdateMutation):
        changes = {
            field_name: getattr(mutation.patch, field_name)
            for field_name in mutation.patch.model_fields_set
        }
        if all(getattr(current, name) == value for name, value in changes.items()):
            raise InvalidInputError
    if mutation.occurred_at < current.updated_at:
        raise StorageUnavailableError
    try:
        if isinstance(mutation, TaskUpdateMutation):
            fields = mutation.patch.model_fields_set
            patch = mutation.patch
            return replace(
                current,
                title=(
                    cast("str", patch.title) if "title" in fields else current.title
                ),
                objective=(
                    cast("str", patch.objective)
                    if "objective" in fields
                    else current.objective
                ),
                priority=(
                    cast("int", patch.priority)
                    if "priority" in fields
                    else current.priority
                ),
                available_at=(
                    patch.available_at
                    if "available_at" in fields
                    else current.available_at
                ),
                approval=(
                    cast("ApprovalRequirement", patch.approval)
                    if "approval" in fields
                    else current.approval
                ),
                acceptance=(
                    cast("tuple[AcceptanceCriterion, ...]", patch.acceptance)
                    if "acceptance" in fields
                    else current.acceptance
                ),
                context=(
                    cast("tuple[ContextReference, ...]", patch.context)
                    if "context" in fields
                    else current.context
                ),
                version=current.version + 1,
                updated_at=mutation.occurred_at,
            )
        blocking_reason = (
            mutation.reason if isinstance(mutation, TaskBlockMutation) else None
        )
        return replace(
            current,
            state=next_state,
            blocking_reason=blocking_reason,
            version=current.version + 1,
            updated_at=mutation.occurred_at,
        )
    except DomainValidationError as error:
        raise StorageUnavailableError from error


def _mutation_fingerprint(mutation: _LifecycleMutation) -> str:
    """Hash exact caller-controlled lifecycle semantics.

    Args:
        mutation: Validated lifecycle mutation.

    Returns:
        Lowercase SHA-256 digest of canonical semantic input.

    """
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "expected_version": mutation.expected_version,
            "input": _mutation_input_mapping(mutation),
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mutation_input_mapping(mutation: _LifecycleMutation) -> dict[str, object]:
    """Serialize exact operation-specific caller input for fingerprinting.

    Args:
        mutation: Validated lifecycle mutation.

    Returns:
        Canonical JSON-compatible operation input.

    """
    if isinstance(mutation, TaskUpdateMutation):
        return _patch_mapping(mutation)
    if isinstance(mutation, (TaskBlockMutation, TaskCancelMutation)):
        return {"reason": mutation.reason}
    return {}


def _event_payload(
    mutation: _LifecycleMutation,
    *,
    task: Task,
) -> dict[str, JsonValue]:
    """Build one bounded operation-specific lifecycle event payload.

    Args:
        mutation: Validated lifecycle mutation.
        task: Committed post-operation Task snapshot.

    Returns:
        Stable event metadata without infrastructure details.

    """
    if isinstance(mutation, TaskUpdateMutation):
        return {
            "changes": tuple(sorted(mutation.patch.model_fields_set)),
            "version": task.version,
        }
    if isinstance(mutation, (TaskBlockMutation, TaskCancelMutation)):
        return {"reason": mutation.reason, "version": task.version}
    return {"version": task.version}


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


def _load_authorized_task(  # noqa: PLR0913 - explicit authorization boundary.
    connection: sqlite3.Connection,
    *,
    task_uid: TaskId,
    project_id: str,
    actor_subject_id: str,
    actor: AuthenticatedActor | None = None,
    occurred_at: datetime | None = None,
    required_kind: SubjectKind | None = None,
) -> Task:
    """Authorize one Operator and hydrate the complete scoped Task.

    Args:
        connection: Active validated write transaction.
        task_uid: Canonical Task identity.
        project_id: Canonical Project identity text.
        actor_subject_id: Authenticated Subject identity text.
        actor: Authenticated actor context, or the tokenless build bridge.
        occurred_at: Authoritative authentication time when ``actor`` is set.
        required_kind: Optional exact Subject-kind constraint.

    Returns:
        Complete persisted Task including ordered dependencies.

    Raises:
        PermissionDeniedError: If Project authorization is absent or disabled.
        TaskNotFoundError: If the authorized scoped Task does not exist.

    """
    authorized = require_task_operator(
        connection,
        actor=actor,
        actor_subject_id=SubjectId(actor_subject_id),
        project_id=ProjectId(project_id),
        occurred_at=occurred_at,
        required_kind=required_kind,
    )
    if authorized is not None:
        row = connection.execute(
            """
            SELECT
                p.key,
                t.uid, t.project_id, t.number, t.key, t.title, t.objective,
                t.state, t.priority, t.available_at, t.approval,
                t.acceptance_json, t.context_json, t.blocking_reason,
                t.current_result_id, t.version, t.created_by, t.created_at,
                t.updated_at
            FROM projects AS p
            LEFT JOIN tasks AS t
              ON t.project_id = p.id AND t.uid = ?
            WHERE p.id = ?
            """,
            (str(task_uid), project_id),
        ).fetchone()
        if row is None:
            raise PermissionDeniedError
        if row[1] is None:
            raise TaskNotFoundError
        dependencies = _load_dependencies(
            connection,
            task_uid=task_uid,
            project_id=project_id,
        )
        task = task_from_row(row[1:], depends_on=dependencies)
        if task.key != build_task_key(require_text(row[0]), task.number):
            raise StorageUnavailableError
        return task
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
    mutation: _TaskEventMutation,
    task: Task,
    event_type: TaskEventType,
    payload: Mapping[str, JsonValue],
) -> TaskEventRecord:
    """Append one authenticated TaskEvent inside the owning transaction."""
    actor_kind = (
        SubjectKind.HUMAN if mutation.actor is None else mutation.actor.subject_kind
    )
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
            actor_kind.value,
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
        actor_kind=actor_kind,
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
    result, event_records = _parse_mutation_outcome(require_text(row[1]))
    for event_record in event_records:
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
    event_records: Sequence[TaskEventRecord],
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
                    "events": [
                        task_event_record_mapping(record) for record in event_records
                    ],
                    "task": task_mapping(result.task),
                }
            ),
            serialize_timestamp(occurred_at),
        ),
    )


def _parse_mutation_outcome(
    value: str,
) -> tuple[TaskMutationResult, tuple[TaskEventRecord, ...]]:
    """Parse and validate one exact canonical mutation replay outcome."""
    decoded = parse_json_object(
        value,
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(decoded) != _MUTATION_OUTCOME_KEYS:
        raise StorageUnavailableError
    task_value = decoded["task"]
    events_value = decoded["events"]
    if (
        not isinstance(task_value, dict)
        or set(task_value) != TASK_FIELD_SET
        or not isinstance(events_value, list)
        or len(events_value) not in (1, 2)
        or any(not isinstance(item, dict) for item in events_value)
    ):
        raise StorageUnavailableError
    task = task_from_mapping(cast("Mapping[str, object]", task_value))
    event_records = tuple(
        task_event_record_from_mapping(cast("Mapping[str, object]", item))
        for item in events_value
    )
    try:
        result = TaskMutationResult(
            task=task,
            events=tuple(record.event for record in event_records),
        )
    except ValueError as error:
        raise StorageUnavailableError from error
    return result, event_records


def _require_matching_mutation(
    result: TaskMutationResult,
    *,
    mutation: _LifecycleMutation,
    plan: _MutationPlan,
    fresh: bool,
) -> None:
    """Validate one fresh or replayed lifecycle result against its mutation."""
    task = result.task
    event = result.events[-1]
    invalid_common = (
        task.uid != mutation.task_uid
        or task.project_id != mutation.project_id
        or task.version != mutation.expected_version + 1
        or (
            fresh
            and (
                event.id != mutation.event_id
                or event.request_id != mutation.request_id
                or event.occurred_at != mutation.occurred_at
            )
        )
        or event.event_type is not plan.event_type
        or event.actor_subject_id != mutation.actor_subject_id
        or event.occurred_at != task.updated_at
        or dict(event.payload) != _event_payload(mutation, task=task)
    )
    if invalid_common:
        raise StorageUnavailableError
    _require_matching_expiry_prefix(result, mutation=mutation, fresh=fresh)
    if isinstance(mutation, TaskUpdateMutation):
        valid_outcome = task.state in (TaskState.OPEN, TaskState.BLOCKED) and all(
            getattr(task, name) == getattr(mutation.patch, name)
            for name in mutation.patch.model_fields_set
        )
    elif isinstance(mutation, TaskBlockMutation):
        valid_outcome = (
            task.state is TaskState.BLOCKED and task.blocking_reason == mutation.reason
        )
    elif isinstance(mutation, TaskUnblockMutation):
        valid_outcome = task.state is TaskState.OPEN and task.blocking_reason is None
    else:
        valid_outcome = (
            task.state is TaskState.CANCELLED and task.blocking_reason is None
        )
    if not valid_outcome:
        raise StorageUnavailableError


def _require_matching_expiry_prefix(
    result: TaskMutationResult,
    *,
    mutation: _ClaimGuardedMutation,
    fresh: bool,
) -> None:
    """Validate a nullable lazy-expiry prefix against the owning mutation."""
    if len(result.events) == 1:
        return
    expired = result.events[0]
    payload = expired.payload
    if (
        expired.event_type is not TaskEventType.CLAIM_EXPIRED
        or expired.actor_subject_id != mutation.actor_subject_id
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
        or parse_timestamp(payload["lease_expires_at"]) > mutation.occurred_at
    ):
        raise StorageUnavailableError
