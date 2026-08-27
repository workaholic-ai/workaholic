"""Golden specification for exclusive local Human and Agent execution."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    GoldenCliInvocation,
    require_array,
    require_error,
    require_object,
    require_string,
    require_success,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from subprocess import CompletedProcess

    from tests.golden import GoldenJourneyRunner, JsonObject

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
]


# Keep the exit journey linear so every fresh process observes durable state.
def test_human_and_agent_claims_are_exclusive_across_fresh_cli_processes(  # noqa: PLR0915
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """Human and Agent owners execute safely through one bootstrap Subject."""
    workspace = tmp_path / "agent-workspace"
    workspace.mkdir()
    bootstrap = _json_success(
        golden_runner,
        ("up", "--project-key", "ACME", "--idempotency-key", "agent-up"),
        workspace=workspace,
        context="bootstrap",
    )
    subject = require_object(bootstrap["subject"], context="bootstrap Subject")
    subject_id = require_string(subject["id"], context="bootstrap Subject ID")
    assert subject["kind"] == "human"

    human_task = _add_task(
        golden_runner,
        workspace=workspace,
        title="Human-owned delivery",
        idempotency_key="human-task",
    )
    human_claim = _json_success(
        golden_runner,
        (
            "task",
            "claim",
            "ACME-1",
            "--lease",
            "8h",
            "--idempotency-key",
            "human-claim",
        ),
        workspace=workspace,
        context="Human Claim",
    )
    claimed_human_task = require_object(
        human_claim["task"],
        context="Human claimed Task",
    )
    claimed_human = require_object(human_claim["claim"], context="Human Claim")
    assert human_claim["attempt"] is None
    assert claimed_human["subject_id"] == subject_id
    assert claimed_human["attempt_id"] is None
    assert claimed_human_task["version"] == human_task["version"] == 1
    _assert_events(
        human_claim,
        expected_types=("task_claimed",),
        subject_id=subject_id,
        attempt_id=None,
    )

    human_renewal = _json_success(
        golden_runner,
        (
            "task",
            "renew",
            "ACME-1",
            "--lease",
            "12h",
            "--idempotency-key",
            "human-renew",
        ),
        workspace=workspace,
        context="Human renewal",
    )
    renewed_human = require_object(
        human_renewal["claim"],
        context="renewed Human Claim",
    )
    assert require_string(
        renewed_human["lease_expires_at"],
        context="renewed Human Lease expiry",
    ) > require_string(
        claimed_human["lease_expires_at"],
        context="original Human Lease expiry",
    )
    assert (
        require_object(
            human_renewal["task"],
            context="renewed Human Task",
        )["version"]
        == 1
    )
    assert human_renewal["attempt"] is None

    human_update = _json_success(
        golden_runner,
        (
            "task",
            "update",
            "ACME-1",
            "--priority",
            "80",
            "--expected-version",
            "1",
            "--idempotency-key",
            "human-update",
        ),
        workspace=workspace,
        context="Human owned mutation",
    )
    assert (
        require_object(human_update["task"], context="updated Human Task")["version"]
        == 2
    )
    human_submission = _json_success(
        golden_runner,
        (
            "task",
            "submit",
            "ACME-1",
            "--comment",
            "Implemented manually.",
            "--expected-version",
            "2",
            "--result-file",
            "-",
            "--idempotency-key",
            "human-submit",
        ),
        workspace=workspace,
        input_text=_result_payload("Human implementation complete."),
        context="Human submission",
    )
    submitted_human_result = require_object(
        human_submission["result"],
        context="Human Result",
    )
    assert submitted_human_result["attempt_id"] is None
    assert submitted_human_result["submitted_by"] == subject_id
    assert (
        require_object(
            human_submission["task"],
            context="completed Human Task",
        )["state"]
        == "done"
    )

    agent_task = _add_task(
        golden_runner,
        workspace=workspace,
        title="Agent-owned delivery",
        idempotency_key="agent-task",
    )
    agent_claim = _json_success(
        golden_runner,
        (
            "task",
            "claim",
            "--lease",
            "15m",
            "--idempotency-key",
            "agent-claim",
        ),
        workspace=workspace,
        context="Agent Claim",
    )
    claimed_agent_task = require_object(
        agent_claim["task"],
        context="Agent claimed Task",
    )
    agent_claim_record = require_object(agent_claim["claim"], context="Agent Claim")
    agent_attempt = require_object(agent_claim["attempt"], context="Agent Attempt")
    attempt_id = require_string(agent_attempt["id"], context="Agent Attempt ID")
    assert claimed_agent_task["uid"] == agent_task["uid"]
    assert claimed_agent_task["version"] == 1
    assert agent_claim_record["subject_id"] == subject_id
    assert agent_claim_record["attempt_id"] == attempt_id
    assert agent_attempt["subject_id"] == subject_id
    assert agent_attempt["status"] == "active"
    _assert_events(
        agent_claim,
        expected_types=("task_claimed",),
        subject_id=subject_id,
        attempt_id=attempt_id,
    )

    heartbeat = _json_success(
        golden_runner,
        (
            "task",
            "heartbeat",
            "ACME-2",
            "--attempt",
            attempt_id,
            "--lease",
            "30m",
            "--idempotency-key",
            "agent-heartbeat",
        ),
        workspace=workspace,
        context="Agent heartbeat",
    )
    heartbeat_claim = require_object(
        heartbeat["claim"],
        context="heartbeat Claim",
    )
    assert require_string(
        heartbeat_claim["lease_expires_at"],
        context="heartbeat Lease expiry",
    ) > require_string(
        agent_claim_record["lease_expires_at"],
        context="original Agent Lease expiry",
    )
    assert require_object(heartbeat["task"], context="heartbeat Task")["version"] == 1

    progress_input = {
        "message": "Implementing and verifying the change.",
        "percent_complete": 70,
        "observations": [
            {
                "kind": "risk",
                "text": "The final integration check is still running.",
            }
        ],
    }
    progress = _json_success(
        golden_runner,
        (
            "task",
            "progress",
            "ACME-2",
            "--attempt",
            attempt_id,
            "--input-file",
            "-",
            "--idempotency-key",
            "agent-progress",
        ),
        workspace=workspace,
        input_text=json.dumps(progress_input, separators=(",", ":")),
        context="Agent progress",
    )
    assert require_object(progress["task"], context="progress Task")["version"] == 1
    assert (
        require_object(progress["attempt"], context="progress Attempt")["id"]
        == attempt_id
    )
    _assert_events(
        progress,
        expected_types=("progress_reported", "observation_added"),
        subject_id=subject_id,
        attempt_id=attempt_id,
    )

    locked = golden_runner.cli(
        (
            "task",
            "update",
            "ACME-2",
            "--priority",
            "90",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ),
        cwd=workspace,
    )
    require_error(locked, expected_code="TASK_LOCKED")

    agent_submission = _json_success(
        golden_runner,
        (
            "task",
            "submit",
            "ACME-2",
            "--attempt",
            attempt_id,
            "--expected-version",
            "1",
            "--result-file",
            "-",
            "--idempotency-key",
            "agent-submit",
        ),
        workspace=workspace,
        input_text=_result_payload("Agent implementation complete."),
        context="Agent submission",
    )
    submitted_agent_task = require_object(
        agent_submission["task"],
        context="submitted Agent Task",
    )
    submitted_agent_result = require_object(
        agent_submission["result"],
        context="Agent Result",
    )
    terminal_attempt = require_object(
        agent_submission["attempt"],
        context="terminal Agent Attempt",
    )
    assert submitted_agent_task["state"] == "done"
    assert submitted_agent_task["version"] == 2
    assert submitted_agent_result["submitted_by"] == subject_id
    assert submitted_agent_result["attempt_id"] == attempt_id
    assert terminal_attempt["id"] == attempt_id
    assert terminal_attempt["status"] == "submitted"
    assert terminal_attempt["ended_at"] is not None
    assert agent_submission["claim"] is None
    _assert_events(
        agent_submission,
        expected_types=("result_submitted", "task_completed"),
        subject_id=subject_id,
        attempt_id=attempt_id,
    )

    require_error(
        golden_runner.cli(
            (
                "task",
                "heartbeat",
                "ACME-2",
                "--attempt",
                attempt_id,
                "--json",
                "--non-interactive",
            ),
            cwd=workspace,
        ),
        expected_code="LEASE_LOST",
    )

    expiring_task = _add_task(
        golden_runner,
        workspace=workspace,
        title="Lease-expiry delivery",
        idempotency_key="expiry-task",
    )
    expiring_claim = _json_success(
        golden_runner,
        (
            "task",
            "claim",
            "--lease",
            "1s",
            "--idempotency-key",
            "expiring-claim",
        ),
        workspace=workspace,
        context="expiring Agent Claim",
    )
    expiring_attempt = require_object(
        expiring_claim["attempt"],
        context="expiring Agent Attempt",
    )
    expired_attempt_id = require_string(
        expiring_attempt["id"],
        context="expiring Attempt ID",
    )
    assert (
        require_object(expiring_claim["task"], context="expiring Task")["uid"]
        == expiring_task["uid"]
    )
    time.sleep(1.1)
    require_error(
        golden_runner.cli(
            (
                "task",
                "heartbeat",
                "ACME-3",
                "--attempt",
                expired_attempt_id,
                "--json",
                "--non-interactive",
            ),
            cwd=workspace,
        ),
        expected_code="LEASE_LOST",
    )
    reclaimed = _json_success(
        golden_runner,
        (
            "task",
            "claim",
            "--lease",
            "15m",
            "--idempotency-key",
            "reclaimed-agent-claim",
        ),
        workspace=workspace,
        context="reclaimed Agent Claim",
    )
    reclaimed_attempt = require_object(
        reclaimed["attempt"],
        context="reclaimed Agent Attempt",
    )
    reclaimed_attempt_id = require_string(
        reclaimed_attempt["id"],
        context="reclaimed Attempt ID",
    )
    assert reclaimed_attempt_id != expired_attempt_id
    reclaimed_events = _assert_events(
        reclaimed,
        expected_types=("claim_expired", "task_claimed"),
        subject_id=subject_id,
        attempt_id=None,
        mixed_attempt_ids=(expired_attempt_id, reclaimed_attempt_id),
    )
    assert reclaimed_events[0]["attempt_id"] == expired_attempt_id
    assert reclaimed_events[1]["attempt_id"] == reclaimed_attempt_id
    require_error(
        golden_runner.cli(
            (
                "task",
                "progress",
                "ACME-3",
                "--attempt",
                expired_attempt_id,
                "--input-file",
                "-",
                "--json",
                "--non-interactive",
            ),
            cwd=workspace,
            input_text=json.dumps({"message": "Stale writer."}),
        ),
        expected_code="LEASE_LOST",
    )

    race_task = _add_task(
        golden_runner,
        workspace=workspace,
        title="Contended delivery",
        idempotency_key="race-task",
    )
    assert race_task["key"] == "ACME-4"
    race_results = golden_runner.cli_race(
        (
            GoldenCliInvocation(
                arguments=(
                    "task",
                    "claim",
                    "ACME-4",
                    "--lease",
                    "8h",
                    "--idempotency-key",
                    "race-human",
                    "--json",
                    "--non-interactive",
                ),
                cwd=workspace,
            ),
            GoldenCliInvocation(
                arguments=(
                    "task",
                    "claim",
                    "--lease",
                    "15m",
                    "--idempotency-key",
                    "race-agent-one",
                    "--json",
                    "--non-interactive",
                ),
                cwd=workspace,
            ),
            GoldenCliInvocation(
                arguments=(
                    "task",
                    "claim",
                    "--lease",
                    "15m",
                    "--idempotency-key",
                    "race-agent-two",
                    "--json",
                    "--non-interactive",
                ),
                cwd=workspace,
            ),
        )
    )
    _assert_exclusive_race(race_results, subject_id=subject_id)

    history = _json_success(
        golden_runner,
        ("task", "events", "ACME-2", "--limit", "100"),
        workspace=workspace,
        context="Agent Task history",
    )
    history_events = [
        require_object(item, context="Agent TaskEvent")
        for item in require_array(history["events"], context="Agent history")
    ]
    assert [event["type"] for event in history_events] == [
        "task_created",
        "task_claimed",
        "claim_renewed",
        "progress_reported",
        "observation_added",
        "result_submitted",
        "task_completed",
    ]
    assert all(event["actor_subject_id"] == subject_id for event in history_events)
    assert history_events[0]["attempt_id"] is None
    assert all(event["attempt_id"] == attempt_id for event in history_events[1:])


def _json_success(
    runner: GoldenJourneyRunner,
    arguments: Sequence[str],
    *,
    workspace: Path,
    context: str,
    input_text: str | None = None,
) -> JsonObject:
    """Run one fresh JSON CLI process and require an object payload."""
    result = runner.cli(
        (*arguments, "--json", "--non-interactive"),
        cwd=workspace,
        input_text=input_text,
    )
    return require_object(require_success(result), context=context)


def _add_task(
    runner: GoldenJourneyRunner,
    *,
    workspace: Path,
    title: str,
    idempotency_key: str,
) -> JsonObject:
    """Create and return one Task through a fresh public CLI process."""
    data = _json_success(
        runner,
        (
            "task",
            "add",
            title,
            "--idempotency-key",
            idempotency_key,
        ),
        workspace=workspace,
        context=f"{title} creation",
    )
    assert data.keys() == {"task"}
    return require_object(data["task"], context=title)


def _result_payload(summary: str) -> str:
    """Serialize one deterministic closed Result input."""
    return json.dumps(
        {
            "summary": summary,
            "criteria": [],
            "artifacts": [],
            "proposed_follow_ups": [],
        },
        separators=(",", ":"),
    )


def _assert_events(
    data: JsonObject,
    *,
    expected_types: tuple[str, ...],
    subject_id: str,
    attempt_id: str | None,
    mixed_attempt_ids: tuple[str, ...] | None = None,
) -> list[JsonObject]:
    """Validate one attributable ordered Phase 4 event batch."""
    events = [
        require_object(item, context="Phase 4 TaskEvent")
        for item in require_array(data["events"], context="Phase 4 events")
    ]
    assert [event["type"] for event in events] == list(expected_types)
    assert all(event["actor_subject_id"] == subject_id for event in events)
    if mixed_attempt_ids is None:
        assert all(event["attempt_id"] == attempt_id for event in events)
    else:
        assert tuple(event["attempt_id"] for event in events) == mixed_attempt_ids
    return events


def _assert_exclusive_race(
    results: tuple[CompletedProcess[str], ...],
    *,
    subject_id: str,
) -> None:
    """Require one Human-or-Agent winner and only documented loser outcomes."""
    winners = [index for index, result in enumerate(results) if result.returncode == 0]
    assert len(winners) == 1
    winner_index = winners[0]
    winner = require_object(
        require_success(results[winner_index]),
        context="Claim race winner",
    )
    winner_task = require_object(winner["task"], context="race winner Task")
    winner_claim = require_object(winner["claim"], context="race winner Claim")
    assert winner_task["key"] == "ACME-4"
    assert winner_task["version"] == 1
    assert winner_claim["subject_id"] == subject_id

    if winner_index == 0:
        assert winner["attempt"] is None
        assert winner_claim["attempt_id"] is None
    else:
        winner_attempt = require_object(
            winner["attempt"],
            context="race winner Attempt",
        )
        assert winner_claim["attempt_id"] == winner_attempt["id"]
        assert winner_attempt["status"] == "active"

    for index, result in enumerate(results):
        if index == winner_index:
            continue
        expected_code = "TASK_LOCKED" if index == 0 else "NO_TASK_AVAILABLE"
        error = require_error(result, expected_code=expected_code)
        assert error["retryable"] is True
