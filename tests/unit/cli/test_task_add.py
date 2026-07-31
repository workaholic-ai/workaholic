"""Unit tests for ``workaholic task add``."""

from __future__ import annotations

from dataclasses import replace
from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy, task
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import TaskCreateRequest

_RUNNER = CliRunner()
_TASK_ADD_ERRORS = (
    ApplicationErrorCode.INVALID_INPUT,
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.PROFILE_NOT_FOUND,
    ApplicationErrorCode.PROFILE_INVALID,
    ApplicationErrorCode.PROFILE_UNSUPPORTED,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PROJECT_NOT_FOUND,
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "add"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_add_json_emits_exact_default_task_and_request() -> None:
    """Title-only creation retains all documented defaults and fields."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "add",
            "First persistent task",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    data = require_object(
        require_success(_completed(result)),
        context="task-add data",
    )
    assert data == {
        "task": {
            "uid": "tsk_first",
            "project_id": "prj_acme",
            "number": 1,
            "key": "ACME-1",
            "title": "First persistent task",
            "objective": "First persistent task",
            "state": "open",
            "priority": 50,
            "version": 1,
            "created_by": "sub_local",
            "created_at": "2026-07-30T12:30:00Z",
            "updated_at": "2026-07-30T12:30:00Z",
        }
    }
    assert session.task_create_requests == [
        TaskCreateRequest(title="First persistent task")
    ]
    assert provider.call_count == 1


def test_task_add_forwards_unicode_objective_priority_and_idempotency() -> None:
    """All optional mutation inputs reach the Session without CLI rewriting."""
    session = RecordingSession()
    session.create_task_result = replace(
        task(),
        title="Ž release",
        objective="Ship the Ž release",
        priority=0,
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "add",
            "Ž release",
            "--objective",
            "Ship the Ž release",
            "--priority",
            "0",
            "--idempotency-key",
            "task-1",
            "--project",
            "DOCS",
            "--json",
        ],
    )

    payload = require_object(
        require_success(_completed(result)),
        context="task-add data",
    )
    created = require_object(payload["task"], context="created task")
    assert created["title"] == "Ž release"
    assert created["objective"] == "Ship the Ž release"
    assert created["priority"] == 0
    assert session.task_create_requests == [
        TaskCreateRequest(
            title="Ž release",
            objective="Ship the Ž release",
            priority=0,
            idempotency_key="task-1",
            project="DOCS",
        )
    ]


def test_task_add_human_summary_escapes_control_characters() -> None:
    """Human output cannot turn Task title controls into terminal sequences."""
    session = RecordingSession()
    session.create_task_result = replace(
        task(),
        title="Line one\n\u001b[31mLine two",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "add", "Line one\n\u001b[31mLine two"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        'ACME-1\topen\tpriority=50\t"Line one\\n\\u001b[31mLine two"\n'
    )
    assert "\u001b" not in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        (("--priority", "101"), "Task-create input is invalid."),
        (("--idempotency-key", ""), "Task-create input is invalid."),
        (("--idempotency-key", "x" * 129), "Task-create input is invalid."),
        (("--project", "invalid key"), "Task-create input is invalid."),
    ],
)
def test_task_add_rejects_request_boundary_values_before_session(
    arguments: tuple[str, ...],
    expected_message: str,
) -> None:
    """Strict request bounds fail before any Session or state acquisition."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "add",
            "First persistent task",
            *arguments,
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == expected_message
    assert provider.call_count == 0
    assert session.task_create_requests == []


@pytest.mark.parametrize(
    ("title", "objective"),
    [
        (" ", None),
        ("x" * 201, None),
        ("Valid title", " "),
        ("Valid title", "x" * 4_001),
    ],
)
def test_task_add_delegates_semantic_text_bounds_to_session(
    title: str,
    objective: str | None,
) -> None:
    """Domain text validation is delegated and returns structured input errors."""
    session = RecordingSession()
    session.failures["create_task"] = ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        "Task-create Session request is invalid.",
    )
    arguments = ["task", "add", title]
    if objective is not None:
        arguments.extend(("--objective", objective))
    arguments.extend(("--json", "--non-interactive"))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task-create Session request is invalid."
    assert session.task_create_requests == [
        TaskCreateRequest(title=title, objective=objective)
    ]


@pytest.mark.parametrize("code", _TASK_ADD_ERRORS)
def test_task_add_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented Task-create failure."""
    session = RecordingSession()
    session.failures["create_task"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "add", "First persistent task", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_task_add_unknown_failure_redacts_storage_diagnostics() -> None:
    """Tracebacks, SQL, and database paths never leak to JSON stdout."""
    private_detail = "sqlite SELECT * FROM tasks at /private/tasks.sqlite3"
    session = RecordingSession()
    session.failures["create_task"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "add", "First persistent task", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert "Traceback" not in result.stdout
    assert result.stderr == ""


def test_task_add_help_and_non_interactive_never_acquire_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Help is complete and a non-interactive mutation never prompts."""
    provider = SessionProviderSpy(RecordingSession())
    help_result = _RUNNER.invoke(
        create_app(provider),
        ["task", "add", "--help"],
    )
    help_output = unstyle(help_result.stdout)

    assert help_result.exit_code == 0
    for expected in (
        "TITLE",
        "--objective",
        "--priority",
        "--idempotency-key",
        "--project",
        "--json",
        "--non-interactive",
    ):
        assert expected in help_output
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("task add --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "add",
            "First persistent task",
            "--non-interactive",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert provider.call_count == 1
