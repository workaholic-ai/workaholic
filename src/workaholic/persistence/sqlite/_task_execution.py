"""Atomic structured Agent execution operations for SQLite."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ApplicationError,
    IdempotencyConflictError,
    ReportTaskProgressMutation,
    TaskProgressResult,
)
from workaholic.domain import (
    PROGRESS_OBSERVATIONS_MAX_ITEMS,
    DomainValidationError,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskEventType,
)
from workaholic.persistence.sqlite._claim_records import (
    TASK_ATTEMPT_FIELD_SET,
    TASK_CLAIM_MAPPING_FIELD_SET,
    TaskAttemptRecord,
    TaskClaimRecord,
    task_attempt_record_from_mapping,
    task_attempt_record_mapping,
    task_claim_record_from_mapping,
    task_claim_record_mapping,
)
from workaholic.persistence.sqlite._claim_state import (
    StoredClaimState,
    load_claim_state,
    require_current_claim_owner,
)
from workaholic.persistence.sqlite._event_records import (
    TaskEventRecord,
    insert_task_event,
    require_persisted_task_event_record,
    task_event_record_from_mapping,
    task_event_record_mapping,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    canonical_json,
    parse_json_object,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite._task_lifecycle import _load_agent_task
from workaholic.persistence.sqlite._task_records import (
    TASK_FIELD_SET,
    task_from_mapping,
    task_mapping,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from workaholic.domain import JsonValue, TaskProgress

_REPORT_PROGRESS_OPERATION: Final = "task.progress.report"
_PROGRESS_OUTCOME_KEYS: Final = frozenset(("attempt", "claim", "events", "task"))
_MAX_PROGRESS_EVENTS: Final = PROGRESS_OBSERVATIONS_MAX_ITEMS + 1


def report_task_progress(
    database_path: Path,
    mutation: ReportTaskProgressMutation,
) -> TaskProgressResult:
    """Atomically append one attributable structured Agent progress batch.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated current Attempt, progress, and event identities.

    Returns:
        Unchanged Task and ownership snapshots with committed progress events.

    Raises:
        ApplicationError: If authorization, Lease, or idempotency checks fail.
        StorageUnavailableError: If trusted input or storage is malformed.

    """
    candidate: object = mutation
    if type(candidate) is not ReportTaskProgressMutation:
        raise StorageUnavailableError
    fingerprint = _progress_fingerprint(candidate)
    try:
        with open_write_transaction(database_path) as connection:
            task = _load_agent_task(
                connection,
                task_uid=candidate.task_uid,
                project_id=candidate.project_id,
                actor_subject_id=candidate.actor_subject_id,
                actor=candidate.actor,
                occurred_at=candidate.occurred_at,
            )
            replay = _read_idempotent_progress(
                connection,
                actor_subject_id=str(candidate.actor_subject_id),
                caller_key=candidate.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                _require_matching_progress_result(
                    replay,
                    mutation=candidate,
                    fresh=False,
                )
                return replay
            state = require_current_claim_owner(
                load_claim_state(connection, task=task),
                subject_id=candidate.actor_subject_id,
                attempt_id=candidate.attempt_id,
                now=candidate.occurred_at,
            )
            attempt = _require_agent_attempt(state)
            event_records = _append_progress_events(
                connection,
                task=task,
                mutation=candidate,
            )
            result = TaskProgressResult(
                task=task,
                claim=state.claim,
                attempt=attempt,
                events=tuple(record.event for record in event_records),
            )
            _require_matching_progress_result(
                result,
                mutation=candidate,
                fresh=True,
            )
            _record_idempotent_progress(
                connection,
                mutation=candidate,
                request_fingerprint=fingerprint,
                result=result,
                event_records=event_records,
            )
            return result
    except ApplicationError:
        raise
    except StorageUnavailableError:
        raise
    except (DomainValidationError, IndexError, TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _require_agent_attempt(state: StoredClaimState) -> TaskAttempt:
    """Return the Agent Attempt required by a non-null owner token.

    Args:
        state: Current stored Agent Claim state.

    Returns:
        Active Attempt paired with the Claim.

    Raises:
        StorageUnavailableError: If persistence returns Human Claim state.

    """
    if state.attempt is None:
        raise StorageUnavailableError
    return state.attempt


def _append_progress_events(
    connection: sqlite3.Connection,
    *,
    task: Task,
    mutation: ReportTaskProgressMutation,
) -> tuple[TaskEventRecord, ...]:
    """Append the progress header and ordered observation events.

    Args:
        connection: Active schema-validated write transaction.
        task: Unchanged Task receiving the progress events.
        mutation: Validated progress and owned event identities.

    Returns:
        Persisted records in semantic input order.

    Raises:
        StorageUnavailableError: If the Task input is malformed.

    """
    if not isinstance(task, Task):
        raise StorageUnavailableError
    records = [
        insert_task_event(
            connection,
            event_id=mutation.progress_reported_event_id,
            task=task,
            actor_subject_id=mutation.actor_subject_id,
            request_id=mutation.request_id,
            event_type=TaskEventType.PROGRESS_REPORTED,
            occurred_at=mutation.occurred_at,
            payload=_progress_event_payload(mutation.progress),
            attempt_id=mutation.attempt_id,
            actor_kind=(
                SubjectKind.HUMAN
                if mutation.actor is None
                else mutation.actor.subject_kind
            ),
        )
    ]
    for event_id, observation in zip(
        mutation.observation_event_ids,
        mutation.progress.observations or (),
        strict=True,
    ):
        records.append(
            insert_task_event(
                connection,
                event_id=event_id,
                task=task,
                actor_subject_id=mutation.actor_subject_id,
                request_id=mutation.request_id,
                event_type=TaskEventType.OBSERVATION_ADDED,
                occurred_at=mutation.occurred_at,
                payload={"kind": observation.kind.value, "text": observation.text},
                attempt_id=mutation.attempt_id,
                actor_kind=(
                    SubjectKind.HUMAN
                    if mutation.actor is None
                    else mutation.actor.subject_kind
                ),
            )
        )
    return tuple(records)


def _progress_fingerprint(mutation: ReportTaskProgressMutation) -> str:
    """Hash exact caller-controlled progress semantics.

    Args:
        mutation: Validated structured progress mutation.

    Returns:
        Lowercase SHA-256 digest excluding generated identities and time.

    """
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "attempt_id": str(mutation.attempt_id),
            "progress": _progress_mapping(mutation.progress),
            "project_id": str(mutation.project_id),
            "task_uid": str(mutation.task_uid),
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _progress_mapping(progress: TaskProgress) -> dict[str, object]:
    """Serialize complete structured progress for fingerprinting.

    Args:
        progress: Validated immutable progress input.

    Returns:
        Canonical JSON-compatible mapping that preserves nullable collections.

    """
    return {
        "message": progress.message,
        "observations": (
            None
            if progress.observations is None
            else [
                {"kind": observation.kind.value, "text": observation.text}
                for observation in progress.observations
            ]
        ),
        "percent_complete": progress.percent_complete,
    }


def _progress_event_payload(progress: TaskProgress) -> dict[str, JsonValue]:
    """Build the closed progress header payload without observation data.

    Args:
        progress: Validated immutable progress input.

    Returns:
        Only supplied message and percentage fields.

    """
    payload: dict[str, JsonValue] = {}
    if progress.message is not None:
        payload["message"] = progress.message
    if progress.percent_complete is not None:
        payload["percent_complete"] = progress.percent_complete
    return payload


def _expected_event_payloads(
    progress: TaskProgress,
) -> tuple[dict[str, JsonValue], ...]:
    """Return exact progress and ordered observation event payloads.

    Args:
        progress: Validated immutable progress input.

    Returns:
        Header payload followed by one payload per ordered observation.

    """
    observations = progress.observations or ()
    payloads: list[dict[str, JsonValue]] = [_progress_event_payload(progress)]
    payloads.extend(
        {"kind": observation.kind.value, "text": observation.text}
        for observation in observations
    )
    return tuple(payloads)


def _read_idempotent_progress(
    connection: sqlite3.Connection,
    *,
    actor_subject_id: str,
    caller_key: str | None,
    request_fingerprint: str,
) -> TaskProgressResult | None:
    """Return an exact progress replay or reject conflicting key reuse.

    Args:
        connection: Active schema-validated write transaction.
        actor_subject_id: Authenticated idempotency scope.
        caller_key: Optional caller-controlled replay key.
        request_fingerprint: Canonical semantic request digest.

    Returns:
        Exact historic outcome, or ``None`` for a new or unkeyed request.

    Raises:
        IdempotencyConflictError: If the key names different semantics.
        StorageUnavailableError: If the durable outcome or events differ.

    """
    if caller_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (actor_subject_id, _REPORT_PROGRESS_OPERATION, caller_key),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    result, event_records = _parse_progress_outcome(require_text(row[1]))
    if result.claim.subject_id.value != actor_subject_id:
        raise StorageUnavailableError
    for event_record in event_records:
        require_persisted_task_event_record(connection, expected=event_record)
    return result


def _record_idempotent_progress(
    connection: sqlite3.Connection,
    *,
    mutation: ReportTaskProgressMutation,
    request_fingerprint: str,
    result: TaskProgressResult,
    event_records: Sequence[TaskEventRecord],
) -> None:
    """Persist one closed progress result in its owning transaction.

    Args:
        connection: Active schema-validated write transaction.
        mutation: Validated keyed progress mutation.
        request_fingerprint: Canonical semantic request digest.
        result: Closed committed progress outcome.
        event_records: Exact durable event records in semantic order.

    """
    if mutation.idempotency_key is None:
        return
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(mutation.actor_subject_id),
            _REPORT_PROGRESS_OPERATION,
            mutation.idempotency_key,
            request_fingerprint,
            canonical_json(
                {
                    "attempt": task_attempt_record_mapping(
                        TaskAttemptRecord(
                            project_id=result.task.project_id,
                            attempt=result.attempt,
                        )
                    ),
                    "claim": task_claim_record_mapping(
                        TaskClaimRecord(
                            project_id=result.task.project_id,
                            claim=result.claim,
                        )
                    ),
                    "events": [
                        task_event_record_mapping(record) for record in event_records
                    ],
                    "task": task_mapping(result.task),
                }
            ),
            serialize_timestamp(mutation.occurred_at),
        ),
    )


def _parse_progress_outcome(
    value: str,
) -> tuple[TaskProgressResult, tuple[TaskEventRecord, ...]]:
    """Parse and validate one exact durable progress replay outcome.

    Args:
        value: Canonical JSON outcome from idempotency storage.

    Returns:
        Validated result and its attribution-preserving event records.

    Raises:
        StorageUnavailableError: If shape, values, or relationships are invalid.

    """
    decoded = parse_json_object(
        value,
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(decoded) != _PROGRESS_OUTCOME_KEYS:
        raise StorageUnavailableError
    task_value = decoded["task"]
    claim_value = decoded["claim"]
    attempt_value = decoded["attempt"]
    events_value = decoded["events"]
    if (
        not isinstance(task_value, dict)
        or set(task_value) != TASK_FIELD_SET
        or not isinstance(claim_value, dict)
        or set(claim_value) != TASK_CLAIM_MAPPING_FIELD_SET
        or not isinstance(attempt_value, dict)
        or set(attempt_value) != TASK_ATTEMPT_FIELD_SET
        or not isinstance(events_value, list)
        or not 1 <= len(events_value) <= _MAX_PROGRESS_EVENTS
        or any(not isinstance(item, dict) for item in events_value)
    ):
        raise StorageUnavailableError
    task = task_from_mapping(cast("Mapping[str, object]", task_value))
    claim_record = task_claim_record_from_mapping(
        cast("Mapping[str, object]", claim_value)
    )
    attempt_record = task_attempt_record_from_mapping(
        cast("Mapping[str, object]", attempt_value)
    )
    event_records = tuple(
        task_event_record_from_mapping(cast("Mapping[str, object]", item))
        for item in events_value
    )
    if (
        claim_record.project_id != task.project_id
        or attempt_record.project_id != task.project_id
    ):
        raise StorageUnavailableError
    try:
        result = TaskProgressResult(
            task=task,
            claim=claim_record.claim,
            attempt=attempt_record.attempt,
            events=tuple(record.event for record in event_records),
        )
    except ValueError as error:
        raise StorageUnavailableError from error
    return result, event_records


def _require_matching_progress_result(
    result: TaskProgressResult,
    *,
    mutation: ReportTaskProgressMutation,
    fresh: bool,
) -> None:
    """Validate a fresh or replayed result against semantic progress input.

    Args:
        result: Closed fresh or historic repository outcome.
        mutation: Current equivalent structured progress mutation.
        fresh: Whether generated identities and request time must match.

    Raises:
        StorageUnavailableError: If durable semantics differ from the mutation.

    """
    expected_ids = (
        mutation.progress_reported_event_id,
        *mutation.observation_event_ids,
    )
    expected_types = (
        TaskEventType.PROGRESS_REPORTED,
        *(TaskEventType.OBSERVATION_ADDED for _item in mutation.observation_event_ids),
    )
    expected_payloads = _expected_event_payloads(mutation.progress)
    if (
        result.task.uid != mutation.task_uid
        or result.task.project_id != mutation.project_id
        or result.claim.subject_id != mutation.actor_subject_id
        or result.claim.attempt_id != mutation.attempt_id
        or result.attempt.id != mutation.attempt_id
        or len(result.events) != len(expected_types)
        or tuple(event.event_type for event in result.events) != expected_types
        or tuple(dict(event.payload) for event in result.events) != expected_payloads
    ):
        raise StorageUnavailableError
    if fresh and (
        tuple(event.id for event in result.events) != expected_ids
        or any(
            event.request_id != mutation.request_id
            or event.occurred_at != mutation.occurred_at
            for event in result.events
        )
    ):
        raise StorageUnavailableError
