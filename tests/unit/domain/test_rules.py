"""Unit tests for pure cumulative domain rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from workaholic.domain import (
    JSON_MAX_ARRAY_ITEMS,
    JSON_MAX_DEPTH,
    JSON_MAX_OBJECT_ITEMS,
    JSON_MAX_STRING_LENGTH,
    DomainPermissionError,
    DomainValidationError,
    ProjectId,
    SubjectId,
    build_task_key,
    normalize_bounded_printable_text,
    normalize_project_name,
    normalize_task_objective,
    normalize_task_title,
    parse_rfc3339_utc_timestamp,
    require_phase_one_owner,
    validate_acceptance_criterion_id,
    validate_json_scalar,
    validate_json_value,
    validate_lowercase_sha256,
    validate_media_type,
    validate_positive_integer,
    validate_profile_name,
    validate_project_key,
    validate_task_key,
    validate_task_priority,
    validate_uri_reference,
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


def test_bounded_printable_text_normalizes_unicode_and_custom_bounds() -> None:
    """Shared Phase 3 text validation trims and NFC-normalizes Unicode."""
    assert (
        normalize_bounded_printable_text(
            "  Cafe\u0301  ",
            label="Text",
            minimum=1,
            maximum=4,
        )
        == "Café"
    )


@pytest.mark.parametrize(
    ("value", "minimum", "maximum"),
    [
        ("", 1, 10),
        ("too long", 1, 4),
        ("line\nbreak", 1, 20),
        (None, 1, 20),
        ("ok", True, 20),
        ("ok", 2, 1),
        ("ok", -1, 2),
    ],
)
def test_bounded_printable_text_rejects_invalid_values_and_bounds(
    value: object,
    minimum: object,
    maximum: object,
) -> None:
    """Shared text validation rejects unsafe values and ambiguous bounds."""
    with pytest.raises(DomainValidationError):
        normalize_bounded_printable_text(
            value,
            label="Text",
            minimum=minimum,  # type: ignore[arg-type]
            maximum=maximum,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "value",
    ["ac_a", "ac_0", "ac_A-b_2", "ac_" + ("a" * 64)],
)
def test_acceptance_criterion_id_accepts_exact_grammar(value: str) -> None:
    """Criterion identifiers preserve stable, bounded opaque suffixes."""
    assert validate_acceptance_criterion_id(value) == value


@pytest.mark.parametrize(
    "value",
    ["ac_", "bad_a", "ac_ümlaut", "ac_a.b", "ac_" + ("a" * 65), None],
)
def test_acceptance_criterion_id_rejects_malformed_values(value: object) -> None:
    """Criterion identifiers reject missing, unsafe, and oversized suffixes."""
    with pytest.raises(DomainValidationError, match="criterion ID"):
        validate_acceptance_criterion_id(value)


def test_rfc3339_parser_accepts_canonical_utc_seconds_and_fraction() -> None:
    """Canonical ``Z`` timestamps parse to timezone-aware UTC datetimes."""
    assert parse_rfc3339_utc_timestamp(
        "2026-08-01T12:00:00Z",
        label="Timestamp",
    ) == datetime(2026, 8, 1, 12, tzinfo=UTC)
    assert (
        parse_rfc3339_utc_timestamp(
            "2026-08-01T12:00:00.123456Z",
            label="Timestamp",
        ).microsecond
        == 123456
    )


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-01T12:00:00+00:00",
        "2026-08-01T15:00:00+03:00",
        "2026-02-30T12:00:00Z",
        "2026-08-01 12:00:00Z",
        "2026-08-01T12:00:00.1234567Z",
        1,
    ],
)
def test_rfc3339_parser_rejects_noncanonical_or_invalid_values(value: object) -> None:
    """Structured timestamps do not accept offsets, coercion, or invalid dates."""
    with pytest.raises(DomainValidationError, match="RFC 3339 UTC"):
        parse_rfc3339_utc_timestamp(value, label="Timestamp")


@pytest.mark.parametrize(
    "value",
    [
        "workspace://repo/spec.md",
        "https://example.com/a%20b",
        "urn:example:task:42",
        "file:/tmp/result.json",
    ],
)
def test_uri_reference_accepts_inert_absolute_uris(value: str) -> None:
    """URI validation accepts common absolute references without opening them."""
    assert validate_uri_reference(value, label="URI") == value


@pytest.mark.parametrize(
    "value",
    [
        "relative/path",
        "https://example.com/white space",
        "https://example.com/bad%2",
        "https:\\example.com",
        "x:",
        "line\nbreak:bad",
        "x" * 2049,
        None,
    ],
)
def test_uri_reference_rejects_malformed_or_unbounded_values(value: object) -> None:
    """URI references reject relative, ambiguous, unsafe, and oversized input."""
    with pytest.raises(DomainValidationError, match="URI"):
        validate_uri_reference(value, label="URI")


@pytest.mark.parametrize(
    "value",
    ["text/plain", "application/vnd.api+json", "x-a/x_b"],
)
def test_media_type_accepts_lowercase_type_subtype_tokens(value: str) -> None:
    """Artifact media types accept lowercase RFC-style tokens."""
    assert validate_media_type(value) == value


@pytest.mark.parametrize(
    "value",
    ["Text/plain", "text", "text/plain; charset=utf-8", "t/", "a" * 128, None],
)
def test_media_type_rejects_invalid_or_oversized_values(value: object) -> None:
    """Artifact media types reject case, parameters, missing tokens, and coercion."""
    with pytest.raises(DomainValidationError, match="type/subtype"):
        validate_media_type(value)


def test_sha256_accepts_exact_lowercase_hex_digest() -> None:
    """Artifact digests preserve one canonical lowercase representation."""
    assert validate_lowercase_sha256("0123456789abcdef" * 4) == ("0123456789abcdef" * 4)


@pytest.mark.parametrize("value", ["a" * 63, "A" * 64, "g" * 64, None])
def test_sha256_rejects_wrong_length_case_alphabet_or_type(value: object) -> None:
    """Artifact digests reject every noncanonical representation."""
    with pytest.raises(DomainValidationError, match="64 lowercase"):
        validate_lowercase_sha256(value)


def test_recursive_json_accepts_nested_finite_values_and_boolean_scalars() -> None:
    """Bounded recursive JSON accepts objects, arrays, booleans, and finite numbers."""
    validate_json_value(
        {
            "null": None,
            "boolean": True,
            "integer": 1,
            "float": 1.5,
            "string": "value",
            "nested": [{"ok": False}],
        },
        label="Payload",
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (float("nan"), "finite"),
        (float("inf"), "finite"),
        ("x" * (JSON_MAX_STRING_LENGTH + 1), "strings"),
        ({f"k{i}": i for i in range(JSON_MAX_OBJECT_ITEMS + 1)}, "objects"),
        ([None] * (JSON_MAX_ARRAY_ITEMS + 1), "arrays"),
        ({" bad": 1}, "keys"),
        ({"": 1}, "keys"),
        ({"line\nbreak": 1}, "keys"),
        ({"x" * 129: 1}, "keys"),
        ({1: "value"}, "keys"),
        ({1, 2}, "JSON value"),
    ],
)
def test_recursive_json_rejects_unbounded_or_unsupported_values(
    value: object,
    message: str,
) -> None:
    """Recursive JSON rejects invalid numbers, shapes, keys, and collection sizes."""
    with pytest.raises(DomainValidationError, match=message):
        validate_json_value(value, label="Payload")


def test_recursive_json_rejects_values_beyond_depth_limit() -> None:
    """Nested input cannot exceed the explicit recursion-depth budget."""
    value: object = None
    for _ in range(JSON_MAX_DEPTH + 1):
        value = [value]

    with pytest.raises(DomainValidationError, match="depth"):
        validate_json_value(value, label="Payload")
