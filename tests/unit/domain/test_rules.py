"""Unit tests for pure cumulative domain rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from workaholic.domain import (
    DomainPermissionError,
    DomainValidationError,
    ProjectId,
    SubjectId,
    build_task_key,
    normalize_project_name,
    normalize_task_objective,
    normalize_task_title,
    require_phase_one_owner,
    validate_json_scalar,
    validate_positive_integer,
    validate_profile_name,
    validate_project_key,
    validate_task_key,
    validate_task_priority,
    validate_utc_timestamp,
    validate_workspace_root,
)

_AWARE_NOW = datetime(2026, 7, 30, 10, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _SubjectAccessStub:
    """Explicit Subject projection used to exercise authorization rules."""

    id: SubjectId
    enabled: bool


@dataclass(frozen=True, slots=True)
class _ProjectGrantAccessStub:
    """Explicit ProjectGrant projection used to exercise authorization rules."""

    subject_id: SubjectId
    project_id: ProjectId
    role: str


@dataclass(frozen=True, slots=True)
class _UnvalidatedSubjectAccessStub:
    """Subject-shaped object containing deliberately unvalidated fields."""

    id: object
    enabled: object


@dataclass(frozen=True, slots=True)
class _UnvalidatedProjectGrantAccessStub:
    """ProjectGrant-shaped object containing deliberately unvalidated fields."""

    subject_id: object
    project_id: object
    role: object


@pytest.mark.parametrize("value", ["AA", "ACME", "A123456789012345"])
def test_project_key_accepts_inclusive_format_boundaries(value: str) -> None:
    """Project keys accept the minimum, ordinary, and maximum valid lengths."""
    assert validate_project_key(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "A",
        "A1234567890123456",
        "acme",
        "A-CME",
        " ACME",
        "ACME ",
        "",
        123,
    ],
)
def test_project_key_rejects_invalid_runtime_values(value: object) -> None:
    """Project key validation enforces the exact uppercase Phase 1 grammar."""
    with pytest.raises(DomainValidationError, match="Project key"):
        validate_project_key(value)


def test_project_name_normalizes_unicode_before_enforcing_bounds() -> None:
    """Project names trim text and collapse canonical Unicode equivalents."""
    decomposed = "  Cafe\u0301  "

    assert normalize_project_name(decomposed) == "Café"
    assert normalize_project_name("e\u0301" * 200) == "é" * 200


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * 201,
        "line\nbreak",
        None,
        True,
    ],
)
def test_project_name_rejects_invalid_runtime_values(value: object) -> None:
    """Project names reject empty, oversized, unsafe, and non-string values."""
    with pytest.raises(DomainValidationError, match="Project name"):
        normalize_project_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "local",
        "team_1",
        "agent-profile",
        "a" + ("1" * 31),
    ],
)
def test_profile_name_accepts_exact_phase_two_grammar(value: str) -> None:
    """Profile names accept every documented lowercase ASCII boundary."""
    assert validate_profile_name(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1local",
        "Local",
        "local.profile",
        "local profile",
        "a" + ("1" * 32),
        None,
        False,
    ],
)
def test_profile_name_rejects_invalid_runtime_values(value: object) -> None:
    """Profile validation rejects coercion and every out-of-grammar value."""
    with pytest.raises(DomainValidationError, match="Profile name"):
        validate_profile_name(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".", "."),
        ("src/agents", "src/agents"),
        ("src//./agents", "src/agents"),
        ("src/../agents", "agents"),
        ("src\\agents", "src/agents"),
        ("src/..", "."),
    ],
)
def test_workspace_root_normalizes_safe_relative_paths(
    value: str,
    expected: str,
) -> None:
    """Workspace roots have one portable lexical representation."""
    assert validate_workspace_root(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute",
        "../escape",
        "child/../../escape",
        r"C:\absolute",
        r"\rooted",
        r"\\server\share",
        "bad\x00path",
        "bad\npath",
        None,
        True,
    ],
)
def test_workspace_root_rejects_unsafe_runtime_values(value: object) -> None:
    """Workspace roots reject absolute, escaping, unsafe, and coerced input."""
    with pytest.raises(DomainValidationError, match="Workspace root"):
        validate_workspace_root(value)


def test_task_text_is_trimmed_and_counts_unicode_characters() -> None:
    """Title and objective boundaries apply after trimming Unicode text."""
    assert normalize_task_title("  café  ") == "café"
    assert normalize_task_title("x" * 200) == "x" * 200
    assert normalize_task_objective("  outcome  ") == "outcome"
    assert normalize_task_objective("🧭" * 4_000) == "🧭" * 4_000


@pytest.mark.parametrize("value", ["", "   ", "x" * 201, None])
def test_task_title_rejects_empty_oversized_and_non_string_values(
    value: object,
) -> None:
    """Task titles enforce the documented 1-200 character bounds."""
    with pytest.raises(DomainValidationError, match="Task title"):
        normalize_task_title(value)


@pytest.mark.parametrize("value", ["", "   ", "x" * 4_001, None])
def test_task_objective_rejects_empty_oversized_and_non_string_values(
    value: object,
) -> None:
    """Task objectives enforce the documented 1-4,000 character bounds."""
    with pytest.raises(DomainValidationError, match="Task objective"):
        normalize_task_objective(value)


@pytest.mark.parametrize("value", [0, 50, 100])
def test_task_priority_accepts_inclusive_boundaries(value: int) -> None:
    """Priority validation accepts every documented boundary value."""
    assert validate_task_priority(value) == value


@pytest.mark.parametrize("value", [-1, 101, True, 50.0, "50"])
def test_task_priority_rejects_out_of_range_and_ambiguous_values(
    value: object,
) -> None:
    """Priority validation rejects booleans, coercions, and values out of range."""
    with pytest.raises(DomainValidationError, match="Task priority"):
        validate_task_priority(value)


@pytest.mark.parametrize("value", [1, 42])
def test_positive_integer_accepts_real_positive_integers(value: int) -> None:
    """Positive counters accept integers greater than zero."""
    assert validate_positive_integer(value, label="Counter") == value


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_positive_integer_rejects_non_positive_or_ambiguous_values(
    value: object,
) -> None:
    """Positive counters reject bool and implicit numeric coercion."""
    with pytest.raises(DomainValidationError, match="Counter"):
        validate_positive_integer(value, label="Counter")


def test_task_key_build_and_validation_preserve_identity_components() -> None:
    """Human keys encode the immutable Project key and exact Task number."""
    assert build_task_key("ACME", 42) == "ACME-42"
    assert validate_task_key("ACME-42", task_number=42, project_key="ACME") == "ACME-42"


@pytest.mark.parametrize(
    ("value", "number"),
    [
        ("ACME-41", 42),
        ("ACME-042", 42),
        ("OTHER-42", 42),
        ("acme-42", 42),
        ("ACME", 42),
        ("ACME-x", 42),
        (42, 42),
    ],
)
def test_task_key_rejects_format_and_number_mismatches(
    value: object,
    number: int,
) -> None:
    """Stored Task keys cannot disagree with their Project-local number."""
    with pytest.raises(DomainValidationError, match="Task key"):
        validate_task_key(value, task_number=number, project_key="ACME")


def test_utc_timestamp_accepts_aware_zero_offset_datetime() -> None:
    """UTC validation accepts aware timestamps whose actual offset is zero."""
    value = datetime(2026, 7, 30, 10, 30, tzinfo=UTC)

    assert validate_utc_timestamp(value, label="Timestamp") is value


@pytest.mark.parametrize(
    "value",
    [
        _AWARE_NOW.replace(tzinfo=None),
        datetime(2026, 7, 30, 10, 30, tzinfo=timezone(timedelta(hours=3))),
        "2026-07-30T10:30:00Z",
    ],
)
def test_utc_timestamp_rejects_naive_non_utc_and_non_datetime_values(
    value: object,
) -> None:
    """Domain timestamps never rely on a local offset or string coercion."""
    with pytest.raises(
        DomainValidationError,
        match=r"timezone-aware UTC|datetime",
    ):
        validate_utc_timestamp(value, label="Timestamp")


@pytest.mark.parametrize("value", [None, False, True, 0, 1.5, "value"])
def test_json_scalar_accepts_interoperable_scalar_values(value: object) -> None:
    """Event payload values accept each supported finite JSON scalar kind."""
    validate_json_scalar(value, label="Payload")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), [], {}, object()])
def test_json_scalar_rejects_non_finite_and_nested_values(value: object) -> None:
    """Event payload values reject non-finite numbers and nested structures."""
    with pytest.raises(DomainValidationError, match="finite JSON scalar"):
        validate_json_scalar(value, label="Payload")


def test_phase_one_owner_rule_accepts_matching_enabled_grant() -> None:
    """The enabled Subject owning the target Project may perform a write."""
    subject_id = SubjectId("sub_local")
    project_id = ProjectId("prj_acme")

    require_phase_one_owner(
        subject=_SubjectAccessStub(id=subject_id, enabled=True),
        grant=_ProjectGrantAccessStub(
            subject_id=subject_id,
            project_id=project_id,
            role="owner",
        ),
        target_project_id=project_id,
    )


@pytest.mark.parametrize(
    ("subject", "grant"),
    [
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=False),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_other"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_other"),
                role="owner",
            ),
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="viewer",
            ),
        ),
    ],
)
def test_phase_one_owner_rule_rejects_missing_effective_ownership(
    subject: _SubjectAccessStub,
    grant: _ProjectGrantAccessStub,
) -> None:
    """Disabled, foreign, or non-Owner grant combinations are denied."""
    with pytest.raises(DomainPermissionError, match="Owner grant"):
        require_phase_one_owner(
            subject=subject,
            grant=grant,
            target_project_id=ProjectId("prj_acme"),
        )


def test_phase_one_owner_rule_validates_boundary_types() -> None:
    """Authorization inputs are runtime validated before permission evaluation."""
    with pytest.raises(DomainValidationError, match="Subject value"):
        require_phase_one_owner(
            subject="sub_local",
            grant=_ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
            target_project_id=ProjectId("prj_acme"),
        )

    with pytest.raises(DomainValidationError, match="ProjectGrant value"):
        require_phase_one_owner(
            subject=_SubjectAccessStub(
                id=SubjectId("sub_local"),
                enabled=True,
            ),
            grant="owner",
            target_project_id=ProjectId("prj_acme"),
        )


@pytest.mark.parametrize(
    ("subject", "grant", "target_project_id", "message"),
    [
        (
            _UnvalidatedSubjectAccessStub(id="sub_local", enabled=True),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
            ProjectId("prj_acme"),
            "SubjectId",
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _UnvalidatedProjectGrantAccessStub(
                subject_id="sub_local",
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
            ProjectId("prj_acme"),
            "SubjectId",
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _UnvalidatedProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id="prj_acme",
                role="owner",
            ),
            ProjectId("prj_acme"),
            "ProjectId",
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
            "prj_acme",
            "ProjectId",
        ),
        (
            _UnvalidatedSubjectAccessStub(
                id=SubjectId("sub_local"),
                enabled=1,
            ),
            _ProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role="owner",
            ),
            ProjectId("prj_acme"),
            "enabled state",
        ),
        (
            _SubjectAccessStub(id=SubjectId("sub_local"), enabled=True),
            _UnvalidatedProjectGrantAccessStub(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role=object(),
            ),
            ProjectId("prj_acme"),
            "Project role",
        ),
    ],
)
def test_phase_one_owner_rule_rejects_unvalidated_structural_fields(
    subject: object,
    grant: object,
    target_project_id: object,
    message: str,
) -> None:
    """Authorization validates every structural field before comparing grants."""
    with pytest.raises(DomainValidationError, match=message):
        require_phase_one_owner(
            subject=subject,
            grant=grant,
            target_project_id=target_project_id,
        )
