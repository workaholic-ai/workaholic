"""Unit tests for defensive Phase 4 SQLite Claim helper boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast

import pytest

from workaholic.application import (
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    TaskClaimResult,
)
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    DomainValidationError,
    ProjectId,
    RequestId,
    SubjectId,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite._claim_records import (
    TaskAttemptRecord,
    TaskClaimRecord,
    task_attempt_record_from_row,
    task_attempt_record_mapping,
    task_claim_record_from_row,
    task_claim_record_mapping,
)
from workaholic.persistence.sqlite._claim_state import (
    StoredClaimState,
    current_claim_state,
    load_claim_state,
    load_claim_states,
)
from workaholic.persistence.sqlite._event_records import (
    TaskEventRecord,
    insert_task_event,
    task_event_record_from_mapping,
    task_event_record_from_row,
    task_event_record_mapping,
)
from workaholic.persistence.sqlite._records import canonical_json, serialize_timestamp
from workaholic.persistence.sqlite._task_claims import (
    _execute_claim,
    _materialize_expired_claim,
    _parse_claim_outcome,
    _require_matching_claim_result,
    claim_task,
)
from workaholic.persistence.sqlite._task_records import task_mapping
from workaholic.persistence.sqlite.errors import StorageUnavailableError
from workaholic.persistence.sqlite.repository import SQLiteRepository

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from workaholic.application import Clock
    from workaholic.domain import JsonValue

_TASK_TIME = datetime(2026, 8, 20, 9, tzinfo=UTC)
_NOW = datetime(2026, 8, 20, 10, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_claims")
_SUBJECT_ID = SubjectId("sub_local")


class _InvalidCursor:
    """Expose one invalid SQLite cursor identity."""

    lastrowid = True


class _InvalidCursorConnection:
    """Accept an event insert but return an invalid allocated cursor."""

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> _InvalidCursor:
        """Return a cursor whose boolean row identity fails closed."""
        assert statement
        assert parameters
        return _InvalidCursor()


class _RowCountCursor:
    """Expose one controlled SQLite mutation count."""

    def __init__(self, rowcount: int) -> None:
        """Store the controlled affected-row count."""
        self.rowcount = rowcount


class _RowCountConnection:
    """Return controlled row counts for defensive mutation tests."""

    def __init__(self, rowcounts: list[int]) -> None:
        """Store ordered mutation counts."""
        self._rowcounts = iter(rowcounts)

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> _RowCountCursor:
        """Return the next controlled affected-row count."""
        assert statement
        assert parameters
        return _RowCountCursor(next(self._rowcounts))


class _RowsCursor:
    """Expose controlled SQLite query rows."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        """Store rows returned by ``fetchall``."""
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return a copy of the controlled rows."""
        return list(self._rows)


class _RowsConnection:
    """Return controlled rows for Claim hydration corruption tests."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        """Store the query rows."""
        self._rows = rows

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> _RowsCursor:
        """Return the controlled query cursor."""
        assert statement
        assert parameters
        return _RowsCursor(self._rows)


def _task(*, project_id: ProjectId = _PROJECT_ID) -> Task:
    """Build one complete ready Task fixture."""
    return Task(
        uid=TaskId("tsk_claim"),
        project_id=project_id,
        number=1,
        key="ACME-1",
        title="Claim me",
        objective="Exercise the Claim helper contracts.",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=_SUBJECT_ID,
        created_at=_TASK_TIME,
        updated_at=_TASK_TIME,
    )


def _human_claim() -> TaskClaim:
    """Build one current Human Claim fixture."""
    return TaskClaim(
        task_uid=TaskId("tsk_claim"),
        task_key="ACME-1",
        subject_id=_SUBJECT_ID,
        attempt_id=None,
        claimed_at=_NOW,
        lease_expires_at=_NOW + timedelta(hours=8),
    )


def _agent_attempt() -> TaskAttempt:
    """Build one active Agent Attempt fixture."""
    return TaskAttempt(
        id=AttemptId("atm_claim"),
        task_uid=TaskId("tsk_claim"),
        subject_id=_SUBJECT_ID,
        status=AttemptStatus.ACTIVE,
        started_at=_NOW,
        ended_at=None,
        lease_expires_at=_NOW + timedelta(minutes=15),
    )


def _agent_claim() -> TaskClaim:
    """Build the Claim paired with the active Agent Attempt fixture."""
    attempt = _agent_attempt()
    return TaskClaim(
        task_uid=attempt.task_uid,
        task_key="ACME-1",
        subject_id=attempt.subject_id,
        attempt_id=attempt.id,
        claimed_at=attempt.started_at,
        lease_expires_at=attempt.lease_expires_at,
    )


def _event(
    event_type: TaskEventType,
    *,
    cursor: int,
    attempt_id: AttemptId | None,
    payload: Mapping[str, JsonValue],
) -> TaskEvent:
    """Build one attributable Claim event fixture."""
    return TaskEvent(
        id=TaskEventId(f"evt_{event_type.value}_{cursor}"),
        cursor=cursor,
        task_uid=TaskId("tsk_claim"),
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_claim"),
        event_type=event_type,
        occurred_at=_NOW,
        payload=payload,
        attempt_id=attempt_id,
    )


def _record(event: TaskEvent) -> TaskEventRecord:
    """Wrap one Phase 4 event with its persisted Human actor snapshot."""
    return TaskEventRecord(
        event=event,
        actor_kind=SubjectKind.HUMAN,
        attempt_id=event.attempt_id,
    )


def _human_mutation() -> ClaimTaskMutation:
    """Build one semantic Human Claim mutation fixture."""
    return ClaimTaskMutation(
        project_id=_PROJECT_ID,
        task_uid=TaskId("tsk_claim"),
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_candidate"),
        occurred_at=_NOW,
        lease_duration_seconds=28_800,
        task_claimed_event_id=TaskEventId("evt_candidate_claimed"),
        claim_expired_event_id=TaskEventId("evt_candidate_expired"),
    )


def _agent_mutation() -> ClaimNextTaskMutation:
    """Build one semantic Agent Claim mutation fixture."""
    return ClaimNextTaskMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_candidate"),
        occurred_at=_NOW,
        attempt_id=AttemptId("atm_candidate"),
        lease_duration_seconds=900,
        task_claimed_event_id=TaskEventId("evt_candidate_claimed"),
        claim_expired_event_id=TaskEventId("evt_candidate_expired"),
    )


def _claim_event(claim: TaskClaim, *, cursor: int = 2) -> TaskEvent:
    """Build the exact task-claimed event for a Claim fixture."""
    return _event(
        TaskEventType.TASK_CLAIMED,
        cursor=cursor,
        attempt_id=claim.attempt_id,
        payload={"lease_expires_at": serialize_timestamp(claim.lease_expires_at)},
    )


def _outcome_mapping(*, agent: bool) -> dict[str, object]:
    """Build one exact durable Claim idempotency outcome mapping."""
    task = _task()
    claim = _agent_claim() if agent else _human_claim()
    attempt = _agent_attempt() if agent else None
    event_record = _record(_claim_event(claim))
    return {
        "attempt": (
            None
            if attempt is None
            else task_attempt_record_mapping(
                TaskAttemptRecord(project_id=_PROJECT_ID, attempt=attempt)
            )
        ),
        "claim": task_claim_record_mapping(
            TaskClaimRecord(project_id=_PROJECT_ID, claim=claim)
        ),
        "events": [task_event_record_mapping(event_record)],
        "task": task_mapping(task),
    }


def test_claim_state_validators_reject_untrusted_runtime_inputs() -> None:
    """Claim hydration helpers do not trust type hints or inconsistent pairs."""
    claim = _human_claim()
    attempt = _agent_attempt()
    with pytest.raises(StorageUnavailableError):
        StoredClaimState(
            project_id=cast("ProjectId", object()),
            claim=claim,
            attempt=None,
        )
    with pytest.raises(StorageUnavailableError):
        StoredClaimState(
            project_id=_PROJECT_ID,
            claim=claim,
            attempt=cast("TaskAttempt", object()),
        )
    with pytest.raises(StorageUnavailableError):
        StoredClaimState(
            project_id=_PROJECT_ID,
            claim=claim,
            attempt=attempt,
        )
    with pytest.raises(StorageUnavailableError):
        load_claim_state(
            cast("sqlite3.Connection", object()),
            task=cast("Task", object()),
        )


def test_batch_claim_hydration_rejects_bad_collections_and_handles_empty() -> None:
    """Batch hydration requires a unique sequence of complete Task objects."""
    connection = cast("sqlite3.Connection", object())
    task = _task()
    for tasks in ("bad", (object(),), (task, task)):
        with pytest.raises(StorageUnavailableError):
            load_claim_states(
                connection,
                tasks=cast("tuple[Task, ...]", tasks),
            )
    assert load_claim_states(connection, tasks=()) == {}


@pytest.mark.parametrize(
    "row",
    [
        (
            "bad",
            "prj_claims",
            "sub_local",
            None,
            "2026-08-20T10:00:00Z",
            "2026-08-20T18:00:00Z",
            *(None,) * 8,
        ),
        (
            "tsk_other",
            "prj_claims",
            "sub_local",
            None,
            "2026-08-20T10:00:00Z",
            "2026-08-20T18:00:00Z",
            *(None,) * 8,
        ),
        (
            "tsk_claim",
            "prj_other",
            "sub_local",
            None,
            "2026-08-20T10:00:00Z",
            "2026-08-20T18:00:00Z",
            *(None,) * 8,
        ),
        (
            "tsk_claim",
            "prj_claims",
            "sub_local",
            None,
            "2026-08-20T10:00:00Z",
            "2026-08-20T18:00:00Z",
            "unexpected",
            *(None,) * 7,
        ),
        (
            "tsk_claim",
            "prj_claims",
            "sub_local",
            "atm_claim",
            "2026-08-20T10:00:00Z",
            "2026-08-20T10:15:00Z",
            "atm_claim",
            "tsk_claim",
            "prj_other",
            "sub_local",
            "active",
            "2026-08-20T10:00:00Z",
            None,
            "2026-08-20T10:15:00Z",
        ),
    ],
)
def test_claim_hydration_rejects_corrupt_join_rows(
    row: tuple[object, ...],
) -> None:
    """Malformed identities, relationships, and nullable joins fail closed."""
    connection = cast("sqlite3.Connection", _RowsConnection([row]))

    with pytest.raises(StorageUnavailableError):
        load_claim_states(connection, tasks=(_task(),))


def test_current_claim_projection_rejects_bad_state_and_time() -> None:
    """Current-Lease projection validates state and authoritative UTC time."""
    state = StoredClaimState(
        project_id=_PROJECT_ID,
        claim=_human_claim(),
        attempt=None,
    )
    with pytest.raises(StorageUnavailableError):
        current_claim_state(cast("StoredClaimState", object()), now=_NOW)
    with pytest.raises(StorageUnavailableError):
        current_claim_state(state, now=_NOW.replace(tzinfo=None))
    assert current_claim_state(None, now=_NOW) is None


def test_claim_record_wrappers_reject_wrong_runtime_types() -> None:
    """Attempt and Claim durable wrappers fail before serialization."""
    with pytest.raises(StorageUnavailableError):
        TaskAttemptRecord(
            project_id=cast("ProjectId", object()),
            attempt=_agent_attempt(),
        )
    with pytest.raises(StorageUnavailableError):
        TaskClaimRecord(
            project_id=_PROJECT_ID,
            claim=cast("TaskClaim", object()),
        )


def test_claim_record_row_codecs_reject_nonsequences_and_wrong_lengths() -> None:
    """Claim and Attempt rows require exact non-text sequence shapes."""
    for row in ("bad", ("short",)):
        with pytest.raises(StorageUnavailableError):
            task_attempt_record_from_row(cast("tuple[object, ...]", row))
        with pytest.raises(StorageUnavailableError):
            task_claim_record_from_row(
                cast("tuple[object, ...]", row),
                task_key="ACME-1",
            )


def test_event_record_and_row_boundaries_reject_malformed_values() -> None:
    """Event snapshots and row codecs reject mismatched or ambiguous shapes."""
    valid_event = _claim_event(_human_claim())
    with pytest.raises(StorageUnavailableError):
        TaskEventRecord(
            event=cast("TaskEvent", object()),
            actor_kind=SubjectKind.HUMAN,
            attempt_id=None,
        )
    with pytest.raises(StorageUnavailableError):
        TaskEventRecord(
            event=valid_event,
            actor_kind=cast("SubjectKind", object()),
            attempt_id=None,
        )
    for row in ("bad", (1,)):
        with pytest.raises(StorageUnavailableError):
            task_event_record_from_row(cast("tuple[object, ...]", row))
    mapping = task_event_record_mapping(_record(valid_event))
    with pytest.raises(StorageUnavailableError):
        task_event_record_from_mapping({**mapping, "payload": 3})


def test_event_insert_maps_invalid_input_and_allocated_cursor() -> None:
    """The shared event writer rejects bad domain input and SQLite cursor output."""
    connection = cast("sqlite3.Connection", _InvalidCursorConnection())
    with pytest.raises(StorageUnavailableError):
        insert_task_event(
            connection,
            event_id=cast("TaskEventId", object()),
            task=_task(),
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_claim"),
            event_type=TaskEventType.TASK_CLAIMED,
            occurred_at=_NOW,
            payload={"lease_expires_at": serialize_timestamp(_NOW)},
            attempt_id=None,
        )
    with pytest.raises(StorageUnavailableError):
        insert_task_event(
            connection,
            event_id=TaskEventId("evt_invalid_cursor"),
            task=_task(),
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_claim"),
            event_type=TaskEventType.TASK_CLAIMED,
            occurred_at=_NOW,
            payload={"lease_expires_at": serialize_timestamp(_NOW)},
            attempt_id=None,
        )


@pytest.mark.parametrize("mode", ["human", "agent"])
def test_claim_outcome_parser_round_trips_human_and_agent(
    mode: Literal["human", "agent"],
) -> None:
    """Closed durable outcomes retain nullable Attempt semantics exactly."""
    agent = mode == "agent"
    mapping = _outcome_mapping(agent=agent)

    result, records = _parse_claim_outcome(canonical_json(mapping))

    assert (result.attempt is not None) is agent
    assert result.claim is not None
    assert len(records) == 1
    assert result.events == (records[0].event,)


def test_claim_outcome_parser_rejects_open_and_wrong_shapes() -> None:
    """Durable replay parsing rejects every open or ambiguous outer shape."""
    valid = _outcome_mapping(agent=False)
    invalid_values = (
        {**valid, "unknown": True},
        {**valid, "task": None},
        {**valid, "claim": None},
        {**valid, "attempt": "bad"},
        {**valid, "events": {}},
        {**valid, "events": [*cast("list[object]", valid["events"]), {}, {}]},
        {**valid, "events": [1]},
    )
    for value in invalid_values:
        with pytest.raises(StorageUnavailableError):
            _parse_claim_outcome(canonical_json(value))


def test_claim_outcome_parser_rejects_project_and_pair_inconsistency() -> None:
    """Replay outcomes cannot cross Project or Human/Agent ownership boundaries."""
    project_mismatch = _outcome_mapping(agent=False)
    claim_mapping = cast("dict[str, object]", project_mismatch["claim"])
    project_mismatch["claim"] = {**claim_mapping, "project_id": "prj_other"}
    with pytest.raises(StorageUnavailableError):
        _parse_claim_outcome(canonical_json(project_mismatch))

    invalid_pair = _outcome_mapping(agent=False)
    invalid_pair["attempt"] = task_attempt_record_mapping(
        TaskAttemptRecord(project_id=_PROJECT_ID, attempt=_agent_attempt())
    )
    with pytest.raises(StorageUnavailableError):
        _parse_claim_outcome(canonical_json(invalid_pair))


def test_matching_claim_result_rejects_adapter_contract_violations() -> None:
    """Fresh and replayed outcomes remain bound to exact mutation semantics."""
    human_claim = _human_claim()
    valid_human = TaskClaimResult(
        task=_task(),
        claim=human_claim,
        attempt=None,
        events=(_claim_event(human_claim),),
    )
    human_mutation = _human_mutation()
    _require_matching_claim_result(valid_human, mutation=human_mutation)

    wrong_project = valid_human.model_copy(
        update={"task": _task(project_id=ProjectId("prj_other"))}
    )
    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(wrong_project, mutation=human_mutation)

    wrong_human_mutation = human_mutation.model_copy(
        update={"task_uid": TaskId("tsk_other")}
    )
    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(valid_human, mutation=wrong_human_mutation)

    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(valid_human, mutation=_agent_mutation())

    agent_claim = _agent_claim()
    attempt = _agent_attempt()
    valid_agent = TaskClaimResult(
        task=_task(),
        claim=agent_claim,
        attempt=attempt,
        events=(_claim_event(agent_claim),),
    )
    no_events = valid_agent.model_copy(update={"events": ()})
    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(no_events, mutation=_agent_mutation())

    repeated_event = _claim_event(agent_claim, cursor=3)
    wrong_sequence = valid_agent.model_copy(
        update={"events": (valid_agent.events[0], repeated_event)}
    )
    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(wrong_sequence, mutation=_agent_mutation())

    wrong_payload = replace(valid_agent.events[0], payload={"unexpected": True})
    with pytest.raises(StorageUnavailableError):
        _require_matching_claim_result(
            valid_agent.model_copy(update={"events": (wrong_payload,)}),
            mutation=_agent_mutation(),
        )


def test_matching_claim_result_rejects_invalid_expiry_event() -> None:
    """A two-event reclaim must retain exact shared request/time expiry metadata."""
    claim = _agent_claim()
    claimed_event = _claim_event(claim)
    invalid_expiry = _event(
        TaskEventType.CLAIM_EXPIRED,
        cursor=1,
        attempt_id=AttemptId("atm_old"),
        payload={
            "lease_expires_at": serialize_timestamp(_NOW + timedelta(seconds=1))
        },
    )
    result = TaskClaimResult(
        task=_task(),
        claim=claim,
        attempt=_agent_attempt(),
        events=(invalid_expiry, claimed_event),
    )

    with pytest.raises(StorageUnavailableError) as caught:
        _require_matching_claim_result(result, mutation=_agent_mutation())
    assert caught.traceback[-1].lineno >= 728


def test_materialized_expiry_defenses_reject_current_or_changed_state() -> None:
    """Stale cleanup refuses current ownership and impossible row-count races."""
    current = StoredClaimState(
        project_id=_PROJECT_ID,
        claim=_human_claim(),
        attempt=None,
    )
    with pytest.raises(StorageUnavailableError):
        _materialize_expired_claim(
            cast("sqlite3.Connection", object()),
            task=_task(),
            state=current,
            mutation=_human_mutation(),
        )

    stale_claim = replace(
        _human_claim(),
        claimed_at=_NOW - timedelta(minutes=5),
        lease_expires_at=_NOW,
    )
    stale_human = StoredClaimState(
        project_id=_PROJECT_ID,
        claim=stale_claim,
        attempt=None,
    )
    with pytest.raises(StorageUnavailableError):
        _materialize_expired_claim(
            cast("sqlite3.Connection", _RowCountConnection([0])),
            task=_task(),
            state=stale_human,
            mutation=_human_mutation(),
        )

    stale_attempt = replace(
        _agent_attempt(),
        started_at=_NOW - timedelta(minutes=5),
        lease_expires_at=_NOW,
    )
    stale_agent = StoredClaimState(
        project_id=_PROJECT_ID,
        claim=replace(
            _agent_claim(),
            claimed_at=stale_attempt.started_at,
            lease_expires_at=stale_attempt.lease_expires_at,
        ),
        attempt=stale_attempt,
    )
    with pytest.raises(StorageUnavailableError):
        _materialize_expired_claim(
            cast("sqlite3.Connection", _RowCountConnection([1, 0])),
            task=_task(),
            state=stale_agent,
            mutation=_agent_mutation(),
        )


@pytest.mark.parametrize(
    "failure",
    [
        StorageUnavailableError(),
        DomainValidationError("invalid domain state"),
        IndexError(),
        OverflowError(),
        TypeError(),
        ValueError(),
    ],
)
def test_execute_claim_maps_unexpected_validation_failure(
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unexpected helper validation failures collapse to the storage boundary."""
    def _raise_value_error(*args: object, **kwargs: object) -> None:
        """Raise one controlled non-application validation failure."""
        del args, kwargs
        raise failure

    @contextmanager
    def _transaction(_database_path: Path) -> Iterator[sqlite3.Connection]:
        """Yield one connection stub inside the operation try boundary."""
        yield cast("sqlite3.Connection", object())

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._task_claims._require_authorized_project",
        _raise_value_error,
    )
    monkeypatch.setattr(
        "workaholic.persistence.sqlite._task_claims.open_write_transaction",
        _transaction,
    )
    with pytest.raises(StorageUnavailableError):
        _execute_claim(tmp_path / "missing.db", mutation=_human_mutation())


def test_claim_task_runtime_boundary_rejects_wrong_mutation(tmp_path: Path) -> None:
    """The Human repository entry point does not trust its mutation type hint."""
    with pytest.raises(StorageUnavailableError):
        claim_task(tmp_path / "missing.db", cast("ClaimTaskMutation", object()))


def test_repository_rejects_clock_without_now(tmp_path: Path) -> None:
    """The repository validates its injected authoritative-clock boundary."""
    with pytest.raises(TypeError, match=r"clock must provide now\(\)"):
        SQLiteRepository(tmp_path / "local.db", clock=cast("Clock", object()))
