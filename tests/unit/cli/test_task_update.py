"""Unit tests for ``workaholic task update``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    TaskUpdatePatch,
)
from workaholic.cli.main import create_app
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
)
from workaholic.session import TaskDetailsRequest, TaskUpdateRequest

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "update"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_update_explicit_version_forwards_exact_inline_patch() -> None:
    """Explicit automation input skips reads and preserves its strict request."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "update",
            "ACME-1",
            "--title",
            "Revised title",
            "--objective",
            "Revised objective",
            "--priority",
            "0",
            "--available-at",
            "2026-08-02T10:00:00Z",
            "--approval",
            "human",
            "--expected-version",
            "7",
            "--idempotency-key",
            "update-7",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="update data")
    assert require_object(data["task"], context="updated task")["version"] == 2
    events = data["events"]
    assert isinstance(events, list)
    assert len(events) == 1
    assert require_object(events[0], context="update event") == {
        "id": "evt_updated",
        "cursor": 2,
        "task_uid": "tsk_first",
        "project_id": "prj_acme",
        "actor_subject_id": "sub_local",
        "actor_kind": "human",
        "attempt_id": None,
        "request_id": "req_updated",
        "type": "task_updated",
        "occurred_at": "2026-07-30T12:30:00Z",
        "payload": {},
    }
    assert session.task_details_requests == []
    assert session.task_update_requests == [
        TaskUpdateRequest(
            task="ACME-1",
            expected_version=7,
            idempotency_key="update-7",
            project="ACME",
            patch=TaskUpdatePatch(
                title="Revised title",
                objective="Revised objective",
                priority=0,
                available_at=datetime(2026, 8, 2, 10, tzinfo=UTC),
                approval=ApprovalRequirement.HUMAN,
            ),
        )
    ]
    assert provider.call_count == 1
    assert result.stderr == ""


def test_task_update_merges_disjoint_structured_and_inline_fields(
    tmp_path: Path,
) -> None:
    """Structured collections and scalar options compose only when disjoint."""
    source = tmp_path / "update.json"
    source.write_text(
        json.dumps(
            {
                "acceptance": [
                    {"id": "ac_tests", "text": "Tests pass", "required": True}
                ],
                "context": [{"uri": "https://example.test/spec", "version": "v2"}],
            }
        ),
        encoding="utf-8",
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "update",
            "tsk_first",
            "--priority",
            "90",
            "--input-file",
            str(source),
            "--expected-version",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert session.task_update_requests == [
        TaskUpdateRequest(
            task="tsk_first",
            expected_version=1,
            patch=TaskUpdatePatch(
                priority=90,
                acceptance=(
                    AcceptanceCriterion(
                        id="ac_tests",
                        text="Tests pass",
                        required=True,
                    ),
                ),
                context=(
                    ContextReference(
                        uri="https://example.test/spec",
                        version="v2",
                    ),
                ),
            ),
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ("--expected-version", "0", "--title", "New"),
        ("--expected-version", "1"),
        (
            "--expected-version",
            "1",
            "--available-at",
            "2026-08-02T10:00:00Z",
            "--clear-available-at",
        ),
    ],
)
def test_task_update_rejects_invalid_patch_before_session(
    arguments: tuple[str, ...],
) -> None:
    """Version and patch invariants fail before context or persistence access."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "update", "ACME-1", *arguments, "--json"],
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert provider.call_count == 0
    assert session.task_update_requests == []


def test_task_update_rejects_file_option_overlap_before_session(
    tmp_path: Path,
) -> None:
    """A field cannot have both structured-file and scalar ownership."""
    source = tmp_path / "overlap.json"
    source.write_text('{"priority":80}', encoding="utf-8")
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "update",
            "ACME-1",
            "--priority",
            "90",
            "--input-file",
            str(source),
            "--expected-version",
            "1",
            "--json",
        ],
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "mode",
    [
        ("--json",),
        ("--non-interactive",),
    ],
)
def test_task_update_automation_requires_version_before_structured_input(
    mode: tuple[str, ...],
) -> None:
    """Unsafe omission fails before reading a named file or acquiring Session."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "update",
            "ACME-1",
            "--input-file",
            "does-not-exist.json",
            *mode,
        ],
    )

    if "--json" in mode:
        detail = require_error(_completed(result), expected_code="INVALID_INPUT")
        assert detail["message"] == (
            "Task mutation requires --expected-version for automation."
        )
    else:
        assert result.exit_code == 2
        assert result.stderr == (
            "Task mutation requires --expected-version for automation.\n"
        )
    assert provider.call_count == 0


def test_task_update_redirected_human_mode_requires_explicit_version() -> None:
    """CliRunner's non-terminal stdin cannot use Human convenience fetching."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "update", "ACME-1", "--title", "New"],
    )

    assert result.exit_code == 2
    assert provider.call_count == 0
    assert "requires --expected-version" in result.stderr


def test_task_update_interactive_confirmation_reads_once_and_uses_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal Human confirms one displayed snapshot without a retry."""
    monkeypatch.setattr(
        "workaholic.cli.task_mutations._is_interactive_terminal",
        lambda: True,
    )
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "update", "ACME-1", "--title", "Confirmed"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "ACME-1\topen\tversion=1\taction=update its definition" in result.stdout
    assert "Proceed? [y/N]: y" in result.stdout
    assert session.task_details_requests == [TaskDetailsRequest(task="ACME-1")]
    assert session.task_update_requests == [
        TaskUpdateRequest(
            task="ACME-1",
            expected_version=1,
            patch=TaskUpdatePatch(title="Confirmed"),
        )
    ]
    assert provider.call_count == 1


def test_task_update_interactive_decline_is_successful_and_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined convenience mutation performs no write and exits zero."""
    monkeypatch.setattr(
        "workaholic.cli.task_mutations._is_interactive_terminal",
        lambda: True,
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "update", "ACME-1", "--title", "Declined"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "ACME-1\topen\tversion=1\taction=update its definition" in result.stdout
    assert result.stdout.endswith("No changes made.\n")
    assert session.task_details_requests == [TaskDetailsRequest(task="ACME-1")]
    assert session.task_update_requests == []


def test_task_update_version_conflict_is_rendered_once_without_retry() -> None:
    """A stale caller receives the unchanged conflict after one write attempt."""
    session = RecordingSession()
    session.failures["update_task"] = ApplicationError(
        ApplicationErrorCode.VERSION_CONFLICT,
        "Task version does not match expected_version.",
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "update",
            "ACME-1",
            "--title",
            "Stale",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="VERSION_CONFLICT")
    assert detail["message"] == "Task version does not match expected_version."
    assert len(session.task_update_requests) == 1
    assert session.task_details_requests == []
    assert provider.call_count == 1
