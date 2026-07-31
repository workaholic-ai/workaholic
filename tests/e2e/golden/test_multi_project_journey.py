"""Golden specification for persistent multi-Project Workspace discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    require_array,
    require_error,
    require_object,
    require_string,
    require_success,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from tests.golden import GoldenJourneyRunner, JsonObject

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
]


def _success_object(
    golden_runner: GoldenJourneyRunner,
    arguments: Sequence[str],
    *,
    cwd: Path,
    context: str,
    environment: Mapping[str, str] | None = None,
) -> JsonObject:
    """Run one fresh CLI process and require an object success payload.

    Args:
        golden_runner: Isolated fresh-process CLI harness.
        arguments: Public CLI arguments after the executable.
        cwd: Exact working directory for context discovery.
        context: Human-readable assertion boundary.
        environment: Optional documented trusted environment selectors.

    Returns:
        Validated JSON object from the success envelope.

    """
    return require_object(
        require_success(
            golden_runner.cli(
                arguments,
                cwd=cwd,
                environment=environment,
            )
        ),
        context=context,
    )


def _task_keys(data: JsonObject, *, context: str) -> list[str]:
    """Extract ordered Task keys from one validated list payload.

    Args:
        data: Task-list success object.
        context: Human-readable assertion boundary.

    Returns:
        Task keys in response order.

    """
    tasks = require_array(data.get("tasks"), context=f"{context} tasks")
    return [
        require_string(
            require_object(task, context=f"{context} task").get("key"),
            context=f"{context} Task key",
        )
        for task in tasks
    ]


def test_each_working_directory_selects_its_bound_project(  # noqa: PLR0915
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """Nested and repeated bindings select durable isolated Project state."""
    acme_workspace = tmp_path / "acme"
    nested_docs_workspace = acme_workspace / "documentation"
    docs_mirror_workspace = tmp_path / "docs-mirror"
    unbound_workspace = tmp_path / "unbound"
    isolated_workspace = tmp_path / "isolated-acme"
    for workspace in (
        nested_docs_workspace,
        docs_mirror_workspace,
        unbound_workspace,
        isolated_workspace,
    ):
        workspace.mkdir(parents=True)

    acme_deep = acme_workspace / "src" / "service"
    nested_docs_deep = nested_docs_workspace / "guides" / "drafts"
    docs_mirror_deep = docs_mirror_workspace / "notes" / "review"
    isolated_deep = isolated_workspace / "src" / "worker"
    for descendant in (
        acme_deep,
        nested_docs_deep,
        docs_mirror_deep,
        isolated_deep,
    ):
        descendant.mkdir(parents=True)

    bootstrap_data = _success_object(
        golden_runner,
        (
            "up",
            "--project-key",
            "ACME",
            "--project-name",
            "Acme delivery",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "multi-up-acme",
        ),
        cwd=acme_workspace,
        context="ACME bootstrap",
    )
    primary_instance = require_object(
        bootstrap_data.get("instance"),
        context="ACME Instance",
    )
    acme_project = require_object(
        bootstrap_data.get("project"),
        context="ACME Project",
    )
    assert acme_project.get("key") == "ACME"

    docs_data = _success_object(
        golden_runner,
        (
            "project",
            "create",
            "--key",
            "DOCS",
            "--name",
            "Documentation",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "multi-create-docs",
        ),
        cwd=acme_deep,
        context="DOCS creation",
    )
    docs_project = require_object(
        docs_data.get("project"),
        context="DOCS Project",
    )
    assert docs_project.get("key") == "DOCS"

    for workspace in (nested_docs_workspace, docs_mirror_workspace):
        binding = _success_object(
            golden_runner,
            (
                "project",
                "bind",
                "DOCS",
                str(workspace),
                "--json",
                "--non-interactive",
            ),
            cwd=acme_deep,
            context=f"DOCS binding at {workspace.name}",
        )
        assert binding.get("project") == docs_project
        assert binding.get("workspace_root") == str(workspace)
        assert binding.get("context_source") == str(workspace / ".workaholic.env")

    acme_task_data = _success_object(
        golden_runner,
        (
            "task",
            "add",
            "Acme implementation",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "multi-task-acme",
        ),
        cwd=acme_deep,
        context="ACME Task creation",
    )
    acme_task = require_object(acme_task_data.get("task"), context="ACME Task")
    assert (acme_task.get("number"), acme_task.get("key")) == (1, "ACME-1")

    docs_task_data = _success_object(
        golden_runner,
        (
            "task",
            "add",
            "Documentation draft",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "multi-task-docs",
        ),
        cwd=nested_docs_deep,
        context="DOCS Task creation",
    )
    docs_task = require_object(docs_task_data.get("task"), context="DOCS Task")
    assert (docs_task.get("number"), docs_task.get("key")) == (1, "DOCS-1")

    acme_context = _success_object(
        golden_runner,
        ("context", "--json", "--non-interactive"),
        cwd=acme_deep,
        context="deep ACME context",
    )
    nested_docs_context = _success_object(
        golden_runner,
        ("context", "--json", "--non-interactive"),
        cwd=nested_docs_deep,
        context="nearest nested DOCS context",
    )
    mirror_docs_context = _success_object(
        golden_runner,
        ("context", "--json", "--non-interactive"),
        cwd=docs_mirror_deep,
        context="mirrored DOCS context",
    )
    assert acme_context.get("project") == acme_project
    assert acme_context.get("workspace_root") == str(acme_workspace)
    assert nested_docs_context.get("project") == docs_project
    assert nested_docs_context.get("workspace_root") == str(nested_docs_workspace)
    assert mirror_docs_context.get("project") == docs_project
    assert mirror_docs_context.get("workspace_root") == str(docs_mirror_workspace)

    acme_list = _success_object(
        golden_runner,
        ("task", "list", "--json", "--non-interactive"),
        cwd=acme_deep,
        context="context-selected ACME Task list",
    )
    nested_docs_list = _success_object(
        golden_runner,
        ("task", "list", "--json", "--non-interactive"),
        cwd=nested_docs_deep,
        context="context-selected DOCS Task list",
    )
    mirrored_docs_list = _success_object(
        golden_runner,
        ("task", "list", "--json", "--non-interactive"),
        cwd=docs_mirror_deep,
        context="second-path DOCS Task list",
    )
    assert _task_keys(acme_list, context="ACME list") == ["ACME-1"]
    assert _task_keys(nested_docs_list, context="nested DOCS list") == ["DOCS-1"]
    assert _task_keys(mirrored_docs_list, context="mirrored DOCS list") == ["DOCS-1"]

    explicit_docs_list = _success_object(
        golden_runner,
        (
            "task",
            "list",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ),
        cwd=acme_deep,
        context="explicit DOCS Task list",
    )
    assert explicit_docs_list == nested_docs_list

    all_projects_list = _success_object(
        golden_runner,
        (
            "task",
            "list",
            "--all-projects",
            "--json",
            "--non-interactive",
        ),
        cwd=unbound_workspace,
        context="all-Project Task list",
    )
    assert _task_keys(all_projects_list, context="all-Project list") == [
        "ACME-1",
        "DOCS-1",
    ]
    assert require_array(
        all_projects_list.get("tasks"),
        context="all-Project tasks",
    ) == [acme_task, docs_task]

    restarted_status = _success_object(
        golden_runner,
        ("status", "--json", "--non-interactive"),
        cwd=acme_deep,
        context="restarted ACME status",
    )
    assert restarted_status.get("instance") == primary_instance
    assert restarted_status.get("project") == acme_project

    hostile_workspace = acme_workspace / "src" / "blocked"
    hostile_workspace.mkdir()
    hostile_context = hostile_workspace / ".workaholic.env"
    hostile_context.write_text(
        "WORKAHOLIC_CONTEXT_VERSION=1\nUNTRUSTED=value\n",
        encoding="utf-8",
    )
    hostile_before = hostile_context.read_bytes()
    malformed_result = golden_runner.cli(
        ("task", "list", "--json", "--non-interactive"),
        cwd=hostile_workspace,
    )
    require_error(malformed_result, expected_code="CONTEXT_INVALID")
    assert hostile_context.read_bytes() == hostile_before

    isolated_bootstrap = _success_object(
        golden_runner,
        (
            "up",
            "--project-key",
            "ACME",
            "--project-name",
            "Isolated Acme",
            "--profile",
            "isolated",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "isolated-up-acme",
        ),
        cwd=isolated_workspace,
        context="isolated-profile ACME bootstrap",
    )
    isolated_instance = require_object(
        isolated_bootstrap.get("instance"),
        context="isolated Instance",
    )
    isolated_project = require_object(
        isolated_bootstrap.get("project"),
        context="isolated ACME Project",
    )
    assert isolated_project.get("key") == acme_project.get("key") == "ACME"
    assert isolated_project.get("id") != acme_project.get("id")
    assert isolated_instance.get("id") != primary_instance.get("id")

    isolated_task_data = _success_object(
        golden_runner,
        (
            "task",
            "add",
            "Isolated implementation",
            "--json",
            "--non-interactive",
            "--idempotency-key",
            "isolated-task-acme",
        ),
        cwd=isolated_deep,
        context="isolated-profile Task creation",
    )
    isolated_task = require_object(
        isolated_task_data.get("task"),
        context="isolated ACME Task",
    )
    assert isolated_task.get("key") == acme_task.get("key") == "ACME-1"
    assert isolated_task.get("uid") != acme_task.get("uid")

    isolated_all_projects = _success_object(
        golden_runner,
        (
            "task",
            "list",
            "--all-projects",
            "--json",
            "--non-interactive",
        ),
        cwd=unbound_workspace,
        context="trusted environment-selected isolated Task list",
        environment={"WORKAHOLIC_PROFILE": "isolated"},
    )
    assert require_array(
        isolated_all_projects.get("tasks"),
        context="isolated all-Project tasks",
    ) == [isolated_task]
