"""Golden specification for the persistent solo-developer journey."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    require_array,
    require_object,
    require_string,
    require_success,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.golden import GoldenJourneyRunner, JsonObject

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
]


def test_solo_tasks_remain_visible_after_reopening_the_project(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """A task created locally remains visible to a later CLI process."""
    workspace = tmp_path / "solo-workspace"
    workspace.mkdir()

    up_data = require_object(
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
        ),
        context="up data",
    )
    assert up_data.keys() == {"instance", "project", "subject", "workspace"}
    project = require_object(up_data.get("project"), context="up project")
    subject = require_object(up_data.get("subject"), context="up subject")
    project_id = require_string(project.get("id"), context="Project UID")
    subject_id = require_string(subject.get("id"), context="creator Subject UID")
    assert project.get("key") == "ACME"

    add_arguments = (
        "task",
        "add",
        "First persistent task",
        "--json",
        "--non-interactive",
        "--idempotency-key",
        "solo-task-add",
    )
    created_data = require_object(
        require_success(
            golden_runner.cli(
                add_arguments,
                cwd=workspace,
            )
        ),
        context="task-add data",
    )
    created_task = require_object(
        created_data.get("task"),
        context="created task",
    )
    task_uid = require_string(created_task.get("uid"), context="Task UID")
    expected_task_fields = {
        "uid",
        "project_id",
        "number",
        "key",
        "title",
        "objective",
        "state",
        "priority",
        "available_at",
        "approval",
        "acceptance",
        "context",
        "depends_on",
        "blocking_reason",
        "current_result_id",
        "version",
        "created_by",
        "created_at",
        "updated_at",
        "views",
        "readiness_reasons",
    }

    assert created_data.keys() == {"task"}
    assert created_task.keys() == expected_task_fields
    assert created_task.get("project_id") == project_id
    assert created_task.get("number") == 1
    assert created_task.get("key") == "ACME-1"
    assert created_task.get("title") == "First persistent task"
    assert created_task.get("objective") == "First persistent task"
    assert created_task.get("state") == "open"
    assert created_task.get("priority") == 50
    assert created_task.get("available_at") is None
    assert created_task.get("approval") == "none"
    assert created_task.get("acceptance") == []
    assert created_task.get("context") == []
    assert created_task.get("depends_on") == []
    assert created_task.get("blocking_reason") is None
    assert created_task.get("current_result_id") is None
    assert created_task.get("version") == 1
    assert created_task.get("created_by") == subject_id
    assert created_task.get("views") == {
        "ready": True,
        "running": False,
        "scheduled": False,
        "stale": False,
        "awaiting_review": False,
    }
    assert created_task.get("readiness_reasons") == []
    assert require_string(
        created_task.get("created_at"),
        context="Task created_at",
    ) == require_string(
        created_task.get("updated_at"),
        context="Task updated_at",
    )

    replayed_data = require_object(
        require_success(
            golden_runner.cli(
                add_arguments,
                cwd=workspace,
            )
        ),
        context="replayed task-add data",
    )
    assert replayed_data == created_data

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

    assert listed_data.keys() == {"tasks", "next_cursor"}
    assert listed_data.get("next_cursor") is None
    assert listed_tasks == [created_task]

    shown_by_key = require_object(
        require_success(
            golden_runner.cli(
                (
                    "task",
                    "show",
                    "ACME-1",
                    "--json",
                    "--non-interactive",
                ),
                cwd=workspace,
            )
        ),
        context="task-show by key data",
    )
    shown_by_uid = require_object(
        require_success(
            golden_runner.cli(
                (
                    "task",
                    "show",
                    task_uid,
                    "--json",
                    "--non-interactive",
                ),
                cwd=workspace,
            )
        ),
        context="task-show by UID data",
    )
    expected_details: JsonObject = {
        "task": created_task,
        "prerequisites": [],
        "current_result": None,
    }
    assert shown_by_key == expected_details
    assert shown_by_uid == expected_details
