"""CLI contract tests for Task Claim ownership commands."""

from __future__ import annotations

from datetime import timedelta
from subprocess import CompletedProcess

import pytest
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import (
    LeaseLostError,
    NoTaskAvailableError,
    TaskLockedError,
)
from workaholic.cli.main import create_app
from workaholic.domain import AttemptId
from workaholic.session import (
    AgentHeartbeatRequest,
    AgentReleaseRequest,
    AgentTaskClaimRequest,
    HumanClaimReleaseRequest,
    HumanClaimRenewRequest,
    HumanTaskClaimRequest,
)

_RUNNER = CliRunner()


def _completed(result: Result, command: str = "claim") -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions.

    Args:
        result: Captured command result.
        command: Task ownership subcommand name.

    Returns:
        Equivalent completed process.

    """
    return CompletedProcess(
        args=("workaholic", "task", command),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_targeted_claim_dispatches_to_human_without_an_attempt() -> None:
    """A Task operand selects a targeted Human Claim and its long Lease path."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "claim",
            "ACME-1",
            "--lease",
            "8h",
            "--project",
            "ACME",
            "--idempotency-key",
            "human-claim-1",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="claim")
    assert set(data) == {"task", "claim", "attempt", "events"}
    claim = require_object(data["claim"], context="Claim")
    assert set(claim) == {
        "task_uid",
        "task_key",
        "subject_id",
        "attempt_id",
        "claimed_at",
        "lease_expires_at",
    }
    assert claim["attempt_id"] is None
    assert data["attempt"] is None
    assert session.human_claim_requests == [
        HumanTaskClaimRequest(
            task="ACME-1",
            lease=timedelta(hours=8),
            project="ACME",
            idempotency_key="human-claim-1",
        )
    ]
    assert session.agent_claim_requests == []


def test_bare_claim_dispatches_to_agent_and_returns_closed_attempt() -> None:
    """An omitted Task pulls the next ready Task and exposes its owner token."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "claim", "--lease", "15m", "--project", "ACME", "--json"],
    )

    data = require_object(require_success(_completed(result)), context="claim")
    claim = require_object(data["claim"], context="Claim")
    attempt = require_object(data["attempt"], context="Attempt")
    assert set(attempt) == {
        "id",
        "task_uid",
        "subject_id",
        "status",
        "lease_expires_at",
        "started_at",
        "ended_at",
    }
    assert claim["attempt_id"] == "atm_cli"
    assert attempt["id"] == "atm_cli"
    assert attempt["status"] == "active"
    assert attempt["ended_at"] is None
    events = data["events"]
    assert isinstance(events, list)
    event = require_object(events[0], context="claim event")
    assert event["actor_kind"] == "agent"
    assert event["attempt_id"] == "atm_cli"
    assert session.agent_claim_requests == [
        AgentTaskClaimRequest(lease=timedelta(minutes=15), project="ACME")
    ]
    assert session.human_claim_requests == []


def test_renew_uses_human_default_and_summary_never_invents_attempt() -> None:
    """Human renewal preserves the domain default and prints no Attempt token."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "renew", "tsk_first", "--idempotency-key", "renew-1"],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "Claim: sub_local" in result.stdout
    assert "Attempt:" not in result.stdout
    assert "atm_" not in result.stdout
    assert session.human_renew_requests == [
        HumanClaimRenewRequest(
            task="tsk_first",
            idempotency_key="renew-1",
        )
    ]


def test_heartbeat_requires_and_forwards_exact_attempt_owner_token() -> None:
    """Agent heartbeat sends the exact Attempt and Agent-bounded Lease."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "heartbeat",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--lease",
            "1s",
            "--project",
            "ACME",
            "--idempotency-key",
            "heartbeat-1",
            "--json",
        ],
    )

    assert require_success(_completed(result, "heartbeat"))
    assert session.agent_heartbeat_requests == [
        AgentHeartbeatRequest(
            task="ACME-1",
            attempt=AttemptId("atm_cli"),
            lease=timedelta(seconds=1),
            project="ACME",
            idempotency_key="heartbeat-1",
        )
    ]


@pytest.mark.parametrize(
    ("arguments", "human_count", "agent_count"),
    [
        (["ACME-1", "--idempotency-key", "release-1"], 1, 0),
        (
            [
                "ACME-1",
                "--attempt",
                "atm_cli",
                "--idempotency-key",
                "release-1",
            ],
            0,
            1,
        ),
    ],
)
def test_release_attempt_presence_selects_owner_path(
    arguments: list[str],
    human_count: int,
    agent_count: int,
) -> None:
    """A null Attempt releases Human ownership; a token releases Agent work."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "release", *arguments, "--project", "ACME", "--json"],
    )

    data = require_object(
        require_success(_completed(result, "release")),
        context="release",
    )
    assert data["claim"] is None
    assert len(session.human_release_requests) == human_count
    assert len(session.agent_release_requests) == agent_count
    if agent_count:
        assert session.agent_release_requests == [
            AgentReleaseRequest(
                task="ACME-1",
                attempt=AttemptId("atm_cli"),
                project="ACME",
                idempotency_key="release-1",
            )
        ]
        attempt = require_object(data["attempt"], context="released Attempt")
        assert attempt["status"] == "released"
        assert attempt["ended_at"] is not None
    else:
        assert session.human_release_requests == [
            HumanClaimReleaseRequest(
                task="ACME-1",
                project="ACME",
                idempotency_key="release-1",
            )
        ]
        assert data["attempt"] is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["claim", "--lease", "1h30m"], "Task-claim input is invalid."),
        (["claim", "ACME-1", "--lease", "59s"], "Task-claim input is invalid."),
        (["heartbeat", "ACME-1"], "Task-heartbeat input is invalid."),
        (
            ["heartbeat", "ACME-1", "--attempt", "invalid"],
            "Task-heartbeat input is invalid.",
        ),
        (
            ["release", "ACME-1", "--attempt", "invalid"],
            "Task-release input is invalid.",
        ),
    ],
)
def test_invalid_inputs_use_stable_json_error_without_session_acquisition(
    arguments: list[str],
    message: str,
) -> None:
    """Malformed ownership input cannot reach the Session or leak its value."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", *arguments, "--json", "--non-interactive"],
    )

    error = require_error(
        _completed(result, arguments[0]),
        expected_code="INVALID_INPUT",
    )
    assert error == {
        "code": "INVALID_INPUT",
        "message": message,
        "retryable": False,
    }
    assert provider.call_count == 0
    assert "invalid" not in result.stderr


@pytest.mark.parametrize(
    ("operation", "command", "failure", "code", "retryable"),
    [
        (
            "claim_next_task",
            "claim",
            NoTaskAvailableError(),
            "NO_TASK_AVAILABLE",
            True,
        ),
        ("claim_task", "claim", TaskLockedError(), "TASK_LOCKED", True),
        ("renew_claim", "renew", LeaseLostError(), "LEASE_LOST", False),
    ],
)
def test_claim_errors_keep_stable_exit_and_retry_contract(
    operation: str,
    command: str,
    failure: Exception,
    code: str,
    retryable: bool,  # noqa: FBT001 - parameterized contract value
) -> None:
    """The three ownership errors remain machine-readable and deterministic."""
    session = RecordingSession()
    session.failures[operation] = failure
    arguments = ["task", command]
    if operation != "claim_next_task":
        arguments.append("ACME-1")
    arguments.append("--json")

    result = _RUNNER.invoke(create_app(SessionProviderSpy(session)), arguments)

    error = require_error(_completed(result, command), expected_code=code)
    assert result.exit_code in (3, 4)
    assert error["retryable"] is retryable
    assert result.stderr == ""


def test_claim_redacts_unexpected_failure_in_json_and_human_streams() -> None:
    """Private Session diagnostics never cross either output boundary."""
    private_detail = "sqlite:///private/claim.db"
    session = RecordingSession()
    session.failures["claim_next_task"] = RuntimeError(private_detail)

    json_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "claim", "--json"],
    )
    human_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "claim"],
    )

    json_error = require_error(
        _completed(json_result),
        expected_code="INTERNAL_ERROR",
    )
    assert json_error["message"] == "An unexpected internal error occurred."
    assert json_result.stderr == ""
    assert human_result.stdout == ""
    assert human_result.stderr == "An unexpected internal error occurred.\n"
    assert private_detail not in json_result.stdout + human_result.stderr


@pytest.mark.parametrize("command", ["claim", "renew", "heartbeat", "release"])
def test_claim_command_help_exposes_stable_automation_options(command: str) -> None:
    """Every ownership command documents project, replay, and output controls."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        [
            "task",
            command,
            "--help",
        ],
    )

    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--idempotency-key" in result.stdout
    assert "--json" in result.stdout
    assert "--non-interactive" in result.stdout
    if command != "release":
        assert "--lease" in result.stdout
    if command in {"heartbeat", "release"}:
        assert "--attempt" in result.stdout
