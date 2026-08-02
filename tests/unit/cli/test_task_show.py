"""Unit tests for ``workaholic task show``."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import TaskDetailsRequest

_RUNNER = CliRunner()
_TASK_SHOW_ERRORS = (
    ApplicationErrorCode.INVALID_INPUT,
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.PROFILE_NOT_FOUND,
    ApplicationErrorCode.PROFILE_INVALID,
    ApplicationErrorCode.PROFILE_UNSUPPORTED,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PROJECT_NOT_FOUND,
    ApplicationErrorCode.TASK_NOT_FOUND,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "show"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@pytest.mark.parametrize("selector", ["ACME-1", "tsk_first"])
def test_task_show_json_accepts_stable_key_and_uid(selector: str) -> None:
    """Both documented selector forms reach the Session unchanged."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "show", selector, "--json", "--non-interactive"],
        input=None,
    )

    data = require_object(
        require_success(_completed(result)),
        context="task-show data",
    )
    selected = require_object(data["task"], context="shown task")
    assert selected == {
        "uid": "tsk_first",
        "project_id": "prj_acme",
        "number": 1,
        "key": "ACME-1",
        "title": "First persistent task",
        "objective": "First persistent task",
        "state": "open",
        "priority": 50,
        "available_at": None,
        "approval": "none",
        "acceptance": [],
        "context": [],
        "depends_on": [],
        "blocking_reason": None,
        "current_result_id": None,
        "version": 1,
        "created_by": "sub_local",
        "created_at": "2026-07-30T12:30:00Z",
        "updated_at": "2026-07-30T12:30:00Z",
        "views": {
            "ready": True,
            "running": False,
            "scheduled": False,
            "stale": False,
            "awaiting_review": False,
        },
        "readiness_reasons": [],
    }
    assert data["prerequisites"] == []
    assert data["current_result"] is None
    assert session.task_details_requests == [TaskDetailsRequest(task=selector)]
    assert provider.call_count == 1
    assert result.stderr == ""


def test_task_show_forwards_explicit_project_override() -> None:
    """Task lookup can explicitly select another same-Instance Project."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "show",
            "DOCS-1",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert session.task_details_requests == [
        TaskDetailsRequest(task="DOCS-1", project="DOCS")
    ]


def test_task_show_human_output_is_deterministic() -> None:
    """Default show rendering gives one concise stable Task summary."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["task", "show", "ACME-1"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        'ACME-1\topen\tpriority=50\t"First persistent task"\n'
        "Version: 1\n"
        "Readiness: ready (none)\n"
        "Prerequisites: none\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("selector", "options"),
    [
        ("", ()),
        ("x" * 257, ()),
        ("ACME-1", ("--project", "invalid key")),
    ],
)
def test_task_show_rejects_invalid_selector_before_session(
    selector: str,
    options: tuple[str, ...],
) -> None:
    """A selector outside Session bounds cannot trigger context access."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "show",
            selector,
            *options,
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task selector is invalid."
    assert provider.call_count == 0
    assert session.task_details_requests == []


@pytest.mark.parametrize("code", _TASK_SHOW_ERRORS)
def test_task_show_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented Task-show failure."""
    session = RecordingSession()
    session.failures["get_task_details"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "show", "ACME-1", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_task_show_unknown_failure_is_fully_redacted() -> None:
    """Unexpected selector diagnostics never leak into either output stream."""
    private_detail = "private SQL and database path diagnostic"
    session = RecordingSession()
    session.failures["get_task_details"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "show", "ACME-1", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr


def test_task_group_help_and_show_non_interactive_are_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task help acquires no Session and show never prompts."""
    provider = SessionProviderSpy(RecordingSession())
    group_help = _RUNNER.invoke(create_app(provider), ["task", "--help"])
    show_help = _RUNNER.invoke(
        create_app(provider),
        ["task", "show", "--help"],
    )

    assert group_help.exit_code == 0
    for command in (
        "add",
        "add-dependency",
        "block",
        "cancel",
        "list",
        "remove-dependency",
        "show",
        "unblock",
        "update",
    ):
        assert command in unstyle(group_help.stdout)
    assert show_help.exit_code == 0
    assert "TASK" in unstyle(show_help.stdout)
    assert "--project" in unstyle(show_help.stdout)
    assert "--json" in unstyle(show_help.stdout)
    assert "--non-interactive" in unstyle(show_help.stdout)
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("task show --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "show", "ACME-1", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
    assert provider.call_count == 1
