"""Integration tests for atomic idempotent SQLite Task creation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from operator import attrgetter
from threading import Event
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    IdempotencyConflictError,
    PermissionDeniedError,
    TaskCreationMutation,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskEventId,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLitePhaseOneRepository,
    StorageUnavailableError,
    initialize_empty_store,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

_NOW = datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC)
_CANONICAL_NOW = "2026-07-30T12:15:30.654321Z"


def _repository(tmp_path: Path) -> SQLitePhaseOneRepository:
    """Create an initialized and bootstrapped SQLite repository.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository with one enabled local Owner and ACME Project.

    """
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLitePhaseOneRepository(database_path)
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW - timedelta(minutes=1),
            project_key="ACME",
        )
    )
    return repository


def _mutation(
    suffix: str,
    *,
    title: str = "First task",
    objective: str = "First task",
    idempotency_key: str | None = None,
    occurred_at: datetime = _NOW,
) -> TaskCreationMutation:
    """Build one valid Task creation mutation.

    Args:
        suffix: Opaque generated-identity suffix.
        title: Normalized Task title.
        objective: Normalized desired outcome.
        idempotency_key: Optional caller retry key.
        occurred_at: Authoritative transaction timestamp.

    Returns:
        Validated attributable mutation.

    """
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{suffix}"),
        event_id=TaskEventId(f"evt_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        project_id=ProjectId("prj_acme"),
        actor_subject_id=SubjectId("sub_local"),
        occurred_at=occurred_at,
        title=title,
        objective=objective,
        priority=50,
        idempotency_key=idempotency_key,
    )


def _task_counts(database_path: Path) -> tuple[int, int, int, int]:
    """Read Task, event, allocation, and task-idempotency counts.

    Args:
        database_path: Initialized bootstrapped store.

    Returns:
        Task count, event count, next number, and task idempotency count.

    """
    with open_read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM tasks),
                (SELECT count(*) FROM task_events),
                (SELECT next_task_number FROM projects WHERE id = 'prj_acme'),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'task.create'
                )
            """
        ).fetchone()
    assert row is not None
    assert len(row) == 4
    assert all(type(value) is int for value in row)
    return cast("tuple[int, int, int, int]", row)


def _disable_subject(connection: sqlite3.Connection) -> object:
    """Disable the active local Subject."""
    return connection.execute("UPDATE subjects SET enabled = 0 WHERE id = 'sub_local'")


def _remove_grant(connection: sqlite3.Connection) -> object:
    """Remove the active Subject's Project grant."""
    return connection.execute("DELETE FROM project_grants")


def _create_pair(
    pair: tuple[SQLitePhaseOneRepository, TaskCreationMutation],
) -> Task:
    """Create one Task through a repository/mutation concurrency pair.

    Args:
        pair: Repository and mutation to execute.

    Returns:
        Committed Task.

    """
    repository, mutation = pair
    return repository.create_task(mutation)


def test_create_task_commits_stable_task_and_attributable_event(
    tmp_path: Path,
) -> None:
    """One mutation commits allocation, Task, and explanatory event together."""
    repository = _repository(tmp_path)

    task = repository.create_task(_mutation("first"))

    assert task.number == 1
    assert task.key == "ACME-1"
    assert task.title == "First task"
    assert task.objective == "First task"
    assert task.priority == 50
    assert task.version == 1
    assert task.created_at == _NOW
    assert task.updated_at == _NOW
    assert task.created_by == SubjectId("sub_local")
    assert _task_counts(repository.database_path) == (1, 1, 2, 0)
    with open_read_connection(repository.database_path) as connection:
        event = connection.execute(
            """
            SELECT cursor, id, task_uid, project_id, actor_subject_id,
                   request_id, event_type, occurred_at, payload_json
            FROM task_events
            """
        ).fetchone()
    assert event is not None
    assert event[0:8] == (
        1,
        "evt_first",
        "tsk_first",
        "prj_acme",
        "sub_local",
        "req_first",
        "task_created",
        _CANONICAL_NOW,
    )
    payload = json.loads(event[8])
    assert payload == {
        "key": "ACME-1",
        "number": 1,
        "objective": "First task",
        "priority": 50,
        "state": "open",
        "title": "First task",
        "version": 1,
    }
    assert not {
        "request_id",
        "database",
        "storage",
        "secret",
        "token",
    }.intersection(payload)


def test_matching_idempotency_replay_returns_original_task_and_event(
    tmp_path: Path,
) -> None:
    """Matching semantic input replays without allocating or emitting again."""
    repository = _repository(tmp_path)
    first = repository.create_task(_mutation("first", idempotency_key="task-add-1"))

    replayed = repository.create_task(
        _mutation(
            "replay",
            idempotency_key="task-add-1",
            occurred_at=_NOW + timedelta(hours=1),
        )
    )

    assert replayed == first
    assert _task_counts(repository.database_path) == (1, 1, 2, 1)
    with open_read_connection(repository.database_path) as connection:
        outcome = connection.execute(
            """
            SELECT subject_scope, operation, caller_key, outcome_json, created_at
            FROM idempotency_records
            WHERE operation = 'task.create'
            """
        ).fetchone()
    assert outcome is not None
    assert outcome[0:3] == ("sub_local", "task.create", "task-add-1")
    decoded = json.loads(outcome[3])
    assert decoded["event_id"] == "evt_first"
    assert decoded["task"]["uid"] == "tsk_first"
    assert decoded["task"]["key"] == "ACME-1"
    assert outcome[4] == _CANONICAL_NOW


def test_conflicting_idempotency_reuse_changes_nothing(tmp_path: Path) -> None:
    """One caller key cannot represent a different title or other semantic input."""
    repository = _repository(tmp_path)
    repository.create_task(_mutation("first", idempotency_key="task-add-1"))
    before = _task_counts(repository.database_path)

    with pytest.raises(IdempotencyConflictError) as captured:
        repository.create_task(
            _mutation(
                "conflict",
                title="Different task",
                objective="Different task",
                idempotency_key="task-add-1",
            )
        )

    assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
    assert _task_counts(repository.database_path) == before


@pytest.mark.parametrize(
    "tamper",
    [
        "project",
        "shape",
        "event_type",
        "task_shape",
        "task_uid",
    ],
)
def test_tampered_idempotency_outcome_is_never_replayed(
    tamper: str,
    tmp_path: Path,
) -> None:
    """Replay validates outcome shape, Task fields, and its attributable event."""
    repository = _repository(tmp_path)
    repository.create_task(_mutation("first", idempotency_key="task-add-1"))
    with open_write_transaction(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT outcome_json
            FROM idempotency_records
            WHERE operation = 'task.create'
            """
        ).fetchone()
        assert row is not None
        outcome = json.loads(row[0])
        if tamper == "project":
            outcome["task"]["project_id"] = "prj_wrong"
        elif tamper == "shape":
            outcome = {"wrong": "shape"}
        elif tamper == "event_type":
            outcome["event_id"] = 7
        elif tamper == "task_shape":
            del outcome["task"]["title"]
        else:
            outcome["task"]["uid"] = "invalid"
        connection.execute(
            """
            UPDATE idempotency_records
            SET outcome_json = ?
            WHERE operation = 'task.create'
            """,
            (json.dumps(outcome, separators=(",", ":"), sort_keys=True),),
        )

    with pytest.raises(StorageUnavailableError):
        repository.create_task(_mutation("replay", idempotency_key="task-add-1"))


def test_idempotency_replay_requires_its_persisted_event(tmp_path: Path) -> None:
    """A replay outcome cannot hide a missing Task-created event."""
    repository = _repository(tmp_path)
    repository.create_task(_mutation("first", idempotency_key="task-add-1"))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute("DELETE FROM task_events")

    with pytest.raises(StorageUnavailableError):
        repository.create_task(_mutation("replay", idempotency_key="task-add-1"))


@pytest.mark.parametrize("revoke", [_disable_subject, _remove_grant])
def test_disabled_or_non_owner_subject_cannot_create(
    revoke: Callable[[sqlite3.Connection], object],
    tmp_path: Path,
) -> None:
    """Task creation revalidates active Owner authorization in its transaction."""
    repository = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        revoke(connection)

    with pytest.raises(PermissionDeniedError) as captured:
        repository.create_task(_mutation("forbidden"))

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED
    assert _task_counts(repository.database_path)[0:2] == (0, 0)


def test_failure_after_task_insert_rolls_back_task_event_and_number(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An event failure commits neither Task nor allocation increment."""
    repository = _repository(tmp_path)

    def fail_event(*_arguments: object, **_keywords: object) -> object:
        """Simulate event persistence failure after Task insertion."""
        message = "injected event failure"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._tasks._insert_task_event",
        fail_event,
    )

    with pytest.raises(RuntimeError, match="injected"):
        repository.create_task(_mutation("failed", idempotency_key="failed-task"))

    assert _task_counts(repository.database_path) == (0, 0, 1, 0)


def test_observer_cannot_see_uncommitted_task_without_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A concurrent reader sees neither side of an unfinished mutation."""
    repository = _repository(tmp_path)
    task_inserted = Event()
    release_writer = Event()

    def block_then_fail(*_arguments: object, **_keywords: object) -> object:
        """Pause after Task insertion and fail after the observer reads."""
        task_inserted.set()
        assert release_writer.wait(timeout=5)
        message = "injected event failure"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._tasks._insert_task_event",
        block_then_fail,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(repository.create_task, _mutation("blocked"))
        assert task_inserted.wait(timeout=5)
        with open_read_connection(repository.database_path) as connection:
            assert connection.execute("SELECT count(*) FROM tasks").fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM task_events"
            ).fetchone() == (0,)
        release_writer.set()
        with pytest.raises(RuntimeError, match="injected"):
            pending.result(timeout=5)

    assert _task_counts(repository.database_path) == (0, 0, 1, 0)


def test_concurrent_creates_allocate_distinct_monotonic_numbers(
    tmp_path: Path,
) -> None:
    """Separate connections serialize allocation without duplicate Human keys."""
    repository = _repository(tmp_path)
    repositories = [
        SQLitePhaseOneRepository(repository.database_path) for _ in range(10)
    ]
    mutations = [
        _mutation(str(index), title=f"Task {index}", objective=f"Task {index}")
        for index in range(10)
    ]

    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = list(
            executor.map(
                _create_pair,
                zip(repositories, mutations, strict=True),
            )
        )

    ordered = sorted(tasks, key=attrgetter("number"))
    assert [task.number for task in ordered] == list(range(1, 11))
    assert [task.key for task in ordered] == [
        f"ACME-{number}" for number in range(1, 11)
    ]
    assert _task_counts(repository.database_path) == (10, 10, 11, 0)
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT cursor FROM task_events ORDER BY cursor"
        ).fetchall() == [(number,) for number in range(1, 11)]


def test_concurrent_matching_idempotency_creates_one_task(tmp_path: Path) -> None:
    """Concurrent first uses of one caller key commit one logical mutation."""
    repository = _repository(tmp_path)
    repositories = [
        SQLitePhaseOneRepository(repository.database_path) for _ in range(4)
    ]
    mutations = [
        _mutation(str(index), idempotency_key="concurrent-task") for index in range(4)
    ]

    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = list(
            executor.map(
                _create_pair,
                zip(repositories, mutations, strict=True),
            )
        )

    assert len(set(tasks)) == 1
    assert _task_counts(repository.database_path) == (1, 1, 2, 1)


def test_allocator_accepts_valid_gaps_without_reusing_numbers(tmp_path: Path) -> None:
    """A higher persisted next number remains authoritative and stable."""
    repository = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "UPDATE projects SET next_task_number = 3 WHERE id = 'prj_acme'"
        )

    task = repository.create_task(_mutation("third"))

    assert task.number == 3
    assert task.key == "ACME-3"
    assert _task_counts(repository.database_path) == (1, 1, 4, 0)


def test_repository_runtime_rejects_unvalidated_task_mutation(tmp_path: Path) -> None:
    """The repository does not trust its TaskCreationMutation type hint alone."""
    repository = _repository(tmp_path)

    with pytest.raises(StorageUnavailableError):
        repository.create_task(
            object(),  # type: ignore[arg-type]
        )
