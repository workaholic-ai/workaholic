"""Unit tests for strict SQLite scalar and serialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workaholic.persistence.sqlite._records import (
    canonical_json,
    parse_timestamp,
    require_boolean,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError


def test_canonical_serialization_round_trips_supported_values() -> None:
    """Shared serialization is deterministic and preserves UTC microseconds."""
    timestamp = datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC)

    assert canonical_json({"text": "value", "number": 1}) == (
        '{"number":1,"text":"value"}'
    )
    assert serialize_timestamp(timestamp) == "2026-07-30T12:15:30.654321Z"
    assert parse_timestamp("2026-07-30T12:15:30.654321Z") == timestamp
    assert require_text("value") == "value"
    assert require_integer(0, minimum=0) == 0
    assert require_boolean(0) is False
    assert require_boolean(1) is True


@pytest.mark.parametrize(
    ("helper", "value"),
    [
        (require_text, ""),
        (require_text, 1),
        (require_integer, True),
        (require_integer, 0),
        (require_boolean, 2),
        (require_boolean, False),
        (parse_timestamp, "2026-07-30T12:15:30Z"),
        (parse_timestamp, 1),
    ],
)
def test_scalar_helpers_reject_ambiguous_or_noncanonical_values(
    helper: object,
    value: object,
) -> None:
    """Wrong SQLite storage classes and timestamp shapes fail safely."""
    assert callable(helper)
    with pytest.raises(StorageUnavailableError):
        helper(value)
