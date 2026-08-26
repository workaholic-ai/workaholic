"""Integration tests for atomic optimistic SQLite Task dependencies."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from workaholic.application import (
    AddTaskDependencyMutation,
    ApplicationErrorCode,
    BootstrapMutation,
    DependencyConflictError,
    DependencyCycleError,
    GetTask,
    IdempotencyConflictError,
    InvalidTransitionError,
    RemoveTaskDependencyMutation,
    TaskCreationMutation,
    TaskNotFoundError,
    VersionConflictError,
)
from workaholic.domain import (
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
    from collections.abc import Callable
    from pathlib import Path

_CREATED_AT = datetime(2026, 8, 1, 8, 0, 0, 111111, tzinfo=UTC)
_UPDATED_AT = datetime(2026, 8, 1, 9, 0, 0, 222222, tzinfo=UTC)


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, tuple[Task, ...]]:
    """Create one bootstrapped Project with four independent Tasks.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and Tasks ordered by Project-local number.

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
    tasks = tuple(
        repository.create_task(
            TaskCreationMutation(
                task_id=TaskId(f"tsk_{number}"),
                event_id=TaskEventId(f"evt_create_{number}"),
                request_id=RequestId(f"req_create_{number}"),
                project_id=bootstrap.project.id,
                actor_subject_id=bootstrap.subject.id,
                occurred_at=_CREATED_AT + timedelta(seconds=number),
                title=f"Task {number}",
                objective=f"Task {number}",
                priority=50,
            )
        )
        for number in range(1, 5)
    )
    return repository, tasks


def _add(  # noqa: PLR0913 - explicit mutation controls aid boundary tests.
    task: Task,
    prerequisite: Task,
    suffix: str,
    *,
    expected_version: int = 1,
    idempotency_key: str | None = None,
    occurred_at: datetime = _UPDATED_AT,
) -> AddTaskDependencyMutation:
    """Build one attributable dependency addition mutation.

    Args:
        task: Dependant Task.
        prerequisite: Proposed prerequisite Task.
        suffix: Attribution identity suffix.
        expected_version: Optimistic dependant version.
        idempotency_key: Optional caller replay key.
        occurred_at: Authoritative mutation timestamp.

    Returns:
        Validated addition mutation.

    """
    return AddTaskDependencyMutation(
        task_uid=task.uid,
        prerequisite_uid=prerequisite.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        event_id=TaskEventId(f"evt_{suffix}"),
        claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )


def _remove(
    task: Task,
    prerequisite: Task,
    suffix: str,
    *,
    expected_version: int,
    idempotency_key: str | None = None,
) -> RemoveTaskDependencyMutation:
    """Build one attributable dependency removal mutation.

    Args:
        task: Dependant Task.
        prerequisite: Existing prerequisite Task.
        suffix: Attribution identity suffix.
        expected_version: Optimistic dependant version.
        idempotency_key: Optional caller replay key.

    Returns:
        Validated removal mutation.

    """
    return RemoveTaskDependencyMutation(
        task_uid=task.uid,
        prerequisite_uid=prerequisite.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        event_id=TaskEventId(f"evt_{suffix}"),
        claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=_UPDATED_AT + timedelta(minutes=1),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )


def _snapshot(database_path: Path) -> tuple[tuple[object, ...], ...]:
    """Read all dependency-owned rows for rollback comparisons.

    Args:
        database_path: Initialized SQLite store.

    Returns:
        Stable rows from Tasks, edges, events, and dependency idempotency.

    """
    with open_read_connection(database_path) as connection:
        return (
            tuple(connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall()),
            tuple(
                connection.execute(
                    """
                    SELECT * FROM task_dependencies
                    ORDER BY task_uid, prerequisite_uid
                    """
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    "SELECT * FROM task_events ORDER BY cursor"
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    """
                    SELECT * FROM idempotency_records
                    WHERE operation IN ('task.dependency.add', 'task.dependency.remove')
                    ORDER BY operation, caller_key
                    """
                ).fetchall()
            ),
        )


def test_add_and_remove_round_trip_versions_only_dependant_and_append_events(
    tmp_path: Path,
) -> None:
    """Each graph edit versions one Task and emits one safe update event."""
    repository, tasks = _repository(tmp_path)
    dependant, prerequisite = tasks[:2]

    added = repository.add_task_dependency(
        _add(
            dependant,
            prerequisite,
            "add",
            idempotency_key="add-edge-1",
        )
    )

    assert added.task.depends_on == (prerequisite.uid,)
    assert added.task.version == 2
    assert added.task.updated_at == _UPDATED_AT
    assert added.events[0].event_type is TaskEventType.TASK_UPDATED
    assert dict(added.events[0].payload) == {
        "dependency": "added",
        "prerequisite_uid": str(prerequisite.uid),
        "version": 2,
    }
    unchanged_prerequisite = repository.get_task(
        GetTask(
            project_id=prerequisite.project_id,
            subject_id=SubjectId("sub_local"),
            task=prerequisite.uid,
        )
    )
    assert unchanged_prerequisite == prerequisite

    removed = repository.remove_task_dependency(
        _remove(
            added.task,
            prerequisite,
            "remove",
            expected_version=2,
            idempotency_key="remove-edge-1",
        )
    )

    assert removed.task.depends_on == ()
    assert removed.task.version == 3
    assert dict(removed.events[0].payload) == {
        "dependency": "removed",
        "prerequisite_uid": str(prerequisite.uid),
        "version": 3,
    }
    reopened = SQLiteRepository(repository.database_path)
    assert (
        reopened.get_task(
            GetTask(
                project_id=dependant.project_id,
                subject_id=SubjectId("sub_local"),
                task=dependant.uid,
            )
        )
        == removed.task
    )


def test_dependencies_are_returned_in_stable_human_key_order(tmp_path: Path) -> None:
    """Insertion order cannot leak through the immutable Task dependency tuple."""
    repository, tasks = _repository(tmp_path)
    dependant, first, second = tasks[:3]
    after_second = repository.add_task_dependency(
        _add(dependant, second, "second")
    ).task

    after_first = repository.add_task_dependency(
        _add(after_second, first, "first", expected_version=2)
    ).task

    assert after_first.depends_on == (first.uid, second.uid)


@pytest.mark.parametrize(
    "state",
    [TaskState.REVIEW, TaskState.DONE, TaskState.CANCELLED],
)
def test_dependency_changes_reject_review_and_terminal_dependants_atomically(
    state: TaskState,
    tmp_path: Path,
) -> None:
    """Only open and blocked Tasks permit graph edits."""
    repository, tasks = _repository(tmp_path)
    dependant, prerequisite = tasks[:2]
    with open_write_transaction(repository.database_path) as connection:
        if state is TaskState.REVIEW:
            connection.execute(
                """
                INSERT INTO task_results (
                    id, task_uid, submitted_by, submitted_at, review_status
                ) VALUES ('res_pending', ?, 'sub_local', ?, 'pending')
                """,
                (str(dependant.uid), "2026-08-01T08:30:00.123456Z"),
            )
        connection.execute(
            "UPDATE tasks SET state = ?, current_result_id = ? WHERE uid = ?",
            (
                state.value,
                "res_pending" if state is TaskState.REVIEW else None,
                str(dependant.uid),
            ),
        )
    before = _snapshot(repository.database_path)

    with pytest.raises(InvalidTransitionError) as captured:
        repository.add_task_dependency(_add(dependant, prerequisite, "invalid"))

    assert captured.value.code is ApplicationErrorCode.INVALID_TRANSITION
    assert _snapshot(repository.database_path) == before


def test_self_duplicate_absent_and_cross_project_edges_are_typed_conflicts(
    tmp_path: Path,
) -> None:
    """Expected non-cycle graph conflicts never leak SQLite failures."""
    repository, tasks = _repository(tmp_path)
    dependant, prerequisite = tasks[:2]
    foreign = replace(
        prerequisite,
        uid=TaskId("tsk_foreign"),
        project_id=ProjectId("prj_other"),
        key="OTHER-2",
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            )
            VALUES ('prj_other', 'ins_local', 'OTHER', 'Other', 3, ?)
            """,
            ("2026-08-01T08:00:00.111111Z",),
        )
        values = (
            str(foreign.uid),
            str(foreign.project_id),
            foreign.number,
            foreign.key,
            foreign.title,
            foreign.objective,
            foreign.state.value,
            foreign.priority,
            None,
            foreign.approval.value,
            "[]",
            "[]",
            None,
            None,
            1,
            str(foreign.created_by),
            "2026-08-01T08:00:01.111111Z",
            "2026-08-01T08:00:01.111111Z",
        )
        connection.execute(
            """
            INSERT INTO tasks (
                uid, project_id, number, key, title, objective, state, priority,
                available_at, approval, acceptance_json, context_json,
                blocking_reason, current_result_id, version, created_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    cases = (
        _add(dependant, dependant, "self"),
        _add(dependant, foreign, "foreign"),
    )
    for mutation in cases:
        before = _snapshot(repository.database_path)
        with pytest.raises(DependencyConflictError):
            repository.add_task_dependency(mutation)
        assert _snapshot(repository.database_path) == before

    added = repository.add_task_dependency(_add(dependant, prerequisite, "valid"))
    operations: tuple[Callable[[], object], ...] = (
        lambda: repository.add_task_dependency(
            _add(added.task, prerequisite, "duplicate", expected_version=2)
        ),
        lambda: repository.remove_task_dependency(
            _remove(added.task, tasks[2], "absent", expected_version=2)
        ),
    )
    for execute in operations:
        before = _snapshot(repository.database_path)
        with pytest.raises(DependencyConflictError):
            execute()
        assert _snapshot(repository.database_path) == before


def test_missing_prerequisite_is_task_not_found_without_mutation(
    tmp_path: Path,
) -> None:
    """An unknown Task identity is distinguished from a graph conflict."""
    repository, tasks = _repository(tmp_path)
    missing = TaskId("tsk_missing")
    mutation = AddTaskDependencyMutation(
        task_uid=tasks[0].uid,
        prerequisite_uid=missing,
        project_id=tasks[0].project_id,
        actor_subject_id=SubjectId("sub_local"),
        event_id=TaskEventId("evt_missing"),
        claim_expired_event_id=TaskEventId("evt_missing_expired"),
        request_id=RequestId("req_missing"),
        occurred_at=_UPDATED_AT,
        expected_version=1,
    )
    before = _snapshot(repository.database_path)

    with pytest.raises(TaskNotFoundError) as captured:
        repository.add_task_dependency(mutation)

    assert captured.value.code is ApplicationErrorCode.TASK_NOT_FOUND
    assert _snapshot(repository.database_path) == before


def test_cycles_of_multiple_depths_are_rejected_without_partial_edges(
    tmp_path: Path,
) -> None:
    """Cycle detection follows complete graph paths without recursion limits."""
    repository, tasks = _repository(tmp_path)
    first, second, third = tasks[:3]
    second_with_edge = repository.add_task_dependency(
        _add(second, first, "second_first")
    ).task
    third_with_edge = repository.add_task_dependency(
        _add(third, second_with_edge, "third_second")
    ).task
    before = _snapshot(repository.database_path)

    with pytest.raises(DependencyCycleError) as captured:
        repository.add_task_dependency(_add(first, third_with_edge, "cycle"))

    assert captured.value.code is ApplicationErrorCode.DEPENDENCY_CYCLE
    assert _snapshot(repository.database_path) == before


def test_preexisting_corrupt_cycle_is_storage_failure_not_semantic_conflict(
    tmp_path: Path,
) -> None:
    """A corrupted persisted graph is never misreported as caller input conflict."""
    repository, tasks = _repository(tmp_path)
    first, second, third = tasks[:3]
    with open_write_transaction(repository.database_path) as connection:
        connection.executemany(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (
                (str(first.uid), str(second.uid), str(first.project_id)),
                (str(second.uid), str(first.uid), str(first.project_id)),
            ),
        )
    before = _snapshot(repository.database_path)

    with pytest.raises(StorageUnavailableError):
        repository.add_task_dependency(_add(third, first, "corrupt"))

    assert _snapshot(repository.database_path) == before


def test_stale_version_and_idempotency_conflict_leave_graph_unchanged(
    tmp_path: Path,
) -> None:
    """Optimistic and replay preconditions cover edges, events, and records."""
    repository, tasks = _repository(tmp_path)
    dependant, prerequisite, other = tasks[:3]
    mutation = _add(
        dependant,
        prerequisite,
        "first",
        idempotency_key="edge-1",
    )
    result = repository.add_task_dependency(mutation)
    assert repository.add_task_dependency(mutation) == result
    before = _snapshot(repository.database_path)

    with pytest.raises(IdempotencyConflictError):
        repository.add_task_dependency(
            _add(dependant, other, "conflict", idempotency_key="edge-1")
        )
    assert _snapshot(repository.database_path) == before
    with pytest.raises(VersionConflictError):
        repository.add_task_dependency(_add(dependant, other, "stale"))
    assert _snapshot(repository.database_path) == before


def test_event_insert_failure_rolls_back_edge_version_and_idempotency(
    tmp_path: Path,
) -> None:
    """A late unique-event failure cannot expose a partially changed graph."""
    repository, tasks = _repository(tmp_path)
    dependant, prerequisite = tasks[:2]
    mutation = _add(dependant, prerequisite, "create_1", idempotency_key="edge-1")
    before = _snapshot(repository.database_path)

    with pytest.raises(StorageUnavailableError):
        repository.add_task_dependency(mutation)

    assert _snapshot(repository.database_path) == before


def test_concurrent_graph_edits_with_same_version_have_one_winner(
    tmp_path: Path,
) -> None:
    """SQLite serialization plus optimistic version permits one concurrent edit."""
    repository, tasks = _repository(tmp_path)
    dependant, first, second = tasks[:3]
    mutations = (
        _add(dependant, first, "race_first"),
        _add(dependant, second, "race_second"),
    )

    def execute(mutation: AddTaskDependencyMutation) -> object:
        """Run one competing mutation through an independent repository."""
        try:
            return SQLiteRepository(repository.database_path).add_task_dependency(
                mutation
            )
        except VersionConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(execute, mutations))

    successes = tuple(item for item in outcomes if not isinstance(item, Exception))
    conflicts = tuple(
        item for item in outcomes if isinstance(item, VersionConflictError)
    )
    assert len(successes) == 1
    assert len(conflicts) == 1
    persisted = repository.get_task(
        GetTask(
            project_id=dependant.project_id,
            subject_id=SubjectId("sub_local"),
            task=dependant.uid,
        )
    )
    assert persisted.version == 2
    assert len(persisted.depends_on) == 1
