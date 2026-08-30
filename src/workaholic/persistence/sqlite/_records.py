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
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    Token,
    TokenId,
    TokenStatus,
    TokenSummary,
    derive_token_status,
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
PROJECT_GRANT_FIELDS = (
    "instance_id",
    "subject_id",
    "project_id",
    "role",
    "version",
    "granted_by",
    "created_at",
    "updated_at",
)
PROJECT_GRANT_FIELD_SET = frozenset(PROJECT_GRANT_FIELDS)
SUBJECT_FIELDS = (
    "id",
    "instance_id",
    "kind",
    "handle",
    "display_name",
    "enabled",
    "is_instance_admin",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)
SUBJECT_FIELD_SET = frozenset(SUBJECT_FIELDS)
TOKEN_FIELDS = (
    "id",
    "instance_id",
    "subject_id",
    "token_hash",
    "created_by",
    "created_at",
    "activated_at",
    "expires_at",
    "revoked_at",
    "revoked_by",
)
TOKEN_SUMMARY_FIELDS = (
    "id",
    "subject_id",
    "status",
    "created_by",
    "created_at",
    "activated_at",
    "expires_at",
    "revoked_at",
    "revoked_by",
)
TOKEN_SUMMARY_FIELD_SET = frozenset(TOKEN_SUMMARY_FIELDS)


def project_grant_to_mapping(value: ProjectGrant) -> dict[str, object]:
    """Serialize one ProjectGrant into canonical durable fields.

    Args:
        value: Validated cumulative Project grant.

    Returns:
        New exact mapping suitable for safe idempotency outcomes.

    Raises:
        StorageUnavailableError: If the runtime value is malformed.

    """
    candidate: object = value
    if not isinstance(candidate, ProjectGrant):
        raise StorageUnavailableError
    return {
        "instance_id": str(candidate.instance_id),
        "subject_id": str(candidate.subject_id),
        "project_id": str(candidate.project_id),
        "role": candidate.role.value,
        "version": candidate.version,
        "granted_by": str(candidate.granted_by),
        "created_at": serialize_timestamp(candidate.created_at),
        "updated_at": serialize_timestamp(candidate.updated_at),
    }


def project_grant_from_mapping(value: Mapping[str, object]) -> ProjectGrant:
    """Deserialize one exact canonical ProjectGrant mapping.

    Args:
        value: Candidate grant fields.

    Returns:
        Validated immutable ProjectGrant.

    Raises:
        StorageUnavailableError: If the mapping is malformed or non-exact.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != PROJECT_GRANT_FIELD_SET:
        raise StorageUnavailableError
    return _build_project_grant(
        tuple(candidate[field] for field in PROJECT_GRANT_FIELDS)
    )


def project_grant_from_row(value: Sequence[object]) -> ProjectGrant:
    """Deserialize one ProjectGrant selected in canonical field order.

    Args:
        value: SQLite row in ``PROJECT_GRANT_FIELDS`` order.

    Returns:
        Validated immutable ProjectGrant.

    Raises:
        StorageUnavailableError: If the row is malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(PROJECT_GRANT_FIELDS):
        raise StorageUnavailableError
    return _build_project_grant(candidate)


def _build_project_grant(value: Sequence[object]) -> ProjectGrant:
    """Build one ProjectGrant from shape-checked persisted values."""
    try:
        return ProjectGrant(
            instance_id=InstanceId(require_text(value[0])),
            subject_id=SubjectId(require_text(value[1])),
            project_id=ProjectId(require_text(value[2])),
            role=ProjectRole(require_text(value[3])),
            version=require_integer(value[4]),
            granted_by=SubjectId(require_text(value[5])),
            created_at=parse_timestamp(value[6]),
            updated_at=parse_timestamp(value[7]),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def token_from_row(value: Sequence[object]) -> Token:
    """Deserialize one Token selected in ``TOKEN_FIELDS`` order.

    Args:
        value: SQLite row values in canonical Token field order.

    Returns:
        Validated immutable Token with its digest kept private.

    Raises:
        StorageUnavailableError: If the row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TOKEN_FIELDS):
        raise StorageUnavailableError
    try:
        return Token(
            id=TokenId(require_text(candidate[0])),
            instance_id=InstanceId(require_text(candidate[1])),
            subject_id=SubjectId(require_text(candidate[2])),
            token_hash=require_text(candidate[3]),
            created_by=SubjectId(require_text(candidate[4])),
            created_at=parse_timestamp(candidate[5]),
            activated_at=parse_optional_timestamp(candidate[6]),
            expires_at=parse_timestamp(candidate[7]),
            revoked_at=parse_optional_timestamp(candidate[8]),
            revoked_by=(
                None if candidate[9] is None else SubjectId(require_text(candidate[9]))
            ),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def token_to_summary(value: Token, *, now: datetime) -> TokenSummary:
    """Project one persisted Token into non-secret metadata at a fixed time.

    Args:
        value: Validated persisted Token.
        now: Authoritative UTC projection time.

    Returns:
        Closed public Token lifecycle summary.

    Raises:
        StorageUnavailableError: If runtime inputs violate their contracts.

    """
    candidate_token: object = value
    candidate_now: object = now
    if not isinstance(candidate_token, Token) or not isinstance(
        candidate_now,
        datetime,
    ):
        raise StorageUnavailableError
    try:
        status = derive_token_status(candidate_token, now=candidate_now)
        return TokenSummary(
            id=candidate_token.id,
            subject_id=candidate_token.subject_id,
            status=status,
            created_by=candidate_token.created_by,
            created_at=candidate_token.created_at,
            activated_at=candidate_token.activated_at,
            expires_at=candidate_token.expires_at,
            revoked_at=candidate_token.revoked_at,
            revoked_by=candidate_token.revoked_by,
        )
    except (DomainValidationError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def token_summary_to_mapping(value: TokenSummary) -> dict[str, object]:
    """Serialize one non-secret Token summary into canonical durable fields.

    Args:
        value: Token metadata snapshot to serialize.

    Returns:
        New mapping without a raw Token or digest.

    Raises:
        StorageUnavailableError: If the runtime value is malformed.

    """
    candidate: object = value
    if not isinstance(candidate, TokenSummary):
        raise StorageUnavailableError
    return {
        "id": str(candidate.id),
        "subject_id": str(candidate.subject_id),
        "status": candidate.status.value,
        "created_by": str(candidate.created_by),
        "created_at": serialize_timestamp(candidate.created_at),
        "activated_at": (
            None
            if candidate.activated_at is None
            else serialize_timestamp(candidate.activated_at)
        ),
        "expires_at": serialize_timestamp(candidate.expires_at),
        "revoked_at": (
            None
            if candidate.revoked_at is None
            else serialize_timestamp(candidate.revoked_at)
        ),
        "revoked_by": (
            None if candidate.revoked_by is None else str(candidate.revoked_by)
        ),
    }


def token_summary_from_mapping(value: Mapping[str, object]) -> TokenSummary:
    """Deserialize one exact non-secret Token summary mapping.

    Args:
        value: Candidate canonical metadata fields.

    Returns:
        Validated immutable Token summary.

    Raises:
        StorageUnavailableError: If the mapping is malformed or non-exact.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != TOKEN_SUMMARY_FIELD_SET:
        raise StorageUnavailableError
    try:
        return TokenSummary(
            id=TokenId(require_text(candidate["id"])),
            subject_id=SubjectId(require_text(candidate["subject_id"])),
            status=TokenStatus(require_text(candidate["status"])),
            created_by=SubjectId(require_text(candidate["created_by"])),
            created_at=parse_timestamp(candidate["created_at"]),
            activated_at=parse_optional_timestamp(candidate["activated_at"]),
            expires_at=parse_timestamp(candidate["expires_at"]),
            revoked_at=parse_optional_timestamp(candidate["revoked_at"]),
            revoked_by=(
                None
                if candidate["revoked_by"] is None
                else SubjectId(require_text(candidate["revoked_by"]))
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def subject_to_mapping(value: Subject) -> dict[str, object]:
    """Serialize one validated Subject into canonical durable fields.

    Args:
        value: Subject to serialize.

    Returns:
        New mapping in canonical Subject field order.

    Raises:
        StorageUnavailableError: If the runtime value is not a Subject.

    """
    candidate: object = value
    if not isinstance(candidate, Subject):
        raise StorageUnavailableError
    return {
        "id": str(candidate.id),
        "instance_id": str(candidate.instance_id),
        "kind": candidate.kind.value,
        "handle": candidate.handle,
        "display_name": candidate.display_name,
        "enabled": candidate.enabled,
        "is_instance_admin": candidate.is_instance_admin,
        "version": candidate.version,
        "created_by": str(candidate.created_by),
        "created_at": serialize_timestamp(candidate.created_at),
        "updated_at": serialize_timestamp(candidate.updated_at),
    }


def subject_from_mapping(value: Mapping[str, object]) -> Subject:
    """Deserialize one exact canonical Subject mapping.

    Args:
        value: Candidate persisted Subject fields.

    Returns:
        Validated immutable Subject.

    Raises:
        StorageUnavailableError: If the mapping shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != SUBJECT_FIELD_SET:
        raise StorageUnavailableError
    ordered = [candidate[field] for field in SUBJECT_FIELDS]
    if type(ordered[5]) is not bool or type(ordered[6]) is not bool:
        raise StorageUnavailableError
    ordered[5] = int(ordered[5])
    ordered[6] = int(ordered[6])
    return _build_subject(tuple(ordered))


def subject_from_row(value: Sequence[object]) -> Subject:
    """Deserialize one Subject selected in ``SUBJECT_FIELDS`` order.

    Args:
        value: SQLite row values in canonical Subject field order.

    Returns:
        Validated immutable Subject.

    Raises:
        StorageUnavailableError: If the row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(SUBJECT_FIELDS):
        raise StorageUnavailableError
    return _build_subject(candidate)


def _build_subject(value: Sequence[object]) -> Subject:
    """Build one Subject from a shape-checked value sequence.

    Args:
        value: Ordered persisted Subject values.

    Returns:
        Validated immutable Subject.

    Raises:
        StorageUnavailableError: If any value violates the Subject contract.

    """
    try:
        persisted_display_name = require_text(value[4])
        subject = Subject(
            id=SubjectId(require_text(value[0])),
            instance_id=InstanceId(require_text(value[1])),
            kind=SubjectKind(require_text(value[2])),
            handle=require_text(value[3]),
            display_name=persisted_display_name,
            enabled=require_boolean(value[5]),
            is_instance_admin=require_boolean(value[6]),
            version=require_integer(value[7]),
            created_by=SubjectId(require_text(value[8])),
            created_at=parse_timestamp(value[9]),
            updated_at=parse_timestamp(value[10]),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error
    if subject.display_name != persisted_display_name:
        raise StorageUnavailableError
    return subject


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
