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

from workaholic.application import ListTasks
from workaholic.persistence.sqlite import SQLitePhaseOneRepository

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
    bootstrap_repository = SQLitePhaseOneRepository(database_path)
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

    observer = SQLitePhaseOneRepository(database_path)
    page = observer.list_tasks(
        ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=100,
        )
    )
    assert page.tasks == ordered
    assert page.next_cursor is None


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
    repository = SQLitePhaseOneRepository(database_path)
    barrier.wait(timeout=10)
    return repository.create_task(
        task_mutation(
            bootstrap,
            f"worker{index}",
            occurred_at=later_timestamp(index),
        )
    )
