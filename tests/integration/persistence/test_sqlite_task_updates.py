"""Integration tests for atomic optimistic SQLite Task field updates."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    GetTask,
    IdempotencyConflictError,
    InvalidInputError,
    InvalidTransitionError,
    PermissionDeniedError,
    TaskCreationMutation,
    TaskNotFoundError,
    TaskUpdateMutation,
    TaskUpdatePatch,
    VersionConflictError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    InstanceId,
    ProjectId,
    RequestId,
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
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 1, 8, 0, 0, 111111, tzinfo=UTC)
_UPDATED_AT = datetime(2026, 8, 1, 9, 0, 0, 222222, tzinfo=UTC)
_AVAILABLE_AT = datetime(2026, 8, 2, 10, 0, 0, 333333, tzinfo=UTC)
_NEW_AVAILABLE_AT = datetime(2026, 8, 3, 11, 0, 0, 444444, tzinfo=UTC)
_INITIAL_ACCEPTANCE = (
    AcceptanceCriterion("ac_initial", "Initial criterion.", required=True),
)
_NEW_ACCEPTANCE = (
    AcceptanceCriterion("ac_second", "Second criterion.", required=False),
    AcceptanceCriterion("ac_first", "First criterion.", required=True),
)
_INITIAL_CONTEXT = (ContextReference("workspace://repo/initial.md", "git:one"),)
_NEW_CONTEXT = (
    ContextReference("https://example.test/spec", "v2"),
    ContextReference("workspace://repo/new.md"),
)


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, Task]:
    """Create one bootstrapped repository containing a complete Task.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and complete initial Task.

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
            title="Initial task",
            objective="Initial objective.",
            priority=50,
            available_at=_AVAILABLE_AT,
            approval=ApprovalRequirement.HUMAN,
            acceptance=_INITIAL_ACCEPTANCE,
            context=_INITIAL_CONTEXT,
        )
    )
    return repository, task


def _mutation(  # noqa: PLR0913 - explicit fixture controls aid boundary tests.
    task: Task,
    patch: TaskUpdatePatch,
    suffix: str = "update",
    *,
    expected_version: int = 1,
    occurred_at: datetime = _UPDATED_AT,
    idempotency_key: str | None = None,
) -> TaskUpdateMutation:
    """Build one valid optimistic update mutation.

    Args:
        task: Target Task snapshot.
        patch: Nonempty editable definition patch.
        suffix: Generated identity suffix.
        expected_version: Required optimistic precondition.
        occurred_at: Authoritative update timestamp.
        idempotency_key: Optional caller replay key.

    Returns:
        Valid attributable update mutation.

    """
    return TaskUpdateMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        event_id=TaskEventId(f"evt_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        expected_version=expected_version,
        patch=patch,
        idempotency_key=idempotency_key,
    )


def _state_snapshot(database_path: Path) -> tuple[object, ...]:
    """Read all update-owned rows for exact rollback comparisons.

    Args:
        database_path: Initialized SQLite store.

    Returns:
        Stable Task, dependency, event, and update-idempotency snapshot.

    """
    with open_read_connection(database_path) as connection:
        task_rows = connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall()
        dependency_rows = connection.execute(
            "SELECT * FROM task_dependencies ORDER BY task_uid, prerequisite_uid"
        ).fetchall()
        event_rows = connection.execute(
            "SELECT * FROM task_events ORDER BY cursor"
        ).fetchall()
        idempotency_rows = connection.execute(
            """
            SELECT * FROM idempotency_records
            WHERE operation = 'task.update'
            ORDER BY subject_scope, caller_key
            """
        ).fetchall()
    return task_rows, dependency_rows, event_rows, idempotency_rows


@pytest.mark.parametrize(
    "patch",
    [
        TaskUpdatePatch(title="Updated task"),
        TaskUpdatePatch(objective="Updated objective."),
        TaskUpdatePatch(priority=80),
        TaskUpdatePatch(available_at=_NEW_AVAILABLE_AT),
        TaskUpdatePatch(available_at=None),
        TaskUpdatePatch(approval=ApprovalRequirement.NONE),
        TaskUpdatePatch(acceptance=_NEW_ACCEPTANCE),
        TaskUpdatePatch(acceptance=()),
        TaskUpdatePatch(context=_NEW_CONTEXT),
        TaskUpdatePatch(context=()),
    ],
    ids=[
        "title",
        "objective",
        "priority",
        "availability-set",
        "availability-clear",
        "approval",
        "acceptance-replace",
        "acceptance-clear",
        "context-replace",
        "context-clear",
    ],
)
def test_update_each_editable_field_round_trips_alone(
    patch: TaskUpdatePatch,
    tmp_path: Path,
) -> None:
    """Every editable field supports an isolated exact replacement."""
    repository, original = _repository(tmp_path)

    result = repository.update_task_if_version(_mutation(original, patch))

    assert result.task.uid == original.uid
    assert result.task.key == original.key
    assert result.task.number == original.number
    assert result.task.project_id == original.project_id
    assert result.task.created_by == original.created_by
    assert result.task.created_at == original.created_at
    assert result.task.state is TaskState.OPEN
    assert result.task.version == 2
    assert result.task.updated_at == _UPDATED_AT
    for field_name in patch.model_fields_set:
        assert getattr(result.task, field_name) == getattr(patch, field_name)
    assert result.events[0].event_type is TaskEventType.TASK_UPDATED
    assert dict(result.events[0].payload) == {
        "changes": tuple(sorted(patch.model_fields_set)),
        "version": 2,
    }
    assert (
        repository.get_task(
            GetTask(
                project_id=original.project_id,
                subject_id=SubjectId("sub_local"),
                task=original.uid,
            )
        )
        == result.task
    )


def test_complete_update_commits_one_version_timestamp_event_and_idempotency(
    tmp_path: Path,
) -> None:
    """A complete definition replacement is one attributable atomic mutation."""
    repository, original = _repository(tmp_path)
    patch = TaskUpdatePatch(
        title="Updated task",
        objective="Updated objective.",
        priority=90,
        available_at=None,
        approval=ApprovalRequirement.NONE,
        acceptance=_NEW_ACCEPTANCE,
        context=_NEW_CONTEXT,
    )

    result = repository.update_task_if_version(
        _mutation(original, patch, idempotency_key="update-complete-1")
    )

    assert result.task.version == 2
    assert result.task.updated_at == _UPDATED_AT
    assert result.task.blocking_reason is None
    assert result.task.current_result_id is None
    event = result.events[0]
    assert event.id == TaskEventId("evt_update")
    assert event.request_id == RequestId("req_update")
    assert event.actor_subject_id == SubjectId("sub_local")
    assert event.occurred_at == _UPDATED_AT
    with open_read_connection(repository.database_path) as connection:
        task_row = connection.execute(
            """
            SELECT title, objective, priority, available_at, approval,
                   acceptance_json, context_json, version, updated_at
            FROM tasks WHERE uid = 'tsk_target'
            """
        ).fetchone()
        event_row = connection.execute(
            """
            SELECT actor_kind, attempt_id, event_type, payload_json
            FROM task_events WHERE id = 'evt_update'
            """
        ).fetchone()
        idempotency_row = connection.execute(
            """
            SELECT subject_scope, operation, caller_key, created_at, outcome_json
            FROM idempotency_records WHERE operation = 'task.update'
            """
        ).fetchone()
    assert task_row is not None
    assert task_row[0:7] == (
        "Updated task",
        "Updated objective.",
        90,
        None,
        "none",
        json.dumps(
            [
                {"id": item.id, "required": item.required, "text": item.text}
                for item in _NEW_ACCEPTANCE
            ],
            separators=(",", ":"),
            sort_keys=True,
        ),
        json.dumps(
            [{"uri": item.uri, "version": item.version} for item in _NEW_CONTEXT],
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    assert task_row[7:] == (2, "2026-08-01T09:00:00.222222Z")
    assert event_row is not None
    assert event_row[0:3] == ("human", None, "task_updated")
    assert json.loads(event_row[3]) == {
        "changes": sorted(patch.model_fields_set),
        "version": 2,
    }
    assert idempotency_row is not None
    assert idempotency_row[0:4] == (
        "sub_local",
        "task.update",
        "update-complete-1",
        "2026-08-01T09:00:00.222222Z",
    )
    outcome = json.loads(idempotency_row[4])
    assert set(outcome) == {"event", "task"}
    assert outcome["task"]["version"] == 2
    assert outcome["event"]["id"] == "evt_update"


def test_no_op_update_is_rejected_without_writes(tmp_path: Path) -> None:
    """Supplying only current values cannot create a false version or event."""
    repository, original = _repository(tmp_path)
    before = _state_snapshot(repository.database_path)

    with pytest.raises(InvalidInputError) as captured:
        repository.update_task_if_version(
            _mutation(
                original,
                TaskUpdatePatch(
                    title=original.title,
                    objective=original.objective,
                ),
                idempotency_key="no-op-1",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert _state_snapshot(repository.database_path) == before


def test_stale_version_is_rejected_without_writes(tmp_path: Path) -> None:
    """A stale optimistic precondition cannot become last-write-wins."""
    repository, original = _repository(tmp_path)
    before = _state_snapshot(repository.database_path)

    with pytest.raises(VersionConflictError) as captured:
        repository.update_task_if_version(
            _mutation(
                original,
                TaskUpdatePatch(title="Updated task"),
                expected_version=2,
            )
        )

    assert captured.value.code is ApplicationErrorCode.VERSION_CONFLICT
    assert _state_snapshot(repository.database_path) == before


def test_authorized_missing_task_is_distinct_from_cross_project_scope(
    tmp_path: Path,
) -> None:
    """Authorized absence is not confused with a concealed Project boundary."""
    repository, original = _repository(tmp_path)
    missing = _mutation(original, TaskUpdatePatch(priority=80)).model_copy(
        update={"task_uid": TaskId("tsk_missing")}
    )

    with pytest.raises(TaskNotFoundError) as missing_error:
        repository.update_task_if_version(missing)
    with pytest.raises(PermissionDeniedError) as concealed_error:
        repository.update_task_if_version(
            missing.model_copy(update={"project_id": ProjectId("prj_other")})
        )

    assert missing_error.value.code is ApplicationErrorCode.TASK_NOT_FOUND
    assert concealed_error.value.code is ApplicationErrorCode.PERMISSION_DENIED


@pytest.mark.parametrize(
    "state", [TaskState.REVIEW, TaskState.DONE, TaskState.CANCELLED]
)
def test_review_and_terminal_tasks_refuse_definition_updates(
    state: TaskState,
    tmp_path: Path,
) -> None:
    """Generic definition edits cannot mutate review or terminal Tasks."""
    repository, original = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "UPDATE tasks SET state = ? WHERE uid = ?",
            (state.value, str(original.uid)),
        )
    before = _state_snapshot(repository.database_path)

    with pytest.raises(InvalidTransitionError) as captured:
        repository.update_task_if_version(
            _mutation(original, TaskUpdatePatch(title="Updated task"))
        )

    assert captured.value.code is ApplicationErrorCode.INVALID_TRANSITION
    assert _state_snapshot(repository.database_path) == before


def test_blocked_task_definition_update_preserves_state_and_reason(
    tmp_path: Path,
) -> None:
    """An editable blocked Task remains blocked for its recorded reason."""
    repository, original = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE tasks SET state = 'blocked', blocking_reason = 'Waiting'
            WHERE uid = ?
            """,
            (str(original.uid),),
        )

    result = repository.update_task_if_version(
        _mutation(original, TaskUpdatePatch(priority=80))
    )

    assert result.task.state is TaskState.BLOCKED
    assert result.task.blocking_reason == "Waiting"
    assert result.task.priority == 80


def test_matching_idempotency_replay_returns_historic_result_without_writes(
    tmp_path: Path,
) -> None:
    """A semantic retry returns original Task and event despite new metadata."""
    repository, original = _repository(tmp_path)
    patch = TaskUpdatePatch(priority=80)
    first = repository.update_task_if_version(
        _mutation(original, patch, "first", idempotency_key="update-1")
    )
    before = _state_snapshot(repository.database_path)

    replay = repository.update_task_if_version(
        _mutation(
            original,
            patch,
            "retry",
            occurred_at=_UPDATED_AT + timedelta(hours=1),
            idempotency_key="update-1",
        )
    )

    assert replay == first
    assert replay.events[0].id == TaskEventId("evt_first")
    assert replay.task.updated_at == _UPDATED_AT
    assert _state_snapshot(repository.database_path) == before


@pytest.mark.parametrize(
    "conflicting",
    [
        TaskUpdatePatch(priority=90),
        TaskUpdatePatch(title="Different patch"),
        TaskUpdatePatch(available_at=None),
    ],
)
def test_conflicting_idempotency_reuse_precedes_current_version_check(
    conflicting: TaskUpdatePatch,
    tmp_path: Path,
) -> None:
    """A reused caller key reports semantic conflict even after version advances."""
    repository, original = _repository(tmp_path)
    repository.update_task_if_version(
        _mutation(
            original,
            TaskUpdatePatch(priority=80),
            idempotency_key="update-1",
        )
    )
    before = _state_snapshot(repository.database_path)

    with pytest.raises(IdempotencyConflictError) as captured:
        repository.update_task_if_version(
            _mutation(original, conflicting, "conflict", idempotency_key="update-1")
        )

    assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
    assert _state_snapshot(repository.database_path) == before


@pytest.mark.parametrize(
    "tamper",
    [
        "shape",
        "task",
        "event",
        "event-shape",
        "result-mismatch",
        "event-row",
    ],
)
def test_tampered_replay_outcome_or_event_is_never_returned(
    tamper: str,
    tmp_path: Path,
) -> None:
    """Replay validates exact outcome shape, semantics, and persisted event."""
    repository, original = _repository(tmp_path)
    patch = TaskUpdatePatch(priority=80)
    repository.update_task_if_version(
        _mutation(original, patch, idempotency_key="update-1")
    )
    with open_write_transaction(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT outcome_json FROM idempotency_records
            WHERE operation = 'task.update'
            """
        ).fetchone()
        assert row is not None
        outcome = json.loads(row[0])
        if tamper == "shape":
            outcome = {"wrong": "shape"}
        elif tamper == "task":
            outcome["task"]["priority"] = 90
        elif tamper == "event":
            outcome["event"]["event_type"] = "task_blocked"
        elif tamper == "event-shape":
            outcome["event"] = 7
        elif tamper == "result-mismatch":
            outcome["event"]["task_uid"] = "tsk_other"
        else:
            connection.execute(
                "UPDATE task_events SET payload_json = '{}' WHERE id = 'evt_update'"
            )
        if tamper != "event-row":
            connection.execute(
                """
                UPDATE idempotency_records SET outcome_json = ?
                WHERE operation = 'task.update'
                """,
                (json.dumps(outcome, separators=(",", ":"), sort_keys=True),),
            )

    with pytest.raises(StorageUnavailableError):
        repository.update_task_if_version(
            _mutation(original, patch, "retry", idempotency_key="update-1")
        )


def test_inconsistent_project_key_and_task_key_fail_closed(tmp_path: Path) -> None:
    """A corrupted immutable namespace relationship is never used for mutation."""
    repository, original = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute("UPDATE projects SET key = 'BETA'")

    with pytest.raises(StorageUnavailableError):
        repository.update_task_if_version(
            _mutation(original, TaskUpdatePatch(priority=80))
        )


def _disable_subject(connection: sqlite3.Connection) -> object:
    """Disable the authorized local Human Subject."""
    return connection.execute("UPDATE subjects SET enabled = 0")


def _remove_grant(connection: sqlite3.Connection) -> object:
    """Remove the authorized local Project grant."""
    return connection.execute("DELETE FROM project_grants")


@pytest.mark.parametrize("revoke", [_disable_subject, _remove_grant])
def test_update_revalidates_active_human_owner_authorization(
    revoke: Callable[[sqlite3.Connection], object],
    tmp_path: Path,
) -> None:
    """Disabled Humans and missing grants cannot mutate or replay a Task."""
    repository, original = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        revoke(connection)
    before = _state_snapshot(repository.database_path)

    with pytest.raises(PermissionDeniedError) as captured:
        repository.update_task_if_version(
            _mutation(original, TaskUpdatePatch(priority=80))
        )

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED
    assert _state_snapshot(repository.database_path) == before


@pytest.mark.parametrize("failure_point", ["event", "idempotency"])
def test_injected_failure_rolls_back_task_children_event_and_idempotency(
    failure_point: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every failure point restores the complete pre-mutation relational state."""
    repository, original = _repository(tmp_path)
    prerequisite = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_prerequisite"),
            event_id=TaskEventId("evt_prerequisite"),
            request_id=RequestId("req_prerequisite"),
            project_id=original.project_id,
            actor_subject_id=SubjectId("sub_local"),
            occurred_at=_CREATED_AT + timedelta(seconds=1),
            title="Prerequisite",
            objective="Prerequisite objective.",
            priority=40,
        )
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO task_dependencies (project_id, task_uid, prerequisite_uid)
            VALUES (?, ?, ?)
            """,
            (str(original.project_id), str(original.uid), str(prerequisite.uid)),
        )
    before = _state_snapshot(repository.database_path)

    def fail(*_arguments: object, **_keywords: object) -> object:
        """Raise one injected storage failure at a selected write boundary."""
        message = f"injected {failure_point} failure"
        raise RuntimeError(message)

    target = (
        "workaholic.persistence.sqlite._task_lifecycle._insert_task_event"
        if failure_point == "event"
        else "workaholic.persistence.sqlite._task_lifecycle._record_idempotent_mutation"
    )
    monkeypatch.setattr(target, fail)

    with pytest.raises(RuntimeError, match="injected"):
        repository.update_task_if_version(
            _mutation(
                original,
                TaskUpdatePatch(
                    title="Updated task",
                    acceptance=_NEW_ACCEPTANCE,
                    context=_NEW_CONTEXT,
                ),
                idempotency_key="rollback-1",
            )
        )

    assert _state_snapshot(repository.database_path) == before


def test_invalid_runtime_mutation_and_inconsistent_timestamp_fail_closed(
    tmp_path: Path,
) -> None:
    """Adapter boundary bypasses and impossible Task timestamps never persist."""
    repository, original = _repository(tmp_path)
    before = _state_snapshot(repository.database_path)

    with pytest.raises(StorageUnavailableError):
        repository.update_task_if_version(cast("TaskUpdateMutation", object()))
    with pytest.raises(StorageUnavailableError):
        repository.update_task_if_version(
            _mutation(
                original,
                TaskUpdatePatch(priority=80),
                occurred_at=_CREATED_AT - timedelta(seconds=1),
            )
        )

    first = repository.update_task_if_version(
        _mutation(original, TaskUpdatePatch(priority=80))
    )
    after_first = _state_snapshot(repository.database_path)
    with pytest.raises(StorageUnavailableError):
        repository.update_task_if_version(
            _mutation(
                first.task,
                TaskUpdatePatch(priority=90),
                expected_version=2,
                occurred_at=_UPDATED_AT - timedelta(microseconds=1),
            )
        )

    assert before != after_first
    assert _state_snapshot(repository.database_path) == after_first
