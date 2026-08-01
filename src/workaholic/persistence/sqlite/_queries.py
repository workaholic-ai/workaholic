"""Authorized, deterministic, and non-mutating cumulative SQLite queries."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from workaholic.application import (
    ApplicationError,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    InvalidInputError,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    NotInitializedError,
    PermissionDeniedError,
    ProjectNotFoundError,
    ReadTaskEvents,
    StatusResult,
    TaskEventPage,
    TaskNotFoundError,
    TaskPage,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskId,
    validate_project_key,
)
from workaholic.persistence.sqlite._records import (
    canonical_json,
    parse_timestamp,
    project_from_row,
    require_boolean,
    require_text,
)
from workaholic.persistence.sqlite._task_records import task_from_row
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from workaholic.domain import Project

_CURSOR_PREFIX: Final = "v2."
_CURSOR_KEYS: Final = frozenset(
    (
        "after",
        "instance_id",
        "profile",
        "project_id",
        "selection",
        "subject_id",
        "v",
    )
)
_CURSOR_VERSION: Final = 2
_MAX_SQLITE_INTEGER: Final = 9_223_372_036_854_775_807
_SUBJECT_FIELD_COUNT: Final = 5
_PROJECT_ORDERED_TASK_FIELD_COUNT: Final = 19
_ALL_PROJECT_POSITION_FIELD_COUNT: Final = 2
_Selection = Literal["project", "all_projects"]


@dataclass(frozen=True, slots=True)
class _CursorBinding:
    """Authoritative selection identities bound into an opaque cursor."""

    profile: str
    instance_id: InstanceId
    subject_id: SubjectId
    selection: _Selection
    project_id: ProjectId | None


@dataclass(frozen=True, slots=True)
class _CursorPosition:
    """Last stable ordering position represented by an opaque cursor."""

    project_key: str | None
    task_number: int


def get_local_status(
    database_path: Path,
    command: GetLocalStatus,
) -> StatusResult:
    """Read the exact selected local identity and authorization graph.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated status query.

    Returns:
        Current authorized local status.

    Raises:
        NotInitializedError: If the referenced local graph does not exist.
        PermissionDeniedError: If the Subject is not its active Owner.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, GetLocalStatus):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    i.id, i.created_at,
                    p.id, p.instance_id, p.key, p.name, p.created_at,
                    s.id, s.kind, s.display_name, s.enabled,
                    s.is_instance_admin,
                    g.subject_id, g.project_id, g.role
                FROM instances AS i
                JOIN projects AS p ON p.instance_id = i.id
                LEFT JOIN subjects AS s ON s.id = ?
                LEFT JOIN project_grants AS g
                  ON g.subject_id = s.id AND g.project_id = p.id
                WHERE i.id = ? AND p.id = ?
                """,
                (
                    str(candidate.subject_id),
                    str(candidate.instance_id),
                    str(candidate.project_id),
                ),
            ).fetchone()
            if row is None:
                raise NotInitializedError
            _require_owner_values(
                kind=row[8],
                enabled=row[10],
                is_instance_admin=row[11],
                role=row[14],
            )
            instance = Instance(
                id=InstanceId(require_text(row[0])),
                created_at=parse_timestamp(row[1]),
            )
            project = project_from_row(row[2:7])
            subject = _subject_from_values(row[7:12])
            grant = ProjectGrant(
                subject_id=SubjectId(require_text(row[12])),
                project_id=ProjectId(require_text(row[13])),
                role=ProjectRole(require_text(row[14])),
            )
            return StatusResult(
                profile=candidate.profile,
                instance=instance,
                project=project,
                subject=subject,
                grant=grant,
            )
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def list_projects(
    database_path: Path,
    command: ListProjects,
) -> tuple[Project, ...]:
    """Read all Projects authorized for one active local Subject.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Project-list query.

    Returns:
        Projects ordered by immutable key ascending.

    Raises:
        NotInitializedError: If the selected Instance does not exist.
        PermissionDeniedError: If the selected Subject is unavailable or disabled.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, ListProjects):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            _require_instance(connection, candidate.instance_id)
            _require_active_subject(connection, candidate.subject_id)
            rows = connection.execute(
                """
                SELECT p.id, p.instance_id, p.key, p.name, p.created_at
                FROM projects AS p
                JOIN project_grants AS g
                  ON g.project_id = p.id AND g.subject_id = ?
                WHERE p.instance_id = ? AND g.role = ?
                ORDER BY p.key ASC
                """,
                (
                    str(candidate.subject_id),
                    str(candidate.instance_id),
                    ProjectRole.OWNER.value,
                ),
            ).fetchall()
            return tuple(project_from_row(row) for row in rows)
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def get_project_by_key(
    database_path: Path,
    command: GetProjectByKey,
) -> Project:
    """Read one authorized Project by immutable key.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Instance-, Subject-, and key-bound lookup.

    Returns:
        Matching authorized Project.

    Raises:
        NotInitializedError: If the selected Instance does not exist.
        PermissionDeniedError: If the selected Subject is unavailable or disabled.
        ProjectNotFoundError: If no authorized Project has the selected key.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, GetProjectByKey):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            _require_instance(connection, candidate.instance_id)
            _require_active_subject(connection, candidate.subject_id)
            rows = connection.execute(
                """
                SELECT p.id, p.instance_id, p.key, p.name, p.created_at
                FROM projects AS p
                JOIN project_grants AS g
                  ON g.project_id = p.id AND g.subject_id = ?
                WHERE p.instance_id = ? AND p.key = ? AND g.role = ?
                LIMIT 2
                """,
                (
                    str(candidate.subject_id),
                    str(candidate.instance_id),
                    candidate.project_key,
                    ProjectRole.OWNER.value,
                ),
            ).fetchall()
            if not rows:
                raise ProjectNotFoundError
            if len(rows) != 1:
                raise StorageUnavailableError
            return project_from_row(rows[0])
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def list_tasks(database_path: Path, command: ListTasks) -> TaskPage:
    """Read one stable Project-bound page of Tasks.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Task-list query.

    Returns:
        Tasks ordered by number and an opaque continuation cursor.

    Raises:
        InvalidInputError: If a cursor is malformed or cross-Project.
        NotInitializedError: If the selected Project does not exist.
        PermissionDeniedError: If the selected Subject is not its active Owner.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, ListTasks):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            project = _require_authorized_project(
                connection,
                project_id=candidate.project_id,
                subject_id=candidate.subject_id,
            )
            binding = _CursorBinding(
                profile=candidate.profile,
                instance_id=project.instance_id,
                subject_id=candidate.subject_id,
                selection="project",
                project_id=project.id,
            )
            position = _decode_cursor(
                candidate.cursor,
                binding=binding,
            )
            rows = connection.execute(
                """
                SELECT
                    uid, project_id, number, key, title, objective, state,
                    priority, available_at, approval, acceptance_json,
                    context_json, blocking_reason, current_result_id, version,
                    created_by, created_at, updated_at
                FROM tasks
                WHERE project_id = ? AND number > ?
                ORDER BY number ASC
                LIMIT ?
                """,
                (
                    str(candidate.project_id),
                    position.task_number,
                    candidate.limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > candidate.limit
            selected_rows = rows[: candidate.limit]
            dependency_ids = _load_task_dependencies(
                connection,
                tuple(TaskId(require_text(row[0])) for row in selected_rows),
            )
            tasks = tuple(
                _require_task_project_key(
                    task_from_row(
                        row,
                        depends_on=dependency_ids[TaskId(require_text(row[0]))],
                    ),
                    project_key=project.key,
                )
                for row in selected_rows
            )
            next_cursor = (
                _encode_cursor(
                    binding,
                    _CursorPosition(
                        project_key=None,
                        task_number=tasks[-1].number,
                    ),
                )
                if has_more
                else None
            )
            return TaskPage(tasks=tasks, next_cursor=next_cursor)
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def list_tasks_for_instance(
    database_path: Path,
    command: ListInstanceTasks,
) -> TaskPage:
    """Read one stable page across authorized Projects in an Instance.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated profile-, Instance-, and Subject-bound query.

    Returns:
        Tasks ordered by Project key and Project-local number.

    Raises:
        InvalidInputError: If a cursor is malformed or cross-selection.
        NotInitializedError: If the selected Instance does not exist.
        PermissionDeniedError: If the selected Subject is unavailable or disabled.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, ListInstanceTasks):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            _require_instance(connection, candidate.instance_id)
            _require_active_subject(connection, candidate.subject_id)
            binding = _CursorBinding(
                profile=candidate.profile,
                instance_id=candidate.instance_id,
                subject_id=candidate.subject_id,
                selection="all_projects",
                project_id=None,
            )
            position = _decode_cursor(candidate.cursor, binding=binding)
            after_key = "" if position.project_key is None else position.project_key
            rows = connection.execute(
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
                JOIN project_grants AS g
                  ON g.project_id = p.id AND g.subject_id = ?
                WHERE
                    p.instance_id = ?
                    AND g.role = ?
                    AND (
                        p.key > ?
                        OR (p.key = ? AND t.number > ?)
                    )
                ORDER BY p.key ASC, t.number ASC
                LIMIT ?
                """,
                (
                    str(candidate.subject_id),
                    str(candidate.instance_id),
                    ProjectRole.OWNER.value,
                    after_key,
                    after_key,
                    position.task_number,
                    candidate.limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > candidate.limit
            selected_rows = rows[: candidate.limit]
            dependency_ids = _load_task_dependencies(
                connection,
                tuple(TaskId(require_text(row[1])) for row in selected_rows),
            )
            tasks = tuple(
                _task_from_project_ordered_row(
                    row,
                    depends_on=dependency_ids[TaskId(require_text(row[1]))],
                )
                for row in selected_rows
            )
            next_cursor = None
            if has_more:
                last_row = selected_rows[-1]
                next_cursor = _encode_cursor(
                    binding,
                    _CursorPosition(
                        project_key=require_text(last_row[0]),
                        task_number=tasks[-1].number,
                    ),
                )
            return TaskPage(tasks=tasks, next_cursor=next_cursor)
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def get_task(database_path: Path, command: GetTask) -> Task:
    """Read one Project-scoped Task by exact UID or stable Human key.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Task lookup query.

    Returns:
        Matching immutable Task.

    Raises:
        TaskNotFoundError: If the selector does not resolve in this Project.
        NotInitializedError: If the selected Project does not exist.
        PermissionDeniedError: If the selected Subject is not its active Owner.
        StorageUnavailableError: If persisted values violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, GetTask):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            project = _require_authorized_project(
                connection,
                project_id=candidate.project_id,
                subject_id=candidate.subject_id,
            )
            selector_column = "uid" if isinstance(candidate.task, TaskId) else "key"
            if selector_column == "uid":
                row = connection.execute(
                    """
                    SELECT
                        uid, project_id, number, key, title, objective, state,
                        priority, available_at, approval, acceptance_json,
                        context_json, blocking_reason, current_result_id, version,
                        created_by, created_at, updated_at
                    FROM tasks
                    WHERE project_id = ? AND uid = ?
                    """,
                    (str(candidate.project_id), str(candidate.task)),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT
                        uid, project_id, number, key, title, objective, state,
                        priority, available_at, approval, acceptance_json,
                        context_json, blocking_reason, current_result_id, version,
                        created_by, created_at, updated_at
                    FROM tasks
                    WHERE project_id = ? AND key = ?
                    """,
                    (str(candidate.project_id), candidate.task),
                ).fetchone()
            if row is None:
                raise TaskNotFoundError
            task_uid = TaskId(require_text(row[0]))
            dependency_ids = _load_task_dependencies(connection, (task_uid,))
            return _require_task_project_key(
                task_from_row(row, depends_on=dependency_ids[task_uid]),
                project_key=project.key,
            )
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def read_task_events_after(
    database_path: Path,
    command: ReadTaskEvents,
) -> TaskEventPage:
    """Read one authorized TaskEvent snapshot through the focused adapter.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated TaskEvent cursor query.

    Returns:
        Polling-safe ascending TaskEvent page.

    """
    from workaholic.persistence.sqlite._event_queries import (  # noqa: PLC0415
        read_task_events_after as read_focused_events,
    )

    return read_focused_events(database_path, command)


def _require_instance(
    connection: sqlite3.Connection,
    instance_id: InstanceId,
) -> None:
    """Require one exact persisted Instance.

    Args:
        connection: Active validated read snapshot.
        instance_id: Selected Instance identity.

    Raises:
        NotInitializedError: If the Instance does not exist.

    """
    row = connection.execute(
        "SELECT 1 FROM instances WHERE id = ?",
        (str(instance_id),),
    ).fetchone()
    if row != (1,):
        raise NotInitializedError


def _require_active_subject(
    connection: sqlite3.Connection,
    subject_id: SubjectId,
) -> None:
    """Require one enabled local Human Instance administrator.

    Args:
        connection: Active validated read snapshot.
        subject_id: Selected Subject identity.

    Raises:
        PermissionDeniedError: If the Subject is missing, disabled, or invalid.

    """
    row = connection.execute(
        """
        SELECT kind, enabled, is_instance_admin
        FROM subjects
        WHERE id = ?
        """,
        (str(subject_id),),
    ).fetchone()
    if row is None:
        raise PermissionDeniedError
    _require_owner_values(
        kind=row[0],
        enabled=row[1],
        is_instance_admin=row[2],
        role=ProjectRole.OWNER.value,
    )


def _require_authorized_project(
    connection: sqlite3.Connection,
    *,
    project_id: ProjectId,
    subject_id: SubjectId,
) -> Project:
    """Require and return a selected Project with its active local Owner.

    Args:
        connection: Active validated read snapshot.
        project_id: Selected Project identity.
        subject_id: Selected actor identity.

    Returns:
        Validated authorized Project.

    Raises:
        NotInitializedError: If the Project does not exist.
        PermissionDeniedError: If the Subject is not its enabled Owner.

    """
    row = connection.execute(
        """
        SELECT
            p.id, p.instance_id, p.key, p.name, p.created_at,
            s.kind, s.enabled, s.is_instance_admin, g.role
        FROM projects AS p
        LEFT JOIN subjects AS s ON s.id = ?
        LEFT JOIN project_grants AS g
          ON g.project_id = p.id AND g.subject_id = s.id
        WHERE p.id = ?
        """,
        (str(subject_id), str(project_id)),
    ).fetchone()
    if row is None:
        raise NotInitializedError
    _require_owner_values(
        kind=row[5],
        enabled=row[6],
        is_instance_admin=row[7],
        role=row[8],
    )
    return project_from_row(row[0:5])


def _require_owner_values(
    *,
    kind: object,
    enabled: object,
    is_instance_admin: object,
    role: object,
) -> None:
    """Validate the exact Phase 1 local Owner authorization values.

    Args:
        kind: Persisted Subject kind.
        enabled: Persisted enabled flag.
        is_instance_admin: Persisted local administrator flag.
        role: Persisted Project role.

    Raises:
        PermissionDeniedError: If any authorization requirement is absent.

    """
    if (
        kind != SubjectKind.HUMAN.value
        or enabled != 1
        or is_instance_admin != 1
        or role != ProjectRole.OWNER.value
    ):
        raise PermissionDeniedError


def _subject_from_values(value: tuple[object, ...]) -> Subject:
    """Deserialize one Subject row in the canonical selected-field order.

    Args:
        value: SQLite Subject values.

    Returns:
        Validated Subject.

    Raises:
        StorageUnavailableError: If the row has an unexpected shape.

    """
    if len(value) != _SUBJECT_FIELD_COUNT:
        raise StorageUnavailableError
    return Subject(
        id=SubjectId(require_text(value[0])),
        kind=SubjectKind(require_text(value[1])),
        display_name=require_text(value[2]),
        enabled=require_boolean(value[3]),
        is_instance_admin=require_boolean(value[4]),
    )


def _task_from_project_ordered_row(
    value: tuple[object, ...],
    *,
    depends_on: Sequence[TaskId] = (),
) -> Task:
    """Deserialize and cross-check one all-Projects ordered Task row.

    Args:
        value: Project key followed by canonical Task fields.
        depends_on: Separately loaded prerequisite identities in stable key order.

    Returns:
        Validated Task whose key agrees with its Project ordering key.

    Raises:
        StorageUnavailableError: If row shape or cross-table values disagree.

    """
    if len(value) != _PROJECT_ORDERED_TASK_FIELD_COUNT:
        raise StorageUnavailableError
    project_key = require_text(value[0])
    task = task_from_row(
        value[1:_PROJECT_ORDERED_TASK_FIELD_COUNT],
        depends_on=depends_on,
    )
    return _require_task_project_key(task, project_key=project_key)


def _load_task_dependencies(
    connection: sqlite3.Connection,
    task_ids: Sequence[TaskId],
) -> dict[TaskId, tuple[TaskId, ...]]:
    """Load complete dependency identities for a bounded Task batch.

    One batch query avoids page-size-dependent round trips. Ordering by the
    prerequisite's immutable Human key gives all Task surfaces the same stable
    dependency order required by later detail and readiness projections.

    Args:
        connection: Active validated read connection.
        task_ids: Task identities selected by the owning query.

    Returns:
        Every requested Task identity mapped to its ordered prerequisites.

    Raises:
        StorageUnavailableError: If storage returns an unexpected Task identity.

    """
    ordered_ids = tuple(dict.fromkeys(task_ids))
    dependencies: dict[TaskId, list[TaskId]] = {task_id: [] for task_id in ordered_ids}
    if not ordered_ids:
        return {}
    placeholders = ", ".join("?" for _task_id in ordered_ids)
    rows = connection.execute(
        f"""
        SELECT d.task_uid, d.prerequisite_uid, prerequisite.key
        FROM task_dependencies AS d
        LEFT JOIN tasks AS prerequisite
          ON prerequisite.uid = d.prerequisite_uid
         AND prerequisite.project_id = d.project_id
        WHERE d.task_uid IN ({placeholders})
        ORDER BY d.task_uid ASC, prerequisite.key ASC
        """,  # noqa: S608 - only generated parameter placeholders are interpolated.
        tuple(str(task_id) for task_id in ordered_ids),
    ).fetchall()
    for row in rows:
        task_id = TaskId(require_text(row[0]))
        if task_id not in dependencies:
            raise StorageUnavailableError
        require_text(row[2])
        dependencies[task_id].append(TaskId(require_text(row[1])))
    return {task_id: tuple(values) for task_id, values in dependencies.items()}


def _require_task_project_key(task: Task, *, project_key: str) -> Task:
    """Require a persisted Task key to match its Project namespace.

    Args:
        task: Validated persisted Task.
        project_key: Immutable key loaded from the owning Project row.

    Returns:
        The unchanged validated Task.

    Raises:
        StorageUnavailableError: If cross-table key fields disagree.

    """
    if task.key != f"{project_key}-{task.number}":
        raise StorageUnavailableError
    return task


def _encode_cursor(
    binding: _CursorBinding,
    position: _CursorPosition,
) -> str:
    """Encode one canonical selection-bound pagination cursor.

    Args:
        binding: Authoritative profile and selection identities.
        position: Last ordering position returned to the caller.

    Returns:
        Opaque URL-safe, unpadded, versioned cursor.

    """
    after: object
    if binding.selection == "project":
        after = position.task_number
    else:
        after = [position.project_key, position.task_number]
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
            "v": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _decode_cursor(
    cursor: str | None,
    *,
    binding: _CursorBinding,
) -> _CursorPosition:
    """Decode and validate one canonical selection-bound cursor.

    Args:
        cursor: Optional opaque cursor supplied by a caller.
        binding: Authoritative identities selected by the current query.

    Returns:
        Last returned ordering position, or the initial position.

    Raises:
        InvalidInputError: If the cursor is malformed, noncanonical, or cross-Project.

    """
    if cursor is None:
        return _CursorPosition(project_key=None, task_number=0)
    try:
        position = _parse_cursor(cursor, binding=binding)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InvalidInputError from error
    return position


def _parse_cursor(
    cursor: str,
    *,
    binding: _CursorBinding,
) -> _CursorPosition:
    """Parse one non-null cursor before its errors are mapped publicly.

    Args:
        cursor: Opaque caller cursor.
        binding: Authoritative identities selected by the current query.

    Returns:
        Last returned ordering position.

    Raises:
        ValueError: If the cursor is malformed, noncanonical, or cross-selection.

    """
    if not cursor.startswith(_CURSOR_PREFIX):
        raise ValueError
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    if not encoded or "=" in encoded:
        raise ValueError
    padding = "=" * (-len(encoded) % 4)
    payload_bytes = base64.b64decode(
        f"{encoded}{padding}",
        altchars=b"-_",
        validate=True,
    )
    decoded: object = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(decoded, dict) or set(decoded) != _CURSOR_KEYS:
        raise ValueError
    payload = cast("Mapping[str, object]", decoded)
    version = payload["v"]
    after = payload["after"]
    profile = payload["profile"]
    instance_value = payload["instance_id"]
    subject_value = payload["subject_id"]
    selection = payload["selection"]
    project_value = payload["project_id"]
    if (
        type(version) is not int
        or version != _CURSOR_VERSION
        or not isinstance(profile, str)
        or profile != binding.profile
        or not isinstance(instance_value, str)
        or instance_value != str(binding.instance_id)
        or not isinstance(subject_value, str)
        or subject_value != str(binding.subject_id)
        or not isinstance(selection, str)
        or selection != binding.selection
    ):
        raise ValueError
    position = _parse_cursor_position(
        after=after,
        project_value=project_value,
        binding=binding,
    )
    if _encode_cursor(binding, position) != cursor:
        raise ValueError
    return position


def _parse_cursor_position(
    *,
    after: object,
    project_value: object,
    binding: _CursorBinding,
) -> _CursorPosition:
    """Validate selection-specific Project identity and ordering position.

    Args:
        after: Decoded selection-specific ordering value.
        project_value: Decoded selected Project identity or null.
        binding: Authoritative current-query identities.

    Returns:
        Validated ordering position.

    Raises:
        ValueError: If Project scope or position does not match the selection.

    """
    if binding.selection == "project":
        if (
            binding.project_id is None
            or not isinstance(project_value, str)
            or project_value != str(binding.project_id)
        ):
            raise ValueError
        task_number = _require_cursor_task_number(after)
        return _CursorPosition(project_key=None, task_number=task_number)
    if binding.project_id is not None or project_value is not None:
        raise ValueError
    if not isinstance(after, list) or len(after) != _ALL_PROJECT_POSITION_FIELD_COUNT:
        raise ValueError
    project_key = after[0]
    if not isinstance(project_key, str):
        raise TypeError
    validate_project_key(project_key)
    task_number = _require_cursor_task_number(after[1])
    return _CursorPosition(
        project_key=project_key,
        task_number=task_number,
    )


def _require_cursor_task_number(value: object) -> int:
    """Require one positive SQLite-compatible Task number.

    Args:
        value: Candidate decoded ordering number.

    Returns:
        Validated positive integer.

    Raises:
        ValueError: If the number is boolean, nonpositive, or too large.

    """
    if type(value) is not int or value < 1 or value > _MAX_SQLITE_INTEGER:
        raise ValueError
    return value
