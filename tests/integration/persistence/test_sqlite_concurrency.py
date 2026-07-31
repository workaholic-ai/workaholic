"""Separate-connection concurrency acceptance for the SQLite adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from operator import attrgetter
from threading import Barrier
from typing import TYPE_CHECKING

import pytest
from tests.contract.phase_one import (
    bootstrap_mutation,
    later_timestamp,
    task_mutation,
)

from workaholic.application import (
    ListTasks,
    ProjectCreationMutation,
    ProjectCreationResult,
    ProjectKeyConflictError,
)
from workaholic.domain import ProjectId, RequestId
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    open_read_connection,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult
    from workaholic.domain import Task

pytestmark = pytest.mark.integration

_WORKER_COUNT = 8


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
