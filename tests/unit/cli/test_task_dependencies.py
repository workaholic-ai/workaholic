"""Unit tests for Task dependency mutation CLI commands."""

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
    TaskAddDependencyRequest,
    TaskRemoveDependencyRequest,
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


def test_task_add_dependency_forwards_exact_optimistic_request() -> None:
    """Dependency addition preserves selectors, scope, version, and replay key."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "add-dependency",
            "ACME-2",
            "tsk_first",
            "--expected-version",
            "2",
            "--idempotency-key",
            "dependency-2",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    require_success(_completed(result, "add-dependency"))
    assert session.task_add_dependency_requests == [
        TaskAddDependencyRequest(
            task="ACME-2",
            prerequisite="tsk_first",
            expected_version=2,
            idempotency_key="dependency-2",
            project="ACME",
        )
    ]
    assert session.task_details_requests == []


def test_task_remove_dependency_forwards_exact_optimistic_request() -> None:
    """Dependency removal versions only its selected dependant Task."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "remove-dependency",
            "tsk_second",
            "ACME-1",
            "--expected-version",
            "3",
            "--idempotency-key",
            "dependency-remove-3",
        ],
    )

    assert result.exit_code == 0
    assert session.task_remove_dependency_requests == [
        TaskRemoveDependencyRequest(
            task="tsk_second",
            prerequisite="ACME-1",
            expected_version=3,
            idempotency_key="dependency-remove-3",
        )
    ]


@pytest.mark.parametrize(
    ("command", "operation", "code"),
    [
        (
            "add-dependency",
            "add_task_dependency",
            ApplicationErrorCode.DEPENDENCY_CONFLICT,
        ),
        (
            "add-dependency",
            "add_task_dependency",
            ApplicationErrorCode.DEPENDENCY_CYCLE,
        ),
        (
            "add-dependency",
            "add_task_dependency",
            ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY,
        ),
        (
            "remove-dependency",
            "remove_task_dependency",
            ApplicationErrorCode.DEPENDENCY_CONFLICT,
        ),
        (
            "remove-dependency",
            "remove_task_dependency",
            ApplicationErrorCode.VERSION_CONFLICT,
        ),
    ],
)
def test_task_dependency_errors_are_unchanged_and_not_retried(
    command: str,
    operation: str,
    code: ApplicationErrorCode,
) -> None:
    """Graph and optimistic conflicts retain their public error identities."""
    session = RecordingSession()
    session.failures[operation] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            command,
            "ACME-2",
            "ACME-1",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result, command), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    request_log = (
        session.task_add_dependency_requests
        if command == "add-dependency"
        else session.task_remove_dependency_requests
    )
    assert len(request_log) == 1
    assert session.task_details_requests == []


@pytest.mark.parametrize("selector_position", [2, 3])
def test_task_dependency_rejects_invalid_selector_before_session(
    selector_position: int,
) -> None:
    """Malformed dependant and prerequisite selectors cannot acquire context."""
    arguments = [
        "task",
        "add-dependency",
        "ACME-2",
        "ACME-1",
        "--expected-version",
        "1",
        "--json",
    ]
    arguments[selector_position] = ""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(create_app(provider), arguments)

    require_error(_completed(result, "add-dependency"), expected_code="INVALID_INPUT")
    assert provider.call_count == 0
    assert session.task_add_dependency_requests == []


@pytest.mark.parametrize("command", ["add-dependency", "remove-dependency"])
def test_task_dependency_help_documents_both_selectors_and_controls(
    command: str,
) -> None:
    """Dependency help exposes both graph operands and automation controls."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(create_app(provider), ["task", command, "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    for expected in (
        "TASK",
        "PREREQUISITE",
        "--expected-version",
        "--idempotency-key",
        "--project",
        "--json",
        "--non-interactive",
    ):
        assert expected in output
    assert provider.call_count == 0
