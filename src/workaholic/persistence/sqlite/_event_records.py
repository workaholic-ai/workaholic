"""Canonical Phase 4 TaskEvent serialization and strict row codecs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from workaholic.domain import (
    AttemptId,
    ProjectId,
    RequestId,
    SubjectId,
    SubjectKind,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
)
from workaholic.persistence.sqlite._records import (
    EVENT_PAYLOAD_JSON_MAX_LENGTH,
    canonical_json,
    parse_json_object,
    parse_timestamp,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from workaholic.domain import JsonValue, Task

TASK_EVENT_FIELDS: Final = (
    "cursor",
    "id",
    "task_uid",
    "project_id",
    "actor_subject_id",
    "actor_kind",
    "attempt_id",
    "request_id",
    "event_type",
    "occurred_at",
    "payload_json",
)
TASK_EVENT_MAPPING_FIELDS: Final = (
    "id",
    "cursor",
    "task_uid",
    "project_id",
    "actor_subject_id",
    "actor_kind",
    "attempt_id",
    "request_id",
    "event_type",
    "occurred_at",
    "payload",
)
TASK_EVENT_MAPPING_FIELD_SET: Final = frozenset(TASK_EVENT_MAPPING_FIELDS)


@dataclass(frozen=True, slots=True)
class TaskEventRecord:
    """One hydrated TaskEvent plus its immutable actor and Attempt snapshot."""

    event: TaskEvent
    actor_kind: SubjectKind
    attempt_id: AttemptId | None

    def __post_init__(self) -> None:
        """Validate the Phase 4 event-attribution snapshot contract."""
        candidate_event: object = self.event
        candidate_kind: object = self.actor_kind
        if not isinstance(candidate_event, TaskEvent):
            raise StorageUnavailableError
        if (
            not isinstance(candidate_kind, SubjectKind)
            or candidate_kind.value != SubjectKind.HUMAN.value
            or self.attempt_id != candidate_event.attempt_id
        ):
            raise StorageUnavailableError


def insert_task_event(  # noqa: PLR0913 - exact durable event boundary.
    connection: sqlite3.Connection,
    *,
    event_id: TaskEventId,
    task: Task,
    actor_subject_id: SubjectId,
    request_id: RequestId,
    event_type: TaskEventType,
    occurred_at: datetime,
    payload: Mapping[str, JsonValue],
    attempt_id: AttemptId | None,
) -> TaskEventRecord:
    """Append one attributable Task event in an active write transaction.

    Args:
        connection: Active schema-validated SQLite write transaction.
        event_id: Globally unique candidate event identity.
        task: Complete Task receiving the event.
        actor_subject_id: Authenticated bootstrap Subject identity.
        request_id: Current logical request identity.
        event_type: Closed semantic event type.
        occurred_at: Authoritative UTC transaction time.
        payload: Validated JSON-compatible event payload.
        attempt_id: Agent Attempt attribution or ``None`` for Human execution.

    Returns:
        Persisted event with its allocated monotonic cursor.

    Raises:
        StorageUnavailableError: If an input or allocated cursor is malformed.

    """
    try:
        event = TaskEvent(
            id=event_id,
            cursor=1,
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=actor_subject_id,
            request_id=request_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            attempt_id=attempt_id,
        )
    except (TypeError, ValueError) as error:
        raise StorageUnavailableError from error
    inserted = connection.execute(
        """
        INSERT INTO task_events (
            id, task_uid, project_id, actor_subject_id, actor_kind, attempt_id,
            request_id, event_type, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(event.id),
            str(event.task_uid),
            str(event.project_id),
            str(event.actor_subject_id),
            SubjectKind.HUMAN.value,
            None if attempt_id is None else str(attempt_id),
            str(event.request_id),
            event.event_type.value,
            serialize_timestamp(event.occurred_at),
            canonical_json(event.payload),
        ),
    )
    persisted = TaskEvent(
        id=event.id,
        cursor=require_integer(inserted.lastrowid),
        task_uid=event.task_uid,
        project_id=event.project_id,
        actor_subject_id=event.actor_subject_id,
        request_id=event.request_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        payload=event.payload,
        attempt_id=event.attempt_id,
    )
    return TaskEventRecord(
        event=persisted,
        actor_kind=SubjectKind.HUMAN,
        attempt_id=attempt_id,
    )


def task_event_record_mapping(record: TaskEventRecord) -> dict[str, object]:
    """Serialize one hydrated event into its durable replay mapping.

    Args:
        record: Validated event with attribution snapshot.

    Returns:
        JSON-compatible exact event mapping.

    Raises:
        StorageUnavailableError: If the runtime value is not an event record.

    """
    candidate: object = record
    if not isinstance(candidate, TaskEventRecord):
        raise StorageUnavailableError
    event = candidate.event
    return {
        "actor_kind": candidate.actor_kind.value,
        "actor_subject_id": str(event.actor_subject_id),
        "attempt_id": (
            None if candidate.attempt_id is None else str(candidate.attempt_id)
        ),
        "cursor": event.cursor,
        "event_type": event.event_type.value,
        "id": str(event.id),
        "occurred_at": serialize_timestamp(event.occurred_at),
        "payload": dict(event.payload),
        "project_id": str(event.project_id),
        "request_id": str(event.request_id),
        "task_uid": str(event.task_uid),
    }


def task_event_record_from_mapping(value: Mapping[str, object]) -> TaskEventRecord:
    """Deserialize one exact durable event mapping.

    Args:
        value: Candidate event mapping.

    Returns:
        Validated hydrated event record.

    Raises:
        StorageUnavailableError: If shape, values, or attribution are invalid.

    """
    candidate: object = value
    if (
        not isinstance(candidate, Mapping)
        or set(candidate) != TASK_EVENT_MAPPING_FIELD_SET
    ):
        raise StorageUnavailableError
    return _build_event_record(
        (
            candidate["cursor"],
            candidate["id"],
            candidate["task_uid"],
            candidate["project_id"],
            candidate["actor_subject_id"],
            candidate["actor_kind"],
            candidate["attempt_id"],
            candidate["request_id"],
            candidate["event_type"],
            candidate["occurred_at"],
            candidate["payload"],
        ),
        payload_is_json=False,
    )


def task_event_row(record: TaskEventRecord) -> tuple[object, ...]:
    """Serialize one event record into exact ``TASK_EVENT_FIELDS`` order.

    Args:
        record: Validated event with attribution snapshot.

    Returns:
        SQLite-compatible row values.

    """
    mapping = task_event_record_mapping(record)
    return (
        mapping["cursor"],
        mapping["id"],
        mapping["task_uid"],
        mapping["project_id"],
        mapping["actor_subject_id"],
        mapping["actor_kind"],
        mapping["attempt_id"],
        mapping["request_id"],
        mapping["event_type"],
        mapping["occurred_at"],
        canonical_json(record.event.payload),
    )


def task_event_record_from_row(value: Sequence[object]) -> TaskEventRecord:
    """Deserialize one event selected in ``TASK_EVENT_FIELDS`` order.

    Args:
        value: SQLite row values in canonical event field order.

    Returns:
        Validated hydrated event record.

    Raises:
        StorageUnavailableError: If row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TASK_EVENT_FIELDS):
        raise StorageUnavailableError
    return _build_event_record(candidate, payload_is_json=True)


def _build_event_record(
    value: Sequence[object],
    *,
    payload_is_json: bool,
) -> TaskEventRecord:
    """Build one hydrated event from shape-checked ordered values.

    Args:
        value: Ordered event values.
        payload_is_json: Whether the payload value is serialized JSON text.

    Returns:
        Validated event record.

    Raises:
        StorageUnavailableError: If any value violates the event contract.

    """
    try:
        actor_kind = SubjectKind(require_text(value[5]))
        attempt_text = None if value[6] is None else require_text(value[6])
        attempt_id = None if attempt_text is None else AttemptId(attempt_text)
        payload_value = value[10]
        if payload_is_json:
            payload = parse_json_object(
                payload_value,
                maximum=EVENT_PAYLOAD_JSON_MAX_LENGTH,
            )
        elif isinstance(payload_value, Mapping):
            payload = dict(payload_value)
        else:
            raise StorageUnavailableError
        return TaskEventRecord(
            event=TaskEvent(
                id=TaskEventId(require_text(value[1])),
                cursor=require_integer(value[0]),
                task_uid=TaskId(require_text(value[2])),
                project_id=ProjectId(require_text(value[3])),
                actor_subject_id=SubjectId(require_text(value[4])),
                request_id=RequestId(require_text(value[7])),
                event_type=TaskEventType(require_text(value[8])),
                occurred_at=parse_timestamp(value[9]),
                payload=cast("Mapping[str, JsonValue]", payload),
                attempt_id=attempt_id,
            ),
            actor_kind=actor_kind,
            attempt_id=attempt_id,
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error
