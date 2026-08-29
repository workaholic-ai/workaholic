"""Authorized, bounded, and non-mutating SQLite TaskEvent history queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application import (
    ApplicationError,
    InvalidInputError,
    ReadTaskEvents,
    TaskEventPage,
    TaskEventResult,
    TaskNotFoundError,
)
from workaholic.domain import TaskId, build_task_key
from workaholic.persistence.sqlite._event_records import (
    TASK_EVENT_FIELDS,
    TaskEventRecord,
    task_event_record_from_row,
)
from workaholic.persistence.sqlite._queries import (
    _require_authorized_project,
    _require_query_actor,
)
from workaholic.persistence.sqlite._records import require_integer, require_text
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from workaholic.domain import Project, ProjectId

_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


def read_task_events_after(
    database_path: Path,
    command: ReadTaskEvents,
    *,
    now: datetime | None = None,
) -> TaskEventPage:
    """Read one stable authorized TaskEvent page after an Instance cursor.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Validated Task, Project, actor, cursor, and limit query.
        now: Authoritative Token validation time for authenticated reads.

    Returns:
        Strictly ascending events and the greatest observed cursor.

    Raises:
        InvalidInputError: If the runtime command bypasses validation.
        TaskNotFoundError: If the selected Task is absent from the Project.
        ApplicationError: If Project authorization or initialization fails.
        StorageUnavailableError: If persisted records violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, ReadTaskEvents):
        raise InvalidInputError
    try:
        with open_read_connection(database_path) as connection:
            _require_query_actor(
                connection,
                actor=candidate.actor,
                instance_id=(
                    None if candidate.actor is None else candidate.actor.instance_id
                ),
                subject_id=candidate.subject_id,
                occurred_at=now,
            )
            project = _require_authorized_project(
                connection,
                project_id=candidate.project_id,
                subject_id=candidate.subject_id,
                actor=candidate.actor,
                occurred_at=now,
            )
            task_uid = _resolve_task_uid(
                connection,
                command=candidate,
                project=project,
            )
            rows = (
                ()
                if candidate.after > _MAX_SQLITE_INTEGER
                else connection.execute(
                    f"""
                    SELECT {", ".join(f"e.{field}" for field in TASK_EVENT_FIELDS)}
                    FROM task_events AS e
                    JOIN tasks AS t
                      ON t.uid = e.task_uid AND t.project_id = e.project_id
                    WHERE
                        e.task_uid = ?
                        AND e.project_id = ?
                        AND e.cursor > ?
                    ORDER BY e.cursor ASC
                    LIMIT ?
                    """,  # noqa: S608 - field names are a closed module constant.
                    (
                        str(task_uid),
                        str(candidate.project_id),
                        candidate.after,
                        candidate.limit,
                    ),
                ).fetchall()
            )
            records = tuple(task_event_record_from_row(row) for row in rows)
            events = tuple(
                _event_result(
                    record,
                    task_uid=task_uid,
                    project_id=candidate.project_id,
                )
                for record in records
            )
            next_cursor = events[-1].cursor if events else candidate.after
            return TaskEventPage(events=events, next_cursor=next_cursor)
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (IndexError, OverflowError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _resolve_task_uid(
    connection: sqlite3.Connection,
    *,
    command: ReadTaskEvents,
    project: Project,
) -> TaskId:
    """Resolve one exact Task selector inside an authorized Project snapshot.

    Args:
        connection: Active validated read snapshot.
        command: Validated TaskEvent query.
        project: Authorized selected Project.

    Returns:
        Canonical Task identity.

    Raises:
        TaskNotFoundError: If the selector does not resolve.
        StorageUnavailableError: If Task key fields are inconsistent.

    """
    if isinstance(command.task, TaskId):
        row = connection.execute(
            """
            SELECT uid, number, key
            FROM tasks
            WHERE project_id = ? AND uid = ?
            """,
            (str(command.project_id), str(command.task)),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT uid, number, key
            FROM tasks
            WHERE project_id = ? AND key = ?
            """,
            (str(command.project_id), command.task),
        ).fetchone()
    if row is None:
        raise TaskNotFoundError
    task_uid = TaskId(require_text(row[0]))
    number = require_integer(row[1])
    if require_text(row[2]) != build_task_key(project.key, number):
        raise StorageUnavailableError
    return task_uid


def _event_result(
    record: TaskEventRecord,
    *,
    task_uid: TaskId,
    project_id: ProjectId,
) -> TaskEventResult:
    """Convert one strict persistence record into a flat application result.

    Args:
        record: Hydrated strict event record.
        task_uid: Authoritative selected Task identity.
        project_id: Authoritative selected Project identity.

    Returns:
        Flat immutable attributable event.

    Raises:
        StorageUnavailableError: If the record crosses selected scope.

    """
    event = record.event
    if event.task_uid != task_uid or event.project_id != project_id:
        raise StorageUnavailableError
    return TaskEventResult(
        id=event.id,
        cursor=event.cursor,
        task_uid=event.task_uid,
        project_id=event.project_id,
        actor_subject_id=event.actor_subject_id,
        actor_kind=record.actor_kind,
        attempt_id=record.attempt_id,
        request_id=event.request_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )
