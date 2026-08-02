"""Unit tests for Human Task Result approval and rejection."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import (
    TaskApproveRequest,
    TaskDetailsRequest,
    TaskRejectRequest,
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


def test_task_approve_forwards_comment_and_renders_exact_transition() -> None:
    """Approval completes the current Result with two ordered Human events."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "approve",
            "ACME-1",
            "--comment",
            "Reviewed locally",
            "--expected-version",
            "2",
            "--idempotency-key",
            "approve-2",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(
        require_success(_completed(result, "approve")), context="approval"
    )
    task = require_object(data["task"], context="approved Task")
    retained_result = require_object(data["result"], context="approved Result")
    review = require_object(retained_result["review"], context="approved review")
    events = data["events"]
    assert task["state"] == "done"
    assert review["status"] == "approved"
    assert review["comment"] == "Looks good."
    assert retained_result["attempt_id"] is None
    assert isinstance(events, list)
    assert [require_object(event, context="event")["type"] for event in events] == [
        "review_approved",
        "task_completed",
    ]
    assert session.task_approve_requests == [
        TaskApproveRequest(
            task="ACME-1",
            comment="Reviewed locally",
            expected_version=2,
            idempotency_key="approve-2",
            project="ACME",
        )
    ]
    assert session.task_details_requests == []


def test_task_reject_requires_and_forwards_reason() -> None:
    """Rejection records the required reason and reopens the Task."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "reject",
            "tsk_first",
            "--reason",
            "Evidence is incomplete",
            "--expected-version",
            "2",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(
        require_success(_completed(result, "reject")), context="rejection"
    )
    task = require_object(data["task"], context="reopened Task")
    retained_result = require_object(data["result"], context="rejected Result")
    review = require_object(retained_result["review"], context="rejected review")
    assert task["state"] == "open"
    assert task["current_result_id"] is None
    assert review["status"] == "rejected"
    assert review["reason"] == "Please address the missing evidence."
    assert session.task_reject_requests == [
        TaskRejectRequest(
            task="tsk_first",
            reason="Evidence is incomplete",
            expected_version=2,
        )
    ]


def test_task_reject_requires_reason_before_session() -> None:
    """The parser cannot issue an unattributed Result rejection."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "reject", "ACME-1", "--expected-version", "2"],
    )

    assert result.exit_code == 2
    assert "Missing option '--reason'" in unstyle(result.output)
    assert provider.call_count == 0


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_task_review_automation_requires_expected_version(command: str) -> None:
    """Every non-interactive review mutation rejects an omitted version."""
    provider = SessionProviderSpy(RecordingSession())
    arguments = ["task", command, "ACME-1"]
    if command == "reject":
        arguments.extend(("--reason", "Incomplete"))
    arguments.extend(("--json", "--non-interactive"))

    result = _RUNNER.invoke(create_app(provider), arguments)

    detail = require_error(_completed(result, command), expected_code="INVALID_INPUT")
    assert detail["message"] == (
        "Task mutation requires --expected-version for automation."
    )
    assert provider.call_count == 0


def test_task_review_rejects_invalid_project_before_session() -> None:
    """A malformed Project selector fails strict request validation."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "reject",
            "ACME-1",
            "--reason",
            "Incomplete",
            "--project",
            "invalid key",
            "--expected-version",
            "2",
            "--json",
        ],
    )

    detail = require_error(_completed(result, "reject"), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task-review input is invalid."
    assert provider.call_count == 0


def test_task_approve_interactive_decline_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined Human review uses its one snapshot and leaves Result pending."""
    monkeypatch.setattr(
        "workaholic.cli.task_mutations._is_interactive_terminal",
        lambda: True,
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "approve", "ACME-1"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert result.stdout.endswith("No changes made.\n")
    assert session.task_details_requests == [TaskDetailsRequest(task="ACME-1")]
    assert session.task_approve_requests == []


@pytest.mark.parametrize(
    ("command", "operation"),
    [("approve", "approve_result"), ("reject", "reject_result")],
)
def test_task_review_version_conflict_is_not_retried(
    command: str,
    operation: str,
) -> None:
    """A stale review fails after exactly one Session mutation call."""
    session = RecordingSession()
    session.failures[operation] = ApplicationError(
        ApplicationErrorCode.VERSION_CONFLICT,
        "Task version does not match expected_version.",
    )
    arguments = ["task", command, "ACME-1"]
    if command == "reject":
        arguments.extend(("--reason", "Incomplete"))
    arguments.extend(("--expected-version", "2", "--json", "--non-interactive"))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
    )

    require_error(_completed(result, command), expected_code="VERSION_CONFLICT")
    assert session.task_details_requests == []
    if command == "approve":
        assert len(session.task_approve_requests) == 1
        assert session.task_reject_requests == []
    else:
        assert len(session.task_reject_requests) == 1
        assert session.task_approve_requests == []


def test_task_review_unknown_failure_is_redacted() -> None:
    """Private adapter diagnostics cannot escape the review boundary."""
    private_detail = "private result-row and database path"
    session = RecordingSession()
    session.failures["approve_result"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "approve",
            "ACME-1",
            "--expected-version",
            "2",
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(
        _completed(result, "approve"), expected_code="INTERNAL_ERROR"
    )
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr


@pytest.mark.parametrize(
    ("command", "extra"),
    [("approve", ()), ("reject", ("--reason",))],
)
def test_task_review_help_documents_contract(
    command: str,
    extra: tuple[str, ...],
) -> None:
    """Review help advertises its explicit concurrency and output contract."""
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
        *extra,
    ):
        assert option in output
    assert "--attempt" not in output
    assert provider.call_count == 0
