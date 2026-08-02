"""Unit tests for opaque domain identifier value objects."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from workaholic.domain import (
    DomainValidationError,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
)

type _IdentifierType = (
    type[InstanceId]
    | type[ProjectId]
    | type[SubjectId]
    | type[TaskId]
    | type[TaskEventId]
    | type[RequestId]
    | type[ResultId]
)

_IDENTIFIER_CASES: tuple[tuple[_IdentifierType, str], ...] = (
    (InstanceId, "ins_"),
    (ProjectId, "prj_"),
    (SubjectId, "sub_"),
    (TaskId, "tsk_"),
    (TaskEventId, "evt_"),
    (RequestId, "req_"),
    (ResultId, "res_"),
)


@pytest.mark.parametrize(("identifier_type", "prefix"), _IDENTIFIER_CASES)
def test_identifier_accepts_and_serializes_opaque_suffix(
    identifier_type: _IdentifierType,
    prefix: str,
) -> None:
    """Each identifier preserves a supported opaque UUID-like suffix."""
    serialized = f"{prefix}019c0d91-7b8a-7000-8000-0123456789ab"
    identifier = identifier_type(serialized)

    assert str(identifier) == serialized
    assert identifier.value == serialized


@pytest.mark.parametrize(("identifier_type", "prefix"), _IDENTIFIER_CASES)
@pytest.mark.parametrize(
    "suffix",
    [
        "",
        " white-space",
        "slash/value",
        "dot.value",
        "ümlaut",
        "a\nb",
        "a" * 129,
    ],
)
def test_identifier_rejects_unsupported_suffix(
    identifier_type: _IdentifierType,
    prefix: str,
    suffix: str,
) -> None:
    """Identifier constructors reject empty, unsafe, and oversized suffixes."""
    with pytest.raises(DomainValidationError):
        identifier_type(f"{prefix}{suffix}")


@pytest.mark.parametrize(("identifier_type", "prefix"), _IDENTIFIER_CASES)
def test_identifier_rejects_wrong_prefix_and_non_string(
    identifier_type: _IdentifierType,
    prefix: str,
) -> None:
    """Identifier constructors validate both runtime type and category prefix."""
    with pytest.raises(DomainValidationError, match="must start"):
        identifier_type(f"bad_{prefix}value")
    with pytest.raises(DomainValidationError, match="must be a string"):
        identifier_type(123)  # type: ignore[arg-type]


def test_identifiers_are_immutable_and_category_specific() -> None:
    """Identifier values cannot change and different categories never compare equal."""
    instance_id = InstanceId("ins_same")

    with pytest.raises(FrozenInstanceError):
        instance_id.value = "ins_changed"  # type: ignore[misc]

    assert instance_id != cast("object", ProjectId("prj_same"))
