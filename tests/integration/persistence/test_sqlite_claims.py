"""Integration coverage for atomic SQLite Claim acquisition."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from workaholic.application import (
    BootstrapMutation,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetTaskDetails,
    IdempotencyConflictError,
    InvalidTransitionError,
    NoTaskAvailableError,
    TaskCreationMutation,
    TaskLockedError,
)
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskEventType,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite._records import serialize_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult
    from workaholic.domain import Task

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 20, 9, tzinfo=UTC)
_NOW = datetime(2026, 8, 20, 10, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_claims")
_SUBJECT_ID = SubjectId("sub_local")


class _Clock:
    """Return one fixed authoritative query time."""

    def now(self) -> datetime:
        """Return the fixed Phase 4 test timestamp."""
        return _NOW


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, BootstrapResult]:
    """Bootstrap one isolated version-4 repository."""
    repository = SQLiteRepository(tmp_path / "local.db", clock=_Clock())
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
    priority: int = 50,
    available_at: datetime | None = None,
) -> Task:
    """Create one deterministic Task through the public repository façade."""
    return repository.create_task(
        TaskCreationMutation(
            task_id=TaskId(f"tsk_{suffix}"),
            event_id=TaskEventId(f"evt_create_{suffix}"),
            request_id=RequestId(f"req_create_{suffix}"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            title=f"Task {suffix}",
            objective=f"Complete Task {suffix}.",
            priority=priority,
            available_at=available_at,
        )
    )


def _human_mutation(
    task: Task,
    suffix: str,
    *,
    occurred_at: datetime = _NOW,
    duration: int = 28_800,
    idempotency_key: str | None = None,
) -> ClaimTaskMutation:
    """Build one exact targeted Human Claim mutation."""
    return ClaimTaskMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_human_{suffix}"),
        occurred_at=occurred_at,
        lease_duration_seconds=duration,
        task_claimed_event_id=TaskEventId(f"evt_claimed_{suffix}"),
        claim_expired_event_id=TaskEventId(f"evt_expired_{suffix}"),
        idempotency_key=idempotency_key,
    )


def _agent_mutation(
    suffix: str,
    *,
    occurred_at: datetime = _NOW,
    duration: int = 900,
    idempotency_key: str | None = None,
    claimed_event_id: TaskEventId | None = None,
) -> ClaimNextTaskMutation:
    """Build one exact Project-scoped Agent Claim mutation."""
    return ClaimNextTaskMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_agent_{suffix}"),
        occurred_at=occurred_at,
        attempt_id=AttemptId(f"atm_{suffix}"),
        lease_duration_seconds=duration,
        task_claimed_event_id=(
            claimed_event_id
            if claimed_event_id is not None
            else TaskEventId(f"evt_claimed_{suffix}")
        ),
        claim_expired_event_id=TaskEventId(f"evt_expired_{suffix}"),
        idempotency_key=idempotency_key,
    )


def _insert_claim(
    repository: SQLiteRepository,
    task: Task,
    *,
    attempt_id: AttemptId | None,
    lease_expires_at: datetime,
) -> None:
    """Insert one schema-valid current-or-stale Claim fixture."""
    claimed_at = _NOW - timedelta(minutes=5)
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


def _row_counts(repository: SQLiteRepository) -> tuple[int, int, int, int]:
    """Return Attempt, Claim, event, and idempotency row counts."""
    with open_read_connection(repository.database_path) as connection:
        return (
            connection.execute("SELECT count(*) FROM task_attempts").fetchone()[0],
            connection.execute("SELECT count(*) FROM task_claims").fetchone()[0],
            connection.execute("SELECT count(*) FROM task_events").fetchone()[0],
            connection.execute("SELECT count(*) FROM idempotency_records").fetchone()[
                0
            ],
        )


def test_human_claim_is_atomic_attributable_and_task_version_stable(
    tmp_path: Path,
) -> None:
    """A Human target creates one null-Attempt Lease without touching Task state."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "human")

    result = repository.claim_task(_human_mutation(task, "human"))

    assert result.task == task
    assert result.claim is not None
    assert result.claim.attempt_id is None
    assert result.claim.claimed_at == _NOW
    assert result.claim.lease_expires_at == _NOW + timedelta(hours=8)
    assert result.attempt is None
    assert tuple(event.event_type for event in result.events) == (
        TaskEventType.TASK_CLAIMED,
    )
    assert result.events[0].attempt_id is None
    assert dict(result.events[0].payload) == {
        "lease_expires_at": serialize_timestamp(_NOW + timedelta(hours=8))
    }
    details = repository.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )
    assert details.task.version == task.version
    assert details.task.updated_at == task.updated_at


def test_same_owner_human_reclaim_is_an_exact_noop(tmp_path: Path) -> None:
    """Repeated owned Human Claim does not renew, emit, or update the Task."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "human_noop")
    first = repository.claim_task(_human_mutation(task, "first"))
    before = _row_counts(repository)

    repeated = repository.claim_task(
        _human_mutation(
            task,
            "second",
            occurred_at=_NOW + timedelta(hours=1),
            duration=60,
        )
    )

    assert repeated.task == first.task
    assert repeated.claim == first.claim
    assert repeated.attempt is None
    assert repeated.events == ()
    assert _row_counts(repository) == before


def test_human_target_rejects_agent_owner_and_unready_task(tmp_path: Path) -> None:
    """A current Agent token locks its Task and ordinary unready state conflicts."""
    repository, _bootstrap = _repository(tmp_path)
    locked = _create_task(repository, "locked", priority=90)
    scheduled = _create_task(
        repository,
        "scheduled",
        available_at=_NOW + timedelta(seconds=1),
    )
    repository.claim_next_task(_agent_mutation("owner"))
    before = _row_counts(repository)

    with pytest.raises(TaskLockedError):
        repository.claim_task(_human_mutation(locked, "locked"))
    with pytest.raises(InvalidTransitionError):
        repository.claim_task(_human_mutation(scheduled, "scheduled"))

    assert _row_counts(repository) == before


def test_agent_pull_uses_ready_order_and_skips_dependencies_schedule_and_locks(
    tmp_path: Path,
) -> None:
    """Agent selection applies complete readiness before deterministic ranking."""
    repository, _bootstrap = _repository(tmp_path)
    prerequisite = _create_task(repository, "prerequisite", priority=1)
    dependent = _create_task(repository, "dependent", priority=100)
    _scheduled = _create_task(
        repository,
        "scheduled_agent",
        priority=99,
        available_at=_NOW + timedelta(seconds=1),
    )
    locked = _create_task(repository, "active_lock", priority=98)
    available_later = _create_task(
        repository,
        "available_later",
        priority=80,
        available_at=_NOW - timedelta(seconds=1),
    )
    absent_availability = _create_task(
        repository,
        "absent_availability",
        priority=80,
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (str(dependent.uid), str(prerequisite.uid), str(_PROJECT_ID)),
        )
    _insert_claim(
        repository,
        locked,
        attempt_id=None,
        lease_expires_at=_NOW + timedelta(minutes=1),
    )

    result = repository.claim_next_task(_agent_mutation("ordered"))

    assert result.task.uid == absent_availability.uid
    assert result.task.priority == available_later.priority
    assert result.claim is not None
    assert result.claim.attempt_id == AttemptId("atm_ordered")
    assert result.attempt is not None
    assert result.attempt.status is AttemptStatus.ACTIVE
    assert result.task.version == absent_availability.version


def test_agent_pull_with_no_ready_candidate_commits_nothing(tmp_path: Path) -> None:
    """No availability returns the stable retryable error with zero residue."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(
        repository,
        "none",
        available_at=_NOW + timedelta(hours=1),
    )
    before = _row_counts(repository)

    with pytest.raises(NoTaskAvailableError):
        repository.claim_next_task(_agent_mutation("none", idempotency_key="no-task"))

    assert _row_counts(repository) == before
    assert task.version == 1


@pytest.mark.parametrize("old_attempt_id", [None, AttemptId("atm_expired_old")])
def test_exact_expiry_is_materialized_before_agent_reclaim(
    old_attempt_id: AttemptId | None,
    tmp_path: Path,
) -> None:
    """Selected stale ownership ends before a new Agent Claim is recorded."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "reclaim")
    _insert_claim(
        repository,
        task,
        attempt_id=old_attempt_id,
        lease_expires_at=_NOW,
    )

    result = repository.claim_next_task(_agent_mutation("new"))

    assert result.task == task
    assert result.claim is not None
    assert result.claim.attempt_id == AttemptId("atm_new")
    assert tuple(event.event_type for event in result.events) == (
        TaskEventType.CLAIM_EXPIRED,
        TaskEventType.TASK_CLAIMED,
    )
    assert result.events[0].attempt_id == old_attempt_id
    assert result.events[1].attempt_id == AttemptId("atm_new")
    assert dict(result.events[0].payload) == {
        "lease_expires_at": serialize_timestamp(_NOW)
    }
    with open_read_connection(repository.database_path) as connection:
        if old_attempt_id is not None:
            assert connection.execute(
                "SELECT status, ended_at FROM task_attempts WHERE id = ?",
                (str(old_attempt_id),),
            ).fetchone() == (AttemptStatus.EXPIRED.value, serialize_timestamp(_NOW))
        assert connection.execute(
            "SELECT count(*) FROM task_claims WHERE task_uid = ?",
            (str(task.uid),),
        ).fetchone() == (1,)


def test_human_claim_materializes_stale_human_owner(tmp_path: Path) -> None:
    """A targeted Human Claim can reclaim an exactly expired Human Lease."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "human_reclaim")
    _insert_claim(
        repository,
        task,
        attempt_id=None,
        lease_expires_at=_NOW,
    )

    result = repository.claim_task(_human_mutation(task, "human_reclaim"))

    assert result.claim is not None
    assert result.claim.claimed_at == _NOW
    assert tuple(event.event_type for event in result.events) == (
        TaskEventType.CLAIM_EXPIRED,
        TaskEventType.TASK_CLAIMED,
    )
    assert all(event.attempt_id is None for event in result.events)


def test_agent_claim_idempotency_replays_closed_outcome_and_conflicts(
    tmp_path: Path,
) -> None:
    """Generated identities do not prevent replay; semantic changes conflict."""
    repository, _bootstrap = _repository(tmp_path)
    _create_task(repository, "idempotent")
    first = repository.claim_next_task(
        _agent_mutation("first", idempotency_key="pull-once")
    )
    before = _row_counts(repository)

    replay = repository.claim_next_task(
        _agent_mutation(
            "retry",
            occurred_at=_NOW + timedelta(seconds=1),
            idempotency_key="pull-once",
        )
    )

    assert replay == first
    assert _row_counts(repository) == before
    with pytest.raises(IdempotencyConflictError):
        repository.claim_next_task(
            _agent_mutation(
                "conflict",
                duration=901,
                idempotency_key="pull-once",
            )
        )
    assert _row_counts(repository) == before


def test_event_failure_rolls_back_attempt_claim_and_idempotency(tmp_path: Path) -> None:
    """A late event collision cannot expose partial Claim acquisition state."""
    repository, _bootstrap = _repository(tmp_path)
    _create_task(repository, "rollback")
    before = _row_counts(repository)

    with pytest.raises(StorageUnavailableError):
        repository.claim_next_task(
            _agent_mutation(
                "rollback",
                idempotency_key="rollback",
                claimed_event_id=TaskEventId("evt_create_rollback"),
            )
        )

    assert _row_counts(repository) == before


def test_claim_rejects_authoritative_clock_regression_without_residue(
    tmp_path: Path,
) -> None:
    """A Claim cannot predate the Task snapshot it would purportedly observe."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "clock_regression")
    before = _row_counts(repository)

    with pytest.raises(StorageUnavailableError):
        repository.claim_task(
            _human_mutation(
                task,
                "clock_regression",
                occurred_at=_CREATED_AT,
            )
        )

    assert _row_counts(repository) == before


def test_idempotent_replay_fails_closed_when_original_event_is_missing(
    tmp_path: Path,
) -> None:
    """A durable replay never invents an event deleted by external corruption."""
    repository, _bootstrap = _repository(tmp_path)
    _create_task(repository, "corrupt_replay")
    mutation = _agent_mutation(
        "corrupt_replay",
        idempotency_key="corrupt-replay",
    )
    result = repository.claim_next_task(mutation)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "DELETE FROM task_events WHERE id = ?",
            (str(result.events[-1].id),),
        )

    with pytest.raises(StorageUnavailableError):
        repository.claim_next_task(mutation)


def test_claim_survives_repository_reopen_and_rejects_wrong_runtime_type(
    tmp_path: Path,
) -> None:
    """Durable ownership rehydrates while public methods validate at runtime."""
    repository, _bootstrap = _repository(tmp_path)
    task = _create_task(repository, "reopen")
    claimed = repository.claim_next_task(_agent_mutation("reopen"))

    reopened = SQLiteRepository(repository.database_path, clock=_Clock())
    details = reopened.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )
    assert details.claim == claimed.claim
    assert details.attempt == claimed.attempt
    with pytest.raises(StorageUnavailableError):
        reopened.claim_next_task(object())  # type: ignore[arg-type]
