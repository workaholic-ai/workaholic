"""Golden specification for shared remote team coordination."""

from __future__ import annotations

import json
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

    from tests.golden import GoldenJourneyRunner, SubjectKind

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
    pytest.mark.requires_network,
    pytest.mark.skip(
        reason=(
            "Phase 6: missing authenticated server, RemoteSession, and shared-team "
            "workflow."
        )
    ),
]


def test_two_remote_users_and_one_agent_share_one_server(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """Remote Subjects observe one authoritative task lifecycle."""
    subjects: dict[str, SubjectKind] = {
        "alice": "human",
        "bob": "human",
        "code-agent": "agent",
    }
    with golden_runner.instance(
        backend="sqlite",
        project_key="ACME",
        remote=True,
        root=tmp_path,
        subjects=subjects,
    ) as instance:
        alice_environment = instance.environment_for("alice")
        bob_environment = instance.environment_for("bob")
        agent_environment = instance.environment_for("code-agent")

        created_data = require_object(
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "add",
                        "Shared remote task",
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        "team-alice-create",
                    ),
                    cwd=tmp_path,
                    environment=alice_environment,
                )
            ),
            context="Alice task-add data",
        )
        created_task = require_object(
            created_data.get("task"),
            context="Alice created task",
        )
        task_key = require_string(created_task.get("key"), context="shared task key")

        claim_data = require_object(
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "claim",
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        "team-agent-claim",
                    ),
                    cwd=tmp_path,
                    environment=agent_environment,
                )
            ),
            context="Agent claim data",
        )
        attempt = require_object(
            claim_data.get("attempt"),
            context="Agent Attempt",
        )
        attempt_id = require_string(attempt.get("id"), context="Agent Attempt ID")

        require_success(
            golden_runner.cli(
                (
                    "task",
                    "submit",
                    task_key,
                    "--attempt",
                    attempt_id,
                    "--result-file",
                    "-",
                    "--json",
                    "--non-interactive",
                    "--idempotency-key",
                    "team-agent-submit",
                ),
                cwd=tmp_path,
                input_text=json.dumps(
                    {
                        "summary": "Completed through RemoteSession.",
                        "criteria": [],
                        "artifacts": [],
                        "proposed_follow_ups": [],
                    },
                    separators=(",", ":"),
                ),
                environment=agent_environment,
            )
        )

        bob_list_data = require_object(
            require_success(
                golden_runner.cli(
                    ("task", "list", "--json", "--non-interactive"),
                    cwd=tmp_path,
                    environment=bob_environment,
                )
            ),
            context="Bob task-list data",
        )
        bob_tasks = require_array(
            bob_list_data.get("tasks"),
            context="Bob listed tasks",
        )
        matching_tasks = [
            require_object(task, context="Bob listed task")
            for task in bob_tasks
            if require_object(task, context="Bob listed task").get("key") == task_key
        ]

        assert len(matching_tasks) == 1
        assert matching_tasks[0].get("state") == "done"
