"""Canonical TaskClaim and authenticated Agent TaskAttempt record codecs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    ProjectId,
    SubjectId,
    TaskAttempt,
    TaskClaim,
    TaskId,
)
from workaholic.persistence.sqlite._records import (
    parse_optional_timestamp,
    parse_timestamp,
    require_optional_text,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

TASK_ATTEMPT_FIELDS: Final = (
    "id",
    "task_uid",
    "project_id",
    "subject_id",
    "status",
    "started_at",
    "ended_at",
    "lease_expires_at",
)
TASK_ATTEMPT_FIELD_SET: Final = frozenset(TASK_ATTEMPT_FIELDS)
TASK_CLAIM_FIELDS: Final = (
    "task_uid",
    "project_id",
    "subject_id",
    "attempt_id",
    "claimed_at",
    "lease_expires_at",
)
TASK_CLAIM_MAPPING_FIELDS: Final = (
    "task_uid",
    "task_key",
    "project_id",
    "subject_id",
    "attempt_id",
    "claimed_at",
    "lease_expires_at",
)
TASK_CLAIM_MAPPING_FIELD_SET: Final = frozenset(TASK_CLAIM_MAPPING_FIELDS)


@dataclass(frozen=True, slots=True)
class TaskAttemptRecord:
    """One Agent Attempt plus its durable Project ownership dimension."""

    project_id: ProjectId
    attempt: TaskAttempt

    def __post_init__(self) -> None:
        """Validate the exact runtime record types."""
        project_id: object = self.project_id
        attempt: object = self.attempt
        if not isinstance(project_id, ProjectId) or not isinstance(
            attempt, TaskAttempt
        ):
            raise StorageUnavailableError


@dataclass(frozen=True, slots=True)
class TaskClaimRecord:
    """One current Claim plus its durable Project ownership dimension."""

    project_id: ProjectId
    claim: TaskClaim

    def __post_init__(self) -> None:
        """Validate the exact runtime record types."""
        project_id: object = self.project_id
        claim: object = self.claim
        if not isinstance(project_id, ProjectId) or not isinstance(claim, TaskClaim):
            raise StorageUnavailableError


def task_attempt_record_mapping(record: TaskAttemptRecord) -> dict[str, object]:
    """Serialize one Attempt record into its exact durable mapping.

    Args:
        record: Validated Attempt and Project record.

    Returns:
        New mapping in canonical Attempt field order.

    Raises:
        StorageUnavailableError: If the runtime value is not an Attempt record.

    """
    candidate: object = record
    if not isinstance(candidate, TaskAttemptRecord):
        raise StorageUnavailableError
    attempt = candidate.attempt
    return {
        "id": str(attempt.id),
        "task_uid": str(attempt.task_uid),
        "project_id": str(candidate.project_id),
        "subject_id": str(attempt.subject_id),
        "status": attempt.status.value,
        "started_at": serialize_timestamp(attempt.started_at),
        "ended_at": (
            None if attempt.ended_at is None else serialize_timestamp(attempt.ended_at)
        ),
        "lease_expires_at": serialize_timestamp(attempt.lease_expires_at),
    }


def task_attempt_row(record: TaskAttemptRecord) -> tuple[object, ...]:
    """Serialize one Attempt into exact ``TASK_ATTEMPT_FIELDS`` order.

    Args:
        record: Validated Attempt and Project record.

    Returns:
        SQLite-compatible Attempt row values.

    """
    mapping = task_attempt_record_mapping(record)
    return tuple(mapping[field] for field in TASK_ATTEMPT_FIELDS)


def task_attempt_record_from_mapping(
    value: Mapping[str, object],
) -> TaskAttemptRecord:
    """Deserialize one exact durable Attempt mapping.

    Args:
        value: Candidate persisted Attempt fields.

    Returns:
        Validated immutable Attempt record.

    Raises:
        StorageUnavailableError: If the mapping shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Mapping) or set(candidate) != TASK_ATTEMPT_FIELD_SET:
        raise StorageUnavailableError
    return _build_attempt_record(
        tuple(candidate[field] for field in TASK_ATTEMPT_FIELDS)
    )


def task_attempt_record_from_row(value: Sequence[object]) -> TaskAttemptRecord:
    """Deserialize one Attempt selected in canonical field order.

    Args:
        value: SQLite row values in ``TASK_ATTEMPT_FIELDS`` order.

    Returns:
        Validated immutable Attempt record.

    Raises:
        StorageUnavailableError: If the row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TASK_ATTEMPT_FIELDS):
        raise StorageUnavailableError
    return _build_attempt_record(candidate)


def _build_attempt_record(value: Sequence[object]) -> TaskAttemptRecord:
    """Build one Attempt record from shape-checked ordered values."""
    try:
        return TaskAttemptRecord(
            project_id=ProjectId(require_text(value[2])),
            attempt=TaskAttempt(
                id=AttemptId(require_text(value[0])),
                task_uid=TaskId(require_text(value[1])),
                subject_id=SubjectId(require_text(value[3])),
                status=AttemptStatus(require_text(value[4])),
                started_at=parse_timestamp(value[5]),
                ended_at=parse_optional_timestamp(value[6]),
                lease_expires_at=parse_timestamp(value[7]),
            ),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def task_claim_record_mapping(record: TaskClaimRecord) -> dict[str, object]:
    """Serialize one Claim record including its stable Human Task key.

    Args:
        record: Validated current Claim and Project record.

    Returns:
        New mapping suitable for durable outcomes.

    Raises:
        StorageUnavailableError: If the runtime value is not a Claim record.

    """
    candidate: object = record
    if not isinstance(candidate, TaskClaimRecord):
        raise StorageUnavailableError
    claim = candidate.claim
    return {
        "task_uid": str(claim.task_uid),
        "task_key": claim.task_key,
        "project_id": str(candidate.project_id),
        "subject_id": str(claim.subject_id),
        "attempt_id": (None if claim.attempt_id is None else str(claim.attempt_id)),
        "claimed_at": serialize_timestamp(claim.claimed_at),
        "lease_expires_at": serialize_timestamp(claim.lease_expires_at),
    }


def task_claim_row(record: TaskClaimRecord) -> tuple[object, ...]:
    """Serialize one Claim into exact ``TASK_CLAIM_FIELDS`` order.

    Args:
        record: Validated current Claim and Project record.

    Returns:
        SQLite-compatible Claim row values without the derivable Task key.

    """
    mapping = task_claim_record_mapping(record)
    return tuple(mapping[field] for field in TASK_CLAIM_FIELDS)


def task_claim_record_from_mapping(
    value: Mapping[str, object],
) -> TaskClaimRecord:
    """Deserialize one exact durable Claim outcome mapping.

    Args:
        value: Candidate persisted Claim fields including stable Task key.

    Returns:
        Validated immutable Claim record.

    Raises:
        StorageUnavailableError: If the mapping shape or values are malformed.

    """
    candidate: object = value
    if (
        not isinstance(candidate, Mapping)
        or set(candidate) != TASK_CLAIM_MAPPING_FIELD_SET
    ):
        raise StorageUnavailableError
    return _build_claim_record(
        tuple(candidate[field] for field in TASK_CLAIM_FIELDS),
        task_key=candidate["task_key"],
    )


def task_claim_record_from_row(
    value: Sequence[object],
    *,
    task_key: object,
) -> TaskClaimRecord:
    """Deserialize one Claim row using its joined stable Task key.

    Args:
        value: SQLite row values in ``TASK_CLAIM_FIELDS`` order.
        task_key: Stable Task key selected from the owning Task.

    Returns:
        Validated immutable Claim record.

    Raises:
        StorageUnavailableError: If shape, values, or Task key are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TASK_CLAIM_FIELDS):
        raise StorageUnavailableError
    return _build_claim_record(candidate, task_key=task_key)


def _build_claim_record(
    value: Sequence[object],
    *,
    task_key: object,
) -> TaskClaimRecord:
    """Build one Claim record from shape-checked ordered values."""
    try:
        attempt_text = require_optional_text(value[3])
        return TaskClaimRecord(
            project_id=ProjectId(require_text(value[1])),
            claim=TaskClaim(
                task_uid=TaskId(require_text(value[0])),
                task_key=require_text(task_key),
                subject_id=SubjectId(require_text(value[2])),
                attempt_id=(None if attempt_text is None else AttemptId(attempt_text)),
                claimed_at=parse_timestamp(value[4]),
                lease_expires_at=parse_timestamp(value[5]),
            ),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error
