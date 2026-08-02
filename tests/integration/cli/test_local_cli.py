"""Fresh-process integration tests for the composed local CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from tests.golden import require_error, require_object, require_success

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.golden import JsonObject, JsonValue

pytestmark = pytest.mark.integration


def _run_cli(
    arguments: Sequence[str],
    *,
    workspace: Path,
    data_directory: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one supported CLI operation in a fresh Python process.

    Args:
        arguments: Public command arguments after the executable.
        workspace: Exact current Workspace directory.
        data_directory: Isolated trusted local data directory.

    Returns:
        Captured completed subprocess.

    """
    environment = os.environ.copy()
    environment.update(
        {
            "NO_COLOR": "1",
            "WORKAHOLIC_CONFIG_DIR": str(data_directory.parent / "config"),
            "WORKAHOLIC_DATA_DIR": str(data_directory),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "workaholic", *arguments],
        check=False,
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
    )


def _assert_uuid7(value: object, *, prefix: str) -> None:
    """Assert one JSON identifier is a canonical prefixed UUID7.

    Args:
        value: Candidate JSON scalar.
        prefix: Required domain prefix.

    """
    assert isinstance(value, str)
    assert value.startswith(prefix)
    parsed = uuid.UUID(value.removeprefix(prefix))
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122


def _mutation_task_and_events(
    result: subprocess.CompletedProcess[str],
    *,
    context: str,
) -> tuple[JsonObject, list[JsonValue]]:
    """Extract the Task and ordered event batch from one CLI mutation.

    Args:
        result: Fresh-process mutation result.
        context: Assertion label for malformed output.

    Returns:
        Validated public Task object and raw ordered event collection.

    """
    data = require_object(require_success(result), context=context)
    task = require_object(data["task"], context=f"{context} Task")
    events = data["events"]
    assert isinstance(events, list)
    return task, events


def test_local_cli_persists_complete_journey_across_fresh_processes(  # noqa: PLR0915 - one public journey
    tmp_path: Path,
) -> None:
    """Every Phase 1 command works through real SQLite and exact context."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"

    before_up = _run_cli(
        ["status", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    require_error(before_up, expected_code="CONTEXT_NOT_FOUND")
    assert before_up.returncode == 3
    assert before_up.stderr == ""
    assert not data_directory.exists()
    assert not (workspace / ".workaholic.env").exists()

    up = _run_cli(
        [
            "up",
            "--project-key",
            "ACME",
            "--idempotency-key",
            "bootstrap-1",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    up_data = require_object(require_success(up), context="up data")
    instance = require_object(up_data["instance"], context="up instance")
    project = require_object(up_data["project"], context="up project")
    subject = require_object(up_data["subject"], context="up subject")
    workspace_data = require_object(up_data["workspace"], context="up workspace")

    _assert_uuid7(instance["id"], prefix="ins_")
    _assert_uuid7(project["id"], prefix="prj_")
    _assert_uuid7(subject["id"], prefix="sub_")
    assert project["key"] == "ACME"
    assert subject["project_role"] == "owner"
    assert workspace_data == {
        "root": str(workspace),
        "context_file": str(workspace / ".workaholic.env"),
    }
    assert (workspace / ".workaholic.env").is_file()
    assert (data_directory / "local.db").is_file()

    status = _run_cli(
        ["status", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    status_data = require_object(require_success(status), context="status data")
    assert status_data["mode"] == "embedded"
    assert status_data["schema_version"] == 3
    assert status_data["instance"] == instance
    assert status_data["project"] == project
    assert status_data["subject"] == subject

    projects = _run_cli(
        ["project", "list", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    assert require_success(projects) == {"projects": [project]}

    add_arguments = [
        "task",
        "add",
        "First persistent task",
        "--idempotency-key",
        "task-1",
        "--json",
        "--non-interactive",
    ]
    first_add = _run_cli(
        add_arguments,
        workspace=workspace,
        data_directory=data_directory,
    )
    replay_add = _run_cli(
        add_arguments,
        workspace=workspace,
        data_directory=data_directory,
    )
    first_add_data = require_object(
        require_success(first_add),
        context="task-add data",
    )
    replay_add_data = require_object(
        require_success(replay_add),
        context="task-add replay data",
    )
    created_task = require_object(first_add_data["task"], context="created task")

    assert replay_add_data == first_add_data
    _assert_uuid7(created_task["uid"], prefix="tsk_")
    assert created_task["key"] == "ACME-1"
    assert created_task["objective"] == "First persistent task"
    assert created_task["priority"] == 50
    assert created_task["version"] == 1
    assert created_task["created_by"] == subject["id"]

    listed = _run_cli(
        ["task", "list", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    listed_data = require_object(require_success(listed), context="task-list data")
    assert listed_data == {
        "tasks": [created_task],
        "next_cursor": None,
    }

    shown_by_key = _run_cli(
        ["task", "show", "ACME-1", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    shown_by_uid = _run_cli(
        [
            "task",
            "show",
            cast("str", created_task["uid"]),
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    expected_details: JsonObject = {
        "task": created_task,
        "prerequisites": [],
        "current_result": None,
    }
    assert require_success(shown_by_key) == expected_details
    assert require_success(shown_by_uid) == expected_details


def test_local_cli_selects_tasks_across_projects_and_rejects_scope_mismatch(  # noqa: PLR0915 - one public journey
    tmp_path: Path,
) -> None:
    """Phase 2 Task selection remains explicit, ordered, and Instance-contained."""
    acme_workspace = tmp_path / "acme"
    docs_workspace = tmp_path / "Documentation Ω"
    unbound_workspace = tmp_path / "unbound"
    other_workspace = tmp_path / "other-instance"
    for workspace in (
        acme_workspace,
        docs_workspace,
        unbound_workspace,
        other_workspace,
    ):
        workspace.mkdir()
    data_directory = tmp_path / "data"
    other_data_directory = tmp_path / "other-data"

    up = _run_cli(
        [
            "up",
            "--project-key",
            "ACME",
            "--project-name",
            "Acme delivery",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    up_data = require_object(require_success(up), context="Phase 2 up data")
    assert (
        require_object(
            up_data["project"],
            context="Phase 2 initial Project",
        )["name"]
        == "Acme delivery"
    )

    create_docs = _run_cli(
        [
            "project",
            "create",
            "--key",
            "DOCS",
            "--name",
            "Documentation Ω",
            "--idempotency-key",
            "create-docs-1",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    docs_data = require_object(
        require_success(create_docs),
        context="DOCS creation data",
    )
    docs_project = require_object(
        docs_data["project"],
        context="created DOCS Project",
    )
    assert docs_project["key"] == "DOCS"
    assert docs_project["name"] == "Documentation Ω"

    bind_docs = _run_cli(
        [
            "project",
            "bind",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
        workspace=docs_workspace,
        data_directory=data_directory,
    )
    bound_context = require_object(
        require_success(bind_docs),
        context="bound DOCS context",
    )
    assert bound_context["project"] == docs_project
    assert bound_context["workspace_root"] == str(docs_workspace)

    create_empty = _run_cli(
        [
            "project",
            "create",
            "--key",
            "EMPTY",
            "--name",
            "Empty Project",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    require_success(create_empty)

    acme_add = _run_cli(
        [
            "task",
            "add",
            "Acme task",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    acme_task = require_object(
        require_object(
            require_success(acme_add),
            context="ACME add data",
        )["task"],
        context="ACME Task",
    )
    assert acme_task["key"] == "ACME-1"

    docs_add = _run_cli(
        [
            "task",
            "add",
            "Bound documentation task",
            "--json",
            "--non-interactive",
        ],
        workspace=docs_workspace,
        data_directory=data_directory,
    )
    docs_task_one = require_object(
        require_object(
            require_success(docs_add),
            context="bound DOCS add data",
        )["task"],
        context="bound DOCS Task",
    )
    assert docs_task_one["key"] == "DOCS-1"

    explicit_add = _run_cli(
        [
            "task",
            "add",
            "Context-free documentation task",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    docs_task_two = require_object(
        require_object(
            require_success(explicit_add),
            context="explicit DOCS add data",
        )["task"],
        context="explicit DOCS Task",
    )
    assert docs_task_two["key"] == "DOCS-2"

    default_list = _run_cli(
        ["task", "list", "--json", "--non-interactive"],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    default_data = require_object(
        require_success(default_list),
        context="context-default Task page",
    )
    assert default_data == {"tasks": [acme_task], "next_cursor": None}

    explicit_list = _run_cli(
        [
            "task",
            "list",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    explicit_data = require_object(
        require_success(explicit_list),
        context="explicit DOCS Task page",
    )
    assert explicit_data == {
        "tasks": [docs_task_one, docs_task_two],
        "next_cursor": None,
    }

    empty_list = _run_cli(
        [
            "task",
            "list",
            "--project",
            "EMPTY",
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    assert require_success(empty_list) == {
        "tasks": [],
        "next_cursor": None,
    }

    first_all_page = _run_cli(
        [
            "task",
            "list",
            "--all-projects",
            "--limit",
            "2",
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    first_all_data = require_object(
        require_success(first_all_page),
        context="first all-Project Task page",
    )
    assert first_all_data["tasks"] == [acme_task, docs_task_one]
    first_cursor = first_all_data["next_cursor"]
    assert isinstance(first_cursor, str)
    assert first_cursor.startswith("v3.")

    second_all_page = _run_cli(
        [
            "task",
            "list",
            "--all-projects",
            "--limit",
            "2",
            "--cursor",
            first_cursor,
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    assert require_success(second_all_page) == {
        "tasks": [docs_task_two],
        "next_cursor": None,
    }

    human_all = _run_cli(
        ["task", "list", "--all-projects", "--non-interactive"],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    assert [
        line.split("\t", maxsplit=1)[0] for line in human_all.stdout.splitlines()
    ] == ["ACME-1", "DOCS-1", "DOCS-2"]
    assert human_all.stderr == ""

    conflicting_scope = _run_cli(
        [
            "task",
            "list",
            "--project",
            "DOCS",
            "--all-projects",
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    require_error(conflicting_scope, expected_code="INVALID_INPUT")
    assert conflicting_scope.returncode == 2
    assert conflicting_scope.stderr == ""

    wrong_prefix = _run_cli(
        [
            "task",
            "show",
            "DOCS-1",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    require_error(wrong_prefix, expected_code="TASK_NOT_FOUND")
    assert wrong_prefix.returncode == 3

    explicit_show = _run_cli(
        [
            "task",
            "show",
            "DOCS-1",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
        workspace=acme_workspace,
        data_directory=data_directory,
    )
    assert require_success(explicit_show) == {
        "task": docs_task_one,
        "prerequisites": [],
        "current_result": None,
    }

    wrong_cursor_scope = _run_cli(
        [
            "task",
            "list",
            "--project",
            "DOCS",
            "--cursor",
            first_cursor,
            "--json",
            "--non-interactive",
        ],
        workspace=unbound_workspace,
        data_directory=data_directory,
    )
    require_error(wrong_cursor_scope, expected_code="INVALID_INPUT")
    assert wrong_cursor_scope.returncode == 2

    other_up = _run_cli(
        [
            "up",
            "--project-key",
            "OTHER",
            "--json",
            "--non-interactive",
        ],
        workspace=other_workspace,
        data_directory=other_data_directory,
    )
    require_success(other_up)

    cross_instance_context = _run_cli(
        ["status", "--json", "--non-interactive"],
        workspace=acme_workspace,
        data_directory=other_data_directory,
    )
    require_error(cross_instance_context, expected_code="CONTEXT_INVALID")
    assert cross_instance_context.returncode == 3
    assert cross_instance_context.stderr == ""


def test_local_cli_persists_phase_three_task_coordination_across_processes(  # noqa: PLR0915 - one public journey
    tmp_path: Path,
) -> None:
    """Task definition, views, transitions, and dependencies survive restarts."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"

    require_success(
        _run_cli(
            [
                "up",
                "--project-key",
                "ACME",
                "--json",
                "--non-interactive",
            ],
            workspace=workspace,
            data_directory=data_directory,
        )
    )

    definition_file = workspace / "dependent-task.json"
    definition_file.write_text(
        json.dumps(
            {
                "available_at": "2099-01-01T00:00:00Z",
                "approval": "human",
                "acceptance": [
                    {
                        "id": "ac_done",
                        "text": "The implementation is verified.",
                        "required": True,
                    }
                ],
                "context": [
                    {
                        "uri": "https://example.test/specification",
                        "version": "v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    foundation_add = _run_cli(
        [
            "task",
            "add",
            "Foundation",
            "--priority",
            "90",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    foundation_data = require_object(
        require_success(foundation_add),
        context="foundation creation",
    )
    foundation = require_object(
        foundation_data["task"],
        context="foundation Task",
    )
    assert foundation["key"] == "ACME-1"
    assert foundation["version"] == 1

    dependent_add = _run_cli(
        [
            "task",
            "add",
            "Dependent delivery",
            "--objective",
            "Deliver only after the foundation",
            "--priority",
            "80",
            "--input-file",
            str(definition_file),
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent_data = require_object(
        require_success(dependent_add),
        context="dependent creation",
    )
    dependent = require_object(dependent_data["task"], context="dependent Task")
    assert dependent["key"] == "ACME-2"
    assert dependent["approval"] == "human"
    assert dependent["views"] == {
        "ready": False,
        "running": False,
        "scheduled": True,
        "stale": False,
        "awaiting_review": False,
    }
    assert dependent["readiness_reasons"] == ["not_yet_available"]

    scheduled = _run_cli(
        [
            "task",
            "list",
            "--view",
            "scheduled",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    scheduled_data = require_object(
        require_success(scheduled),
        context="scheduled view",
    )
    scheduled_tasks = scheduled_data["tasks"]
    assert isinstance(scheduled_tasks, list)
    assert [item["key"] for item in scheduled_tasks if isinstance(item, dict)] == [
        "ACME-2"
    ]

    clear_schedule = _run_cli(
        [
            "task",
            "update",
            "ACME-2",
            "--clear-available-at",
            "--expected-version",
            "1",
            "--idempotency-key",
            "clear-schedule-1",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, events = _mutation_task_and_events(
        clear_schedule,
        context="clear schedule",
    )
    assert dependent["version"] == 2
    assert dependent["available_at"] is None
    assert require_object(events[0], context="update event")["type"] == "task_updated"

    add_dependency = _run_cli(
        [
            "task",
            "add-dependency",
            "ACME-2",
            "ACME-1",
            "--expected-version",
            "2",
            "--idempotency-key",
            "dependency-2",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, events = _mutation_task_and_events(
        add_dependency,
        context="dependency addition",
    )
    assert dependent["version"] == 3
    assert dependent["depends_on"] == [foundation["uid"]]
    assert require_object(events[0], context="dependency event")["type"] == (
        "task_updated"
    )

    ready_with_dependency = _run_cli(
        [
            "task",
            "list",
            "--view",
            "ready",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    ready_data = require_object(
        require_success(ready_with_dependency),
        context="ready dependency view",
    )
    ready_tasks = ready_data["tasks"]
    assert isinstance(ready_tasks, list)
    assert [item["key"] for item in ready_tasks if isinstance(item, dict)] == ["ACME-1"]

    dependent_details = _run_cli(
        ["task", "show", "ACME-2", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    details = require_object(
        require_success(dependent_details),
        context="dependent details",
    )
    detailed_task = require_object(details["task"], context="detailed dependant")
    assert detailed_task["readiness_reasons"] == ["unsatisfied_dependency"]
    prerequisites = details["prerequisites"]
    assert isinstance(prerequisites, list)
    assert [
        require_object(item, context="prerequisite")["key"] for item in prerequisites
    ] == ["ACME-1"]

    cancel_foundation = _run_cli(
        [
            "task",
            "cancel",
            "ACME-1",
            "--reason",
            "Superseded",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    cancelled_foundation, events = _mutation_task_and_events(
        cancel_foundation,
        context="foundation cancellation",
    )
    assert cancelled_foundation["state"] == "cancelled"
    assert cancelled_foundation["version"] == 2
    assert require_object(events[0], context="cancellation event")["type"] == (
        "task_cancelled"
    )

    unsatisfiable_show = _run_cli(
        ["task", "show", "ACME-2", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    unsatisfiable = require_object(
        require_success(unsatisfiable_show),
        context="unsatisfiable details",
    )
    unsatisfiable_task = require_object(
        unsatisfiable["task"],
        context="unsatisfiable Task",
    )
    assert unsatisfiable_task["readiness_reasons"] == ["unsatisfiable_dependency"]

    remove_dependency = _run_cli(
        [
            "task",
            "remove-dependency",
            "ACME-2",
            "ACME-1",
            "--expected-version",
            "3",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, _events = _mutation_task_and_events(
        remove_dependency,
        context="dependency removal",
    )
    assert dependent["depends_on"] == []
    assert dependent["version"] == 4

    block = _run_cli(
        [
            "task",
            "block",
            "ACME-2",
            "--reason",
            "Waiting for operator input",
            "--expected-version",
            "4",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, events = _mutation_task_and_events(block, context="Task block")
    assert dependent["state"] == "blocked"
    assert dependent["blocking_reason"] == "Waiting for operator input"
    assert dependent["version"] == 5
    assert require_object(events[0], context="block event")["type"] == "task_blocked"

    blocked = _run_cli(
        [
            "task",
            "list",
            "--view",
            "blocked",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    blocked_data = require_object(require_success(blocked), context="blocked view")
    blocked_tasks = blocked_data["tasks"]
    assert isinstance(blocked_tasks, list)
    assert [item["key"] for item in blocked_tasks if isinstance(item, dict)] == [
        "ACME-2"
    ]

    unblock = _run_cli(
        [
            "task",
            "unblock",
            "ACME-2",
            "--expected-version",
            "5",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, events = _mutation_task_and_events(unblock, context="Task unblock")
    assert dependent["state"] == "open"
    assert dependent["blocking_reason"] is None
    assert dependent["version"] == 6
    assert require_object(events[0], context="unblock event")["type"] == (
        "task_unblocked"
    )

    stale_update = _run_cli(
        [
            "task",
            "update",
            "ACME-2",
            "--title",
            "Stale title",
            "--expected-version",
            "5",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    require_error(stale_update, expected_code="VERSION_CONFLICT")
    assert stale_update.returncode == 4

    valid_update = _run_cli(
        [
            "task",
            "update",
            "ACME-2",
            "--title",
            "Coordinated delivery",
            "--objective",
            "Deliver after explicit coordination",
            "--expected-version",
            "6",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    dependent, _events = _mutation_task_and_events(valid_update, context="valid update")
    assert dependent["title"] == "Coordinated delivery"
    assert dependent["objective"] == "Deliver after explicit coordination"
    assert dependent["version"] == 7

    persisted_show = _run_cli(
        ["task", "show", "ACME-2", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=data_directory,
    )
    persisted = require_object(
        require_success(persisted_show),
        context="persisted Task details",
    )
    persisted_task = require_object(persisted["task"], context="persisted Task")
    assert persisted_task["title"] == "Coordinated delivery"
    assert persisted_task["version"] == 7
    assert persisted_task["approval"] == "human"
    assert persisted_task["acceptance"] == [
        {
            "id": "ac_done",
            "text": "The implementation is verified.",
            "required": True,
        }
    ]
    assert persisted_task["context"] == [
        {"uri": "https://example.test/specification", "version": "v1"}
    ]
    persisted_views = require_object(
        persisted_task["views"],
        context="persisted Task views",
    )
    assert persisted_views["ready"] is True

    cancel_dependent = _run_cli(
        [
            "task",
            "cancel",
            "ACME-2",
            "--expected-version",
            "7",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    cancelled_dependent, _events = _mutation_task_and_events(
        cancel_dependent,
        context="dependent cancellation",
    )
    assert cancelled_dependent["state"] == "cancelled"
    assert cancelled_dependent["version"] == 8

    cancelled_view = _run_cli(
        [
            "task",
            "list",
            "--view",
            "cancelled",
            "--json",
            "--non-interactive",
        ],
        workspace=workspace,
        data_directory=data_directory,
    )
    cancelled_data = require_object(
        require_success(cancelled_view),
        context="cancelled view",
    )
    cancelled_tasks = cancelled_data["tasks"]
    assert isinstance(cancelled_tasks, list)
    assert [item["key"] for item in cancelled_tasks if isinstance(item, dict)] == [
        "ACME-1",
        "ACME-2",
    ]


def test_invalid_data_directory_fails_without_traceback(tmp_path: Path) -> None:
    """A relative trusted override becomes one safe profile error envelope."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = os.environ.copy()
    environment["WORKAHOLIC_CONFIG_DIR"] = str(tmp_path / "config")
    environment["WORKAHOLIC_DATA_DIR"] = "relative-data"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "workaholic",
            "status",
            "--json",
            "--non-interactive",
        ],
        check=False,
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
    )

    detail = require_error(result, expected_code="PROFILE_INVALID")
    assert detail["retryable"] is False
    assert result.stderr == ""
    assert "Traceback" not in result.stdout


def test_json_subprocess_output_is_one_object_and_one_newline(
    tmp_path: Path,
) -> None:
    """A composed failure keeps the exact non-streaming framing contract."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run_cli(
        ["status", "--json", "--non-interactive"],
        workspace=workspace,
        data_directory=tmp_path / "data",
    )

    assert result.stdout.endswith("\n")
    assert not result.stdout.endswith("\n\n")
    decoded = json.loads(result.stdout)
    assert decoded["schema"] == "workaholic.cli/v1"
    assert result.stderr == ""
