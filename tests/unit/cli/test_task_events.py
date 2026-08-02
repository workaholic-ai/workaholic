"""Unit tests for TaskEvent snapshot and Human follow commands."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Never

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import (
    RecordingSession,
    SessionProviderSpy,
    task_event_page,
    task_event_result,
)
from typer.testing import CliRunner, Result

from workaholic.cli.main import create_app
from workaholic.domain import TaskEventType
from workaholic.session import TaskEventsRequest

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "events"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_events_json_returns_bounded_attributable_snapshot() -> None:
    """Automation receives one exact page with its resumable Instance cursor."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "events",
            "ACME-1",
            "--after",
            "0",
            "--limit",
            "25",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="event page")
    assert data["next_cursor"] == 1
    events = data["events"]
    assert isinstance(events, list)
    assert len(events) == 1
    event = require_object(events[0], context="TaskEvent")
    assert event == {
        "id": "evt_history_1",
        "cursor": 1,
        "task_uid": "tsk_first",
        "project_id": "prj_acme",
        "actor_subject_id": "sub_local",
        "actor_kind": "human",
        "attempt_id": None,
        "request_id": "req_history_1",
        "type": "task_created",
        "occurred_at": "2026-07-30T12:30:00Z",
        "payload": {"version": 1},
    }
    assert session.task_event_requests == [
        TaskEventsRequest(
            task="ACME-1",
            after=0,
            limit=25,
            project="ACME",
        )
    ]


def test_task_events_empty_human_page_preserves_cursor() -> None:
    """An empty Human snapshot remains resumable without inventing events."""
    session = RecordingSession()
    session.task_event_page_result = task_event_page(next_cursor=7)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "events", "tsk_first", "--after", "7", "--limit", "1"],
    )

    assert result.exit_code == 0
    assert result.stdout == "No events.\nNext cursor: 7\n"
    assert session.task_event_requests == [
        TaskEventsRequest(task="tsk_first", after=7, limit=1)
    ]


def test_task_events_human_snapshot_is_deterministic() -> None:
    """Human output uses stable lines followed by the resumable cursor."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["task", "events", "ACME-1"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "1\ttask_created\t2026-07-30T12:30:00Z"
        '\tactor=sub_local\tpayload={"version":1}\n'
        "Next cursor: 1\n"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("--after", "-1"),
        ("--limit", "0"),
        ("--limit", "501"),
    ],
)
def test_task_events_rejects_invalid_page_bounds_before_session(
    arguments: tuple[str, ...],
) -> None:
    """Cursor and page-size bounds are enforced at the public parser."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "events", "ACME-1", *arguments],
    )

    assert result.exit_code == 2
    assert provider.call_count == 0


def test_task_events_rejects_invalid_selector_before_session() -> None:
    """Strict Session request validation rejects an oversized Task selector."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "events", "x" * 257, "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task-event input is invalid."
    assert provider.call_count == 0


def test_task_events_redacts_session_acquisition_failure() -> None:
    """Context-opening diagnostics are normalized before any event query."""
    private_detail = "private profile path"

    def failing_provider() -> Never:
        """Raise one representative private Session-opening failure."""
        raise RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(failing_provider),
        ["task", "events", "ACME-1", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr


@pytest.mark.parametrize("mode", ["--json", "--non-interactive"])
def test_task_events_follow_rejects_automation_modes(mode: str) -> None:
    """Streaming is deliberately unavailable to snapshot automation clients."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "events", "ACME-1", "--follow", mode],
    )

    if mode == "--json":
        detail = require_error(_completed(result), expected_code="INVALID_INPUT")
        assert detail["message"] == (
            "Task-event follow is available only in interactive Human output."
        )
    else:
        assert result.exit_code == 2
        assert result.stderr == (
            "Task-event follow is available only in interactive Human output.\n"
        )
    assert provider.call_count == 0


def test_task_events_follow_polls_cursor_and_emits_each_event_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human follow retains one Session and resumes after the last seen cursor."""
    session = RecordingSession()
    session.task_event_page_results = [
        task_event_page(task_event_result(cursor=1)),
        task_event_page(next_cursor=1),
        task_event_page(
            task_event_result(
                cursor=2,
                event_type=TaskEventType.TASK_UPDATED,
            )
        ),
    ]
    waits = 0

    def interrupt_after_third_page() -> None:
        """Stop the deterministic follow loop after three completed polls."""
        nonlocal waits
        waits += 1
        if waits == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        "workaholic.cli.task_events._wait_for_events",
        interrupt_after_third_page,
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "events", "ACME-1", "--limit", "2", "--follow"],
    )

    assert result.exit_code == 0
    assert result.stdout.count("\ttask_created\t") == 1
    assert result.stdout.count("\ttask_updated\t") == 1
    assert session.task_event_requests == [
        TaskEventsRequest(task="ACME-1", after=0, limit=2),
        TaskEventsRequest(task="ACME-1", after=1, limit=2),
        TaskEventsRequest(task="ACME-1", after=1, limit=2),
    ]
    assert provider.call_count == 1


def test_task_events_redacts_session_failure() -> None:
    """Event queries never expose private adapter diagnostics."""
    private_detail = "private SQL cursor and database filename"
    session = RecordingSession()
    session.failures["read_task_events"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "events", "ACME-1", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr


def test_task_events_rejects_a_regressing_session_cursor() -> None:
    """A malformed adapter page cannot send follow or snapshot polling backward."""
    session = RecordingSession()
    session.task_event_page_result = task_event_page(next_cursor=6)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "events",
            "ACME-1",
            "--after",
            "7",
            "--json",
            "--non-interactive",
        ],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert len(session.task_event_requests) == 1


def test_task_events_help_is_side_effect_free() -> None:
    """History help documents polling controls without acquiring a Session."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "events", "--help"],
    )
    plain_output = unstyle(result.stdout)

    assert result.exit_code == 0
    for option in (
        "TASK",
        "--after",
        "--limit",
        "--follow",
        "--project",
        "--json",
        "--non-interactive",
    ):
        assert option in plain_output
    assert provider.call_count == 0
