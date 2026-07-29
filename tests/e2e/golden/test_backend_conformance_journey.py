"""Golden specification for persistence-adapter parity."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    require_array,
    require_object,
    require_success,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.golden import GoldenJourneyRunner, StorageBackend

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
    pytest.mark.requires_network,
    pytest.mark.requires_postgres,
    pytest.mark.skip(
        reason=("Phase 7: missing JSON, SQLite, and PostgreSQL adapter conformance.")
    ),
]


def test_supported_backends_expose_the_same_task_behavior(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """Equivalent server workflows expose one backend-neutral CLI result."""
    backends: tuple[StorageBackend, ...] = ("json", "sqlite", "postgres")
    snapshots: dict[StorageBackend, tuple[object, object, object]] = {}

    for backend in backends:
        backend_root = tmp_path / backend
        backend_root.mkdir()
        with golden_runner.instance(
            backend=backend,
            project_key="ACME",
            remote=True,
            root=backend_root,
            subjects={"operator": "human"},
        ) as instance:
            environment = instance.environment_for("operator")
            created_data = require_object(
                require_success(
                    golden_runner.cli(
                        (
                            "task",
                            "add",
                            "Backend-neutral task",
                            "--json",
                            "--non-interactive",
                            "--idempotency-key",
                            f"backend-{backend}-create",
                        ),
                        cwd=backend_root,
                        environment=environment,
                    )
                ),
                context=f"{backend} task-add data",
            )
            created_task = require_object(
                created_data.get("task"),
                context=f"{backend} created task",
            )
            listed_data = require_object(
                require_success(
                    golden_runner.cli(
                        ("task", "list", "--json", "--non-interactive"),
                        cwd=backend_root,
                        environment=environment,
                    )
                ),
                context=f"{backend} task-list data",
            )
            listed_tasks = require_array(
                listed_data.get("tasks"),
                context=f"{backend} listed tasks",
            )

            assert len(listed_tasks) == 1
            listed_task = require_object(
                listed_tasks[0],
                context=f"{backend} listed task",
            )
            snapshots[backend] = (
                created_task.get("key"),
                listed_task.get("title"),
                listed_task.get("state"),
            )

    assert snapshots == {
        "json": ("ACME-1", "Backend-neutral task", "open"),
        "sqlite": ("ACME-1", "Backend-neutral task", "open"),
        "postgres": ("ACME-1", "Backend-neutral task", "open"),
    }
