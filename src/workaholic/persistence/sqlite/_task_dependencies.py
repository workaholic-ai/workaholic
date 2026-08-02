"""Atomic optimistic Task dependency graph operations for SQLite."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from workaholic.application import (
    AddTaskDependencyMutation,
    ApplicationError,
    DependencyConflictError,
    DependencyCycleError,
    InvalidTransitionError,
    RemoveTaskDependencyMutation,
    TaskMutationResult,
    TaskNotFoundError,
    VersionConflictError,
)
from workaholic.domain import (
    DomainValidationError,
    Task,
    TaskEventType,
    TaskId,
    TaskTransition,
    transition_task_state,
    validate_dependency_addition,
    validate_dependency_removal,
)
from workaholic.persistence.sqlite._records import canonical_json, require_text
from workaholic.persistence.sqlite._task_lifecycle import (
    _insert_task_event,
    _load_authorized_task,
    _load_dependencies,
    _read_idempotent_mutation,
    _record_idempotent_mutation,
    _write_task_if_version,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Collection, Mapping
    from pathlib import Path

    from workaholic.domain import JsonValue

type _DependencyMutation = AddTaskDependencyMutation | RemoveTaskDependencyMutation

_ADD_OPERATION: Final = "task.dependency.add"
_REMOVE_OPERATION: Final = "task.dependency.remove"


def add_task_dependency(
    database_path: Path,
    mutation: AddTaskDependencyMutation,
) -> TaskMutationResult:
    """Atomically add one same-Project acyclic prerequisite edge.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic dependency addition.

    Returns:
        Committed dependant Task and its attributable update event.

    Raises:
        ApplicationError: If authorization, graph, replay, or version checks fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not AddTaskDependencyMutation:
        raise StorageUnavailableError
    return _execute_dependency_mutation(database_path, mutation=candidate, add=True)


def remove_task_dependency(
    database_path: Path,
    mutation: RemoveTaskDependencyMutation,
) -> TaskMutationResult:
    """Atomically remove one existing same-Project prerequisite edge.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic dependency removal.

    Returns:
        Committed dependant Task and its attributable update event.

    Raises:
        ApplicationError: If authorization, graph, replay, or version checks fail.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = mutation
    if type(candidate) is not RemoveTaskDependencyMutation:
        raise StorageUnavailableError
    return _execute_dependency_mutation(database_path, mutation=candidate, add=False)


def _execute_dependency_mutation(
    database_path: Path,
    *,
    mutation: _DependencyMutation,
    add: bool,
) -> TaskMutationResult:
    """Execute one dependency edit through a single write transaction.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated dependency mutation.
        add: Whether to add rather than remove the edge.

    Returns:
        Fresh or idempotently replayed semantic result.

    Raises:
        ApplicationError: If an expected semantic precondition fails.
        StorageUnavailableError: If storage contains invalid data.

    """
    operation = _ADD_OPERATION if add else _REMOVE_OPERATION
    fingerprint = _mutation_fingerprint(mutation)
    try:
        with open_write_transaction(database_path) as connection:
            current = _load_authorized_task(
                connection,
                task_uid=mutation.task_uid,
                project_id=str(mutation.project_id),
                actor_subject_id=str(mutation.actor_subject_id),
            )
            replay = _read_idempotent_mutation(
                connection,
                operation=operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                _require_matching_result(replay, mutation=mutation, add=add)
                return replay
            if current.version != mutation.expected_version:
                raise VersionConflictError
            _require_editable_state(current, add=add)
            prerequisite = _load_prerequisite(
                connection,
                mutation=mutation,
            )
            graph = _load_project_graph(
                connection,
                project_id=str(mutation.project_id),
            )
            _validate_graph_change(
                dependant=current,
                prerequisite=prerequisite,
                graph=graph,
                add=add,
            )
            _write_edge(connection, mutation=mutation, add=add)
            _require_timestamp_not_before_task(current, mutation=mutation)
            updated = replace(
                current,
                depends_on=_load_dependencies(
                    connection,
                    task_uid=current.uid,
                    project_id=str(current.project_id),
                ),
                version=current.version + 1,
                updated_at=mutation.occurred_at,
            )
            _write_task_if_version(connection, previous=current, updated=updated)
            event = _insert_task_event(
                connection,
                mutation=mutation,
                task=updated,
                event_type=TaskEventType.TASK_UPDATED,
                payload=_event_payload(mutation, task=updated, add=add),
            )
            result = TaskMutationResult(task=updated, events=(event.event,))
            _require_matching_result(result, mutation=mutation, add=add)
            _record_idempotent_mutation(
                connection,
                operation=operation,
                actor_subject_id=str(mutation.actor_subject_id),
                caller_key=mutation.idempotency_key,
                request_fingerprint=fingerprint,
                occurred_at=mutation.occurred_at,
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


def _require_editable_state(task: Task, *, add: bool) -> None:
    """Map the domain transition rule to a stable application outcome.

    Args:
        task: Authoritative dependant Task.
        add: Whether the requested transition adds an edge.

    Raises:
        InvalidTransitionError: If graph edits are illegal in the Task state.

    """
    transition = (
        TaskTransition.ADD_DEPENDENCY if add else TaskTransition.REMOVE_DEPENDENCY
    )
    try:
        transition_task_state(task.state, transition, approval=task.approval)
    except DomainValidationError as error:
        raise InvalidTransitionError from error


def _require_timestamp_not_before_task(
    task: Task,
    *,
    mutation: _DependencyMutation,
) -> None:
    """Require an authoritative mutation time not before persisted Task time.

    Args:
        task: Current authoritative Task.
        mutation: Owning dependency mutation.

    Raises:
        StorageUnavailableError: If the clock moved before persisted state.

    """
    if mutation.occurred_at < task.updated_at:
        raise StorageUnavailableError


def _load_prerequisite(
    connection: sqlite3.Connection,
    *,
    mutation: _DependencyMutation,
) -> Task:
    """Load and classify one prerequisite identity before graph validation.

    Args:
        connection: Active write transaction.
        mutation: Owning dependency mutation.

    Returns:
        Complete same-Project prerequisite Task.

    Raises:
        TaskNotFoundError: If no Task has the supplied identity.
        DependencyConflictError: If the identity belongs to another Project.

    """
    row = connection.execute(
        "SELECT project_id FROM tasks WHERE uid = ?",
        (str(mutation.prerequisite_uid),),
    ).fetchone()
    if row is None:
        raise TaskNotFoundError
    if row != (str(mutation.project_id),):
        raise DependencyConflictError
    return _load_authorized_task(
        connection,
        task_uid=mutation.prerequisite_uid,
        project_id=str(mutation.project_id),
        actor_subject_id=str(mutation.actor_subject_id),
    )


def _load_project_graph(
    connection: sqlite3.Connection,
    *,
    project_id: str,
) -> dict[TaskId, tuple[TaskId, ...]]:
    """Load one complete Project dependency graph for cycle validation.

    Args:
        connection: Active write transaction.
        project_id: Canonical Project identity text.

    Returns:
        Directed Task-to-prerequisite adjacency mapping.

    """
    task_rows = connection.execute(
        "SELECT uid FROM tasks WHERE project_id = ? ORDER BY uid",
        (project_id,),
    ).fetchall()
    graph: dict[TaskId, list[TaskId]] = {
        TaskId(require_text(row[0])): [] for row in task_rows
    }
    edge_rows = connection.execute(
        """
        SELECT task_uid, prerequisite_uid
        FROM task_dependencies
        WHERE project_id = ?
        ORDER BY task_uid, prerequisite_uid
        """,
        (project_id,),
    ).fetchall()
    for task_value, prerequisite_value in edge_rows:
        task_uid = TaskId(require_text(task_value))
        prerequisite_uid = TaskId(require_text(prerequisite_value))
        if task_uid not in graph or prerequisite_uid not in graph:
            raise StorageUnavailableError
        graph[task_uid].append(prerequisite_uid)
    result = {task_uid: tuple(values) for task_uid, values in graph.items()}
    _require_acyclic_graph(result)
    return result


def _require_acyclic_graph(
    graph: Mapping[TaskId, Collection[TaskId]],
) -> None:
    """Require persisted graph state to be acyclic before applying a mutation.

    Args:
        graph: Complete same-Project dependency graph.

    Raises:
        StorageUnavailableError: If persisted edges already contain a cycle.

    """
    for task_uid in graph:
        if _path_exists(
            graph,
            start=task_uid,
            target=task_uid,
            require_edge=True,
        ):
            raise StorageUnavailableError


def _validate_graph_change(
    *,
    dependant: Task,
    prerequisite: Task,
    graph: Mapping[TaskId, Collection[TaskId]],
    add: bool,
) -> None:
    """Apply pure graph validation and map its exact semantic failure.

    Args:
        dependant: Authoritative Task being versioned.
        prerequisite: Existing candidate prerequisite.
        graph: Complete current same-Project graph.
        add: Whether an addition rather than removal is requested.

    Raises:
        DependencyConflictError: For duplicate, absent, self, or cross edges.
        DependencyCycleError: If an addition would create a cycle.

    """
    try:
        if add:
            validate_dependency_addition(
                dependant=dependant,
                prerequisite=prerequisite,
                dependency_graph=graph,
            )
        else:
            validate_dependency_removal(
                dependant=dependant,
                prerequisite=prerequisite,
            )
    except DomainValidationError as error:
        if (
            add
            and prerequisite.uid != dependant.uid
            and _path_exists(
                graph,
                start=prerequisite.uid,
                target=dependant.uid,
            )
        ):
            raise DependencyCycleError from error
        raise DependencyConflictError from error


def _path_exists(
    graph: Mapping[TaskId, Collection[TaskId]],
    *,
    start: TaskId,
    target: TaskId,
    require_edge: bool = False,
) -> bool:
    """Return whether following prerequisite edges reaches a target Task.

    Args:
        graph: Complete same-Project adjacency mapping.
        start: First Task to visit.
        target: Task identity that would close a cycle.
        require_edge: Whether the zero-length start-to-self path is excluded.

    Returns:
        Whether a path exists, without recursion-depth dependence.

    """
    pending = list(graph.get(start, ())) if require_edge else [start]
    visited: set[TaskId] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return False


def _write_edge(
    connection: sqlite3.Connection,
    *,
    mutation: _DependencyMutation,
    add: bool,
) -> None:
    """Apply one already validated edge mutation.

    Args:
        connection: Active write transaction.
        mutation: Validated dependency mutation.
        add: Whether to insert rather than delete.

    Raises:
        DependencyConflictError: If concurrent/invalid edge state is observed.

    """
    values = (
        str(mutation.task_uid),
        str(mutation.prerequisite_uid),
        str(mutation.project_id),
    )
    if add:
        changed = connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            values,
        )
    else:
        changed = connection.execute(
            """
            DELETE FROM task_dependencies
            WHERE task_uid = ? AND prerequisite_uid = ? AND project_id = ?
            """,
            values,
        )
    if changed.rowcount != 1:
        raise DependencyConflictError


def _mutation_fingerprint(mutation: _DependencyMutation) -> str:
    """Hash exact caller-controlled dependency semantics.

    Args:
        mutation: Validated dependency mutation.

    Returns:
        Lowercase SHA-256 digest of canonical semantic input.

    """
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "expected_version": mutation.expected_version,
            "prerequisite_uid": str(mutation.prerequisite_uid),
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_payload(
    mutation: _DependencyMutation,
    *,
    task: Task,
    add: bool,
) -> dict[str, JsonValue]:
    """Build one safe dependency-specific Task update payload.

    Args:
        mutation: Owning dependency mutation.
        task: Committed dependant Task.
        add: Whether the edge was added rather than removed.

    Returns:
        Stable bounded event metadata.

    """
    return {
        "dependency": "added" if add else "removed",
        "prerequisite_uid": str(mutation.prerequisite_uid),
        "version": task.version,
    }


def _require_matching_result(
    result: TaskMutationResult,
    *,
    mutation: _DependencyMutation,
    add: bool,
) -> None:
    """Validate one fresh or replayed dependency result.

    Args:
        result: Candidate semantic result.
        mutation: Owning dependency mutation.
        add: Whether the edge should be present afterward.

    Raises:
        StorageUnavailableError: If the result violates its durable contract.

    """
    task = result.task
    event = result.events[0]
    if (
        task.uid != mutation.task_uid
        or task.project_id != mutation.project_id
        or task.version != mutation.expected_version + 1
        or (mutation.prerequisite_uid in task.depends_on) is not add
        or event.event_type is not TaskEventType.TASK_UPDATED
        or event.actor_subject_id != mutation.actor_subject_id
        or event.occurred_at != task.updated_at
        or dict(event.payload) != _event_payload(mutation, task=task, add=add)
    ):
        raise StorageUnavailableError
