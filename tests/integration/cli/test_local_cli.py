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
    assert status_data["mode"] == "local"
    assert status_data["schema_version"] == 1
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
    assert require_success(shown_by_key) == {"task": created_task}
    assert require_success(shown_by_uid) == {"task": created_task}


def test_invalid_data_directory_fails_without_traceback(tmp_path: Path) -> None:
    """A relative trusted override becomes one safe context error envelope."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = os.environ.copy()
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

    detail = require_error(result, expected_code="CONTEXT_INVALID")
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
