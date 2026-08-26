"""Integration tests for transactional Claim renewal and release semantics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING, Literal

import pytest

from workaholic.application import (
    BootstrapMutation,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetTaskDetails,
    IdempotencyConflictError,
    LeaseLostError,
    ReleaseClaimMutation,
    RenewClaimMutation,
    TaskClaimResult,
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
)
from workaholic.persistence.sqlite._records import require_integer, serialize_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult
    from workaholic.domain import Task

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 21, 9, tzinfo=UTC)
_CLAIM_TIME = datetime(2026, 8, 21, 10, tzinfo=UTC)
_OPERATION_TIME = datetime(2026, 8, 21, 10, 5, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_leases")
_SUBJECT_ID = SubjectId("sub_local")


class _Clock:
    """Return one fixed authoritative query time."""

    def now(self) -> datetime:
        """Return the post-acquisition operation time."""
        return _OPERATION_TIME


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, BootstrapResult, Task]:
    """Bootstrap one repository and one ready Task."""
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
    task = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_lease"),
            event_id=TaskEventId("evt_create_lease"),
            request_id=RequestId("req_create_lease"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            title="Lease lifecycle",
            objective="Verify exact transactional ownership semantics.",
            priority=50,
        )
    )
    return repository, bootstrap, task


def _claim_human(repository: SQLiteRepository, task: Task) -> TaskClaimResult:
    """Acquire one Human Claim with its default resolved eight-hour Lease."""
    return repository.claim_task(
        ClaimTaskMutation(
            project_id=task.project_id,
            task_uid=task.uid,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_claim_human"),
            occurred_at=_CLAIM_TIME,
            lease_duration_seconds=28_800,
            task_claimed_event_id=TaskEventId("evt_claim_human"),
            claim_expired_event_id=TaskEventId("evt_expire_human"),
        )
    )


def _claim_agent(repository: SQLiteRepository) -> TaskClaimResult:
    """Acquire one Agent Claim with its default resolved fifteen-minute Lease."""
    return repository.claim_next_task(
        ClaimNextTaskMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_claim_agent"),
            occurred_at=_CLAIM_TIME,
            attempt_id=AttemptId("atm_current"),
            lease_duration_seconds=900,
            task_claimed_event_id=TaskEventId("evt_claim_agent"),
            claim_expired_event_id=TaskEventId("evt_expire_agent"),
        )
    )


def _renewal(  # noqa: PLR0913 - exact mutation fixture boundary.
    task: Task,
    suffix: str,
    *,
    attempt_id: AttemptId | None,
    duration: int,
    occurred_at: datetime = _OPERATION_TIME,
    idempotency_key: str | None = None,
    event_id: TaskEventId | None = None,
) -> RenewClaimMutation:
    """Build one exact Human-renew or Agent-heartbeat mutation."""
    return RenewClaimMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_renew_{suffix}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        lease_duration_seconds=duration,
        claim_renewed_event_id=(
            event_id if event_id is not None else TaskEventId(f"evt_renew_{suffix}")
        ),
        idempotency_key=idempotency_key,
    )


def _release(  # noqa: PLR0913 - exact mutation fixture boundary.
    task: Task,
    suffix: str,
    *,
    attempt_id: AttemptId | None,
    occurred_at: datetime = _OPERATION_TIME,
    idempotency_key: str | None = None,
    event_id: TaskEventId | None = None,
) -> ReleaseClaimMutation:
    """Build one exact Human or Agent release mutation."""
    return ReleaseClaimMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_release_{suffix}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        claim_released_event_id=(
            event_id if event_id is not None else TaskEventId(f"evt_release_{suffix}")
        ),
        idempotency_key=idempotency_key,
    )


def _counts(repository: SQLiteRepository) -> tuple[int, int, int, int]:
    """Return Attempt, Claim, event, and idempotency row counts."""
    with open_read_connection(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_attempts),
                (SELECT count(*) FROM task_claims),
                (SELECT count(*) FROM task_events),
                (SELECT count(*) FROM idempotency_records)
            """
        ).fetchone()
    if row is None:
        raise AssertionError
    return (
        require_integer(row[0], minimum=0),
        require_integer(row[1], minimum=0),
        require_integer(row[2], minimum=0),
        require_integer(row[3], minimum=0),
    )


def _run_lease_operation(
    repository: SQLiteRepository,
    mutation: RenewClaimMutation | ReleaseClaimMutation,
) -> TaskClaimResult:
    """Dispatch one typed Lease fixture through its semantic repository method."""
    if isinstance(mutation, RenewClaimMutation):
        return repository.renew_claim(mutation)
    return repository.release_claim(mutation)


@pytest.mark.parametrize(
    ("owner", "duration"),
    [
        ("human", 60),
        ("human", 2_592_000),
        ("agent", 1),
        ("agent", 86_400),
    ],
)
def test_renewal_replaces_expiry_at_every_owner_bound(
    owner: Literal["human", "agent"],
    duration: int,
    tmp_path: Path,
) -> None:
    """Renewal replaces, rather than extends, Lease expiry at every exact bound."""
    repository, _bootstrap, task = _repository(tmp_path)
    claimed = (
        _claim_human(repository, task)
        if owner == "human"
        else _claim_agent(repository)
    )
    assert claimed.claim is not None
    attempt_id = None if owner == "human" else AttemptId("atm_current")

    renewed = repository.renew_claim(
        _renewal(
            task,
            f"{owner}_{duration}",
            attempt_id=attempt_id,
            duration=duration,
        )
    )

    expected_expiry = _OPERATION_TIME + timedelta(seconds=duration)
    assert renewed.task == task
    assert renewed.claim is not None
    assert renewed.claim.claimed_at == _CLAIM_TIME
    assert renewed.claim.lease_expires_at == expected_expiry
    assert renewed.claim.lease_expires_at != (
        claimed.claim.lease_expires_at + timedelta(seconds=duration)
    )
    assert tuple(event.event_type for event in renewed.events) == (
        TaskEventType.CLAIM_RENEWED,
    )
    assert dict(renewed.events[0].payload) == {
        "lease_expires_at": serialize_timestamp(expected_expiry)
    }
    if owner == "human":
        assert renewed.attempt is None
    else:
        assert renewed.attempt is not None
        assert renewed.attempt.status is AttemptStatus.ACTIVE
        assert renewed.attempt.lease_expires_at == expected_expiry
    reopened = SQLiteRepository(repository.database_path, clock=_Clock())
    details = reopened.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )
    assert details.task.version == task.version
    assert details.task.updated_at == task.updated_at


@pytest.mark.parametrize("owner", ["human", "agent"])
def test_release_removes_claim_and_terminalizes_nullable_attempt(
    owner: Literal["human", "agent"],
    tmp_path: Path,
) -> None:
    """Release removes ownership and ends only the Agent execution record."""
    repository, _bootstrap, task = _repository(tmp_path)
    claimed = (
        _claim_human(repository, task)
        if owner == "human"
        else _claim_agent(repository)
    )
    assert claimed.claim is not None
    attempt_id = None if owner == "human" else AttemptId("atm_current")

    released = repository.release_claim(
        _release(task, owner, attempt_id=attempt_id)
    )

    assert released.task == task
    assert released.claim is None
    assert tuple(event.event_type for event in released.events) == (
        TaskEventType.CLAIM_RELEASED,
    )
    assert released.events[0].attempt_id == attempt_id
    assert dict(released.events[0].payload) == {
        "lease_expires_at": serialize_timestamp(claimed.claim.lease_expires_at)
    }
    if owner == "human":
        assert released.attempt is None
    else:
        assert released.attempt is not None
        assert released.attempt.status is AttemptStatus.RELEASED
        assert released.attempt.ended_at == _OPERATION_TIME
        assert released.attempt.lease_expires_at == claimed.claim.lease_expires_at
    assert _counts(repository)[1] == 0
    reopened = SQLiteRepository(repository.database_path, clock=_Clock())
    details = reopened.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )
    assert details.claim is None
    assert details.attempt is None
    assert details.task.version == task.version
    with pytest.raises(LeaseLostError):
        reopened.release_claim(
            _release(task, f"{owner}_again", attempt_id=attempt_id)
        )


@pytest.mark.parametrize("operation", ["renew", "release"])
def test_human_path_cannot_mutate_current_agent_claim(
    operation: Literal["renew", "release"],
    tmp_path: Path,
) -> None:
    """A null-Attempt Human token sees an active Agent owner as locked."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_agent(repository)
    before = _counts(repository)

    mutation: RenewClaimMutation | ReleaseClaimMutation = (
        _renewal(task, operation, attempt_id=None, duration=60)
        if operation == "renew"
        else _release(task, operation, attempt_id=None)
    )
    with pytest.raises(TaskLockedError):
        _run_lease_operation(repository, mutation)

    assert _counts(repository) == before


@pytest.mark.parametrize("operation", ["renew", "release"])
def test_agent_path_requires_exact_current_attempt(
    operation: Literal["renew", "release"],
    tmp_path: Path,
) -> None:
    """Unknown, Human-owned, and superseded Agent tokens collapse to Lease lost."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_agent(repository)
    wrong_attempt = AttemptId("atm_wrong")
    before = _counts(repository)

    mutation: RenewClaimMutation | ReleaseClaimMutation = (
        _renewal(task, operation, attempt_id=wrong_attempt, duration=60)
        if operation == "renew"
        else _release(task, operation, attempt_id=wrong_attempt)
    )
    with pytest.raises(LeaseLostError):
        _run_lease_operation(repository, mutation)

    assert _counts(repository) == before


def test_agent_token_cannot_operate_on_human_claim(tmp_path: Path) -> None:
    """A fabricated Agent token cannot take over a current Human Claim."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_human(repository, task)
    before = _counts(repository)

    with pytest.raises(LeaseLostError):
        repository.renew_claim(
            _renewal(
                task,
                "agent_on_human",
                attempt_id=AttemptId("atm_unknown"),
                duration=60,
            )
        )

    assert _counts(repository) == before


@pytest.mark.parametrize("operation", ["renew", "release"])
def test_exact_expiry_loses_lease_without_materializing_stale_state(
    operation: Literal["renew", "release"],
    tmp_path: Path,
) -> None:
    """At ``now == expiry`` Agent ownership is lost with zero lazy writes."""
    repository, _bootstrap, task = _repository(tmp_path)
    repository.claim_next_task(
        ClaimNextTaskMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_short_claim"),
            occurred_at=_CLAIM_TIME,
            attempt_id=AttemptId("atm_short"),
            lease_duration_seconds=1,
            task_claimed_event_id=TaskEventId("evt_short_claim"),
            claim_expired_event_id=TaskEventId("evt_short_expiry"),
        )
    )
    exact_expiry = _CLAIM_TIME + timedelta(seconds=1)
    before = _counts(repository)

    mutation: RenewClaimMutation | ReleaseClaimMutation = (
        _renewal(
            task,
            "expired",
            attempt_id=AttemptId("atm_short"),
            duration=1,
            occurred_at=exact_expiry,
            idempotency_key="expired",
        )
        if operation == "renew"
        else _release(
            task,
            "expired",
            attempt_id=AttemptId("atm_short"),
            occurred_at=exact_expiry,
            idempotency_key="expired",
        )
    )
    with pytest.raises(LeaseLostError):
        _run_lease_operation(repository, mutation)

    assert _counts(repository) == before
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = 'atm_short'"
        ).fetchone() == (AttemptStatus.ACTIVE.value, None)


def test_missing_claim_returns_lease_lost_without_consuming_key(tmp_path: Path) -> None:
    """A missing owner token fails uniformly and leaves no idempotency residue."""
    repository, _bootstrap, task = _repository(tmp_path)
    before = _counts(repository)

    with pytest.raises(LeaseLostError):
        repository.release_claim(
            _release(task, "missing", attempt_id=None, idempotency_key="missing")
        )

    assert _counts(repository) == before


def test_renewal_idempotency_replays_and_semantic_change_conflicts(
    tmp_path: Path,
) -> None:
    """Renewal retry returns the original expiry and rejects changed duration."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_agent(repository)
    first = repository.renew_claim(
        _renewal(
            task,
            "first",
            attempt_id=AttemptId("atm_current"),
            duration=60,
            idempotency_key="renew-once",
        )
    )
    before = _counts(repository)

    replay = repository.renew_claim(
        _renewal(
            task,
            "replay",
            attempt_id=AttemptId("atm_current"),
            duration=60,
            occurred_at=_OPERATION_TIME + timedelta(seconds=1),
            idempotency_key="renew-once",
        )
    )

    assert replay == first
    assert _counts(repository) == before
    with pytest.raises(IdempotencyConflictError):
        repository.renew_claim(
            _renewal(
                task,
                "conflict",
                attempt_id=AttemptId("atm_current"),
                duration=61,
                idempotency_key="renew-once",
            )
        )
    assert _counts(repository) == before


def test_release_idempotency_replays_after_claim_is_gone(tmp_path: Path) -> None:
    """Release retry returns its closed outcome before checking current ownership."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_agent(repository)
    first = repository.release_claim(
        _release(
            task,
            "first",
            attempt_id=AttemptId("atm_current"),
            idempotency_key="release-once",
        )
    )
    before = _counts(repository)

    replay = repository.release_claim(
        _release(
            task,
            "replay",
            attempt_id=AttemptId("atm_current"),
            occurred_at=_OPERATION_TIME + timedelta(seconds=1),
            idempotency_key="release-once",
        )
    )

    assert replay == first
    assert _counts(repository) == before
    with pytest.raises(IdempotencyConflictError):
        repository.release_claim(
            _release(task, "conflict", attempt_id=None, idempotency_key="release-once")
        )


@pytest.mark.parametrize("operation", ["renew", "release"])
def test_event_collision_rolls_back_lease_operation(
    operation: Literal["renew", "release"],
    tmp_path: Path,
) -> None:
    """A late event failure rolls back every Lease and Attempt mutation."""
    repository, _bootstrap, task = _repository(tmp_path)
    claimed = _claim_agent(repository)
    before = _counts(repository)
    assert claimed.claim is not None

    mutation: RenewClaimMutation | ReleaseClaimMutation = (
        _renewal(
            task,
            "rollback",
            attempt_id=AttemptId("atm_current"),
            duration=60,
            idempotency_key="rollback",
            event_id=TaskEventId("evt_create_lease"),
        )
        if operation == "renew"
        else _release(
            task,
            "rollback",
            attempt_id=AttemptId("atm_current"),
            idempotency_key="rollback",
            event_id=TaskEventId("evt_create_lease"),
        )
    )
    with pytest.raises(StorageUnavailableError):
        _run_lease_operation(repository, mutation)

    assert _counts(repository) == before
    details = repository.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=_SUBJECT_ID,
            task=task.uid,
        )
    )
    assert details.claim == claimed.claim
    assert details.attempt == claimed.attempt


def test_two_concurrent_releases_have_one_winner(tmp_path: Path) -> None:
    """Independent connections serialize release so only one owner can end it."""
    repository, _bootstrap, task = _repository(tmp_path)
    _claim_agent(repository)
    barrier = Barrier(2)

    def release_or_lost(index: int) -> TaskClaimResult | LeaseLostError:
        """Release through one independent connection after both workers start."""
        worker = SQLiteRepository(repository.database_path, clock=_Clock())
        mutation = _release(
            task,
            f"race_{index}",
            attempt_id=AttemptId("atm_current"),
        )
        barrier.wait(timeout=10)
        try:
            return worker.release_claim(mutation)
        except LeaseLostError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(release_or_lost, (1, 2)))

    winners = tuple(item for item in outcomes if isinstance(item, TaskClaimResult))
    lost = tuple(item for item in outcomes if isinstance(item, LeaseLostError))
    assert len(winners) == 1
    assert len(lost) == 1
    assert _counts(repository)[1] == 0
