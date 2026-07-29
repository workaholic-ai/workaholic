"""Golden specification for the persistent solo-developer journey."""

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

    from tests.golden import GoldenJourneyRunner

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
    pytest.mark.skip(
        reason=("Phase 1: missing LocalSession, SQLite persistence, and task commands.")
    ),
]


def test_solo_tasks_remain_visible_after_reopening_the_project(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """A task created locally remains visible to a later CLI process."""
    workspace = tmp_path / "solo-workspace"
    workspace.mkdir()

    require_success(
        golden_runner.cli(
            (
                "up",
                "--project-key",
                "ACME",
                "--json",
                "--non-interactive",
                "--idempotency-key",
                "solo-up",
            ),
            cwd=workspace,
        )
    )
    created_data = require_object(
        require_success(
            golden_runner.cli(
                (
                    "task",
                    "add",
                    "First persistent task",
                    "--json",
                    "--non-interactive",
                    "--idempotency-key",
                    "solo-task-add",
                ),
                cwd=workspace,
            )
        ),
        context="task-add data",
    )
    created_task = require_object(
        created_data.get("task"),
        context="created task",
    )

    assert created_task.get("key") == "ACME-1"
    assert created_task.get("title") == "First persistent task"

    listed_data = require_object(
        require_success(
            golden_runner.cli(
                ("task", "list", "--json", "--non-interactive"),
                cwd=workspace,
            )
        ),
        context="task-list data",
    )
    listed_tasks = require_array(
        listed_data.get("tasks"),
        context="listed tasks",
    )

    assert any(
        require_object(task, context="listed task").get("key") == "ACME-1"
        and require_object(task, context="listed task").get("title")
        == "First persistent task"
        for task in listed_tasks
    )
