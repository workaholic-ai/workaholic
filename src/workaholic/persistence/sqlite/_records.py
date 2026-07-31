"""Strict SQLite scalar and canonical serialization helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from workaholic.domain import InstanceId, Project, ProjectId
from workaholic.persistence.sqlite.errors import StorageUnavailableError

_CANONICAL_TIMESTAMP_LENGTH = 27
PROJECT_FIELDS = (
    "id",
    "instance_id",
    "key",
    "name",
    "created_at",
)
PROJECT_FIELD_SET = frozenset(PROJECT_FIELDS)


def project_to_mapping(value: Project) -> dict[str, object]:
    """Serialize one validated Project into canonical durable fields.

    Args:
        value: Project to serialize.

    Returns:
        New mapping in canonical Project field order.

    Raises:
        StorageUnavailableError: If the runtime value is not a Project.

    """
    candidate: object = value
    if not isinstance(candidate, Project):
        raise StorageUnavailableError
    return {
        "id": str(candidate.id),
        "instance_id": str(candidate.instance_id),
        "key": candidate.key,
        "name": candidate.name,
        "created_at": serialize_timestamp(candidate.created_at),
    }


def project_from_mapping(value: Mapping[str, object]) -> Project:
    """Deserialize one exact canonical Project mapping.

    Args:
        value: Candidate persisted Project fields.

    Returns:
        Validated immutable Project.

    Raises:
        StorageUnavailableError: If the mapping shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != PROJECT_FIELD_SET:
        raise StorageUnavailableError
    return _build_project(tuple(candidate[field] for field in PROJECT_FIELDS))


def project_from_row(value: Sequence[object]) -> Project:
    """Deserialize one Project selected in ``PROJECT_FIELDS`` order.

    Args:
        value: SQLite row values in canonical Project field order.

    Returns:
        Validated immutable Project.

    Raises:
        StorageUnavailableError: If the row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(
        candidate,
        (str, bytes),
    ):
        raise StorageUnavailableError
    if len(candidate) != len(PROJECT_FIELDS):
        raise StorageUnavailableError
    return _build_project(candidate)


def _build_project(value: Sequence[object]) -> Project:
    """Build one Project from a shape-checked value sequence.

    Args:
        value: Ordered persisted Project values.

    Returns:
        Validated immutable Project.

    Raises:
        StorageUnavailableError: If any value violates the Project contract.

    """
    try:
        persisted_name = require_text(value[3])
        project = Project(
            id=ProjectId(require_text(value[0])),
            instance_id=InstanceId(require_text(value[1])),
            key=require_text(value[2]),
            name=persisted_name,
            created_at=parse_timestamp(value[4]),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error
    if project.name != persisted_name:
        raise StorageUnavailableError
    return project


def canonical_json(value: Mapping[str, object]) -> str:
    """Serialize one mapping deterministically.

    Args:
        value: JSON-compatible mapping to serialize.

    Returns:
        Canonical compact JSON with sorted keys.

    """
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def serialize_timestamp(value: datetime) -> str:
    """Serialize one authoritative UTC timestamp as canonical RFC 3339 text.

    Args:
        value: Timezone-aware UTC datetime.

    Returns:
        Fixed-width microsecond precision text ending in ``Z``.

    """
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime:
    """Parse one canonical UTC timestamp from SQLite.

    Args:
        value: Persisted timestamp value.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        StorageUnavailableError: If the persisted timestamp is malformed.

    """
    text = require_text(value)
    if (
        len(text) != _CANONICAL_TIMESTAMP_LENGTH
        or not text.endswith("Z")
        or text[10] != "T"
        or text[19] != "."
    ):
        raise StorageUnavailableError
    return datetime.fromisoformat(f"{text[:-1]}+00:00")


def require_text(value: object) -> str:
    """Require one nonempty SQLite text value.

    Args:
        value: Driver value.

    Returns:
        Nonempty string.

    Raises:
        StorageUnavailableError: If persisted data has the wrong type.

    """
    if not isinstance(value, str) or not value:
        raise StorageUnavailableError
    return value


def require_integer(value: object, *, minimum: int = 1) -> int:
    """Require one bounded SQLite integer without accepting booleans.

    Args:
        value: Driver value.
        minimum: Inclusive lower bound.

    Returns:
        Validated integer.

    Raises:
        StorageUnavailableError: If persisted data has the wrong type or range.

    """
    if type(value) is not int or value < minimum:
        raise StorageUnavailableError
    return value


def require_boolean(value: object) -> bool:
    """Deserialize one strict SQLite boolean integer.

    Args:
        value: Driver value.

    Returns:
        Corresponding Python boolean.

    Raises:
        StorageUnavailableError: If the value is not exactly zero or one.

    """
    if type(value) is not int or value not in (0, 1):
        raise StorageUnavailableError
    return bool(value)
