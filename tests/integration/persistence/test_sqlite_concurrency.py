"""Separate-connection concurrency acceptance for the SQLite adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from operator import attrgetter
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Protocol

import pytest
from tests.contract.phase_one import (
    bootstrap_mutation,
    later_timestamp,
    task_mutation,
)

from workaholic.application import (
    ClaimNextTaskMutation,
    LeaseLostError,
    ListTasks,
    NoTaskAvailableError,
    ProjectCreationMutation,
    ProjectCreationResult,
    ProjectKeyConflictError,
    SubmitAgentResultMutation,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskClaimResult,
    TaskMutationResult,
    TaskResultInput,
    TaskUpdateMutation,
    TaskUpdatePatch,
    VersionConflictError,
)
from workaholic.domain import (
    AttemptId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    open_read_connection,
)

if TYPE_CHECKING:
    from multiprocessing.process import BaseProcess

    from workaholic.application import BootstrapResult
    from workaholic.domain import Task

pytestmark = pytest.mark.integration

_WORKER_COUNT = 8
_PROCESS_WORKER_COUNT = 2


class _ProcessBarrier(Protocol):
    """Minimal cross-process barrier surface used by spawned workers."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait until every process reaches the race boundary."""
        ...


class _ProcessQueue(Protocol):
    """Minimal process-safe result channel used by spawned workers."""

    def put(self, obj: object) -> None:
        """Publish one serializable worker outcome."""
        ...

    def get(self, *, timeout: float | None = None) -> object:
        """Read one worker outcome, bounded by ``timeout``."""
        ...

    def close(self) -> None:
        """Release the parent process's queue resources."""
        ...

    def join_thread(self) -> None:
        """Wait for the queue feeder thread to flush."""
        ...


def test_separate_connections_allocate_unique_contiguous_task_numbers(
    tmp_path: Path,
) -> None:
    """Concurrent creates serialize without duplicates, gaps, or lost Tasks."""
    database_path = tmp_path / "local.db"
    bootstrap_repository = SQLiteRepository(database_path)
    bootstrap = bootstrap_repository.bootstrap_local_project(
        bootstrap_mutation("bootstrap")
    )
    barrier = Barrier(_WORKER_COUNT)
    arguments = tuple(
        (database_path, bootstrap, index, barrier)
        for index in range(1, _WORKER_COUNT + 1)
    )

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        created = tuple(executor.map(_create_task, arguments))

    ordered = tuple(sorted(created, key=attrgetter("number")))
    assert tuple(task.number for task in ordered) == tuple(range(1, _WORKER_COUNT + 1))
    assert tuple(task.key for task in ordered) == tuple(
        f"ACME-{number}" for number in range(1, _WORKER_COUNT + 1)
    )
    assert len({task.uid for task in ordered}) == _WORKER_COUNT

    observer = SQLiteRepository(database_path)
    page = observer.list_tasks(
        ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=100,
        )
    )
    assert page.tasks == ordered
    assert page.next_cursor is None


def test_concurrent_same_project_key_has_one_winner_and_stable_conflicts(
    tmp_path: Path,
) -> None:
    """A contended immutable key is committed once without partial grants."""
    database_path = tmp_path / "local.db"
    bootstrap = SQLiteRepository(database_path).bootstrap_local_project(
        bootstrap_mutation("bootstrap")
    )
    barrier = Barrier(_WORKER_COUNT)
    arguments = tuple(
        (database_path, bootstrap, index, "DOCS", barrier)
        for index in range(1, _WORKER_COUNT + 1)
    )

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        outcomes = tuple(executor.map(_create_project_or_conflict, arguments))

    created = tuple(
        outcome for outcome in outcomes if isinstance(outcome, ProjectCreationResult)
    )
    conflicts = tuple(
        outcome for outcome in outcomes if isinstance(outcome, ProjectKeyConflictError)
    )
    assert len(created) == 1
    assert len(conflicts) == _WORKER_COUNT - 1
    with open_read_connection(database_path) as connection:
        project_rows = connection.execute(
            """
            SELECT id, key, next_task_number
            FROM projects
            WHERE key = 'DOCS'
            """
        ).fetchall()
        grant_rows = connection.execute(
            """
            SELECT subject_id, project_id, role
            FROM project_grants
            WHERE project_id = ?
            """,
            (str(created[0].project.id),),
        ).fetchall()
    assert project_rows == [(str(created[0].project.id), "DOCS", 1)]
    assert grant_rows == [("sub_bootstrap", str(created[0].project.id), "owner")]


def test_concurrent_distinct_project_keys_all_commit_independently(
    tmp_path: Path,
) -> None:
    """Serialization preserves all unrelated Project namespace creations."""
    database_path = tmp_path / "local.db"
    bootstrap = SQLiteRepository(database_path).bootstrap_local_project(
        bootstrap_mutation("bootstrap")
    )
    barrier = Barrier(_WORKER_COUNT)
    arguments = tuple(
        (database_path, bootstrap, index, f"P{index}", barrier)
        for index in range(1, _WORKER_COUNT + 1)
    )

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        outcomes = tuple(executor.map(_create_project_or_conflict, arguments))

    assert all(isinstance(item, ProjectCreationResult) for item in outcomes)
    created = tuple(
        item for item in outcomes if isinstance(item, ProjectCreationResult)
    )
    assert len({result.project.id for result in created}) == _WORKER_COUNT
    assert {result.project.key for result in created} == {
        f"P{index}" for index in range(1, _WORKER_COUNT + 1)
    }
    with open_read_connection(database_path) as connection:
        project_rows = connection.execute(
            """
            SELECT key, next_task_number
            FROM projects
            WHERE key GLOB 'P[1-8]'
            ORDER BY key
            """
        ).fetchall()
        grant_count = connection.execute(
            """
            SELECT count(*)
            FROM project_grants
            WHERE project_id IN (
                SELECT id FROM projects WHERE key GLOB 'P[1-8]'
            )
            """
        ).fetchone()
    assert project_rows == [(f"P{index}", 1) for index in range(1, 9)]
    assert grant_count == (_WORKER_COUNT,)


def test_concurrent_task_updates_have_exactly_one_optimistic_winner(
    tmp_path: Path,
) -> None:
    """Two writers at one version cannot produce last-write-wins."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "target"))
    barrier = Barrier(2)
    arguments = tuple(
        (database_path, bootstrap, task, index, None, barrier) for index in range(1, 3)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(_update_task_or_conflict, arguments))

    winners = tuple(item for item in outcomes if isinstance(item, TaskMutationResult))
    conflicts = tuple(
        item for item in outcomes if isinstance(item, VersionConflictError)
    )
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].task.version == 2
    assert winners[0].task.title in {"Worker 1", "Worker 2"}
    page = repository.list_tasks(
        ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=100,
        )
    )
    assert page.tasks == (winners[0].task,)
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_events),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'task.update'
                )
            """
        ).fetchone()
    assert counts == (2, 0)


def test_concurrent_matching_update_key_replays_one_committed_mutation(
    tmp_path: Path,
) -> None:
    """Concurrent semantic retries share one Task version, event, and outcome."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "target"))
    barrier = Barrier(2)
    arguments = tuple(
        (database_path, bootstrap, task, index, "update-once", barrier)
        for index in range(1, 3)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(_update_task_or_conflict, arguments))

    assert all(isinstance(item, TaskMutationResult) for item in outcomes)
    assert outcomes[0] == outcomes[1]
    committed = outcomes[0]
    assert isinstance(committed, TaskMutationResult)
    assert committed.task.version == 2
    assert committed.task.title == "Retried update"
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_events),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'task.update'
                )
            """
        ).fetchone()
    assert counts == (2, 1)


def test_concurrent_block_and_cancel_have_one_versioned_winner(
    tmp_path: Path,
) -> None:
    """Competing semantic transitions cannot both mutate the same Task version."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "target"))
    barrier = Barrier(2)
    arguments = (
        (database_path, bootstrap, task, "block", barrier),
        (database_path, bootstrap, task, "cancel", barrier),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(_transition_task_or_conflict, arguments))

    winners = tuple(item for item in outcomes if isinstance(item, TaskMutationResult))
    conflicts = tuple(
        item for item in outcomes if isinstance(item, VersionConflictError)
    )
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].task.version == 2
    assert winners[0].task.state in (TaskState.BLOCKED, TaskState.CANCELLED)
    page = repository.list_tasks(
        ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=100,
        )
    )
    assert page.tasks == (winners[0].task,)
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_events),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation IN ('task.block', 'task.cancel')
                )
            """
        ).fetchone()
    assert counts == (2, 1)


def test_concurrent_agent_claims_have_exactly_one_owner(tmp_path: Path) -> None:
    """Independent unkeyed pulls cannot both acquire the sole ready Task."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "claim-target"))
    barrier = Barrier(_WORKER_COUNT)
    arguments = tuple(
        (database_path, bootstrap, index, None, barrier)
        for index in range(1, _WORKER_COUNT + 1)
    )

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        outcomes = tuple(executor.map(_claim_next_or_unavailable, arguments))

    winners = tuple(item for item in outcomes if isinstance(item, TaskClaimResult))
    unavailable = tuple(
        item for item in outcomes if isinstance(item, NoTaskAvailableError)
    )
    assert len(winners) == 1
    assert len(unavailable) == _WORKER_COUNT - 1
    assert winners[0].task == task
    assert winners[0].claim is not None
    assert winners[0].attempt is not None
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_attempts),
                (SELECT count(*) FROM task_claims),
                (
                    SELECT count(*) FROM task_events
                    WHERE event_type = 'task_claimed'
                ),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'task.claim.next'
                )
            """
        ).fetchone()
        stored_version = connection.execute(
            "SELECT version FROM tasks WHERE uid = ?",
            (str(task.uid),),
        ).fetchone()
    assert counts == (1, 1, 1, 0)
    assert stored_version == (task.version,)


def test_concurrent_idempotent_agent_claims_share_one_closed_outcome(
    tmp_path: Path,
) -> None:
    """Concurrent equivalent retries create one Attempt, Claim, event, and key."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    repository.create_task(task_mutation(bootstrap, "idempotent-claim"))
    barrier = Barrier(_WORKER_COUNT)
    arguments = tuple(
        (database_path, bootstrap, index, "claim-once", barrier)
        for index in range(1, _WORKER_COUNT + 1)
    )

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        outcomes = tuple(executor.map(_claim_next_or_unavailable, arguments))

    assert all(isinstance(item, TaskClaimResult) for item in outcomes)
    assert all(item == outcomes[0] for item in outcomes[1:])
    committed = outcomes[0]
    assert isinstance(committed, TaskClaimResult)
    assert tuple(event.event_type for event in committed.events) == (
        TaskEventType.TASK_CLAIMED,
    )
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_attempts),
                (SELECT count(*) FROM task_claims),
                (
                    SELECT count(*) FROM task_events
                    WHERE event_type = 'task_claimed'
                ),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'task.claim.next'
                )
            """
        ).fetchone()
    assert counts == (1, 1, 1, 1)


def test_spawned_processes_cannot_double_claim_one_task(tmp_path: Path) -> None:
    """Two real processes racing one SQLite file produce exactly one owner."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "process-claim"))
    context = get_context("spawn")
    barrier = context.Barrier(_PROCESS_WORKER_COUNT)
    queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_spawned_claim_worker,
            args=(
                str(database_path),
                str(bootstrap.project.id),
                str(bootstrap.subject.id),
                index,
                barrier,
                queue,
            ),
        )
        for index in range(1, _PROCESS_WORKER_COUNT + 1)
    )

    _run_spawned_processes(processes)
    outcomes = _read_process_outcomes(queue, count=_PROCESS_WORKER_COUNT)

    assert sorted(status for status, _detail in outcomes) == [
        "claimed",
        "no_task_available",
    ]
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_attempts),
                (SELECT count(*) FROM task_claims),
                (
                    SELECT count(*) FROM task_events
                    WHERE event_type = 'task_claimed'
                )
            """
        ).fetchone()
        stored_version = connection.execute(
            "SELECT version FROM tasks WHERE uid = ?",
            (str(task.uid),),
        ).fetchone()
    assert counts == (1, 1, 1)
    assert stored_version == (task.version,)


def test_spawned_processes_reject_stale_agent_submission(tmp_path: Path) -> None:
    """Two real Agent writers cannot both submit through one active Attempt."""
    database_path = tmp_path / "local.db"
    repository = SQLiteRepository(database_path)
    bootstrap = repository.bootstrap_local_project(bootstrap_mutation("bootstrap"))
    task = repository.create_task(task_mutation(bootstrap, "process-submit"))
    claimed = repository.claim_next_task(
        ClaimNextTaskMutation(
            project_id=bootstrap.project.id,
            actor_subject_id=bootstrap.subject.id,
            request_id=RequestId("req_process_owner"),
            occurred_at=later_timestamp(1),
            attempt_id=AttemptId("atm_process_owner"),
            lease_duration_seconds=900,
            task_claimed_event_id=TaskEventId("evt_process_owner"),
            claim_expired_event_id=TaskEventId("evt_process_owner_expired"),
        )
    )
    assert claimed.attempt is not None
    context = get_context("spawn")
    barrier = context.Barrier(_PROCESS_WORKER_COUNT)
    queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_spawned_submission_worker,
            args=(
                str(database_path),
                str(bootstrap.project.id),
                str(bootstrap.subject.id),
                str(task.uid),
                str(claimed.attempt.id),
                index,
                barrier,
                queue,
            ),
        )
        for index in range(1, _PROCESS_WORKER_COUNT + 1)
    )

    _run_spawned_processes(processes)
    outcomes = _read_process_outcomes(queue, count=_PROCESS_WORKER_COUNT)

    assert sorted(status for status, _detail in outcomes) == [
        "lease_lost",
        "submitted",
    ]
    with open_read_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_results),
                (SELECT count(*) FROM task_claims),
                (
                    SELECT count(*) FROM task_attempts
                    WHERE status = 'submitted'
                ),
                (
                    SELECT count(*) FROM task_events
                    WHERE event_type = 'result_submitted'
                ),
                (
                    SELECT count(*) FROM task_events
                    WHERE event_type = 'task_completed'
                )
            """
        ).fetchone()
        stored = connection.execute(
            "SELECT state, version FROM tasks WHERE uid = ?",
            (str(task.uid),),
        ).fetchone()
    assert counts == (1, 0, 1, 1, 1)
    assert stored == ("done", 2)


def _create_task(
    arguments: tuple[Path, BootstrapResult, int, Barrier],
) -> Task:
    """Create one Task after all independent connections are ready.

    Args:
        arguments: Database path, bootstrap graph, worker number, and barrier.

    Returns:
        Task committed through this worker's repository instance.

    """
    database_path, bootstrap, index, barrier = arguments
    repository = SQLiteRepository(database_path)
    barrier.wait(timeout=10)
    return repository.create_task(
        task_mutation(
            bootstrap,
            f"worker{index}",
            occurred_at=later_timestamp(index),
        )
    )


def _claim_next_or_unavailable(
    arguments: tuple[
        Path,
        BootstrapResult,
        int,
        str | None,
        Barrier,
    ],
) -> TaskClaimResult | NoTaskAvailableError:
    """Run one contended Agent pull through an independent connection.

    Args:
        arguments: Store, identity graph, worker, optional key, and barrier.

    Returns:
        The committed/replayed Claim or expected no-Task outcome.

    """
    database_path, bootstrap, index, caller_key, barrier = arguments
    repository = SQLiteRepository(database_path)
    mutation = ClaimNextTaskMutation(
        project_id=bootstrap.project.id,
        actor_subject_id=bootstrap.subject.id,
        request_id=RequestId(f"req_claim_{index}"),
        occurred_at=later_timestamp(index),
        attempt_id=AttemptId(f"atm_claim_{index}"),
        lease_duration_seconds=900,
        task_claimed_event_id=TaskEventId(f"evt_claim_{index}"),
        claim_expired_event_id=TaskEventId(f"evt_expire_claim_{index}"),
        idempotency_key=caller_key,
    )
    barrier.wait(timeout=10)
    try:
        return repository.claim_next_task(mutation)
    except NoTaskAvailableError as error:
        return error


def _update_task_or_conflict(
    arguments: tuple[
        Path,
        BootstrapResult,
        Task,
        int,
        str | None,
        Barrier,
    ],
) -> TaskMutationResult | VersionConflictError:
    """Run one contended optimistic update through an independent connection.

    Args:
        arguments: Database, identity graph, Task, worker, caller key, and barrier.

    Returns:
        The committed or replayed mutation, or the expected stale-version error.

    """
    database_path, bootstrap, task, index, caller_key, barrier = arguments
    repository = SQLiteRepository(database_path)
    title = f"Worker {index}" if caller_key is None else "Retried update"
    mutation = TaskUpdateMutation(
        task_uid=task.uid,
        project_id=bootstrap.project.id,
        actor_subject_id=bootstrap.subject.id,
        event_id=TaskEventId(f"evt_update_{index}"),
        claim_expired_event_id=TaskEventId(f"evt_update_{index}_expired"),
        request_id=RequestId(f"req_update_{index}"),
        occurred_at=later_timestamp(index),
        expected_version=1,
        patch=TaskUpdatePatch(title=title),
        idempotency_key=caller_key,
    )
    barrier.wait(timeout=10)
    try:
        return repository.update_task_if_version(mutation)
    except VersionConflictError as error:
        return error


def _transition_task_or_conflict(
    arguments: tuple[Path, BootstrapResult, Task, str, Barrier],
) -> TaskMutationResult | VersionConflictError:
    """Run one contended semantic transition through its own connection.

    Args:
        arguments: Database, identity graph, Task, operation, and shared barrier.

    Returns:
        The committed transition or expected stale-version error.

    """
    database_path, bootstrap, task, operation, barrier = arguments
    repository = SQLiteRepository(database_path)
    if operation == "block":
        mutation: TaskBlockMutation | TaskCancelMutation = TaskBlockMutation(
            task_uid=task.uid,
            project_id=bootstrap.project.id,
            actor_subject_id=bootstrap.subject.id,
            event_id=TaskEventId("evt_block_race"),
            claim_expired_event_id=TaskEventId("evt_block_race_expired"),
            request_id=RequestId("req_block_race"),
            occurred_at=later_timestamp(1),
            expected_version=1,
            reason="Waiting.",
            idempotency_key="block-race",
        )
    else:
        mutation = TaskCancelMutation(
            task_uid=task.uid,
            project_id=bootstrap.project.id,
            actor_subject_id=bootstrap.subject.id,
            event_id=TaskEventId("evt_cancel_race"),
            claim_expired_event_id=TaskEventId("evt_cancel_race_expired"),
            request_id=RequestId("req_cancel_race"),
            occurred_at=later_timestamp(2),
            expected_version=1,
            reason="No longer needed.",
            idempotency_key="cancel-race",
        )
    barrier.wait(timeout=10)
    try:
        if isinstance(mutation, TaskBlockMutation):
            return repository.block_task(mutation)
        return repository.cancel_task(mutation)
    except VersionConflictError as error:
        return error


def _create_project_or_conflict(
    arguments: tuple[Path, BootstrapResult, int, str, Barrier],
) -> ProjectCreationResult | ProjectKeyConflictError:
    """Create one Project after all independent connections are ready.

    Args:
        arguments: Database, bootstrap graph, worker, key, and shared barrier.

    Returns:
        Committed result or the expected immutable-key conflict.

    """
    database_path, bootstrap, index, project_key, barrier = arguments
    repository = SQLiteRepository(database_path)
    mutation = ProjectCreationMutation(
        project_id=ProjectId(f"prj_worker{index}"),
        request_id=RequestId(f"req_worker{index}"),
        instance_id=bootstrap.instance.id,
        actor_subject_id=bootstrap.subject.id,
        occurred_at=later_timestamp(index),
        project_key=project_key,
        project_name=f"Project {index}",
    )
    barrier.wait(timeout=10)
    try:
        return repository.create_project(mutation)
    except ProjectKeyConflictError as error:
        return error


def _spawned_claim_worker(  # noqa: PLR0913 - serialized worker boundary.
    database_path: str,
    project_id: str,
    subject_id: str,
    index: int,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Race one Agent pull from an independently spawned Python process.

    Args:
        database_path: Shared SQLite file path.
        project_id: Authorized Project identity.
        subject_id: Authorized local Agent identity.
        index: Stable process-specific identity suffix.
        barrier: Cross-process start barrier.
        queue: Parent-owned outcome channel.

    """
    repository = SQLiteRepository(Path(database_path))
    mutation = ClaimNextTaskMutation(
        project_id=ProjectId(project_id),
        actor_subject_id=SubjectId(subject_id),
        request_id=RequestId(f"req_spawn_claim_{index}"),
        occurred_at=later_timestamp(index + 10),
        attempt_id=AttemptId(f"atm_spawn_claim_{index}"),
        lease_duration_seconds=900,
        task_claimed_event_id=TaskEventId(f"evt_spawn_claim_{index}"),
        claim_expired_event_id=TaskEventId(f"evt_spawn_expired_{index}"),
    )
    barrier.wait(timeout=10)
    try:
        outcome = repository.claim_next_task(mutation)
    except NoTaskAvailableError:
        queue.put(("no_task_available", ""))
    except Exception as error:  # noqa: BLE001 - report child failure to parent.
        queue.put(("unexpected", type(error).__name__))
    else:
        attempt = outcome.attempt
        queue.put(("claimed", "" if attempt is None else str(attempt.id)))


def _spawned_submission_worker(  # noqa: PLR0913 - serialized worker boundary.
    database_path: str,
    project_id: str,
    subject_id: str,
    task_uid: str,
    attempt_id: str,
    index: int,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Race one Agent Result submission from a spawned Python process.

    Args:
        database_path: Shared SQLite file path.
        project_id: Authorized Project identity.
        subject_id: Authorized local Agent identity.
        task_uid: Shared claimed Task identity.
        attempt_id: Shared active Attempt owner token.
        index: Stable process-specific identity suffix.
        barrier: Cross-process start barrier.
        queue: Parent-owned outcome channel.

    """
    repository = SQLiteRepository(Path(database_path))
    mutation = SubmitAgentResultMutation(
        task_uid=TaskId(task_uid),
        project_id=ProjectId(project_id),
        actor_subject_id=SubjectId(subject_id),
        request_id=RequestId(f"req_spawn_submit_{index}"),
        occurred_at=later_timestamp(index + 20),
        expected_version=1,
        attempt_id=AttemptId(attempt_id),
        result_id=ResultId(f"res_spawn_submit_{index}"),
        result_submitted_event_id=TaskEventId(f"evt_spawn_submit_{index}"),
        task_completed_event_id=TaskEventId(f"evt_spawn_complete_{index}"),
        result=TaskResultInput(summary=f"Worker {index} completed the task."),
    )
    barrier.wait(timeout=10)
    try:
        outcome = repository.submit_agent_result(mutation)
    except LeaseLostError:
        queue.put(("lease_lost", ""))
    except VersionConflictError:
        queue.put(("version_conflict", ""))
    except Exception as error:  # noqa: BLE001 - report child failure to parent.
        queue.put(("unexpected", type(error).__name__))
    else:
        queue.put(("submitted", str(outcome.result.id)))


def _run_spawned_processes(processes: tuple[BaseProcess, ...]) -> None:
    """Start and bound a set of test-owned child processes.

    Args:
        processes: Unstarted spawned processes sharing one race barrier.

    Raises:
        Failed: If a process hangs or exits unsuccessfully.

    """
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    hung = tuple(process for process in processes if process.is_alive())
    for process in hung:
        process.terminate()
        process.join(timeout=5)
    if hung:
        pytest.fail("Spawned SQLite concurrency worker timed out.")
    exit_codes = tuple(process.exitcode for process in processes)
    if any(code != 0 for code in exit_codes):
        pytest.fail(f"Spawned SQLite workers exited unsuccessfully: {exit_codes!r}")


def _read_process_outcomes(
    queue: _ProcessQueue,
    *,
    count: int,
) -> tuple[tuple[str, str], ...]:
    """Read and validate the closed child-process outcome wire shape.

    Args:
        queue: Process-safe result channel.
        count: Exact number of expected worker results.

    Returns:
        Ordered validated pairs of stable status and safe detail.

    Raises:
        Failed: If any worker returns a malformed or unexpected result.

    """
    outcomes: list[tuple[str, str]] = []
    try:
        for _index in range(count):
            candidate = queue.get(timeout=10)
            if (
                not isinstance(candidate, tuple)
                or len(candidate) != 2
                or not all(isinstance(value, str) for value in candidate)
            ):
                pytest.fail("Spawned SQLite worker returned a malformed outcome.")
            status, detail = candidate
            if status == "unexpected":
                pytest.fail(f"Spawned SQLite worker failed with {detail}.")
            outcomes.append((status, detail))
    finally:
        queue.close()
        queue.join_thread()
    return tuple(outcomes)
