"""Integration tests for transactional structured Agent progress reporting."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from workaholic.application import (
    BootstrapMutation,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetTask,
    GetTaskDetails,
    IdempotencyConflictError,
    LeaseLostError,
    ReadTaskEvents,
    ReleaseClaimMutation,
    ReportTaskProgressMutation,
    TaskCreationMutation,
    TaskProgressResult,
)
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    DomainValidationError,
    InstanceId,
    ObservationKind,
    ProgressObservation,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskClaim,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskProgress,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite._claim_state import StoredClaimState
from workaholic.persistence.sqlite._task_execution import (
    _append_progress_events,
    _progress_fingerprint,
    _read_idempotent_progress,
    _require_agent_attempt,
    _require_matching_progress_result,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import TaskClaimResult

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 23, 9, tzinfo=UTC)
_CLAIMED_AT = datetime(2026, 8, 23, 10, tzinfo=UTC)
_REPORTED_AT = datetime(2026, 8, 23, 10, 5, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_progress")
_SUBJECT_ID = SubjectId("sub_local")
_ATTEMPT_ID = AttemptId("atm_progress")


class _Clock:
    """Return one fixed time for restart-safe Task detail reads."""

    def now(self) -> datetime:
        """Return a time inside the current Agent Lease.

        Returns:
            Fixed authoritative UTC timestamp.

        """
        return _REPORTED_AT


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, Task]:
    """Bootstrap one Project and ready Task.

    Args:
        tmp_path: Isolated test directory.

    Returns:
        Initialized repository and its unchanged progress target.

    """
    repository = SQLiteRepository(tmp_path / "local.db", clock=_Clock())
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_progress"),
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            request_id=RequestId("req_bootstrap_progress"),
            occurred_at=_CREATED_AT,
            project_key="ACME",
        )
    )
    task = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_progress"),
            event_id=TaskEventId("evt_create_progress"),
            request_id=RequestId("req_create_progress"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            title="Report Agent progress",
            objective="Persist bounded progress without mutating Task state.",
            priority=50,
        )
    )
    return repository, task


def _claim_agent(
    repository: SQLiteRepository,
    *,
    attempt_id: AttemptId = _ATTEMPT_ID,
    claimed_at: datetime = _CLAIMED_AT,
    duration: int = 900,
) -> TaskClaimResult:
    """Acquire the only ready Task through one Agent Attempt.

    Args:
        repository: Initialized repository.
        attempt_id: Generated Agent execution identity.
        claimed_at: Authoritative acquisition time.
        duration: Positive Agent Lease duration in seconds.

    Returns:
        Current Agent Claim and Attempt.

    """
    return repository.claim_next_task(
        ClaimNextTaskMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId(f"req_claim_{attempt_id}"),
            occurred_at=claimed_at,
            attempt_id=attempt_id,
            lease_duration_seconds=duration,
            task_claimed_event_id=TaskEventId(f"evt_claim_{attempt_id}"),
            claim_expired_event_id=TaskEventId(f"evt_expire_{attempt_id}"),
        )
    )


def _progress_mutation(  # noqa: PLR0913 - explicit identity fixture boundary.
    task: Task,
    progress: TaskProgress,
    suffix: str,
    *,
    attempt_id: AttemptId = _ATTEMPT_ID,
    occurred_at: datetime = _REPORTED_AT,
    idempotency_key: str | None = None,
    event_ids: tuple[TaskEventId, ...] | None = None,
) -> ReportTaskProgressMutation:
    """Build one exact progress mutation with aligned event identities.

    Args:
        task: Claimed target Task.
        progress: Validated structured progress.
        suffix: Identity fixture suffix.
        attempt_id: Exact Agent owner token.
        occurred_at: Authoritative report time.
        idempotency_key: Optional caller replay key.
        event_ids: Optional complete header-plus-observation identities.

    Returns:
        Validated repository mutation.

    """
    observation_count = len(progress.observations or ())
    selected_ids = event_ids or (
        TaskEventId(f"evt_progress_{suffix}"),
        *(
            TaskEventId(f"evt_observation_{suffix}_{index}")
            for index in range(observation_count)
        ),
    )
    if len(selected_ids) != observation_count + 1:
        raise AssertionError
    return ReportTaskProgressMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_progress_{suffix}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        progress=progress,
        progress_reported_event_id=selected_ids[0],
        observation_event_ids=selected_ids[1:],
        idempotency_key=idempotency_key,
    )


def _snapshot(repository: SQLiteRepository) -> tuple[object, ...]:
    """Read all progress-relevant durable rows for rollback comparisons.

    Args:
        repository: Initialized SQLite repository.

    Returns:
        Stable Task, ownership, event, Result, and idempotency rows.

    """
    with open_read_connection(repository.database_path) as connection:
        return (
            connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall(),
            connection.execute(
                "SELECT * FROM task_claims ORDER BY task_uid"
            ).fetchall(),
            connection.execute("SELECT * FROM task_attempts ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM task_results ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM task_events ORDER BY cursor").fetchall(),
            connection.execute(
                "SELECT * FROM idempotency_records ORDER BY operation, caller_key"
            ).fetchall(),
        )


def test_progress_persists_closed_ordered_events_without_mutating_task(
    tmp_path: Path,
) -> None:
    """Every observation kind remains ordered, attributable, inert, and durable."""
    repository, task = _repository(tmp_path)
    claimed = _claim_agent(repository)
    observations = tuple(
        ProgressObservation(kind=kind, text=f"Observed {kind.value}")
        for kind in ObservationKind
    )
    progress = TaskProgress(
        message="  Persistence implemented  ",
        percent_complete=70,
        observations=observations,
    )
    mutation = _progress_mutation(task, progress, "complete")

    result = repository.report_task_progress(mutation)

    assert result.task == task
    assert result.claim == claimed.claim
    assert result.attempt == claimed.attempt
    assert result.task.version == task.version
    assert result.task.updated_at == task.updated_at
    assert tuple(event.event_type for event in result.events) == (
        TaskEventType.PROGRESS_REPORTED,
        *(TaskEventType.OBSERVATION_ADDED for _item in observations),
    )
    assert dict(result.events[0].payload) == {
        "message": "Persistence implemented",
        "percent_complete": 70,
    }
    assert tuple(dict(event.payload) for event in result.events[1:]) == tuple(
        {"kind": observation.kind.value, "text": observation.text}
        for observation in observations
    )
    assert tuple(event.id for event in result.events) == (
        mutation.progress_reported_event_id,
        *mutation.observation_event_ids,
    )
    assert all(
        event.actor_subject_id == _SUBJECT_ID
        and event.attempt_id == _ATTEMPT_ID
        and event.request_id == mutation.request_id
        and event.occurred_at == _REPORTED_AT
        for event in result.events
    )
    assert (
        repository.get_task(
            GetTask(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task=task.uid)
        )
        == task
    )
    details = repository.get_task_details(
        GetTaskDetails(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task=task.uid)
    )
    assert details.task == task
    assert details.claim == claimed.claim
    assert details.attempt == claimed.attempt

    reopened = SQLiteRepository(repository.database_path, clock=_Clock())
    first_page = reopened.read_task_events_after(
        ReadTaskEvents(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=task.uid,
            after=result.events[0].cursor - 1,
            limit=2,
        )
    )
    second_page = reopened.read_task_events_after(
        ReadTaskEvents(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=task.key,
            after=first_page.events[-1].cursor,
            limit=10,
        )
    )
    assert tuple(event.id for event in first_page.events) == tuple(
        event.id for event in result.events[:2]
    )
    assert tuple(event.id for event in second_page.events) == tuple(
        event.id for event in result.events[2:]
    )
    assert (
        reopened.get_task_details(
            GetTaskDetails(
                project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task=task.uid
            )
        ).task
        == task
    )


@pytest.mark.parametrize("percent_complete", [0, 100])
def test_progress_accepts_inclusive_percent_boundaries(
    percent_complete: int,
    tmp_path: Path,
) -> None:
    """Both documented percentage boundaries produce one header event."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    progress = TaskProgress(percent_complete=percent_complete)

    result = repository.report_task_progress(
        _progress_mutation(task, progress, f"percent_{percent_complete}")
    )

    assert len(result.events) == 1
    assert dict(result.events[0].payload) == {"percent_complete": percent_complete}


def test_progress_accepts_maximum_observation_batch_and_empty_collection(
    tmp_path: Path,
) -> None:
    """Empty and maximum collections retain their distinct bounded semantics."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    empty = repository.report_task_progress(
        _progress_mutation(task, TaskProgress(observations=()), "empty")
    )
    maximum_observations = tuple(
        ProgressObservation(ObservationKind.NOTE, "x" * 4_000) for _index in range(50)
    )

    maximum = repository.report_task_progress(
        _progress_mutation(
            task,
            TaskProgress(observations=maximum_observations),
            "maximum",
            idempotency_key="maximum-observations",
        )
    )

    assert len(empty.events) == 1
    assert dict(empty.events[0].payload) == {}
    assert len(maximum.events) == 51
    assert all(
        isinstance(event.payload["text"], str) and len(event.payload["text"]) == 4_000
        for event in maximum.events[1:]
    )


@pytest.mark.parametrize(
    "progress_input",
    [
        pytest.param({}, id="missing-fields"),
        pytest.param({"message": ""}, id="empty-message"),
        pytest.param({"message": "x" * 4_001}, id="long-message"),
        pytest.param({"percent_complete": -1}, id="negative-percent"),
        pytest.param({"percent_complete": 101}, id="large-percent"),
        pytest.param({"percent_complete": True}, id="boolean-percent"),
        pytest.param(
            {
                "observations": tuple(
                    ProgressObservation(ObservationKind.NOTE, str(index))
                    for index in range(51)
                )
            },
            id="too-many-observations",
        ),
    ],
)
def test_invalid_progress_is_rejected_before_persistence(
    progress_input: dict[str, object],
    tmp_path: Path,
) -> None:
    """Malformed structured input cannot reach or change the repository."""
    repository, _task = _repository(tmp_path)
    before = _snapshot(repository)

    with pytest.raises(DomainValidationError):
        TaskProgress(**progress_input)  # type: ignore[arg-type]

    assert _snapshot(repository) == before


def test_unknown_observation_kind_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    """An unknown observation discriminator cannot reach storage."""
    repository, _task = _repository(tmp_path)
    before = _snapshot(repository)

    with pytest.raises(DomainValidationError, match="Observation kind"):
        ProgressObservation(
            "unknown",  # type: ignore[arg-type]
            "Unknown kind",
        )

    assert _snapshot(repository) == before


def test_progress_mutation_rejects_unknown_fields_and_event_misalignment(
    tmp_path: Path,
) -> None:
    """The application boundary remains closed and one-to-one with observations."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    progress = TaskProgress(
        observations=(ProgressObservation(ObservationKind.NOTE, "One"),)
    )
    valid = _progress_mutation(task, progress, "closed")
    values = {name: getattr(valid, name) for name in type(valid).model_fields}

    with pytest.raises(ValidationError, match="Extra inputs"):
        ReportTaskProgressMutation.model_validate({**values, "unknown": True})
    with pytest.raises(ValidationError, match="one-for-one"):
        ReportTaskProgressMutation.model_validate(
            {**values, "observation_event_ids": ()}
        )


@pytest.mark.parametrize("owner", ["human", "foreign-attempt", "expired"])
def test_progress_requires_exact_current_agent_lease(
    owner: str,
    tmp_path: Path,
) -> None:
    """Human, foreign, and half-open expired owner tokens all collapse safely."""
    repository, task = _repository(tmp_path)
    report_time = _REPORTED_AT
    attempt_id = _ATTEMPT_ID
    if owner == "human":
        repository.claim_task(
            ClaimTaskMutation(
                project_id=_PROJECT_ID,
                task_uid=task.uid,
                actor_subject_id=_SUBJECT_ID,
                request_id=RequestId("req_claim_human_progress"),
                occurred_at=_CLAIMED_AT,
                lease_duration_seconds=28_800,
                task_claimed_event_id=TaskEventId("evt_claim_human_progress"),
                claim_expired_event_id=TaskEventId("evt_expire_human_progress"),
            )
        )
    else:
        duration = 300 if owner == "expired" else 900
        _claim_agent(repository, duration=duration)
        if owner == "foreign-attempt":
            attempt_id = AttemptId("atm_foreign")
        else:
            report_time = _CLAIMED_AT + timedelta(seconds=duration)
    before = _snapshot(repository)

    with pytest.raises(LeaseLostError):
        repository.report_task_progress(
            _progress_mutation(
                task,
                TaskProgress(message="Should fail"),
                owner,
                attempt_id=attempt_id,
                occurred_at=report_time,
                idempotency_key=f"lost-{owner}",
            )
        )

    assert _snapshot(repository) == before


def test_equivalent_progress_replays_after_release_and_conflicts_are_inert(
    tmp_path: Path,
) -> None:
    """A keyed outcome survives terminal ownership while semantic reuse conflicts."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    progress = TaskProgress(message="Working", percent_complete=50)
    original = repository.report_task_progress(
        _progress_mutation(
            task,
            progress,
            "original",
            idempotency_key="progress-run",
        )
    )
    repository.release_claim(
        ReleaseClaimMutation(
            project_id=_PROJECT_ID,
            task_uid=task.uid,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId("req_release_after_progress"),
            occurred_at=_REPORTED_AT + timedelta(minutes=1),
            attempt_id=_ATTEMPT_ID,
            claim_released_event_id=TaskEventId("evt_release_after_progress"),
        )
    )
    before_replay = _snapshot(repository)

    replay = repository.report_task_progress(
        _progress_mutation(
            task,
            progress,
            "replay",
            occurred_at=_REPORTED_AT + timedelta(minutes=2),
            idempotency_key="progress-run",
        )
    )

    assert replay == original
    assert _snapshot(repository) == before_replay
    with pytest.raises(IdempotencyConflictError):
        repository.report_task_progress(
            _progress_mutation(
                task,
                replace(progress, percent_complete=51),
                "conflict",
                occurred_at=_REPORTED_AT + timedelta(minutes=3),
                idempotency_key="progress-run",
            )
        )
    assert _snapshot(repository) == before_replay


def test_concurrent_equivalent_progress_commits_one_logical_batch(
    tmp_path: Path,
) -> None:
    """Concurrent first uses of one key converge on the sole committed outcome."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    barrier = Barrier(2)

    def report(suffix: str) -> TaskProgressResult:
        """Synchronize and submit one equivalent keyed progress mutation.

        Args:
            suffix: Generated identity suffix excluded from the fingerprint.

        Returns:
            Fresh or idempotently replayed progress outcome.

        """
        mutation = _progress_mutation(
            task,
            TaskProgress(message="Concurrent progress", percent_complete=25),
            suffix,
            idempotency_key="concurrent-progress",
        )
        barrier.wait()
        return repository.report_task_progress(mutation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(report, suffix) for suffix in ("one", "two"))
        results = tuple(future.result(timeout=10) for future in futures)

    assert results[0] == results[1]
    with open_read_connection(repository.database_path) as connection:
        progress_count = connection.execute(
            """
            SELECT count(*)
            FROM task_events
            WHERE task_uid = ? AND event_type = 'progress_reported'
            """,
            (str(task.uid),),
        ).fetchone()
        idempotency_count = connection.execute(
            """
            SELECT count(*)
            FROM idempotency_records
            WHERE operation = 'task.progress.report'
            """
        ).fetchone()
    assert progress_count is not None
    assert progress_count[0] == 1
    assert idempotency_count is not None
    assert idempotency_count[0] == 1


@pytest.mark.parametrize(
    "corruption",
    ["extra-key", "invalid-events", "foreign-project", "invalid-payload"],
)
def test_progress_replay_rejects_corrupt_closed_outcomes(
    corruption: str,
    tmp_path: Path,
) -> None:
    """Every durable outcome boundary fails closed under direct corruption."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    progress = TaskProgress(message="Durable progress")
    repository.report_task_progress(
        _progress_mutation(
            task,
            progress,
            "durable",
            idempotency_key="durable-progress",
        )
    )
    with open_write_transaction(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT outcome_json
            FROM idempotency_records
            WHERE subject_scope = ? AND operation = 'task.progress.report'
              AND caller_key = 'durable-progress'
            """,
            (str(_SUBJECT_ID),),
        ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise AssertionError
        outcome = json.loads(row[0])
        assert isinstance(outcome, dict)
        if corruption == "extra-key":
            outcome["unknown"] = True
        elif corruption == "invalid-events":
            outcome["events"] = []
        elif corruption == "foreign-project":
            outcome["claim"]["project_id"] = "prj_foreign"
        else:
            outcome["events"][0]["payload"] = {"unknown": "value"}
        connection.execute(
            """
            UPDATE idempotency_records
            SET outcome_json = ?
            WHERE subject_scope = ? AND operation = 'task.progress.report'
              AND caller_key = 'durable-progress'
            """,
            (
                json.dumps(outcome, sort_keys=True, separators=(",", ":")),
                str(_SUBJECT_ID),
            ),
        )

    with pytest.raises(StorageUnavailableError):
        repository.report_task_progress(
            _progress_mutation(
                task,
                progress,
                f"replay_{corruption}",
                idempotency_key="durable-progress",
            )
        )


@pytest.mark.parametrize("corruption", ["missing", "changed"])
def test_progress_replay_verifies_immutable_event_storage(
    corruption: str,
    tmp_path: Path,
) -> None:
    """Replay rejects missing or changed event rows named by its closed outcome."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    progress = TaskProgress(message="Immutable progress")
    original = repository.report_task_progress(
        _progress_mutation(
            task,
            progress,
            "immutable",
            idempotency_key="immutable-progress",
        )
    )
    with open_write_transaction(repository.database_path) as connection:
        if corruption == "missing":
            connection.execute(
                "DELETE FROM task_events WHERE id = ?",
                (str(original.events[0].id),),
            )
        else:
            connection.execute(
                "UPDATE task_events SET payload_json = ? WHERE id = ?",
                ('{"message":"Changed"}', str(original.events[0].id)),
            )

    with pytest.raises(StorageUnavailableError):
        repository.report_task_progress(
            _progress_mutation(
                task,
                progress,
                f"event_{corruption}",
                idempotency_key="immutable-progress",
            )
        )


def test_progress_helper_boundaries_reject_impossible_runtime_states(
    tmp_path: Path,
) -> None:
    """Repository helper guards reject wrong types and mismatched closed results."""
    repository, task = _repository(tmp_path)
    with pytest.raises(StorageUnavailableError):
        repository.report_task_progress(object())  # type: ignore[arg-type]
    human_state = StoredClaimState(
        project_id=task.project_id,
        claim=TaskClaim(
            task_uid=task.uid,
            task_key=task.key,
            subject_id=_SUBJECT_ID,
            attempt_id=None,
            claimed_at=_CLAIMED_AT,
            lease_expires_at=_CLAIMED_AT + timedelta(hours=8),
        ),
        attempt=None,
    )
    with pytest.raises(StorageUnavailableError):
        _require_agent_attempt(human_state)
    _claim_agent(repository)
    progress = TaskProgress(message="Matching progress")
    mutation = _progress_mutation(
        task,
        progress,
        "matching",
        idempotency_key="helper-progress",
    )
    result = repository.report_task_progress(mutation)

    with open_write_transaction(repository.database_path) as connection:
        with pytest.raises(StorageUnavailableError):
            _append_progress_events(
                connection,
                task=object(),  # type: ignore[arg-type]
                mutation=mutation,
            )
        connection.execute(
            """
            UPDATE idempotency_records
            SET subject_scope = 'sub_foreign'
            WHERE subject_scope = ? AND operation = 'task.progress.report'
              AND caller_key = 'helper-progress'
            """,
            (str(_SUBJECT_ID),),
        )
        with pytest.raises(StorageUnavailableError):
            _read_idempotent_progress(
                connection,
                actor_subject_id="sub_foreign",
                caller_key="helper-progress",
                request_fingerprint=_progress_fingerprint(mutation),
            )

    with pytest.raises(StorageUnavailableError):
        _require_matching_progress_result(
            result,
            mutation=_progress_mutation(
                task,
                TaskProgress(message="Different progress"),
                "semantic_mismatch",
            ),
            fresh=False,
        )
    with pytest.raises(StorageUnavailableError):
        _require_matching_progress_result(
            result,
            mutation=_progress_mutation(task, progress, "identity_mismatch"),
            fresh=True,
        )


@pytest.mark.parametrize("collision_index", [0, 1, 2])
def test_progress_rolls_back_at_every_event_boundary(
    collision_index: int,
    tmp_path: Path,
) -> None:
    """A durable identity collision rolls back every earlier batch insert."""
    repository, task = _repository(tmp_path)
    claimed = _claim_agent(repository)
    progress = TaskProgress(
        message="Batch",
        observations=(
            ProgressObservation(ObservationKind.NOTE, "First"),
            ProgressObservation(ObservationKind.RISK, "Second"),
        ),
    )
    event_ids = [
        TaskEventId(f"evt_progress_rollback_{collision_index}"),
        TaskEventId(f"evt_observation_rollback_{collision_index}_0"),
        TaskEventId(f"evt_observation_rollback_{collision_index}_1"),
    ]
    event_ids[collision_index] = TaskEventId("evt_create_progress")
    before = _snapshot(repository)

    with pytest.raises(StorageUnavailableError):
        repository.report_task_progress(
            _progress_mutation(
                task,
                progress,
                f"rollback_{collision_index}",
                idempotency_key=f"rollback-{collision_index}",
                event_ids=tuple(event_ids),
            )
        )

    assert _snapshot(repository) == before
    details = repository.get_task_details(
        GetTaskDetails(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task=task.uid)
    )
    assert details.task == task
    assert details.claim == claimed.claim
    assert details.attempt is not None
    assert details.attempt.status is AttemptStatus.ACTIVE
