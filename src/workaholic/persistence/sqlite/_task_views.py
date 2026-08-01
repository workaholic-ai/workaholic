"""Authorized, deterministic, non-mutating Phase 3 SQLite Task views."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ApplicationError,
    GetTaskDetails,
    InvalidInputError,
    ListTasksByView,
    TaskDetails,
    TaskListView,
    TaskNotFoundError,
    TaskPage,
)
from workaholic.domain import (
    TASK_PRIORITY_MAX,
    DomainValidationError,
    ProjectRole,
    Task,
    TaskId,
    TaskReadiness,
    derive_task_readiness,
    validate_project_key,
    validate_utc_timestamp,
)
from workaholic.persistence.sqlite._queries import (
    _CursorBinding,
    _load_task_dependencies,
    _require_active_subject,
    _require_authorized_project,
    _require_instance,
    _require_task_project_key,
    _task_from_project_ordered_row,
)
from workaholic.persistence.sqlite._records import (
    canonical_json,
    parse_timestamp,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._result_records import (
    TASK_RESULT_FIELDS,
    task_result_from_row,
)
from workaholic.persistence.sqlite._task_records import task_from_row
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from workaholic.domain import Project, TaskResult

_MAX_SQLITE_INTEGER: Final = 9_223_372_036_854_775_807
_PROJECT_VIEW_POSITION_FIELD_COUNT: Final = 2
_VIEW_CURSOR_PREFIX: Final = "v3."
_VIEW_CURSOR_VERSION: Final = 3
_VIEW_CURSOR_KEYS: Final = frozenset(
    (
        "after",
        "instance_id",
        "profile",
        "project_id",
        "selection",
        "subject_id",
        "v",
        "view",
    )
)


@dataclass(frozen=True, slots=True)
class _ViewCursorPosition:
    """Exact final ordering position represented by a Phase 3 cursor."""

    priority: int | None
    available_at: str | None
    project_key: str | None
    task_number: int


def get_task_details(
    database_path: Path,
    command: GetTaskDetails,
    *,
    now: datetime,
) -> TaskDetails:
    """Read complete Task details and derive readiness at one explicit time.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Project-scoped Task detail query.
        now: Authoritative UTC readiness time supplied by the repository clock.

    Returns:
        Complete Task, ordered prerequisites, current Result, and readiness.

    Raises:
        TaskNotFoundError: If the scoped selector does not resolve.
        PermissionDeniedError: If the Subject is not an enabled Owner.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = command
    if not isinstance(candidate, GetTaskDetails):
        raise InvalidInputError
    try:
        current_time = validate_utc_timestamp(now, label="Readiness time")
        with open_read_connection(database_path) as connection:
            project = _require_authorized_project(
                connection,
                project_id=candidate.project_id,
                subject_id=candidate.subject_id,
            )
            task = _load_scoped_task(
                connection,
                project=project,
                selector=candidate.task,
            )
            prerequisites = _load_prerequisite_tasks(connection, (task,))[task.uid]
            readiness = derive_task_readiness(
                task=task,
                prerequisites=prerequisites,
                now=current_time,
            )
            current_result = _load_current_result(connection, task=task)
            return TaskDetails(
                task=task,
                readiness=readiness,
                prerequisites=prerequisites,
                current_result=current_result,
            )
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (DomainValidationError, IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def list_tasks_by_view(
    database_path: Path,
    command: ListTasksByView,
    *,
    now: datetime,
) -> TaskPage:
    """Read one authorized deterministic Phase 3 Task-view page.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated scope, view, and cursor query.
        now: Authoritative UTC readiness time supplied by the repository clock.

    Returns:
        Tasks with aligned readiness projections and an opaque v3 cursor.

    Raises:
        InvalidInputError: If a cursor is malformed or crosses scope/view.
        PermissionDeniedError: If the Subject lacks selected Project access.
        StorageUnavailableError: If persisted state violates its contract.

    """
    candidate: object = command
    if not isinstance(candidate, ListTasksByView):
        raise InvalidInputError
    try:
        current_time = validate_utc_timestamp(now, label="Readiness time")
        with open_read_connection(database_path) as connection:
            binding = _view_cursor_binding(connection, candidate)
            position = _decode_view_cursor(
                candidate.cursor,
                binding=binding,
                view=candidate.view,
            )
            rows = _select_view_rows(
                connection,
                command=candidate,
                now=current_time,
                position=position,
            )
            has_more = len(rows) > candidate.limit
            selected_rows = rows[: candidate.limit]
            tasks = _tasks_from_view_rows(connection, selected_rows)
            prerequisites = _load_prerequisite_tasks(connection, tasks)
            readiness = tuple(
                derive_task_readiness(
                    task=task,
                    prerequisites=prerequisites[task.uid],
                    now=current_time,
                )
                for task in tasks
            )
            _require_readiness_matches_view(
                tasks,
                readiness,
                view=candidate.view,
            )
            next_cursor = (
                _encode_view_cursor(
                    binding,
                    view=candidate.view,
                    position=_view_position(tasks[-1], binding=binding),
                )
                if has_more
                else None
            )
            return TaskPage(
                tasks=tasks,
                readiness=readiness,
                next_cursor=next_cursor,
                view=candidate.view,
            )
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (DomainValidationError, IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _load_scoped_task(
    connection: sqlite3.Connection,
    *,
    project: Project,
    selector: TaskId | str,
) -> Task:
    """Load one complete Task within an already authorized Project.

    Args:
        connection: Active read snapshot.
        project: Authorized Project owning the Task.
        selector: Canonical Task identity or stable Human key.

    Returns:
        Matching complete Task.

    Raises:
        TaskNotFoundError: If the selector does not resolve in the Project.

    """
    selector_column = "uid" if isinstance(selector, TaskId) else "key"
    row = connection.execute(
        f"""
        SELECT
            uid, project_id, number, key, title, objective, state,
            priority, available_at, approval, acceptance_json,
            context_json, blocking_reason, current_result_id, version,
            created_by, created_at, updated_at
        FROM tasks
        WHERE project_id = ? AND {selector_column} = ?
        """,  # noqa: S608 - selector column is chosen from a closed constant set.
        (str(project.id), str(selector)),
    ).fetchone()
    if row is None:
        raise TaskNotFoundError
    task_uid = TaskId(require_text(row[0]))
    dependencies = _load_task_dependencies(connection, (task_uid,))[task_uid]
    return _require_task_project_key(
        task_from_row(row, depends_on=dependencies),
        project_key=project.key,
    )


def _load_prerequisite_tasks(
    connection: sqlite3.Connection,
    tasks: Sequence[Task],
) -> dict[TaskId, tuple[Task, ...]]:
    """Batch-load complete prerequisite Tasks for readiness derivation.

    Args:
        connection: Active read snapshot.
        tasks: Selected dependant Tasks.

    Returns:
        Every selected Task mapped to prerequisites ordered by Human key.

    Raises:
        StorageUnavailableError: If an edge references inconsistent storage.

    """
    by_task: dict[TaskId, tuple[Task, ...]] = {task.uid: () for task in tasks}
    prerequisite_ids = tuple(
        dict.fromkeys(
            prerequisite_uid for task in tasks for prerequisite_uid in task.depends_on
        )
    )
    if not prerequisite_ids:
        return by_task
    placeholders = ", ".join("?" for _task_id in prerequisite_ids)
    rows = connection.execute(
        f"""
        SELECT
            p.key,
            t.uid, t.project_id, t.number, t.key, t.title, t.objective,
            t.state, t.priority, t.available_at, t.approval,
            t.acceptance_json, t.context_json, t.blocking_reason,
            t.current_result_id, t.version, t.created_by, t.created_at,
            t.updated_at
        FROM tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        WHERE t.uid IN ({placeholders})
        ORDER BY t.uid
        """,  # noqa: S608 - only generated parameter placeholders are interpolated.
        tuple(str(task_id) for task_id in prerequisite_ids),
    ).fetchall()
    if len(rows) != len(prerequisite_ids):
        raise StorageUnavailableError
    nested_dependencies = _load_task_dependencies(connection, prerequisite_ids)
    loaded = {
        TaskId(require_text(row[1])): _task_from_project_ordered_row(
            row,
            depends_on=nested_dependencies[TaskId(require_text(row[1]))],
        )
        for row in rows
    }
    if set(loaded) != set(prerequisite_ids):
        raise StorageUnavailableError
    for task in tasks:
        try:
            values = tuple(loaded[task_id] for task_id in task.depends_on)
        except KeyError as error:
            raise StorageUnavailableError from error
        if any(value.project_id != task.project_id for value in values):
            raise StorageUnavailableError
        by_task[task.uid] = values
    return by_task


def _load_current_result(
    connection: sqlite3.Connection,
    *,
    task: Task,
) -> TaskResult | None:
    """Load the exact Result currently selected by a Task.

    Args:
        connection: Active read snapshot.
        task: Task whose optional Result selection is being read.

    Returns:
        Selected Result or ``None``.

    Raises:
        StorageUnavailableError: If the selection cannot be resolved exactly.

    """
    if task.current_result_id is None:
        return None
    row = connection.execute(
        f"""
        SELECT {", ".join(TASK_RESULT_FIELDS)}
        FROM task_results
        WHERE id = ? AND task_uid = ?
        """,  # noqa: S608 - field names are a closed module constant.
        (str(task.current_result_id), str(task.uid)),
    ).fetchone()
    if row is None:
        raise StorageUnavailableError
    return task_result_from_row(row)


def _view_cursor_binding(
    connection: sqlite3.Connection,
    command: ListTasksByView,
) -> _CursorBinding:
    """Authorize and build the immutable binding for a view query.

    Args:
        connection: Active read snapshot.
        command: Validated view query.

    Returns:
        Exact identity and selection cursor binding.

    """
    if command.project_id is not None:
        project = _require_authorized_project(
            connection,
            project_id=command.project_id,
            subject_id=command.subject_id,
        )
        return _CursorBinding(
            profile=command.profile,
            instance_id=project.instance_id,
            subject_id=command.subject_id,
            selection="project",
            project_id=project.id,
        )
    if command.instance_id is None:
        raise InvalidInputError
    _require_instance(connection, command.instance_id)
    _require_active_subject(connection, command.subject_id)
    return _CursorBinding(
        profile=command.profile,
        instance_id=command.instance_id,
        subject_id=command.subject_id,
        selection="all_projects",
        project_id=None,
    )


def _select_view_rows(
    connection: sqlite3.Connection,
    *,
    command: ListTasksByView,
    now: datetime,
    position: _ViewCursorPosition,
) -> list[tuple[object, ...]]:
    """Select one bounded view page through a deterministic keyset query.

    Args:
        connection: Active read snapshot.
        command: Validated view query.
        now: Authoritative readiness time.
        position: Exclusive ordering position decoded from the cursor.

    Returns:
        At most ``limit + 1`` Project-key-prefixed Task rows.

    """
    scope_sql: str
    parameters: list[object]
    if command.project_id is not None:
        scope_sql = "t.project_id = ? AND g.subject_id = ?"
        parameters = [str(command.project_id), str(command.subject_id)]
    else:
        scope_sql = "p.instance_id = ? AND g.subject_id = ?"
        parameters = [str(command.instance_id), str(command.subject_id)]
    view_sql, view_parameters = _view_filter(command.view, now=now)
    after_sql, after_parameters = _view_after_filter(
        command.view,
        position=position,
        all_projects=command.project_id is None,
    )
    order_sql = _view_order(
        command.view,
        all_projects=command.project_id is None,
    )
    rows = connection.execute(
        f"""
        SELECT
            p.key,
            t.uid, t.project_id, t.number, t.key, t.title, t.objective,
            t.state, t.priority, t.available_at, t.approval,
            t.acceptance_json, t.context_json, t.blocking_reason,
            t.current_result_id, t.version, t.created_by, t.created_at,
            t.updated_at
        FROM tasks AS t
        JOIN projects AS p ON p.id = t.project_id
        JOIN project_grants AS g ON g.project_id = p.id
        WHERE {scope_sql} AND g.role = ? {view_sql} {after_sql}
        ORDER BY {order_sql}
        LIMIT ?
        """,  # noqa: S608 - fragments are selected from closed module functions.
        (
            *parameters,
            ProjectRole.OWNER.value,
            *view_parameters,
            *after_parameters,
            command.limit + 1,
        ),
    ).fetchall()
    return list(rows)


def _view_filter(
    view: TaskListView,
    *,
    now: datetime,
) -> tuple[str, tuple[object, ...]]:
    """Return the closed SQL predicate implementing one Task view.

    Args:
        view: Requested persisted or derived view.
        now: Authoritative readiness time.

    Returns:
        SQL suffix and bound parameters.

    """
    timestamp = serialize_timestamp(now)
    if view is TaskListView.ALL:
        return "", ()
    if view is TaskListView.READY:
        return (
            """
            AND t.state = 'open'
            AND (t.available_at IS NULL OR t.available_at <= ?)
            AND NOT EXISTS (
                SELECT 1
                FROM task_dependencies AS d
                JOIN tasks AS prerequisite ON prerequisite.uid = d.prerequisite_uid
                WHERE d.task_uid = t.uid
                  AND d.project_id = t.project_id
                  AND prerequisite.state != 'done'
            )
            """,
            (timestamp,),
        )
    if view is TaskListView.SCHEDULED:
        return "AND t.state = 'open' AND t.available_at > ?", (timestamp,)
    state = {
        TaskListView.BLOCKED: "blocked",
        TaskListView.REVIEW: "review",
        TaskListView.DONE: "done",
        TaskListView.CANCELLED: "cancelled",
    }[view]
    return "AND t.state = ?", (state,)


def _view_order(view: TaskListView, *, all_projects: bool) -> str:
    """Return the exact closed ORDER BY expression for one view.

    Args:
        view: Requested Task list view.
        all_projects: Whether Project key participates in ordering.

    Returns:
        Safe SQL ordering fragment.

    """
    if view is not TaskListView.READY:
        return "p.key ASC, t.number ASC" if all_projects else "t.number ASC"
    tie_breaker = "p.key ASC, t.number ASC" if all_projects else "t.number ASC"
    return (
        "t.priority DESC, (t.available_at IS NOT NULL) ASC, "
        f"t.available_at ASC, {tie_breaker}"
    )


def _view_after_filter(
    view: TaskListView,
    *,
    position: _ViewCursorPosition,
    all_projects: bool,
) -> tuple[str, tuple[object, ...]]:
    """Build an exclusive keyset predicate from a validated cursor position.

    Args:
        view: Requested Task list view.
        position: Validated previous ordering position.
        all_projects: Whether Project key participates in ordering.

    Returns:
        SQL suffix and bound parameters.

    """
    if position.task_number == 0:
        return "", ()
    if view is not TaskListView.READY:
        if all_projects:
            return (
                "AND (p.key > ? OR (p.key = ? AND t.number > ?))",
                (position.project_key, position.project_key, position.task_number),
            )
        return "AND t.number > ?", (position.task_number,)
    if position.priority is None:
        raise InvalidInputError
    availability_rank = 0 if position.available_at is None else 1
    availability = position.available_at or ""
    if all_projects:
        return (
            """
            AND (
                t.priority < ?
                OR (t.priority = ? AND (t.available_at IS NOT NULL) > ?)
                OR (
                    t.priority = ?
                    AND (t.available_at IS NOT NULL) = ?
                    AND COALESCE(t.available_at, '') > ?
                )
                OR (
                    t.priority = ?
                    AND (t.available_at IS NOT NULL) = ?
                    AND COALESCE(t.available_at, '') = ?
                    AND p.key > ?
                )
                OR (
                    t.priority = ?
                    AND (t.available_at IS NOT NULL) = ?
                    AND COALESCE(t.available_at, '') = ?
                    AND p.key = ?
                    AND t.number > ?
                )
            )
            """,
            (
                position.priority,
                position.priority,
                availability_rank,
                position.priority,
                availability_rank,
                availability,
                position.priority,
                availability_rank,
                availability,
                position.project_key,
                position.priority,
                availability_rank,
                availability,
                position.project_key,
                position.task_number,
            ),
        )
    return (
        """
        AND (
            t.priority < ?
            OR (t.priority = ? AND (t.available_at IS NOT NULL) > ?)
            OR (
                t.priority = ?
                AND (t.available_at IS NOT NULL) = ?
                AND COALESCE(t.available_at, '') > ?
            )
            OR (
                t.priority = ?
                AND (t.available_at IS NOT NULL) = ?
                AND COALESCE(t.available_at, '') = ?
                AND t.number > ?
            )
        )
        """,
        (
            position.priority,
            position.priority,
            availability_rank,
            position.priority,
            availability_rank,
            availability,
            position.priority,
            availability_rank,
            availability,
            position.task_number,
        ),
    )


def _tasks_from_view_rows(
    connection: sqlite3.Connection,
    rows: Sequence[tuple[object, ...]],
) -> tuple[Task, ...]:
    """Hydrate selected Project-key-prefixed view rows.

    Args:
        connection: Active read snapshot.
        rows: Selected ordered Task rows.

    Returns:
        Complete immutable Tasks with ordered dependency identities.

    """
    dependency_ids = _load_task_dependencies(
        connection,
        tuple(TaskId(require_text(row[1])) for row in rows),
    )
    return tuple(
        _task_from_project_ordered_row(
            row,
            depends_on=dependency_ids[TaskId(require_text(row[1]))],
        )
        for row in rows
    )


def _readiness_matches_view(
    task: Task,
    readiness: TaskReadiness,
    *,
    view: TaskListView,
) -> bool:
    """Return whether one selected Task satisfies the requested view.

    Args:
        task: Selected Task.
        readiness: Authoritative derived readiness projection.
        view: Requested list view.

    Returns:
        Whether the projection and stored state agree with the view.

    """
    if view is TaskListView.ALL:
        return True
    if view is TaskListView.READY:
        return readiness.ready
    if view is TaskListView.SCHEDULED:
        return readiness.scheduled
    if view is TaskListView.REVIEW:
        return readiness.awaiting_review
    return task.state.value == view.value


def _require_readiness_matches_view(
    tasks: Sequence[Task],
    readiness: Sequence[TaskReadiness],
    *,
    view: TaskListView,
) -> None:
    """Require every selected Task to satisfy its requested derived view.

    Args:
        tasks: Selected ordered Tasks.
        readiness: Aligned authoritative readiness projections.
        view: Requested list view.

    Raises:
        StorageUnavailableError: If selection and derivation disagree.

    """
    if any(
        not _readiness_matches_view(task, item, view=view)
        for task, item in zip(tasks, readiness, strict=True)
    ):
        raise StorageUnavailableError


def _view_position(
    task: Task,
    *,
    binding: _CursorBinding,
) -> _ViewCursorPosition:
    """Build one serializable exact ordering position from a Task.

    Args:
        task: Last Task emitted by a view page.
        binding: Current view query binding.

    Returns:
        Exact Phase 3 cursor position.

    """
    project_key, separator, _number = task.key.rpartition("-")
    if separator != "-":
        raise StorageUnavailableError
    return _ViewCursorPosition(
        priority=task.priority,
        available_at=(
            None
            if task.available_at is None
            else serialize_timestamp(task.available_at)
        ),
        project_key=project_key if binding.selection == "all_projects" else None,
        task_number=task.number,
    )


def _encode_view_cursor(
    binding: _CursorBinding,
    *,
    view: TaskListView,
    position: _ViewCursorPosition,
) -> str:
    """Encode a canonical identity-, view-, and ordering-bound cursor.

    Args:
        binding: Authoritative selection identities.
        view: Exact Task view.
        position: Last returned ordering position.

    Returns:
        Opaque URL-safe unpadded version-3 cursor.

    """
    if view is TaskListView.READY:
        after: object = [
            position.priority,
            position.available_at,
            *([position.project_key] if binding.selection == "all_projects" else []),
            position.task_number,
        ]
    elif binding.selection == "all_projects":
        after = [position.project_key, position.task_number]
    else:
        after = position.task_number
    payload = canonical_json(
        {
            "after": after,
            "instance_id": str(binding.instance_id),
            "profile": binding.profile,
            "project_id": (
                None if binding.project_id is None else str(binding.project_id)
            ),
            "selection": binding.selection,
            "subject_id": str(binding.subject_id),
            "v": _VIEW_CURSOR_VERSION,
            "view": view.value,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_VIEW_CURSOR_PREFIX}{encoded}"


def _decode_view_cursor(
    cursor: str | None,
    *,
    binding: _CursorBinding,
    view: TaskListView,
) -> _ViewCursorPosition:
    """Decode and validate one exact Phase 3 view cursor.

    Args:
        cursor: Optional opaque caller cursor.
        binding: Authoritative query identities and scope.
        view: Exact requested Task view.

    Returns:
        Exclusive prior ordering position or the initial position.

    Raises:
        InvalidInputError: If shape, binding, view, or position is invalid.

    """
    if cursor is None:
        return _ViewCursorPosition(
            priority=None,
            available_at=None,
            project_key=None,
            task_number=0,
        )
    try:
        return _parse_view_cursor(cursor, binding=binding, view=view)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InvalidInputError from error


def _parse_view_cursor(
    cursor: str,
    *,
    binding: _CursorBinding,
    view: TaskListView,
) -> _ViewCursorPosition:
    """Parse a non-null cursor before mapping validation failures.

    Args:
        cursor: Opaque caller cursor.
        binding: Authoritative query identities and scope.
        view: Exact requested Task view.

    Returns:
        Validated exclusive ordering position.

    Raises:
        ValueError: If the cursor is malformed or cross-boundary.

    """
    if not cursor.startswith(_VIEW_CURSOR_PREFIX):
        raise ValueError
    encoded = cursor.removeprefix(_VIEW_CURSOR_PREFIX)
    if not encoded or "=" in encoded:
        raise ValueError
    padding = "=" * (-len(encoded) % 4)
    payload_bytes = base64.b64decode(
        f"{encoded}{padding}",
        altchars=b"-_",
        validate=True,
    )
    decoded: object = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(decoded, dict) or set(decoded) != _VIEW_CURSOR_KEYS:
        raise ValueError
    payload = cast("Mapping[str, object]", decoded)
    if (
        payload["v"] != _VIEW_CURSOR_VERSION
        or payload["profile"] != binding.profile
        or payload["instance_id"] != str(binding.instance_id)
        or payload["subject_id"] != str(binding.subject_id)
        or payload["selection"] != binding.selection
        or payload["project_id"]
        != (None if binding.project_id is None else str(binding.project_id))
        or payload["view"] != view.value
    ):
        raise ValueError
    position = _parse_view_position(
        payload["after"],
        view=view,
        all_projects=binding.selection == "all_projects",
    )
    if _encode_view_cursor(binding, view=view, position=position) != cursor:
        raise ValueError
    return position


def _parse_view_position(
    value: object,
    *,
    view: TaskListView,
    all_projects: bool,
) -> _ViewCursorPosition:
    """Validate the exact ordering-position shape for one view and scope.

    Args:
        value: Decoded JSON ``after`` value.
        view: Requested Task view.
        all_projects: Whether Project key participates in ordering.

    Returns:
        Validated cursor position.

    Raises:
        ValueError: If any ordering field is malformed.

    """
    if view is not TaskListView.READY:
        if all_projects:
            if (
                not isinstance(value, list)
                or len(value) != _PROJECT_VIEW_POSITION_FIELD_COUNT
            ):
                raise ValueError
            project_key = validate_project_key(value[0])
            task_number = _positive_cursor_integer(value[1])
        else:
            project_key = None
            task_number = _positive_cursor_integer(value)
        return _ViewCursorPosition(
            priority=None,
            available_at=None,
            project_key=project_key,
            task_number=task_number,
        )
    expected_length = 4 if all_projects else 3
    if not isinstance(value, list) or len(value) != expected_length:
        raise ValueError
    priority = value[0]
    if type(priority) is not int or not 0 <= priority <= TASK_PRIORITY_MAX:
        raise ValueError
    available_value = value[1]
    available_at: str | None
    if available_value is None:
        available_at = None
    elif isinstance(available_value, str):
        parsed = parse_timestamp(available_value)
        if serialize_timestamp(parsed) != available_value:
            raise ValueError
        available_at = available_value
    else:
        raise ValueError
    offset = 1 if all_projects else 0
    project_key = validate_project_key(value[2]) if all_projects else None
    task_number = _positive_cursor_integer(value[2 + offset])
    return _ViewCursorPosition(
        priority=priority,
        available_at=available_at,
        project_key=project_key,
        task_number=task_number,
    )


def _positive_cursor_integer(value: object) -> int:
    """Require one positive SQLite-safe cursor integer.

    Args:
        value: Candidate decoded number.

    Returns:
        Validated integer.

    Raises:
        ValueError: If the value is not positive and SQLite-safe.

    """
    if type(value) is not int or not 1 <= value <= _MAX_SQLITE_INTEGER:
        raise ValueError
    return value
