"""Integration tests for exclusive Claim locks across Human Task mutations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError

from workaholic.application import (
    AddTaskDependencyMutation,
    BootstrapMutation,
    GetTask,
    RemoveTaskDependencyMutation,
    SubmitHumanResultMutation,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskCreationMutation,
    TaskLockedError,
    TaskMutationResult,
    TaskResultInput,
    TaskSubmissionResult,
    TaskUnblockMutation,
    TaskUpdateMutation,
    TaskUpdatePatch,
    VersionConflictError,
)
from workaholic.domain import (
    AttemptId,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    Task,
    TaskEventId,
    TaskEventType,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite._records import serialize_timestamp

if TYPE_CHECKING:
    from pathlib import Path

type _Operation = Literal[
    "update",
    "block",
    "unblock",
    "dependency-add",
    "dependency-remove",
    "cancel",
    "submit",
]
type _OwnerState = Literal["unclaimed", "human", "agent", "expired-agent"]
type _HumanMutation = (
    TaskUpdateMutation
    | TaskBlockMutation
    | TaskUnblockMutation
    | AddTaskDependencyMutation
    | RemoveTaskDependencyMutation
    | TaskCancelMutation
    | SubmitHumanResultMutation
)
type _HumanResult = TaskMutationResult | TaskSubmissionResult

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 22, 9, tzinfo=UTC)
_CLAIMED_AT = datetime(2026, 8, 22, 10, tzinfo=UTC)
_OPERATION_AT = datetime(2026, 8, 22, 10, 5, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_claim_locks")
_SUBJECT_ID = SubjectId("sub_local")
_ATTEMPT_ID = AttemptId("atm_lock_owner")
_RECLAIM_ATTEMPT_ID = AttemptId("atm_lock_reclaimed")
_OPERATIONS: tuple[_Operation, ...] = (
    "update",
    "block",
    "unblock",
    "dependency-add",
    "dependency-remove",
    "cancel",
    "submit",
)
_OWNER_STATES: tuple[_OwnerState, ...] = (
    "unclaimed",
    "human",
    "agent",
    "expired-agent",
)


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, Task, Task]:
    """Bootstrap one Project with target and prerequisite Task fixtures."""
    repository = SQLiteRepository(tmp_path / "local.db")
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_claim_locks"),
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            request_id=RequestId("req_bootstrap_claim_locks"),
            occurred_at=_CREATED_AT,
            project_key="ACME",
        )
    )
    target = repository.create_task(
        _creation(
            task_id=TaskId("tsk_lock_target"),
            event_id=TaskEventId("evt_create_lock_target"),
            request_id=RequestId("req_create_lock_target"),
            title="Claim lock target",
            occurred_at=_CREATED_AT + timedelta(minutes=1),
        )
    )
    prerequisite = repository.create_task(
        _creation(
            task_id=TaskId("tsk_lock_prerequisite"),
            event_id=TaskEventId("evt_create_lock_prerequisite"),
            request_id=RequestId("req_create_lock_prerequisite"),
            title="Claim lock prerequisite",
            occurred_at=_CREATED_AT + timedelta(minutes=2),
        )
    )
    return repository, target, prerequisite


def _creation(
    *,
    task_id: TaskId,
    event_id: TaskEventId,
    request_id: RequestId,
    title: str,
    occurred_at: datetime,
) -> TaskCreationMutation:
    """Build one strict Task creation mutation without hiding its identities."""
    return TaskCreationMutation(
        task_id=task_id,
        event_id=event_id,
        request_id=request_id,
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        occurred_at=occurred_at,
        title=title,
        objective="Exercise one Human mutation lock boundary.",
        priority=50,
    )


def _seed_owner(
    repository: SQLiteRepository,
    task: Task,
    owner_state: _OwnerState,
    *,
    subject_id: SubjectId = _SUBJECT_ID,
    agent_attempt_id: AttemptId = _ATTEMPT_ID,
) -> None:
    """Insert one exact current or expired Claim fixture without changing Task."""
    if owner_state == "unclaimed":
        return
    attempt_id: AttemptId | None = None
    lease_expires_at = _OPERATION_AT + timedelta(hours=8)
    if owner_state in ("agent", "expired-agent"):
        attempt_id = agent_attempt_id
        lease_expires_at = (
            _OPERATION_AT
            if owner_state == "expired-agent"
            else _OPERATION_AT + timedelta(minutes=15)
        )
    with open_write_transaction(repository.database_path) as connection:
        if attempt_id is not None:
            connection.execute(
                """
                INSERT INTO task_attempts (
                    id, task_uid, project_id, subject_id, status, started_at,
                    ended_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, 'active', ?, NULL, ?)
                """,
                (
                    str(attempt_id),
                    str(task.uid),
                    str(task.project_id),
                    str(subject_id),
                    serialize_timestamp(_CLAIMED_AT),
                    serialize_timestamp(lease_expires_at),
                ),
            )
        connection.execute(
            """
            INSERT INTO task_claims (
                task_uid, project_id, subject_id, attempt_id, claimed_at,
                lease_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(task.uid),
                str(task.project_id),
                str(subject_id),
                None if attempt_id is None else str(attempt_id),
                serialize_timestamp(_CLAIMED_AT),
                serialize_timestamp(lease_expires_at),
            ),
        )


def _prepare_operation(
    repository: SQLiteRepository,
    task: Task,
    prerequisite: Task,
    operation: _Operation,
) -> Task:
    """Seed operation-specific state while retaining a previously seeded Claim."""
    with open_write_transaction(repository.database_path) as connection:
        if operation == "unblock":
            connection.execute(
                """
                UPDATE tasks
                SET state = 'blocked', blocking_reason = 'Fixture block',
                    version = version + 1, updated_at = ?
                WHERE uid = ? AND project_id = ?
                """,
                (
                    serialize_timestamp(_CLAIMED_AT + timedelta(minutes=1)),
                    str(task.uid),
                    str(task.project_id),
                ),
            )
        elif operation == "dependency-remove":
            connection.execute(
                """
                INSERT INTO task_dependencies (
                    task_uid, prerequisite_uid, project_id
                ) VALUES (?, ?, ?)
                """,
                (str(task.uid), str(prerequisite.uid), str(task.project_id)),
            )
    return repository.get_task(
        GetTask(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )


def _mutation(  # noqa: PLR0911, PLR0913 - closed cross-operation fixture.
    operation: _Operation,
    task: Task,
    prerequisite: Task,
    *,
    suffix: str,
    expected_version: int | None = None,
    occurred_at: datetime = _OPERATION_AT,
    idempotency_key: str | None = None,
    update_title: str | None = None,
) -> _HumanMutation:
    """Build one exact existing-Task Human mutation for the selected operation."""
    version = task.version if expected_version is None else expected_version
    shared = {
        "task_uid": task.uid,
        "project_id": task.project_id,
        "actor_subject_id": _SUBJECT_ID,
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": occurred_at,
        "expected_version": version,
        "idempotency_key": idempotency_key,
        "claim_expired_event_id": TaskEventId(f"evt_{suffix}_expired"),
    }
    event_id = TaskEventId(f"evt_{suffix}")
    if operation == "update":
        return TaskUpdateMutation.model_validate(
            {
                **shared,
                "event_id": event_id,
                "patch": TaskUpdatePatch(
                    title=(
                        f"Updated by {suffix}" if update_title is None else update_title
                    )
                ),
            }
        )
    if operation == "block":
        return TaskBlockMutation.model_validate(
            {
                **shared,
                "event_id": event_id,
                "reason": "Waiting for an operator decision.",
            }
        )
    if operation == "unblock":
        return TaskUnblockMutation.model_validate({**shared, "event_id": event_id})
    if operation == "dependency-add":
        return AddTaskDependencyMutation.model_validate(
            {
                **shared,
                "event_id": event_id,
                "prerequisite_uid": prerequisite.uid,
            }
        )
    if operation == "dependency-remove":
        return RemoveTaskDependencyMutation.model_validate(
            {
                **shared,
                "event_id": event_id,
                "prerequisite_uid": prerequisite.uid,
            }
        )
    if operation == "cancel":
        return TaskCancelMutation.model_validate(
            {
                **shared,
                "event_id": event_id,
                "reason": "No longer required.",
            }
        )
    return SubmitHumanResultMutation.model_validate(
        {
            **shared,
            "result_id": ResultId(f"res_{suffix}"),
            "result_submitted_event_id": TaskEventId(f"evt_{suffix}_submitted"),
            "task_completed_event_id": TaskEventId(f"evt_{suffix}_completed"),
            "comment": "Completed manually.",
            "result": TaskResultInput(summary="Implemented and verified manually."),
        }
    )


def _execute(  # noqa: PLR0911 - explicit closed-union dispatch aids the test.
    repository: SQLiteRepository,
    mutation: _HumanMutation,
) -> _HumanResult:
    """Dispatch one closed mutation union through its semantic repository method."""
    if isinstance(mutation, TaskUpdateMutation):
        return repository.update_task_if_version(mutation)
    if isinstance(mutation, TaskBlockMutation):
        return repository.block_task(mutation)
    if isinstance(mutation, TaskUnblockMutation):
        return repository.unblock_task(mutation)
    if isinstance(mutation, RemoveTaskDependencyMutation):
        return repository.remove_task_dependency(mutation)
    if isinstance(mutation, AddTaskDependencyMutation):
        return repository.add_task_dependency(mutation)
    if isinstance(mutation, TaskCancelMutation):
        return repository.cancel_task(mutation)
    return repository.submit_human_result(mutation)


def _snapshot(database_path: Path) -> tuple[tuple[object, ...], ...]:
    """Return all lock-sensitive rows for exact no-partial-write assertions."""
    with open_read_connection(database_path) as connection:
        return tuple(
            tuple(connection.execute(statement).fetchall())
            for statement in (
                "SELECT * FROM tasks ORDER BY uid",
                "SELECT * FROM task_dependencies ORDER BY task_uid, prerequisite_uid",
                "SELECT * FROM task_claims ORDER BY task_uid",
                "SELECT * FROM task_attempts ORDER BY id",
                "SELECT * FROM task_results ORDER BY id",
                "SELECT * FROM task_events ORDER BY cursor",
                "SELECT * FROM idempotency_records ORDER BY operation, caller_key",
            )
        )


def _claim_rows(repository: SQLiteRepository, task: Task) -> tuple[object, ...] | None:
    """Read the Task's nullable durable Claim row."""
    with open_read_connection(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT subject_id, attempt_id, claimed_at, lease_expires_at
            FROM task_claims WHERE task_uid = ?
            """,
            (str(task.uid),),
        ).fetchone()
    if row is None:
        return None
    return (row[0], row[1], row[2], row[3])


def _expected_primary_events(operation: _Operation) -> tuple[TaskEventType, ...]:
    """Return the pre-existing semantic event sequence for one Human mutation."""
    if operation == "update" or operation.startswith("dependency-"):
        return (TaskEventType.TASK_UPDATED,)
    if operation == "block":
        return (TaskEventType.TASK_BLOCKED,)
    if operation == "unblock":
        return (TaskEventType.TASK_UNBLOCKED,)
    if operation == "cancel":
        return (TaskEventType.TASK_CANCELLED,)
    return (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("owner_state", _OWNER_STATES)
def test_every_human_mutation_obeys_the_same_claim_lock(
    operation: _Operation,
    owner_state: _OwnerState,
    tmp_path: Path,
) -> None:
    """All Human write paths share lock, expiry, retention, and version rules."""
    repository, target, prerequisite = _repository(tmp_path)
    _seed_owner(repository, target, owner_state)
    target = _prepare_operation(repository, target, prerequisite, operation)
    before = _snapshot(repository.database_path)
    expected_version = target.version + 50 if owner_state == "agent" else None
    mutation = _mutation(
        operation,
        target,
        prerequisite,
        suffix=f"{operation.replace('-', '_')}_{owner_state.replace('-', '_')}",
        expected_version=expected_version,
        idempotency_key=f"{operation}-{owner_state}",
    )

    if owner_state == "agent":
        with pytest.raises(TaskLockedError):
            _execute(repository, mutation)
        assert _snapshot(repository.database_path) == before
        return

    result = _execute(repository, mutation)

    assert result.task.version == target.version + 1
    assert result.task.updated_at == _OPERATION_AT
    expected_events = _expected_primary_events(operation)
    if owner_state == "expired-agent":
        expected_events = (TaskEventType.CLAIM_EXPIRED, *expected_events)
        expired = result.events[0]
        assert expired.attempt_id == _ATTEMPT_ID
        assert dict(expired.payload) == {
            "lease_expires_at": serialize_timestamp(_OPERATION_AT)
        }
        with open_read_connection(repository.database_path) as connection:
            attempt = connection.execute(
                """
                SELECT status, ended_at FROM task_attempts WHERE id = ?
                """,
                (str(_ATTEMPT_ID),),
            ).fetchone()
        assert attempt == ("expired", serialize_timestamp(_OPERATION_AT))
    assert tuple(event.event_type for event in result.events) == expected_events

    terminal = operation in ("cancel", "submit")
    claim = _claim_rows(repository, target)
    if owner_state == "human" and not terminal:
        assert claim == (
            str(_SUBJECT_ID),
            None,
            serialize_timestamp(_CLAIMED_AT),
            serialize_timestamp(_OPERATION_AT + timedelta(hours=8)),
        )
    else:
        assert claim is None


def test_foreign_human_claim_locks_before_the_version_precondition(
    tmp_path: Path,
) -> None:
    """A different authorized Human owner is locked without disclosing version."""
    repository, target, prerequisite = _repository(tmp_path)
    other = SubjectId("sub_other_owner")
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO subjects (
                id, instance_id, kind, handle, display_name, enabled,
                is_instance_admin, version, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(other),
                "ins_claim_locks",
                "human",
                "other-owner",
                "Other owner",
                1,
                0,
                1,
                str(_SUBJECT_ID),
                serialize_timestamp(_CREATED_AT),
                serialize_timestamp(_CREATED_AT),
            ),
        )
        connection.execute(
            """
            INSERT INTO project_grants (
                instance_id, subject_id, project_id, role, version, granted_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ins_claim_locks",
                str(other),
                str(target.project_id),
                "owner",
                1,
                str(_SUBJECT_ID),
                serialize_timestamp(_CREATED_AT),
                serialize_timestamp(_CREATED_AT),
            ),
        )
    _seed_owner(repository, target, "human", subject_id=other)
    before = _snapshot(repository.database_path)
    mutation = _mutation(
        "update",
        target,
        prerequisite,
        suffix="foreign_human",
        expected_version=target.version + 50,
    )
    assert isinstance(mutation, TaskUpdateMutation)

    with pytest.raises(TaskLockedError):
        repository.update_task_if_version(mutation)

    assert _snapshot(repository.database_path) == before


def test_expiry_replay_precedes_a_later_foreign_claim(tmp_path: Path) -> None:
    """Exact replay returns its closed expiry batch after the Task is reclaimed."""
    repository, target, prerequisite = _repository(tmp_path)
    _seed_owner(repository, target, "expired-agent")
    first_mutation = _mutation(
        "update",
        target,
        prerequisite,
        suffix="expiry_first",
        idempotency_key="expiry-replay",
        update_title="Replay-safe update",
    )
    assert isinstance(first_mutation, TaskUpdateMutation)
    first = repository.update_task_if_version(first_mutation)
    _seed_owner(
        repository,
        first.task,
        "agent",
        subject_id=_SUBJECT_ID,
        agent_attempt_id=_RECLAIM_ATTEMPT_ID,
    )
    before = _snapshot(repository.database_path)
    replay_mutation = _mutation(
        "update",
        target,
        prerequisite,
        suffix="expiry_retry",
        occurred_at=_OPERATION_AT + timedelta(minutes=1),
        idempotency_key="expiry-replay",
        update_title="Replay-safe update",
    )
    assert isinstance(replay_mutation, TaskUpdateMutation)

    replay = repository.update_task_if_version(replay_mutation)

    assert replay == first
    assert _snapshot(repository.database_path) == before


def test_failed_human_mutation_does_not_materialize_expiry(tmp_path: Path) -> None:
    """Lazy expiry rolls back when the requested optimistic write cannot commit."""
    repository, target, prerequisite = _repository(tmp_path)
    _seed_owner(repository, target, "expired-agent")
    before = _snapshot(repository.database_path)
    mutation = _mutation(
        "update",
        target,
        prerequisite,
        suffix="expiry_rollback",
        expected_version=target.version + 1,
        idempotency_key="expiry-rollback",
    )
    assert isinstance(mutation, TaskUpdateMutation)

    with pytest.raises(VersionConflictError):
        repository.update_task_if_version(mutation)

    assert _snapshot(repository.database_path) == before


def test_human_mutation_requires_distinct_expiry_and_primary_event_ids(
    tmp_path: Path,
) -> None:
    """The application boundary rejects ambiguous conditional event identities."""
    _repository_instance, target, prerequisite = _repository(tmp_path)
    mutation = _mutation(
        "update",
        target,
        prerequisite,
        suffix="distinct_events",
    )
    assert isinstance(mutation, TaskUpdateMutation)

    with pytest.raises(ValidationError, match="event identities must be distinct"):
        TaskUpdateMutation(
            task_uid=mutation.task_uid,
            project_id=mutation.project_id,
            actor_subject_id=mutation.actor_subject_id,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            expected_version=mutation.expected_version,
            idempotency_key=mutation.idempotency_key,
            event_id=mutation.event_id,
            claim_expired_event_id=mutation.event_id,
            patch=mutation.patch,
        )


def _race_update(
    arguments: tuple[Path, Task, Task, int, Barrier],
) -> TaskMutationResult | VersionConflictError:
    """Run one same-owner optimistic update after synchronizing contenders."""
    database_path, task, prerequisite, index, barrier = arguments
    repository = SQLiteRepository(database_path)
    mutation = _mutation(
        "update",
        task,
        prerequisite,
        suffix=f"owner_race_{index}",
    )
    assert isinstance(mutation, TaskUpdateMutation)
    barrier.wait()
    try:
        return repository.update_task_if_version(mutation)
    except VersionConflictError as error:
        return error


def test_owned_human_claim_serializes_process_contention(tmp_path: Path) -> None:
    """Concurrent owner writes produce one version while retaining ownership."""
    repository, target, prerequisite = _repository(tmp_path)
    _seed_owner(repository, target, "human")
    barrier = Barrier(2)
    arguments = tuple(
        (repository.database_path, target, prerequisite, index, barrier)
        for index in (1, 2)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(_race_update, arguments))

    successes = tuple(
        outcome for outcome in outcomes if isinstance(outcome, TaskMutationResult)
    )
    conflicts = tuple(
        outcome for outcome in outcomes if isinstance(outcome, VersionConflictError)
    )
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].task.version == target.version + 1
    assert _claim_rows(repository, target) is not None
