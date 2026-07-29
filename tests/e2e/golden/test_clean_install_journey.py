"""Golden specification for published-package installation through uvx."""

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
    pytest.mark.requires_network,
    pytest.mark.requires_uv,
    pytest.mark.skip(
        reason=(
            "Phase 9: missing release-candidate publication and clean uvx acceptance."
        )
    ),
]


def test_published_package_runs_through_uvx_in_a_clean_environment(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """A pinned registry release persists tasks across clean uvx invocations."""
    package_spec = golden_runner.published_package_spec()
    workspace = tmp_path / "published-package-workspace"
    workspace.mkdir()

    assert package_spec.startswith("workaholic-ai==")

    version_result = golden_runner.uvx(
        package_spec,
        ("--version",),
        cwd=workspace,
    )
    assert version_result.returncode == 0
    assert version_result.stdout.startswith("workaholic ")

    require_success(
        golden_runner.uvx(
            package_spec,
            (
                "up",
                "--project-key",
                "ACME",
                "--json",
                "--non-interactive",
                "--idempotency-key",
                "clean-install-up",
            ),
            cwd=workspace,
        )
    )
    require_success(
        golden_runner.uvx(
            package_spec,
            (
                "task",
                "add",
                "Published package task",
                "--json",
                "--non-interactive",
                "--idempotency-key",
                "clean-install-task",
            ),
            cwd=workspace,
        )
    )
    listed_data = require_object(
        require_success(
            golden_runner.uvx(
                package_spec,
                ("task", "list", "--json", "--non-interactive"),
                cwd=workspace,
            )
        ),
        context="published task-list data",
    )
    listed_tasks = require_array(
        listed_data.get("tasks"),
        context="published listed tasks",
    )

    assert any(
        require_object(task, context="published listed task").get("key") == "ACME-1"
        and require_object(task, context="published listed task").get("title")
        == "Published package task"
        for task in listed_tasks
    )
