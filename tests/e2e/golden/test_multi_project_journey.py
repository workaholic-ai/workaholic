"""Golden specification for working-directory project discovery."""

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
        reason=(
            "Phase 2: missing project binding, context discovery, and stable "
            "project task keys."
        )
    ),
]


def test_each_working_directory_selects_its_bound_project(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """Separate directories resolve independent Projects without explicit flags."""
    acme_workspace = tmp_path / "acme"
    docs_workspace = tmp_path / "docs"
    acme_workspace.mkdir()
    docs_workspace.mkdir()

    for workspace, project_key in (
        (acme_workspace, "ACME"),
        (docs_workspace, "DOCS"),
    ):
        require_success(
            golden_runner.cli(
                (
                    "up",
                    "--project-key",
                    project_key,
                    "--json",
                    "--non-interactive",
                    "--idempotency-key",
                    f"multi-up-{project_key.casefold()}",
                ),
                cwd=workspace,
            )
        )
        require_success(
            golden_runner.cli(
                (
                    "task",
                    "add",
                    f"{project_key} task",
                    "--json",
                    "--non-interactive",
                    "--idempotency-key",
                    f"multi-task-{project_key.casefold()}",
                ),
                cwd=workspace,
            )
        )

    acme_nested = acme_workspace / "src" / "package"
    docs_nested = docs_workspace / "guides" / "drafts"
    acme_nested.mkdir(parents=True)
    docs_nested.mkdir(parents=True)

    for workspace, expected_key in (
        (acme_nested, "ACME"),
        (docs_nested, "DOCS"),
    ):
        context_data = require_object(
            require_success(
                golden_runner.cli(
                    ("context", "--json", "--non-interactive"),
                    cwd=workspace,
                )
            ),
            context="context data",
        )
        project = require_object(
            context_data.get("project"),
            context="resolved project",
        )
        assert project.get("key") == expected_key

        listed_data = require_object(
            require_success(
                golden_runner.cli(
                    ("task", "list", "--json", "--non-interactive"),
                    cwd=workspace,
                )
            ),
            context="task-list data",
        )
        tasks = require_array(listed_data.get("tasks"), context="listed tasks")
        task_keys = {
            require_object(task, context="listed task").get("key") for task in tasks
        }

        assert task_keys == {f"{expected_key}-1"}
