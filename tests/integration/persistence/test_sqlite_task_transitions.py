"""Integration tests for optimistic SQLite Task state transitions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    IdempotencyConflictError,
    InvalidTransitionError,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskCreationMutation,
    TaskMutationResult,
    TaskUnblockMutation,
    VersionConflictError,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    Task,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 1, 8, 0, 0, 111111, tzinfo=UTC)
_TRANSITION_AT = datetime(2026, 8, 1, 9, 0, 0, 222222, tzinfo=UTC)

type _TransitionMutation = TaskBlockMutation | TaskUnblockMutation | TaskCancelMutation
type _Rows = list[tuple[object, ...]]


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, Task]:
    """Create one bootstrapped repository containing an open Task.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and initial open Task.

    """
    repository = SQLiteRepository(tmp_path / "local.db")
    bootstrap = repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            request_id=RequestId("req_bootstrap"),
            occurred_at=_CREATED_AT - timedelta(minutes=1),
            project_key="ACME",
        )
    )
    task = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_target"),
            event_id=TaskEventId("evt_create"),
            request_id=RequestId("req_create"),
            project_id=bootstrap.project.id,
            actor_subject_id=bootstrap.subject.id,
            occurred_at=_CREATED_AT,
            title="Lifecycle task",
            objective="Exercise explicit state transitions.",
            priority=50,
        )
    )
    return repository, task


def _set_state(
    repository: SQLiteRepository,
    task: Task,
    state: TaskState,
) -> None:
    """Seed one schema-valid lifecycle state for transition-matrix tests.

    Args:
        repository: Initialized test repository.
        task: Target Task.
        state: State to seed without emitting an operation event.

    """
    blocking_reason = "Existing blocker." if state is TaskState.BLOCKED else None
    with open_write_transaction(repository.database_path) as connection:
        if state is TaskState.REVIEW:
            connection.execute(
                """
                INSERT INTO task_results (
                    id, task_uid, submitted_by, submitted_at, comment, summary,
                    review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "res_pending",
                    str(task.uid),
                    "sub_local",
                    "2026-08-01T08:30:00.123456Z",
                    "Completed manually.",
                    "Pending review.",
                    "pending",
                ),
            )
        connection.execute(
            """
            UPDATE tasks
            SET state = ?, blocking_reason = ?, current_result_id = ?
            WHERE uid = ?
            """,
            (
                state.value,
                blocking_reason,
                "res_pending" if state is TaskState.REVIEW else None,
                str(task.uid),
            ),
        )


def _mutation(  # noqa: PLR0913 - explicit fixture controls aid matrix tests.
    operation: str,
    task: Task,
    suffix: str = "transition",
    *,
    expected_version: int = 1,
    occurred_at: datetime = _TRANSITION_AT,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> _TransitionMutation:
    """Build one operation-specific optimistic transition mutation.

    Args:
        operation: One of block, unblock, or cancel.
        task: Target Task snapshot.
        suffix: Generated attribution suffix.
        expected_version: Required optimistic precondition.
        occurred_at: Authoritative transition timestamp.
        reason: Required block or optional cancellation reason.
        idempotency_key: Optional caller replay key.

    Returns:
        Valid operation-specific transition mutation.

    Raises:
        ValueError: If the operation is outside the closed test surface.

    """
    if operation == "block":
        return TaskBlockMutation(
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=SubjectId("sub_local"),
            event_id=TaskEventId(f"evt_{suffix}"),
            claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
            request_id=RequestId(f"req_{suffix}"),
            occurred_at=occurred_at,
            expected_version=expected_version,
            reason=reason or "Waiting for input.",
            idempotency_key=idempotency_key,
        )
    if operation == "unblock":
        return TaskUnblockMutation(
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=SubjectId("sub_local"),
            event_id=TaskEventId(f"evt_{suffix}"),
            claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
            request_id=RequestId(f"req_{suffix}"),
            occurred_at=occurred_at,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
    if operation == "cancel":
        return TaskCancelMutation(
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=SubjectId("sub_local"),
            event_id=TaskEventId(f"evt_{suffix}"),
            claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
            request_id=RequestId(f"req_{suffix}"),
            occurred_at=occurred_at,
            expected_version=expected_version,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    message = "Unknown lifecycle operation fixture."
    raise ValueError(message)


def _execute(
    repository: SQLiteRepository,
    mutation: _TransitionMutation,
) -> TaskMutationResult:
    """Dispatch a typed mutation through its explicit repository operation.

    Args:
        repository: Initialized SQLite repository.
        mutation: Valid transition mutation.

    Returns:
        Committed or replayed transition result.

    """
    if isinstance(mutation, TaskBlockMutation):
        return repository.block_task(mutation)
    if isinstance(mutation, TaskUnblockMutation):
        return repository.unblock_task(mutation)
    return repository.cancel_task(mutation)


def _snapshot(database_path: Path) -> tuple[_Rows, _Rows, _Rows, _Rows, _Rows]:
    """Read all transition-owned and provenance rows for rollback comparison.

    Args:
        database_path: Initialized SQLite store.

    Returns:
        Stable Task, Result, dependency, event, and idempotency snapshot.

    """
    with open_read_connection(database_path) as connection:
        task_rows = connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall()
        result_rows = connection.execute(
            "SELECT * FROM task_results ORDER BY id"
        ).fetchall()
        dependency_rows = connection.execute(
            "SELECT * FROM task_dependencies ORDER BY task_uid, prerequisite_uid"
        ).fetchall()
        event_rows = connection.execute(
            "SELECT * FROM task_events ORDER BY cursor"
        ).fetchall()
        idempotency_rows = connection.execute(
            """
            SELECT * FROM idempotency_records
            WHERE operation IN ('task.block', 'task.unblock', 'task.cancel')
            ORDER BY operation, caller_key
            """
        ).fetchall()
    return task_rows, result_rows, dependency_rows, event_rows, idempotency_rows


_ALLOWED_TRANSITIONS = {
    ("block", TaskState.OPEN),
    ("unblock", TaskState.BLOCKED),
    ("cancel", TaskState.OPEN),
    ("cancel", TaskState.BLOCKED),
    ("cancel", TaskState.REVIEW),
}


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
@pytest.mark.parametrize("initial_state", tuple(TaskState))
def test_transition_matrix_is_exhaustive_and_atomic(
    operation: str,
    initial_state: TaskState,
    tmp_path: Path,
) -> None:
    """Every operation-state pair either commits exactly once or changes nothing."""
    repository, task = _repository(tmp_path)
    _set_state(repository, task, initial_state)
    mutation = _mutation(
        operation,
        task,
        reason="Operator decision." if operation == "cancel" else None,
        idempotency_key=f"{operation}-1",
    )
    before = _snapshot(repository.database_path)

    if (operation, initial_state) not in _ALLOWED_TRANSITIONS:
        with pytest.raises(InvalidTransitionError) as captured:
            _execute(repository, mutation)
        assert captured.value.code is ApplicationErrorCode.INVALID_TRANSITION
        assert _snapshot(repository.database_path) == before
        return

    result = _execute(repository, mutation)

    expected_state = {
        "block": TaskState.BLOCKED,
        "unblock": TaskState.OPEN,
        "cancel": TaskState.CANCELLED,
    }[operation]
    expected_event = {
        "block": TaskEventType.TASK_BLOCKED,
        "unblock": TaskEventType.TASK_UNBLOCKED,
        "cancel": TaskEventType.TASK_CANCELLED,
    }[operation]
    assert result.task.state is expected_state
    assert result.task.version == 2
    assert result.task.updated_at == _TRANSITION_AT
    assert result.task.current_result_id == (
        task.current_result_id
        if initial_state is not TaskState.REVIEW
        else ResultId("res_pending")
    )
    assert result.task.blocking_reason == (
        "Waiting for input." if operation == "block" else None
    )
    event = result.events[0]
    assert event.event_type is expected_event
    assert event.actor_subject_id == SubjectId("sub_local")
    assert event.request_id == RequestId("req_transition")
    assert event.occurred_at == _TRANSITION_AT
    expected_payload: dict[str, object] = {"version": 2}
    if operation == "block":
        expected_payload["reason"] = "Waiting for input."
    elif operation == "cancel":
        expected_payload["reason"] = "Operator decision."
    assert dict(event.payload) == expected_payload
    after = _snapshot(repository.database_path)
    assert len(after[3]) == len(before[3]) + 1
    assert len(after[4]) == len(before[4]) + 1
    assert after[1] == before[1]
    assert after[2] == before[2]


def test_review_cancellation_preserves_selected_result_provenance(
    tmp_path: Path,
) -> None:
    """Cancelling review changes state but neither deletes nor deselects its Result."""
    repository, task = _repository(tmp_path)
    _set_state(repository, task, TaskState.REVIEW)

    result = repository.cancel_task(
        cast(
            "TaskCancelMutation",
            _mutation("cancel", task, reason="Superseded."),
        )
    )

    assert result.task.state is TaskState.CANCELLED
    assert str(result.task.current_result_id) == "res_pending"
    with open_read_connection(repository.database_path) as connection:
        selection = connection.execute(
            "SELECT state, current_result_id FROM tasks WHERE uid = ?",
            (str(task.uid),),
        ).fetchone()
        result_count = connection.execute(
            "SELECT count(*) FROM task_results WHERE id = 'res_pending'"
        ).fetchone()
    assert selection == ("cancelled", "res_pending")
    assert result_count == (1,)


@pytest.mark.parametrize(
    ("reason", "expectation"),
    [
        (None, "invalid"),
        ("", "invalid"),
        ("x" * 1_001, "invalid"),
        ("Waiting.", "valid"),
    ],
)
def test_block_reason_contract_is_required_and_bounded(
    reason: str | None,
    expectation: str,
) -> None:
    """The adapter mutation boundary retains the closed reason contract."""
    data = {
        "task_uid": TaskId("tsk_target"),
        "project_id": ProjectId("prj_acme"),
        "actor_subject_id": SubjectId("sub_local"),
        "event_id": TaskEventId("evt_block"),
        "claim_expired_event_id": TaskEventId("evt_block_expired"),
        "request_id": RequestId("req_block"),
        "occurred_at": _TRANSITION_AT,
        "expected_version": 1,
    }
    if reason is not None:
        data["reason"] = reason

    if expectation == "valid":
        assert TaskBlockMutation.model_validate(data).reason == "Waiting."
    else:
        with pytest.raises(ValidationError):
            TaskBlockMutation.model_validate(data)


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
def test_stale_transition_version_is_rejected_without_writes(
    operation: str,
    tmp_path: Path,
) -> None:
    """Every explicit transition enforces the same optimistic precondition."""
    repository, task = _repository(tmp_path)
    if operation == "unblock":
        _set_state(repository, task, TaskState.BLOCKED)
    before = _snapshot(repository.database_path)

    with pytest.raises(VersionConflictError) as captured:
        _execute(
            repository,
            _mutation(operation, task, expected_version=2),
        )

    assert captured.value.code is ApplicationErrorCode.VERSION_CONFLICT
    assert _snapshot(repository.database_path) == before


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
def test_matching_transition_idempotency_replays_historic_result(
    operation: str,
    tmp_path: Path,
) -> None:
    """Each operation replays one original Task/event without a second version."""
    repository, task = _repository(tmp_path)
    if operation == "unblock":
        _set_state(repository, task, TaskState.BLOCKED)
    reason = "Operator decision." if operation == "cancel" else None
    first = _execute(
        repository,
        _mutation(
            operation,
            task,
            "first",
            reason=reason,
            idempotency_key="transition-1",
        ),
    )
    before = _snapshot(repository.database_path)

    replay = _execute(
        repository,
        _mutation(
            operation,
            task,
            "retry",
            reason=reason,
            occurred_at=_TRANSITION_AT + timedelta(hours=1),
            idempotency_key="transition-1",
        ),
    )

    assert replay == first
    assert replay.events[0].id == TaskEventId("evt_first")
    assert replay.task.version == 2
    assert _snapshot(repository.database_path) == before


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
def test_conflicting_transition_idempotency_precedes_state_and_version_checks(
    operation: str,
    tmp_path: Path,
) -> None:
    """Caller-key reuse cannot become a repeat transition or stale write."""
    repository, task = _repository(tmp_path)
    if operation == "unblock":
        _set_state(repository, task, TaskState.BLOCKED)
    initial_reason = "First reason." if operation == "cancel" else None
    _execute(
        repository,
        _mutation(
            operation,
            task,
            reason=initial_reason,
            idempotency_key="transition-1",
        ),
    )
    before = _snapshot(repository.database_path)
    conflicting_reason = "Second reason." if operation != "unblock" else None
    conflicting_version = 1 if operation != "unblock" else 2

    with pytest.raises(IdempotencyConflictError) as captured:
        _execute(
            repository,
            _mutation(
                operation,
                task,
                "conflict",
                expected_version=conflicting_version,
                reason=conflicting_reason,
                idempotency_key="transition-1",
            ),
        )

    assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
    assert _snapshot(repository.database_path) == before


def test_idempotency_keys_are_namespaced_by_semantic_operation(
    tmp_path: Path,
) -> None:
    """One caller token may safely correlate a block, unblock, and cancellation."""
    repository, task = _repository(tmp_path)
    blocked = repository.block_task(
        cast(
            "TaskBlockMutation",
            _mutation("block", task, "block", idempotency_key="workflow-1"),
        )
    )
    opened = repository.unblock_task(
        cast(
            "TaskUnblockMutation",
            _mutation(
                "unblock",
                blocked.task,
                "unblock",
                expected_version=2,
                occurred_at=_TRANSITION_AT + timedelta(seconds=1),
                idempotency_key="workflow-1",
            ),
        )
    )
    cancelled = repository.cancel_task(
        cast(
            "TaskCancelMutation",
            _mutation(
                "cancel",
                opened.task,
                "cancel",
                expected_version=3,
                occurred_at=_TRANSITION_AT + timedelta(seconds=2),
                reason="Finished elsewhere.",
                idempotency_key="workflow-1",
            ),
        )
    )

    assert cancelled.task.version == 4
    assert cancelled.task.state is TaskState.CANCELLED
    with open_read_connection(repository.database_path) as connection:
        operations = connection.execute(
            """
            SELECT operation FROM idempotency_records
            WHERE caller_key = 'workflow-1'
            ORDER BY operation
            """
        ).fetchall()
    assert operations == [("task.block",), ("task.cancel",), ("task.unblock",)]


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
@pytest.mark.parametrize("failure_point", ["event", "idempotency"])
def test_injected_failure_rolls_back_transition_event_and_idempotency(
    operation: str,
    failure_point: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A write-boundary failure restores Task, Result, event, and caller key."""
    repository, task = _repository(tmp_path)
    if operation == "unblock":
        _set_state(repository, task, TaskState.BLOCKED)
    elif operation == "cancel":
        _set_state(repository, task, TaskState.REVIEW)
    before = _snapshot(repository.database_path)

    def fail(*_arguments: object, **_keywords: object) -> object:
        """Raise one injected failure after the Task write."""
        message = f"injected {failure_point} failure"
        raise RuntimeError(message)

    target = (
        "workaholic.persistence.sqlite._task_lifecycle._insert_task_event"
        if failure_point == "event"
        else "workaholic.persistence.sqlite._task_lifecycle._record_idempotent_mutation"
    )
    monkeypatch.setattr(target, fail)

    with pytest.raises(RuntimeError, match="injected"):
        _execute(
            repository,
            _mutation(
                operation,
                task,
                reason="Operator decision." if operation == "cancel" else None,
                idempotency_key="rollback-1",
            ),
        )

    assert _snapshot(repository.database_path) == before


@pytest.mark.parametrize("operation", ["block", "unblock", "cancel"])
def test_regressive_transition_timestamp_fails_closed(
    operation: str,
    tmp_path: Path,
) -> None:
    """A new Task version cannot move its authoritative timestamp backward."""
    repository, task = _repository(tmp_path)
    if operation == "unblock":
        _set_state(repository, task, TaskState.BLOCKED)
    before = _snapshot(repository.database_path)

    with pytest.raises(StorageUnavailableError):
        _execute(
            repository,
            _mutation(
                operation,
                task,
                occurred_at=_CREATED_AT - timedelta(microseconds=1),
            ),
        )

    assert _snapshot(repository.database_path) == before


def test_repository_transition_methods_fail_closed_on_runtime_type_bypass(
    tmp_path: Path,
) -> None:
    """Each adapter entry point rejects values outside its mutation contract."""
    repository, _task = _repository(tmp_path)

    with pytest.raises(StorageUnavailableError):
        repository.block_task(cast("TaskBlockMutation", object()))
    with pytest.raises(StorageUnavailableError):
        repository.unblock_task(cast("TaskUnblockMutation", object()))
    with pytest.raises(StorageUnavailableError):
        repository.cancel_task(cast("TaskCancelMutation", object()))


def test_transition_event_payloads_are_bounded_and_infrastructure_free(
    tmp_path: Path,
) -> None:
    """Stored transition metadata exposes only safe reason and version fields."""
    repository, task = _repository(tmp_path)
    repository.cancel_task(
        cast(
            "TaskCancelMutation",
            _mutation("cancel", task, reason="No longer required."),
        )
    )

    with open_read_connection(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT actor_kind, attempt_id, payload_json
            FROM task_events WHERE id = 'evt_transition'
            """
        ).fetchone()
    assert row is not None
    assert row[0:2] == ("human", None)
    payload = json.loads(row[2])
    assert payload == {"reason": "No longer required.", "version": 2}
    assert not {
        "database",
        "storage",
        "token",
        "request_id",
        "subject_id",
    }.intersection(payload)
