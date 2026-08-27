"""CLI contract tests for structured Agent Task progress."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, cast

import pytest
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import LeaseLostError, TaskProgressResult
from workaholic.cli.main import create_app
from workaholic.cli.structured_input import STRUCTURED_INPUT_MAX_BYTES
from workaholic.domain import (
    AttemptId,
    ObservationKind,
    ProgressObservation,
    TaskProgress,
)
from workaholic.session import AgentProgressRequest

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions.

    Args:
        result: Captured progress command result.

    Returns:
        Equivalent completed process.

    """
    return CompletedProcess(
        args=("workaholic", "task", "progress"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_progress_accepts_closed_file_and_serializes_ordered_events(
    tmp_path: Path,
) -> None:
    """A file-backed progress report retains order and exact Attempt attribution."""
    source = tmp_path / "progress.json"
    source.write_text(
        json.dumps(
            {
                "message": "Running tests.",
                "percent_complete": 70,
                "observations": [{"kind": "risk", "text": "A retry may be needed."}],
            }
        ),
        encoding="utf-8",
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            str(source),
            "--project",
            "ACME",
            "--idempotency-key",
            "progress-1",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="progress")
    assert set(data) == {"task", "claim", "attempt", "events"}
    attempt = require_object(data["attempt"], context="Attempt")
    assert attempt["id"] == "atm_cli"
    assert attempt["status"] == "active"
    task = require_object(data["task"], context="Task")
    assert task["version"] == 1
    events = data["events"]
    assert isinstance(events, list)
    assert [require_object(event, context="event")["type"] for event in events] == [
        "progress_reported",
        "observation_added",
    ]
    assert all(
        require_object(event, context="event")["attempt_id"] == "atm_cli"
        for event in events
    )
    assert session.agent_progress_requests == [
        AgentProgressRequest(
            task="ACME-1",
            attempt=AttemptId("atm_cli"),
            progress=TaskProgress(
                message="Running tests.",
                percent_complete=70,
                observations=(
                    ProgressObservation(
                        kind=ObservationKind.RISK,
                        text="A retry may be needed.",
                    ),
                ),
            ),
            project="ACME",
            idempotency_key="progress-1",
        )
    ]


def test_task_progress_reads_explicit_stdin_without_prompting() -> None:
    """An Agent may send a minimal progress object through explicit stdin."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            "-",
        ],
        input='{"percent_complete":0}\n',
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "Attempt: atm_cli\tstatus=active" in result.stdout
    assert session.agent_progress_requests[0].progress == TaskProgress(
        percent_complete=0
    )


def test_task_progress_replay_forwards_identical_fingerprint(tmp_path: Path) -> None:
    """Equivalent retries preserve the exact idempotency and progress request."""
    source = tmp_path / "replay-progress.json"
    source.write_text('{"message":"Still working."}', encoding="utf-8")
    session = RecordingSession()
    application = create_app(SessionProviderSpy(session))
    arguments = [
        "task",
        "progress",
        "ACME-1",
        "--attempt",
        "atm_cli",
        "--input-file",
        str(source),
        "--idempotency-key",
        "progress-replay",
        "--json",
    ]

    first = _RUNNER.invoke(application, arguments)
    second = _RUNNER.invoke(application, arguments)

    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    assert len(session.agent_progress_requests) == 2
    assert session.agent_progress_requests[0] == session.agent_progress_requests[1]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"percent_complete": True},
        {"percent_complete": 101},
        {"attempt_id": "atm_forged"},
        {"subject_id": "sub_forged"},
        {"message": "Okay", "unexpected": {}},
        {"observations": [{"kind": "unknown", "text": "No."}]},
        {"observations": [{"kind": "risk", "text": "No.", "nested": {}}]},
        {
            "observations": [
                {"kind": "note", "text": f"Note {index}"} for index in range(51)
            ]
        },
    ],
)
def test_task_progress_rejects_unknown_nested_or_out_of_bounds_input(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    """Closed progress cannot carry identity, extra structure, or bad bounds."""
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            str(source),
            "--json",
        ],
    )

    error = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert error["message"] == "Task-progress input is invalid."
    assert provider.call_count == 0
    assert result.stderr == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["--input-file", "-"],
        ["--attempt", "atm_cli"],
        ["--attempt", "invalid", "--input-file", "-"],
    ],
)
def test_task_progress_requires_attempt_and_input_without_session_access(
    arguments: list[str],
) -> None:
    """Missing or malformed execution ownership stays at the CLI boundary."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "progress", "ACME-1", *arguments, "--json"],
        input='{"message":"Working."}\n',
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert provider.call_count == 0


def test_task_progress_rejects_oversized_input_without_reading_session(
    tmp_path: Path,
) -> None:
    """The shared byte limit applies before progress parsing or Session access."""
    source = tmp_path / "oversized.json"
    source.write_bytes(b"{" + b" " * STRUCTURED_INPUT_MAX_BYTES + b"}")
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            str(source),
            "--json",
        ],
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert provider.call_count == 0


def test_task_progress_preserves_lease_loss_and_redacts_payload() -> None:
    """A stale Agent receives only the fixed Lease failure, never its payload."""
    private_text = "private progress detail"
    session = RecordingSession()
    session.failures["report_progress"] = LeaseLostError()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            "-",
            "--json",
        ],
        input=json.dumps({"message": private_text}),
    )

    error = require_error(_completed(result), expected_code="LEASE_LOST")
    assert error == {
        "code": "LEASE_LOST",
        "message": "The Claim is no longer current.",
        "retryable": False,
    }
    assert private_text not in result.stdout + result.stderr


def test_task_progress_redacts_invalid_session_result_contract() -> None:
    """A Session result outside the strict progress shape becomes internal."""
    session = RecordingSession()
    session.task_progress_result = cast("TaskProgressResult", object())

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "progress",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--input-file",
            "-",
            "--json",
        ],
        input='{"message":"Working."}',
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")


def test_task_progress_help_exposes_required_execution_controls() -> None:
    """Progress help documents ownership, structured input, and replay options."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["task", "progress", "--help"],
    )

    assert result.exit_code == 0
    for option in (
        "--attempt",
        "--input-file",
        "--project",
        "--idempotency-key",
        "--json",
        "--non-interactive",
    ):
        assert option in result.stdout
