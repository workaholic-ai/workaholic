"""Golden specification for autonomous local-agent execution."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    require_error,
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
    pytest.mark.skip(
        reason=(
            "Phase 4: missing agent claims, leases, heartbeats, and result submission."
        )
    ),
]


def test_agent_completes_current_attempt_but_cannot_renew_an_expired_attempt(
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """An Agent heartbeats and submits valid work but loses an expired Lease."""
    subjects: dict[str, SubjectKind] = {
        "operator": "human",
        "agent-one": "agent",
    }
    with golden_runner.instance(
        backend="sqlite",
        project_key="ACME",
        remote=False,
        root=tmp_path,
        subjects=subjects,
    ) as instance:
        operator_environment = instance.environment_for("operator")
        agent_environment = instance.environment_for("agent-one")

        for sequence, title in enumerate(
            ("Completable task", "Lease-expiry task"),
            start=1,
        ):
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "add",
                        title,
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        f"agent-task-{sequence}",
                    ),
                    cwd=tmp_path,
                    environment=operator_environment,
                )
            )

        claim_data = require_object(
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "claim",
                        "--lease",
                        "15m",
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        "agent-claim-completable",
                    ),
                    cwd=tmp_path,
                    environment=agent_environment,
                )
            ),
            context="claim data",
        )
        claimed_task = require_object(
            claim_data.get("task"),
            context="claimed task",
        )
        attempt = require_object(
            claim_data.get("attempt"),
            context="claim attempt",
        )
        task_key = require_string(claimed_task.get("key"), context="claimed task key")
        attempt_id = require_string(attempt.get("id"), context="Attempt ID")

        heartbeat_data = require_object(
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "heartbeat",
                        task_key,
                        "--attempt",
                        attempt_id,
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        "agent-heartbeat-completable",
                    ),
                    cwd=tmp_path,
                    environment=agent_environment,
                )
            ),
            context="heartbeat data",
        )
        heartbeat_attempt = require_object(
            heartbeat_data.get("attempt"),
            context="heartbeat Attempt",
        )
        assert heartbeat_attempt.get("id") == attempt_id

        result_payload = json.dumps(
            {
                "summary": "The requested work is complete.",
                "criteria": [],
                "artifacts": [],
                "proposed_follow_ups": [],
            },
            separators=(",", ":"),
        )
        submitted_data = require_object(
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
                        "agent-submit-completable",
                    ),
                    cwd=tmp_path,
                    input_text=result_payload,
                    environment=agent_environment,
                )
            ),
            context="submission data",
        )
        submitted_task = require_object(
            submitted_data.get("task"),
            context="submitted task",
        )
        assert submitted_task.get("state") == "done"

        expiring_claim_data = require_object(
            require_success(
                golden_runner.cli(
                    (
                        "task",
                        "claim",
                        "--lease",
                        "1s",
                        "--json",
                        "--non-interactive",
                        "--idempotency-key",
                        "agent-claim-expiring",
                    ),
                    cwd=tmp_path,
                    environment=agent_environment,
                )
            ),
            context="expiring claim data",
        )
        expiring_task = require_object(
            expiring_claim_data.get("task"),
            context="expiring claimed task",
        )
        expiring_attempt = require_object(
            expiring_claim_data.get("attempt"),
            context="expiring Attempt",
        )
        expiring_task_key = require_string(
            expiring_task.get("key"),
            context="expiring task key",
        )
        expiring_attempt_id = require_string(
            expiring_attempt.get("id"),
            context="expiring Attempt ID",
        )

        time.sleep(1.1)

        require_error(
            golden_runner.cli(
                (
                    "task",
                    "heartbeat",
                    expiring_task_key,
                    "--attempt",
                    expiring_attempt_id,
                    "--json",
                    "--non-interactive",
                    "--idempotency-key",
                    "agent-heartbeat-expired",
                ),
                cwd=tmp_path,
                environment=agent_environment,
            ),
            expected_code="LEASE_LOST",
        )
