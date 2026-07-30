"""Authorized, deterministic, and non-mutating cumulative SQLite queries."""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ApplicationError,
    GetLocalStatus,
    GetTask,
    InvalidInputError,
    ListProjects,
    ListTasks,
    NotInitializedError,
    PermissionDeniedError,
    StatusResult,
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
    from collections.abc import Mapping
    from pathlib import Path

    from workaholic.domain import Project

_CURSOR_PREFIX: Final = "v1."
_CURSOR_KEYS: Final = frozenset(("after", "project_id", "v"))
_CURSOR_VERSION: Final = 1
_MAX_SQLITE_INTEGER: Final = 9_223_372_036_854_775_807
_SUBJECT_FIELD_COUNT: Final = 5


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
            _require_authorized_project(
                connection,
                project_id=candidate.project_id,
                subject_id=candidate.subject_id,
            )
            after = _decode_cursor(
                candidate.cursor,
                expected_project_id=candidate.project_id,
            )
            rows = connection.execute(
                """
                SELECT
                    uid, project_id, number, key, title, objective, state,
                    priority, version, created_by, created_at, updated_at
                FROM tasks
                WHERE project_id = ? AND number > ?
                ORDER BY number ASC
                LIMIT ?
                """,
                (
                    str(candidate.project_id),
                    after,
                    candidate.limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > candidate.limit
            selected_rows = rows[: candidate.limit]
            tasks = tuple(task_from_row(row) for row in selected_rows)
            next_cursor = (
                _encode_cursor(candidate.project_id, tasks[-1].number)
                if has_more
                else None
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
            _require_authorized_project(
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
                        priority, version, created_by, created_at, updated_at
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
                        priority, version, created_by, created_at, updated_at
                    FROM tasks
                    WHERE project_id = ? AND key = ?
                    """,
                    (str(candidate.project_id), candidate.task),
                ).fetchone()
            if row is None:
                raise TaskNotFoundError
            return task_from_row(row)
    except ApplicationError:
        raise
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


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
) -> None:
    """Require a selected Project and its active local Owner.

    Args:
        connection: Active validated read snapshot.
        project_id: Selected Project identity.
        subject_id: Selected actor identity.

    Raises:
        NotInitializedError: If the Project does not exist.
        PermissionDeniedError: If the Subject is not its enabled Owner.

    """
    row = connection.execute(
        """
        SELECT s.kind, s.enabled, s.is_instance_admin, g.role
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
        kind=row[0],
        enabled=row[1],
        is_instance_admin=row[2],
        role=row[3],
    )


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


def _encode_cursor(project_id: ProjectId, after: int) -> str:
    """Encode one canonical Project-bound pagination cursor.

    Args:
        project_id: Project to which the cursor is bound.
        after: Last Task number returned to the caller.

    Returns:
        Opaque URL-safe, unpadded, versioned cursor.

    """
    payload = canonical_json(
        {
            "after": after,
            "project_id": str(project_id),
            "v": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _decode_cursor(
    cursor: str | None,
    *,
    expected_project_id: ProjectId,
) -> int:
    """Decode and validate one canonical Project-bound cursor.

    Args:
        cursor: Optional opaque cursor supplied by a caller.
        expected_project_id: Project selected by the current query.

    Returns:
        Last returned Task number, or zero for the first page.

    Raises:
        InvalidInputError: If the cursor is malformed, noncanonical, or cross-Project.

    """
    if cursor is None:
        return 0
    try:
        after = _parse_cursor(cursor, expected_project_id=expected_project_id)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InvalidInputError from error
    return after


def _parse_cursor(cursor: str, *, expected_project_id: ProjectId) -> int:
    """Parse one non-null cursor before its errors are mapped publicly.

    Args:
        cursor: Opaque caller cursor.
        expected_project_id: Project selected by the current query.

    Returns:
        Last returned Task number.

    Raises:
        ValueError: If the cursor is malformed, noncanonical, or cross-Project.

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
    project_value = payload["project_id"]
    if (
        type(version) is not int
        or version != _CURSOR_VERSION
        or type(after) is not int
        or after < 1
        or after > _MAX_SQLITE_INTEGER
        or not isinstance(project_value, str)
    ):
        raise ValueError
    project_id = ProjectId(project_value)
    if project_id != expected_project_id or _encode_cursor(project_id, after) != cursor:
        raise ValueError
    return after
