"""Integration tests for non-mutating Phase 4 Claim query projections."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from workaholic.application import (
    BootstrapMutation,
    GetTaskDetails,
    ListTasksByView,
    TaskCreationMutation,
    TaskListView,
)
from workaholic.domain import (
    AttemptId,
    InstanceId,
    ProjectId,
    ReadinessReason,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    initialize_empty_store,
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite._records import serialize_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult, TaskDetails
    from workaholic.domain import Task

_CREATED_AT = datetime(2026, 8, 1, 10, tzinfo=UTC)
_QUERY_TIME = datetime(2026, 8, 2, 12, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_acme")
_SUBJECT_ID = SubjectId("sub_local")


class _Clock:
    """Return one fixed authoritative UTC query time."""

    def __init__(self, now: datetime) -> None:
        """Store the fixed time.

        Args:
            now: UTC time returned by every call.

        """
        self._now = now

    def now(self) -> datetime:
        """Return the fixed authoritative time."""
        return self._now


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, BootstrapResult]:
    """Create one bootstrapped repository with an injected query clock.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and committed bootstrap graph.

    """
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path, clock=_Clock(_QUERY_TIME))
    bootstrap = repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_CREATED_AT,
            project_key="ACME",
        )
    )
    return repository, bootstrap


def _create_task(
    repository: SQLiteRepository,
    suffix: str,
    *,
    seconds: int,
    priority: int = 50,
    available_at: datetime | None = None,
) -> Task:
    """Create one deterministic Task through the production repository.

    Args:
        repository: Bootstrapped SQLite repository.
        suffix: Opaque identity and title suffix.
        seconds: Creation-time offset.
        priority: Ready-order priority.
        available_at: Optional UTC scheduling boundary.

    Returns:
        Committed Task.

    """
    return repository.create_task(
        TaskCreationMutation(
            task_id=TaskId(f"tsk_{suffix}"),
            event_id=TaskEventId(f"evt_{suffix}"),
            request_id=RequestId(f"req_{suffix}"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT + timedelta(seconds=seconds),
            title=f"Task {suffix}",
            objective=f"Complete Task {suffix}.",
            priority=priority,
            available_at=available_at,
        )
    )


def _insert_claim(
    repository: SQLiteRepository,
    task: Task,
    *,
    lease_expires_at: datetime,
    attempt_id: AttemptId | None,
) -> None:
    """Insert one schema-valid stored Human or Agent Claim fixture.

    Args:
        repository: Repository owning the physical store.
        task: Task receiving the Claim.
        lease_expires_at: Exact half-open Lease boundary.
        attempt_id: Null Human ownership or Agent identity.

    """
    claimed_at = _QUERY_TIME - timedelta(minutes=5)
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
                    str(_SUBJECT_ID),
                    serialize_timestamp(claimed_at),
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
                str(_SUBJECT_ID),
                None if attempt_id is None else str(attempt_id),
                serialize_timestamp(claimed_at),
                serialize_timestamp(lease_expires_at),
            ),
        )


def _details(repository: SQLiteRepository, task: Task) -> TaskDetails:
    """Read one Task detail projection through its stable identity.

    Args:
        repository: Repository supplying the authoritative query clock.
        task: Task selected by canonical identity.

    Returns:
        Complete Task and Phase 4 readiness details.

    """
    return repository.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )


@pytest.mark.parametrize("attempt_id", [None, AttemptId("atm_current")])
def test_current_human_and_agent_claims_are_running_and_returned(
    attempt_id: AttemptId | None,
    tmp_path: Path,
) -> None:
    """An unexpired Claim is returned as exclusive running ownership."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "current", seconds=1)
    _insert_claim(
        repository,
        task,
        lease_expires_at=_QUERY_TIME + timedelta(seconds=1),
        attempt_id=attempt_id,
    )
    before = repository.database_path.read_bytes()

    details = _details(repository, task)

    assert details.claim is not None
    assert details.claim.attempt_id == attempt_id
    assert (details.attempt is not None) is (attempt_id is not None)
    assert details.readiness.running is True
    assert details.readiness.ready is False
    assert details.readiness.stale is False
    assert details.readiness.reasons == (ReadinessReason.ACTIVE_CLAIM,)
    assert repository.database_path.read_bytes() == before


@pytest.mark.parametrize("attempt_id", [None, AttemptId("atm_stale")])
def test_exact_expiry_is_stale_ready_hidden_and_stable_after_reopen(
    attempt_id: AttemptId | None,
    tmp_path: Path,
) -> None:
    """At Lease expiry stored ownership is stale, non-owning, and read-only."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "stale", seconds=1)
    _insert_claim(
        repository,
        task,
        lease_expires_at=_QUERY_TIME,
        attempt_id=attempt_id,
    )
    before = repository.database_path.read_bytes()

    details = _details(repository, task)
    reopened = SQLiteRepository(repository.database_path, clock=_Clock(_QUERY_TIME))
    reopened_details = _details(reopened, task)

    assert details == reopened_details
    assert details.claim is None
    assert details.attempt is None
    assert details.readiness.ready is True
    assert details.readiness.running is False
    assert details.readiness.stale is True
    assert details.readiness.reasons == (ReadinessReason.STALE_CLAIM,)
    assert repository.database_path.read_bytes() == before
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM task_claims").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM task_events").fetchone() == (1,)
        assert connection.execute(
            "SELECT version FROM tasks WHERE uid = ?",
            (str(task.uid),),
        ).fetchone() == (1,)


def test_views_align_active_stale_and_other_readiness_reasons(
    tmp_path: Path,
) -> None:
    """Ready filtering and pagination respect Claims plus existing blockers."""
    repository, bootstrap = _repository(tmp_path)
    active = _create_task(repository, "active", seconds=1, priority=100)
    stale = _create_task(repository, "stale", seconds=2, priority=90)
    ready = _create_task(repository, "ready", seconds=3, priority=80)
    scheduled = _create_task(
        repository,
        "scheduled",
        seconds=4,
        priority=70,
        available_at=_QUERY_TIME + timedelta(hours=1),
    )
    blocked = _create_task(repository, "blocked", seconds=5, priority=60)
    prerequisite = _create_task(repository, "prerequisite", seconds=6)
    dependant = _create_task(repository, "dependant", seconds=7, priority=95)
    _insert_claim(
        repository,
        active,
        lease_expires_at=_QUERY_TIME + timedelta(minutes=1),
        attempt_id=AttemptId("atm_active"),
    )
    _insert_claim(
        repository,
        stale,
        lease_expires_at=_QUERY_TIME,
        attempt_id=None,
    )
    _insert_claim(
        repository,
        scheduled,
        lease_expires_at=_QUERY_TIME,
        attempt_id=None,
    )
    _insert_claim(
        repository,
        blocked,
        lease_expires_at=_QUERY_TIME + timedelta(minutes=1),
        attempt_id=None,
    )
    _insert_claim(
        repository,
        dependant,
        lease_expires_at=_QUERY_TIME,
        attempt_id=None,
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE tasks SET state = 'blocked', blocking_reason = 'Waiting.'
            WHERE uid = ?
            """,
            (str(blocked.uid),),
        )
        connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (str(dependant.uid), str(prerequisite.uid), str(_PROJECT_ID)),
        )
    before = repository.database_path.read_bytes()

    all_page = repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.ALL,
        )
    )
    projections = dict(
        zip(
            (task.uid for task in all_page.tasks),
            all_page.readiness,
            strict=True,
        )
    )
    first_ready = repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.READY,
            limit=1,
        )
    )
    second_ready = repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.READY,
            limit=1,
            cursor=first_ready.next_cursor,
        )
    )
    third_ready = repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.READY,
            limit=1,
            cursor=second_ready.next_cursor,
        )
    )

    assert projections[active.uid].running is True
    assert projections[stale.uid].stale is True
    assert projections[scheduled.uid].reasons == (
        ReadinessReason.NOT_YET_AVAILABLE,
        ReadinessReason.STALE_CLAIM,
    )
    assert projections[blocked.uid].reasons == (
        ReadinessReason.TASK_BLOCKED,
        ReadinessReason.ACTIVE_CLAIM,
    )
    assert projections[dependant.uid].reasons == (
        ReadinessReason.UNSATISFIED_DEPENDENCY,
        ReadinessReason.STALE_CLAIM,
    )
    assert first_ready.tasks == (stale,)
    assert first_ready.next_cursor is not None
    assert second_ready.tasks == (ready,)
    assert second_ready.next_cursor is not None
    assert third_ready.tasks == (prerequisite,)
    assert third_ready.next_cursor is None
    assert repository.database_path.read_bytes() == before


def test_malformed_agent_claim_state_fails_closed_without_writes(
    tmp_path: Path,
) -> None:
    """A dangling Agent Claim cannot be mistaken for Human or current state."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "malformed", seconds=1)
    connection = sqlite3.connect(repository.database_path)
    try:
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
                str(_SUBJECT_ID),
                "atm_missing",
                serialize_timestamp(_QUERY_TIME - timedelta(minutes=1)),
                serialize_timestamp(_QUERY_TIME + timedelta(minutes=1)),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    before = repository.database_path.read_bytes()

    with pytest.raises(StorageUnavailableError):
        _details(repository, task)

    assert repository.database_path.read_bytes() == before
