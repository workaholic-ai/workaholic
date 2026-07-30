"""Canonical Task serialization and strict SQLite row deserialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.domain import (
    ProjectId,
    SubjectId,
    Task,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite._records import (
    parse_timestamp,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

TASK_FIELDS = (
    "uid",
    "project_id",
    "number",
    "key",
    "title",
    "objective",
    "state",
    "priority",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)
TASK_FIELD_SET = frozenset(TASK_FIELDS)


def task_mapping(task: Task) -> dict[str, object]:
    """Serialize a Task into its exact durable replay shape.

    Args:
        task: Validated Task result.

    Returns:
        JSON-compatible stable Task field mapping.

    """
    candidate: object = task
    if not isinstance(candidate, Task):
        raise StorageUnavailableError
    return {
        "created_at": serialize_timestamp(candidate.created_at),
        "created_by": str(candidate.created_by),
        "key": candidate.key,
        "number": candidate.number,
        "objective": candidate.objective,
        "priority": candidate.priority,
        "project_id": str(candidate.project_id),
        "state": candidate.state.value,
        "title": candidate.title,
        "uid": str(candidate.uid),
        "updated_at": serialize_timestamp(candidate.updated_at),
        "version": candidate.version,
    }


def task_from_mapping(value: Mapping[str, object]) -> Task:
    """Deserialize and validate one exact durable Task mapping.

    Args:
        value: Persisted Task field mapping.

    Returns:
        Validated immutable Task.

    Raises:
        StorageUnavailableError: If the mapping shape or value is malformed.

    """
    if set(value) != TASK_FIELD_SET:
        raise StorageUnavailableError
    return _build_task(tuple(value[field] for field in TASK_FIELDS))


def task_from_row(value: Sequence[object]) -> Task:
    """Deserialize and validate one Task selected in ``TASK_FIELDS`` order.

    Args:
        value: SQLite row values in the exported Task field order.

    Returns:
        Validated immutable Task.

    Raises:
        StorageUnavailableError: If the row shape or value is malformed.

    """
    if len(value) != len(TASK_FIELDS):
        raise StorageUnavailableError
    return _build_task(value)


def _build_task(value: Sequence[object]) -> Task:
    """Build one Task from a previously shape-checked value sequence.

    Args:
        value: Ordered persisted Task values.

    Returns:
        Validated immutable Task.

    """
    return Task(
        uid=TaskId(require_text(value[0])),
        project_id=ProjectId(require_text(value[1])),
        number=require_integer(value[2]),
        key=require_text(value[3]),
        title=require_text(value[4]),
        objective=require_text(value[5]),
        state=TaskState(require_text(value[6])),
        priority=require_integer(value[7], minimum=0),
        version=require_integer(value[8]),
        created_by=SubjectId(require_text(value[9])),
        created_at=parse_timestamp(value[10]),
        updated_at=parse_timestamp(value[11]),
    )
