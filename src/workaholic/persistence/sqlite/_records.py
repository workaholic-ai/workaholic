"""Strict SQLite scalar and canonical serialization helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping

_CANONICAL_TIMESTAMP_LENGTH = 27


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
