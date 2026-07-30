"""Unit tests for ``workaholic task list``."""

from __future__ import annotations

from dataclasses import replace
from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy, task
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    TaskPage,
)
from workaholic.cli.main import create_app
from workaholic.domain import TaskId
from workaholic.session import TaskListRequest

_RUNNER = CliRunner()
_TASK_LIST_ERRORS = (
    ApplicationErrorCode.INVALID_INPUT,
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "list"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_list_json_preserves_order_pagination_and_request() -> None:
    """The command emits one ascending page and opaque continuation cursor."""
    first = task()
    second = replace(
        first,
        uid=TaskId("tsk_second"),
        number=2,
        key="ACME-2",
        title="Second task",
        objective="Second task",
    )
    session = RecordingSession()
    session.task_page_result = TaskPage(
        tasks=(first, second),
        next_cursor="cursor-page-2",
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "list",
            "--cursor",
            "cursor-page-1",
            "--limit",
            "2",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    data = require_object(
        require_success(_completed(result)),
        context="task-list data",
    )
    tasks = data["tasks"]
    assert isinstance(tasks, list)
    assert [item["key"] for item in tasks if isinstance(item, dict)] == [
        "ACME-1",
        "ACME-2",
    ]
    assert data["next_cursor"] == "cursor-page-2"
    assert session.task_list_requests == [
        TaskListRequest(cursor="cursor-page-1", limit=2)
    ]
    assert provider.call_count == 1
    assert result.stderr == ""


def test_task_list_defaults_and_empty_json_are_explicit() -> None:
    """An empty default page retains both required response fields."""
    session = RecordingSession()
    session.task_page_result = TaskPage(tasks=(), next_cursor=None)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "list", "--json"],
    )

    assert require_success(_completed(result)) == {
        "tasks": [],
        "next_cursor": None,
    }
    assert session.task_list_requests == [TaskListRequest()]


def test_task_list_human_output_is_safe_and_reports_next_cursor() -> None:
    """Human pages escape title controls and preserve opaque paging guidance."""
    unsafe_title_task = replace(task(), title="Line one\nLine two")
    session = RecordingSession()
    session.task_page_result = TaskPage(
        tasks=(unsafe_title_task,),
        next_cursor="cursor-page-2",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "list"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        'ACME-1\topen\tpriority=50\t"Line one\\nLine two"\nNext cursor: cursor-page-2\n'
    )
    assert result.stderr == ""


def test_task_list_empty_human_output_is_deterministic() -> None:
    """An empty Human page is unambiguous."""
    session = RecordingSession()
    session.task_page_result = TaskPage(tasks=(), next_cursor=None)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "list"],
    )

    assert result.exit_code == 0
    assert result.stdout == "No tasks.\n"


@pytest.mark.parametrize("cursor", ["", "x" * 2_049])
def test_task_list_rejects_invalid_cursor_before_session(cursor: str) -> None:
    """Strict cursor bounds fail before context or persistence access."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "list",
            "--cursor",
            cursor,
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task-list input is invalid."
    assert provider.call_count == 0
    assert session.task_list_requests == []


@pytest.mark.parametrize("limit", ["0", "501"])
def test_task_list_parser_rejects_limit_outside_public_bounds(limit: str) -> None:
    """The shared option enforces the documented 1-through-500 range."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["task", "list", "--limit", limit],
    )

    assert result.exit_code == 2
    assert "Invalid value" in unstyle(result.output)


@pytest.mark.parametrize("code", _TASK_LIST_ERRORS)
def test_task_list_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented Task-list failure."""
    session = RecordingSession()
    session.failures["list_tasks"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "list", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_task_list_help_and_non_interactive_never_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Help has paging options and non-interactive listing reads no input."""
    provider = SessionProviderSpy(RecordingSession())
    help_result = _RUNNER.invoke(
        create_app(provider),
        ["task", "list", "--help"],
    )
    output = unstyle(help_result.stdout)

    assert help_result.exit_code == 0
    for expected in ("--cursor", "--limit", "--json", "--non-interactive"):
        assert expected in output
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("task list --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "list", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
    assert provider.call_count == 1
