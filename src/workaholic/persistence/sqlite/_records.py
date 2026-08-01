"""Strict SQLite scalar and canonical serialization helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, cast

from workaholic.domain import (
    DomainValidationError,
    InstanceId,
    Project,
    ProjectId,
    validate_json_value,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

_CANONICAL_TIMESTAMP_LENGTH = 27
_MIN_JSON_DOCUMENT_LENGTH = 2
EVENT_PAYLOAD_JSON_MAX_LENGTH: Final = 65_536
STRUCTURED_COLLECTION_JSON_MAX_LENGTH: Final = 262_144
IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH: Final = 2_097_152
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
    candidate: object = value
    if not isinstance(candidate, Mapping):
        raise StorageUnavailableError
    return canonical_json_value(candidate)


def canonical_json_value(value: object) -> str:
    """Serialize one bounded JSON value deterministically.

    Args:
        value: Candidate recursive JSON value.

    Returns:
        Canonical compact JSON with sorted object keys.

    Raises:
        StorageUnavailableError: If the value violates the bounded JSON contract.

    """
    try:
        validate_json_value(value, label="Persisted JSON")
        return json.dumps(
            _json_compatible_copy(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (DomainValidationError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _json_compatible_copy(value: object) -> object:
    """Copy validated Mapping and Sequence abstractions into JSON-native values."""
    if isinstance(value, Mapping):
        return {key: _json_compatible_copy(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible_copy(item) for item in value]
    return value


def parse_json_object(
    value: object,
    *,
    maximum: int,
) -> dict[str, object]:
    """Parse one bounded canonical JSON object from SQLite.

    Args:
        value: Candidate SQLite text value.
        maximum: Inclusive serialized character bound.

    Returns:
        New validated object mapping.

    Raises:
        StorageUnavailableError: If shape, bounds, keys, or encoding are invalid.

    """
    decoded = _parse_canonical_json(value, maximum=maximum)
    if not isinstance(decoded, dict):
        raise StorageUnavailableError
    return cast("dict[str, object]", decoded)


def parse_json_array(
    value: object,
    *,
    maximum: int,
) -> tuple[object, ...]:
    """Parse one bounded canonical JSON array from SQLite.

    Args:
        value: Candidate SQLite text value.
        maximum: Inclusive serialized character bound.

    Returns:
        Immutable top-level sequence of validated JSON values.

    Raises:
        StorageUnavailableError: If shape, bounds, keys, or encoding are invalid.

    """
    decoded = _parse_canonical_json(value, maximum=maximum)
    if not isinstance(decoded, list):
        raise StorageUnavailableError
    return tuple(decoded)


def _parse_canonical_json(value: object, *, maximum: int) -> object:
    """Parse and validate one canonical bounded JSON document.

    Args:
        value: Candidate SQLite text value.
        maximum: Inclusive serialized character bound.

    Returns:
        Decoded JSON value.

    Raises:
        StorageUnavailableError: If persistence contains noncanonical JSON.

    """
    text = require_text(value)
    if (
        type(maximum) is not int
        or maximum < _MIN_JSON_DOCUMENT_LENGTH
        or len(text) > maximum
    ):
        raise StorageUnavailableError
    try:
        decoded: object = json.loads(text, object_pairs_hook=_unique_json_object)
        validate_json_value(decoded, label="Persisted JSON")
        if canonical_json_value(decoded) != text:
            raise StorageUnavailableError
    except (
        DomainValidationError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise StorageUnavailableError from error
    return decoded


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate serialized keys.

    Args:
        pairs: Ordered object pairs supplied by ``json.loads``.

    Returns:
        New object preserving the decoded values.

    Raises:
        ValueError: If one serialized key appears more than once.

    """
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            message = "Persisted JSON object keys must be unique."
            raise ValueError(message)
        result[key] = item
    return result


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


def parse_optional_timestamp(value: object) -> datetime | None:
    """Parse a nullable canonical UTC timestamp from SQLite.

    Args:
        value: Persisted timestamp text or ``None``.

    Returns:
        Timezone-aware UTC datetime or ``None``.

    """
    return None if value is None else parse_timestamp(value)


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


def require_optional_text(value: object) -> str | None:
    """Require nullable nonempty SQLite text.

    Args:
        value: Driver value.

    Returns:
        Nonempty text or ``None``.

    Raises:
        StorageUnavailableError: If a non-null value is not nonempty text.

    """
    return None if value is None else require_text(value)


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
