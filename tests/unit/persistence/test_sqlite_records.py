"""Unit tests for strict SQLite scalar and serialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from workaholic.domain import InstanceId, Project, ProjectId
from workaholic.persistence.sqlite._records import (
    PROJECT_FIELDS,
    canonical_json,
    parse_timestamp,
    project_from_mapping,
    project_from_row,
    project_to_mapping,
    require_boolean,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError


def _project() -> Project:
    """Build one canonical named Project record fixture.

    Returns:
        Valid immutable Project.

    """
    return Project(
        id=ProjectId("prj_acme"),
        instance_id=InstanceId("ins_local"),
        key="ACME",
        name="Acme Platform",
        created_at=datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC),
    )


def test_project_record_round_trips_mapping_and_sqlite_row() -> None:
    """Named Projects use one exact canonical durable field order."""
    project = _project()
    expected = {
        "id": "prj_acme",
        "instance_id": "ins_local",
        "key": "ACME",
        "name": "Acme Platform",
        "created_at": "2026-07-30T12:15:30.654321Z",
    }

    mapping = project_to_mapping(project)

    assert PROJECT_FIELDS == (
        "id",
        "instance_id",
        "key",
        "name",
        "created_at",
    )
    assert mapping == expected
    assert project_from_mapping(mapping) == project
    assert project_from_row(tuple(mapping[field] for field in PROJECT_FIELDS)) == (
        project
    )


@pytest.mark.parametrize(
    "value",
    [
        {},
        {
            "id": "prj_acme",
            "instance_id": "ins_local",
            "key": "ACME",
            "name": "Acme",
            "created_at": "2026-07-30T12:15:30.654321Z",
            "unknown": "value",
        },
        cast("dict[str, object]", object()),
    ],
)
def test_project_mapping_rejects_noncanonical_shapes(
    value: dict[str, object],
) -> None:
    """Missing, open, and non-mapping Project records fail safely."""
    with pytest.raises(StorageUnavailableError):
        project_from_mapping(value)


@pytest.mark.parametrize(
    "value",
    [
        (),
        ("prj_acme", "ins_local", "ACME", "Acme"),
        ("prj_acme", "ins_local", "ACME", " Acme ", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "ACME", "Cafe\u0301", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "lower", "Acme", "2026-07-30T12:15:30.654321Z"),
        ("prj_acme", "ins_local", "ACME", "Acme", "not-a-timestamp"),
        "not-a-row",
        cast("tuple[object, ...]", object()),
    ],
)
def test_project_row_rejects_noncanonical_shapes_and_values(
    value: tuple[object, ...],
) -> None:
    """Wrong shapes, normalized aliases, and invalid scalars fail safely."""
    with pytest.raises(StorageUnavailableError):
        project_from_row(value)


def test_project_serializer_runtime_validates_input() -> None:
    """The record writer does not trust its Project type hint."""
    with pytest.raises(StorageUnavailableError):
        project_to_mapping(cast("Project", object()))


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
