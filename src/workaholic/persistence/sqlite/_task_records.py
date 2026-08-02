"""Canonical Phase 3 Task serialization and strict row deserialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    ProjectId,
    ResultId,
    SubjectId,
    Task,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite._records import (
    STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
    canonical_json_value,
    parse_json_array,
    parse_optional_timestamp,
    parse_timestamp,
    require_integer,
    require_optional_text,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    from datetime import datetime

TASK_FIELDS: Final = (
    "uid",
    "project_id",
    "number",
    "key",
    "title",
    "objective",
    "state",
    "priority",
    "available_at",
    "approval",
    "acceptance_json",
    "context_json",
    "blocking_reason",
    "current_result_id",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)
TASK_MAPPING_FIELDS: Final = (
    "uid",
    "project_id",
    "number",
    "key",
    "title",
    "objective",
    "state",
    "priority",
    "available_at",
    "approval",
    "acceptance",
    "context",
    "depends_on",
    "blocking_reason",
    "current_result_id",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)
TASK_FIELD_SET: Final = frozenset(TASK_MAPPING_FIELDS)


def task_mapping(task: Task) -> dict[str, object]:
    """Serialize a Task into its exact durable replay shape.

    Args:
        task: Validated Task result.

    Returns:
        JSON-compatible stable Task field mapping.

    Raises:
        StorageUnavailableError: If the runtime value is not a Task.

    """
    candidate: object = task
    if not isinstance(candidate, Task):
        raise StorageUnavailableError
    return {
        "acceptance": [
            {"id": item.id, "required": item.required, "text": item.text}
            for item in candidate.acceptance
        ],
        "approval": candidate.approval.value,
        "available_at": _serialize_optional_timestamp(candidate.available_at),
        "blocking_reason": candidate.blocking_reason,
        "context": [
            {"uri": item.uri, "version": item.version} for item in candidate.context
        ],
        "created_at": serialize_timestamp(candidate.created_at),
        "created_by": str(candidate.created_by),
        "current_result_id": (
            None
            if candidate.current_result_id is None
            else str(candidate.current_result_id)
        ),
        "depends_on": [str(item) for item in candidate.depends_on],
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


def task_row(task: Task) -> tuple[object, ...]:
    """Serialize a Task into exact ``TASK_FIELDS`` SQLite order.

    Args:
        task: Validated Task result.

    Returns:
        SQLite-compatible row values.

    Raises:
        StorageUnavailableError: If the runtime value is not a Task.

    """
    candidate: object = task
    if not isinstance(candidate, Task):
        raise StorageUnavailableError
    return (
        str(candidate.uid),
        str(candidate.project_id),
        candidate.number,
        candidate.key,
        candidate.title,
        candidate.objective,
        candidate.state.value,
        candidate.priority,
        _serialize_optional_timestamp(candidate.available_at),
        candidate.approval.value,
        canonical_json_value(
            [
                {"id": item.id, "required": item.required, "text": item.text}
                for item in candidate.acceptance
            ]
        ),
        canonical_json_value(
            [{"uri": item.uri, "version": item.version} for item in candidate.context]
        ),
        candidate.blocking_reason,
        None
        if candidate.current_result_id is None
        else str(candidate.current_result_id),
        candidate.version,
        str(candidate.created_by),
        serialize_timestamp(candidate.created_at),
        serialize_timestamp(candidate.updated_at),
    )


def task_from_mapping(value: Mapping[str, object]) -> Task:
    """Deserialize and validate one exact durable Task mapping.

    Args:
        value: Persisted Task field mapping.

    Returns:
        Validated immutable Task.

    Raises:
        StorageUnavailableError: If the mapping shape or value is malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != TASK_FIELD_SET:
        raise StorageUnavailableError
    try:
        return Task(
            uid=TaskId(require_text(candidate["uid"])),
            project_id=ProjectId(require_text(candidate["project_id"])),
            number=require_integer(candidate["number"]),
            key=require_text(candidate["key"]),
            title=require_text(candidate["title"]),
            objective=require_text(candidate["objective"]),
            state=TaskState(require_text(candidate["state"])),
            priority=require_integer(candidate["priority"], minimum=0),
            available_at=parse_optional_timestamp(candidate["available_at"]),
            approval=ApprovalRequirement(require_text(candidate["approval"])),
            acceptance=_acceptance_from_sequence(candidate["acceptance"]),
            context=_context_from_sequence(candidate["context"]),
            depends_on=_dependencies_from_sequence(candidate["depends_on"]),
            blocking_reason=require_optional_text(candidate["blocking_reason"]),
            current_result_id=_optional_result_id(candidate["current_result_id"]),
            version=require_integer(candidate["version"]),
            created_by=SubjectId(require_text(candidate["created_by"])),
            created_at=parse_timestamp(candidate["created_at"]),
            updated_at=parse_timestamp(candidate["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def task_from_row(
    value: Sequence[object],
    *,
    depends_on: Sequence[TaskId] = (),
) -> Task:
    """Deserialize one Task selected in ``TASK_FIELDS`` order.

    Args:
        value: SQLite row values in canonical Task field order.
        depends_on: Separately loaded ordered prerequisite identities.

    Returns:
        Validated immutable Task.

    Raises:
        StorageUnavailableError: If the row shape or value is malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TASK_FIELDS):
        raise StorageUnavailableError
    try:
        return Task(
            uid=TaskId(require_text(candidate[0])),
            project_id=ProjectId(require_text(candidate[1])),
            number=require_integer(candidate[2]),
            key=require_text(candidate[3]),
            title=require_text(candidate[4]),
            objective=require_text(candidate[5]),
            state=TaskState(require_text(candidate[6])),
            priority=require_integer(candidate[7], minimum=0),
            available_at=parse_optional_timestamp(candidate[8]),
            approval=ApprovalRequirement(require_text(candidate[9])),
            acceptance=_acceptance_from_sequence(
                parse_json_array(
                    candidate[10],
                    maximum=STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
                )
            ),
            context=_context_from_sequence(
                parse_json_array(
                    candidate[11],
                    maximum=STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
                )
            ),
            depends_on=_dependencies_from_sequence(depends_on),
            blocking_reason=require_optional_text(candidate[12]),
            current_result_id=_optional_result_id(candidate[13]),
            version=require_integer(candidate[14]),
            created_by=SubjectId(require_text(candidate[15])),
            created_at=parse_timestamp(candidate[16]),
            updated_at=parse_timestamp(candidate[17]),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _acceptance_from_sequence(value: object) -> tuple[AcceptanceCriterion, ...]:
    """Decode one closed ordered acceptance-criterion sequence."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StorageUnavailableError
    result: list[AcceptanceCriterion] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"id", "required", "text"}:
            raise StorageUnavailableError
        required = item["required"]
        if type(required) is not bool:
            raise StorageUnavailableError
        result.append(
            AcceptanceCriterion(
                id=require_text(item["id"]),
                text=require_text(item["text"]),
                required=required,
            )
        )
    return tuple(result)


def _context_from_sequence(value: object) -> tuple[ContextReference, ...]:
    """Decode one closed ordered context-reference sequence."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StorageUnavailableError
    result: list[ContextReference] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"uri", "version"}:
            raise StorageUnavailableError
        result.append(
            ContextReference(
                uri=require_text(item["uri"]),
                version=require_optional_text(item["version"]),
            )
        )
    return tuple(result)


def _dependencies_from_sequence(value: object) -> tuple[TaskId, ...]:
    """Decode one ordered prerequisite-identity sequence."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StorageUnavailableError
    return tuple(
        item if isinstance(item, TaskId) else TaskId(require_text(item))
        for item in value
    )


def _optional_result_id(value: object) -> ResultId | None:
    """Decode one nullable Result identity."""
    return None if value is None else ResultId(require_text(value))


def _serialize_optional_timestamp(value: datetime | None) -> str | None:
    """Serialize one nullable validated UTC timestamp."""
    return None if value is None else serialize_timestamp(value)
