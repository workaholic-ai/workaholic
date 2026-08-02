"""Unit tests for Human Task state-transition CLI commands."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import (
    TaskBlockRequest,
    TaskCancelRequest,
    TaskUnblockRequest,
)

_RUNNER = CliRunner()


def _completed(result: Result, command: str) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", command),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_block_forwards_reason_and_optimistic_metadata() -> None:
    """Blocking carries one attributable reason and exact caller version."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "block",
            "ACME-1",
            "--reason",
            "Waiting for credentials",
            "--expected-version",
            "3",
            "--idempotency-key",
            "block-3",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    require_success(_completed(result, "block"))
    assert session.task_block_requests == [
        TaskBlockRequest(
            task="ACME-1",
            reason="Waiting for credentials",
            expected_version=3,
            idempotency_key="block-3",
            project="ACME",
        )
    ]
    assert session.task_details_requests == []


def test_task_unblock_forwards_exact_request() -> None:
    """Unblocking exposes no raw state value or implicit version fetch."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "unblock",
            "tsk_first",
            "--expected-version",
            "4",
            "--idempotency-key",
            "unblock-4",
        ],
    )

    assert result.exit_code == 0
    assert session.task_unblock_requests == [
        TaskUnblockRequest(
            task="tsk_first",
            expected_version=4,
            idempotency_key="unblock-4",
        )
    ]
    assert session.task_details_requests == []


@pytest.mark.parametrize("reason", [None, "No longer needed"])
def test_task_cancel_forwards_optional_reason(reason: str | None) -> None:
    """Cancellation preserves either explicit rationale or deliberate absence."""
    session = RecordingSession()
    arguments = [
        "task",
        "cancel",
        "ACME-1",
        "--expected-version",
        "5",
    ]
    if reason is not None:
        arguments.extend(("--reason", reason))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
    )

    assert result.exit_code == 0
    assert session.task_cancel_requests == [
        TaskCancelRequest(
            task="ACME-1",
            expected_version=5,
            reason=reason,
        )
    ]


@pytest.mark.parametrize(
    ("command", "operation"),
    [
        ("block", "block_task"),
        ("unblock", "unblock_task"),
        ("cancel", "cancel_task"),
    ],
)
def test_task_transition_preserves_invalid_transition_without_retry(
    command: str,
    operation: str,
) -> None:
    """Lifecycle rejection is rendered once from one Session invocation."""
    session = RecordingSession()
    session.failures[operation] = ApplicationError(
        ApplicationErrorCode.INVALID_TRANSITION,
        "The requested Task transition is invalid from its current state.",
    )
    arguments = ["task", command, "ACME-1"]
    if command == "block":
        arguments.extend(("--reason", "Waiting"))
    arguments.extend(("--expected-version", "1", "--json", "--non-interactive"))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
    )

    detail = require_error(
        _completed(result, command), expected_code="INVALID_TRANSITION"
    )
    assert detail["message"] == (
        "The requested Task transition is invalid from its current state."
    )
    if command == "block":
        assert len(session.task_block_requests) == 1
    elif command == "unblock":
        assert len(session.task_unblock_requests) == 1
    else:
        assert len(session.task_cancel_requests) == 1
    assert session.task_details_requests == []


def test_task_block_requires_reason_at_parser_boundary() -> None:
    """The public block command cannot issue an unattributed state change."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "block", "ACME-1", "--expected-version", "1"],
    )

    assert result.exit_code == 2
    assert "Missing option '--reason'" in unstyle(result.output)
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("command", "required_options"),
    [
        ("block", ("--reason",)),
        ("unblock", ()),
        ("cancel", ("--reason",)),
    ],
)
def test_task_transition_help_documents_automation_contract(
    command: str,
    required_options: tuple[str, ...],
) -> None:
    """Each transition advertises version, idempotency, and output controls."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(create_app(provider), ["task", command, "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    for option in (
        "TASK",
        "--expected-version",
        "--idempotency-key",
        "--project",
        "--json",
        "--non-interactive",
        *required_options,
    ):
        assert option in output
    assert provider.call_count == 0
