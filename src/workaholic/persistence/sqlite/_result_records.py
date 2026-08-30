"""Canonical authenticated TaskResult serialization and strict row codecs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    IdempotencyConflictError,
    TaskSubmissionResult,
)
from workaholic.domain import (
    ArtifactReference,
    AttemptId,
    CriterionOutcome,
    CriterionStatus,
    ProposedFollowUp,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    TaskId,
    TaskResult,
)
from workaholic.persistence.sqlite._claim_records import (
    TASK_ATTEMPT_FIELDS,
    TaskAttemptRecord,
    task_attempt_record_from_mapping,
    task_attempt_record_from_row,
    task_attempt_record_mapping,
)
from workaholic.persistence.sqlite._event_records import (
    TaskEventRecord,
    require_persisted_task_event_record,
    task_event_record_from_mapping,
    task_event_record_mapping,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
    canonical_json,
    canonical_json_value,
    parse_json_array,
    parse_json_object,
    parse_optional_timestamp,
    parse_timestamp,
    require_optional_text,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELD_SET,
    task_from_mapping,
    task_mapping,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

TASK_RESULT_FIELDS: Final = (
    "id",
    "task_uid",
    "submitted_by",
    "attempt_id",
    "submitted_at",
    "comment",
    "summary",
    "criteria_json",
    "artifacts_json",
    "proposed_follow_ups_json",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "review_comment",
    "rejection_reason",
)
TASK_RESULT_MAPPING_FIELDS: Final = (
    "id",
    "task_uid",
    "submitted_by",
    "attempt_id",
    "submitted_at",
    "comment",
    "summary",
    "criteria",
    "artifacts",
    "proposed_follow_ups",
    "review",
)
TASK_RESULT_MAPPING_FIELD_SET: Final = frozenset(TASK_RESULT_MAPPING_FIELDS)
_REVIEW_MAPPING_FIELDS: Final = frozenset(
    ("status", "reviewed_by", "reviewed_at", "comment", "reason")
)
_RESULT_OUTCOME_KEYS: Final = frozenset(("attempt", "events", "result", "task"))


def task_result_mapping(result: TaskResult) -> dict[str, object]:
    """Serialize one Result into its exact durable replay shape.

    Args:
        result: Validated Task Result.

    Returns:
        JSON-compatible stable Result mapping.

    Raises:
        StorageUnavailableError: If the runtime value is not a Task Result.

    """
    candidate: object = result
    if not isinstance(candidate, TaskResult):
        raise StorageUnavailableError
    review = candidate.review
    return {
        "artifacts": [
            {
                "media_type": item.media_type,
                "sha256": item.sha256,
                "uri": item.uri,
            }
            for item in candidate.artifacts
        ],
        "attempt_id": (
            None if candidate.attempt_id is None else str(candidate.attempt_id)
        ),
        "comment": candidate.comment,
        "criteria": [
            {
                "criterion_id": item.criterion_id,
                "evidence": item.evidence,
                "status": item.status.value,
            }
            for item in candidate.criteria
        ],
        "id": str(candidate.id),
        "proposed_follow_ups": [
            {"title": item.title} for item in candidate.proposed_follow_ups
        ],
        "review": {
            "comment": review.comment,
            "reason": review.reason,
            "reviewed_at": (
                None
                if review.reviewed_at is None
                else serialize_timestamp(review.reviewed_at)
            ),
            "reviewed_by": (
                None if review.reviewed_by is None else str(review.reviewed_by)
            ),
            "status": review.status.value,
        },
        "submitted_at": serialize_timestamp(candidate.submitted_at),
        "submitted_by": str(candidate.submitted_by),
        "summary": candidate.summary,
        "task_uid": str(candidate.task_uid),
    }


def task_result_row(result: TaskResult) -> tuple[object, ...]:
    """Serialize one Result into exact ``TASK_RESULT_FIELDS`` order.

    Args:
        result: Validated Human or Agent Result.

    Returns:
        SQLite-compatible row values.

    """
    mapping = task_result_mapping(result)
    review = _require_mapping(mapping["review"], fields=_REVIEW_MAPPING_FIELDS)
    return (
        mapping["id"],
        mapping["task_uid"],
        mapping["submitted_by"],
        mapping["attempt_id"],
        mapping["submitted_at"],
        mapping["comment"],
        mapping["summary"],
        canonical_json_value(mapping["criteria"]),
        canonical_json_value(mapping["artifacts"]),
        canonical_json_value(mapping["proposed_follow_ups"]),
        review["status"],
        review["reviewed_by"],
        review["reviewed_at"],
        review["comment"],
        review["reason"],
    )


def task_result_from_mapping(value: Mapping[str, object]) -> TaskResult:
    """Deserialize one exact durable Result mapping.

    Args:
        value: Candidate Result mapping.

    Returns:
        Validated Human or Agent Result.

    Raises:
        StorageUnavailableError: If shape, values, or attribution are invalid.

    """
    candidate: object = value
    if (
        not isinstance(candidate, Mapping)
        or set(candidate) != TASK_RESULT_MAPPING_FIELD_SET
    ):
        raise StorageUnavailableError
    review = _require_mapping(candidate["review"], fields=_REVIEW_MAPPING_FIELDS)
    return _build_result(
        (
            candidate["id"],
            candidate["task_uid"],
            candidate["submitted_by"],
            candidate["attempt_id"],
            candidate["submitted_at"],
            candidate["comment"],
            candidate["summary"],
            candidate["criteria"],
            candidate["artifacts"],
            candidate["proposed_follow_ups"],
            review["status"],
            review["reviewed_by"],
            review["reviewed_at"],
            review["comment"],
            review["reason"],
        ),
        collections_are_json=False,
    )


def task_result_from_row(value: Sequence[object]) -> TaskResult:
    """Deserialize one Result selected in ``TASK_RESULT_FIELDS`` order.

    Args:
        value: SQLite row values in canonical Result field order.

    Returns:
        Validated Human or Agent Result.

    Raises:
        StorageUnavailableError: If row shape or values are malformed.

    """
    candidate: object = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        raise StorageUnavailableError
    if len(candidate) != len(TASK_RESULT_FIELDS):
        raise StorageUnavailableError
    return _build_result(candidate, collections_are_json=True)


def _build_result(
    value: Sequence[object],
    *,
    collections_are_json: bool,
) -> TaskResult:
    """Build one Result from shape-checked ordered values.

    Args:
        value: Ordered Result values.
        collections_are_json: Whether collection values are serialized JSON.

    Returns:
        Validated Human or Agent Result.

    Raises:
        StorageUnavailableError: If any persisted value is invalid.

    """
    try:
        attempt_text = require_optional_text(value[3])
        collections = tuple(
            parse_json_array(
                value[index],
                maximum=STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
            )
            if collections_are_json
            else value[index]
            for index in (7, 8, 9)
        )
        reviewed_by_text = require_optional_text(value[11])
        return TaskResult(
            id=ResultId(require_text(value[0])),
            task_uid=TaskId(require_text(value[1])),
            submitted_by=SubjectId(require_text(value[2])),
            attempt_id=(None if attempt_text is None else AttemptId(attempt_text)),
            submitted_at=parse_timestamp(value[4]),
            comment=require_optional_text(value[5]),
            summary=require_optional_text(value[6]),
            criteria=_criteria_from_sequence(collections[0]),
            artifacts=_artifacts_from_sequence(collections[1]),
            proposed_follow_ups=_follow_ups_from_sequence(collections[2]),
            review=ResultReview(
                status=ResultReviewStatus(require_text(value[10])),
                reviewed_by=(
                    None if reviewed_by_text is None else SubjectId(reviewed_by_text)
                ),
                reviewed_at=parse_optional_timestamp(value[12]),
                comment=require_optional_text(value[13]),
                reason=require_optional_text(value[14]),
            ),
        )
    except (IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _criteria_from_sequence(value: object) -> tuple[CriterionOutcome, ...]:
    """Decode one closed ordered criterion-outcome sequence."""
    items = _require_sequence(value)
    result: list[CriterionOutcome] = []
    fields = frozenset(("criterion_id", "status", "evidence"))
    for item in items:
        mapping = _require_mapping(item, fields=fields)
        result.append(
            CriterionOutcome(
                criterion_id=require_text(mapping["criterion_id"]),
                status=CriterionStatus(require_text(mapping["status"])),
                evidence=require_optional_text(mapping["evidence"]),
            )
        )
    return tuple(result)


def _artifacts_from_sequence(value: object) -> tuple[ArtifactReference, ...]:
    """Decode one closed ordered artifact-reference sequence."""
    items = _require_sequence(value)
    result: list[ArtifactReference] = []
    fields = frozenset(("uri", "media_type", "sha256"))
    for item in items:
        mapping = _require_mapping(item, fields=fields)
        result.append(
            ArtifactReference(
                uri=require_text(mapping["uri"]),
                media_type=require_optional_text(mapping["media_type"]),
                sha256=require_optional_text(mapping["sha256"]),
            )
        )
    return tuple(result)


def _follow_ups_from_sequence(value: object) -> tuple[ProposedFollowUp, ...]:
    """Decode one closed ordered inert follow-up sequence."""
    items = _require_sequence(value)
    result: list[ProposedFollowUp] = []
    for item in items:
        mapping = _require_mapping(item, fields=frozenset(("title",)))
        result.append(ProposedFollowUp(title=require_text(mapping["title"])))
    return tuple(result)


def _require_sequence(value: object) -> Sequence[object]:
    """Require one non-text ordered collection."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise StorageUnavailableError
    return value


def _require_mapping(
    value: object,
    *,
    fields: frozenset[str],
) -> Mapping[str, object]:
    """Require one exact closed string-keyed mapping."""
    if not isinstance(value, Mapping) or set(value) != fields:
        raise StorageUnavailableError
    return value


def read_idempotent_result_outcome(
    connection: sqlite3.Connection,
    *,
    operation: str,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
) -> TaskSubmissionResult | None:
    """Read and validate one historic Result-operation outcome.

    Args:
        connection: Active validated write transaction.
        operation: Closed semantic operation name.
        actor_subject_id: Authenticated Subject scope.
        caller_key: Optional caller-provided idempotency key.
        request_fingerprint: Canonical semantic-input digest.

    Returns:
        Historic outcome when the key exists, otherwise ``None``.

    Raises:
        IdempotencyConflictError: If the key has different semantic input.
        StorageUnavailableError: If replay data or referenced events are invalid.

    """
    if caller_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (actor_subject_id, operation, caller_key),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    result, event_records = parse_result_outcome(require_text(row[1]))
    for event_record in event_records:
        if str(event_record.event.actor_subject_id) != actor_subject_id:
            raise StorageUnavailableError
        require_persisted_task_event_record(connection, expected=event_record)
    if result.attempt is not None:
        actual_attempt = connection.execute(
            f"""
            SELECT {", ".join(TASK_ATTEMPT_FIELDS)}
            FROM task_attempts
            WHERE id = ?
            """,  # noqa: S608 - field names are a closed module constant.
            (str(result.attempt.id),),
        ).fetchone()
        expected_attempt = TaskAttemptRecord(
            project_id=result.task.project_id,
            attempt=result.attempt,
        )
        if (
            actual_attempt is None
            or task_attempt_record_from_row(actual_attempt) != expected_attempt
        ):
            raise StorageUnavailableError
    return result


def record_idempotent_result_outcome(  # noqa: PLR0913 - durable contract.
    connection: sqlite3.Connection,
    *,
    operation: str,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
    occurred_at: datetime,
    result: TaskSubmissionResult,
    event_records: Sequence[TaskEventRecord],
) -> None:
    """Persist one canonical Result replay outcome in its transaction.

    Args:
        connection: Active validated write transaction.
        operation: Closed semantic operation name.
        actor_subject_id: Authenticated Subject scope.
        caller_key: Optional caller-provided idempotency key.
        request_fingerprint: Canonical semantic-input digest.
        occurred_at: Authoritative operation timestamp.
        result: Complete semantic outcome to replay.
        event_records: Durable event records owned by the outcome.

    """
    if caller_key is None:
        return
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor_subject_id,
            operation,
            caller_key,
            request_fingerprint,
            canonical_json(
                {
                    "attempt": (
                        None
                        if result.attempt is None
                        else task_attempt_record_mapping(
                            TaskAttemptRecord(
                                project_id=result.task.project_id,
                                attempt=result.attempt,
                            )
                        )
                    ),
                    "events": [
                        task_event_record_mapping(record) for record in event_records
                    ],
                    "result": task_result_mapping(result.result),
                    "task": task_mapping(result.task),
                }
            ),
            serialize_timestamp(occurred_at),
        ),
    )


def parse_result_outcome(
    value: str,
) -> tuple[TaskSubmissionResult, tuple[TaskEventRecord, ...]]:
    """Parse one exact canonical Result-operation replay outcome.

    Args:
        value: Canonical JSON outcome text.

    Returns:
        Validated semantic outcome and its full event records.

    Raises:
        StorageUnavailableError: If shape, values, or relationships are invalid.

    """
    decoded = parse_json_object(
        value,
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(decoded) != _RESULT_OUTCOME_KEYS:
        raise StorageUnavailableError
    task_value = decoded["task"]
    result_value = decoded["result"]
    events_value = decoded["events"]
    attempt_value = decoded["attempt"]
    if (
        not isinstance(task_value, dict)
        or set(task_value) != TASK_FIELD_SET
        or not isinstance(result_value, dict)
        or not isinstance(events_value, list)
        or (attempt_value is not None and not isinstance(attempt_value, dict))
    ):
        raise StorageUnavailableError
    task = task_from_mapping(cast("Mapping[str, object]", task_value))
    task_result = task_result_from_mapping(cast("Mapping[str, object]", result_value))
    attempt = None
    if isinstance(attempt_value, dict):
        attempt_record = task_attempt_record_from_mapping(
            cast("Mapping[str, object]", attempt_value)
        )
        if attempt_record.project_id != task.project_id:
            raise StorageUnavailableError
        attempt = attempt_record.attempt
    records: list[TaskEventRecord] = []
    for event_value in events_value:
        if not isinstance(event_value, dict):
            raise StorageUnavailableError
        records.append(
            task_event_record_from_mapping(cast("Mapping[str, object]", event_value))
        )
    try:
        outcome = TaskSubmissionResult(
            task=task,
            result=task_result,
            events=tuple(record.event for record in records),
            attempt=attempt,
        )
    except ValueError as error:
        raise StorageUnavailableError from error
    return outcome, tuple(records)
