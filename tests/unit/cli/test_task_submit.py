"""Unit tests for direct Human Task Result submission."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, cast

import pytest
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    TaskResultInput,
)
from workaholic.cli.main import create_app
from workaholic.domain import (
    ArtifactReference,
    CriterionOutcome,
    CriterionStatus,
    ProposedFollowUp,
)
from workaholic.session import (
    TaskDetailsRequest,
    TaskSubmissionResult,
    TaskSubmitRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "task", "submit"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_task_submit_empty_manual_result_has_no_attempt() -> None:
    """A Human can complete work with neither comment nor structured data."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--expected-version",
            "1",
            "--idempotency-key",
            "submit-1",
            "--project",
            "ACME",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="submission")
    submitted = require_object(data["result"], context="submitted Result")
    assert submitted["attempt_id"] is None
    assert submitted["comment"] is None
    review = require_object(submitted["review"], context="Result review")
    assert review["status"] == "not_required"
    events = data["events"]
    assert isinstance(events, list)
    assert [require_object(event, context="event")["type"] for event in events] == [
        "result_submitted",
        "task_completed",
    ]
    assert all(
        require_object(event, context="event")["attempt_id"] is None for event in events
    )
    assert session.task_submit_requests == [
        TaskSubmitRequest(
            task="ACME-1",
            expected_version=1,
            idempotency_key="submit-1",
            project="ACME",
        )
    ]
    assert session.task_details_requests == []


def test_task_submit_accepts_comment_and_closed_structured_result(
    tmp_path: Path,
) -> None:
    """Caller-owned Result content composes with a separate Human comment."""
    source = tmp_path / "result.json"
    source.write_text(
        json.dumps(
            {
                "summary": "Delivered and verified.",
                "criteria": [
                    {
                        "criterion_id": "ac_tests",
                        "status": "passed",
                        "evidence": "Unit and integration tests pass.",
                    }
                ],
                "artifacts": [
                    {
                        "uri": "https://example.test/build/1",
                        "media_type": "application/json",
                        "sha256": "a" * 64,
                    }
                ],
                "proposed_follow_ups": [{"title": "Document the rollout"}],
            }
        ),
        encoding="utf-8",
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "tsk_first",
            "--comment",
            "Implemented manually",
            "--result-file",
            str(source),
            "--expected-version",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert session.task_submit_requests == [
        TaskSubmitRequest(
            task="tsk_first",
            expected_version=4,
            comment="Implemented manually",
            result=TaskResultInput(
                summary="Delivered and verified.",
                criteria=(
                    CriterionOutcome(
                        criterion_id="ac_tests",
                        status=CriterionStatus.PASSED,
                        evidence="Unit and integration tests pass.",
                    ),
                ),
                artifacts=(
                    ArtifactReference(
                        uri="https://example.test/build/1",
                        media_type="application/json",
                        sha256="a" * 64,
                    ),
                ),
                proposed_follow_ups=(ProposedFollowUp(title="Document the rollout"),),
            ),
        )
    ]


def test_task_submit_reads_result_file_from_explicit_stdin() -> None:
    """A file-only submission may opt into bounded structured stdin input."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--result-file",
            "-",
            "--expected-version",
            "1",
        ],
        input='{"summary":"Completed from stdin"}\n',
    )

    assert result.exit_code == 0
    assert session.task_submit_requests == [
        TaskSubmitRequest(
            task="ACME-1",
            expected_version=1,
            result=TaskResultInput(summary="Completed from stdin"),
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"actor_subject_id": "sub_injected"},
        {"attempt_id": "atm_injected"},
        {"task_uid": "tsk_injected"},
        {"summary": "Okay", "unexpected": True},
        {"criteria": [{"criterion_id": "bad id", "status": "passed"}]},
    ],
)
def test_task_submit_rejects_invalid_or_identity_bearing_result_file(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    """The closed Result file cannot supply authority or malformed content."""
    source = tmp_path / "result.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "submit",
            "ACME-1",
            "--result-file",
            str(source),
            "--expected-version",
            "1",
            "--json",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Task-submission input is invalid."
    assert provider.call_count == 0
    assert session.task_submit_requests == []


def test_task_submit_automation_requires_version_before_opening_result_file() -> None:
    """Unsafe automation fails before file IO or Session acquisition."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "submit",
            "ACME-1",
            "--result-file",
            "missing-result.json",
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == (
        "Task mutation requires --expected-version for automation."
    )
    assert provider.call_count == 0


def test_task_submit_interactive_confirmation_reads_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal Human confirms the displayed version used by submission."""
    monkeypatch.setattr(
        "workaholic.cli.task_mutations._is_interactive_terminal",
        lambda: True,
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "submit", "ACME-1", "--comment", "Done"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "ACME-1\topen\tversion=1\taction=submit Human work" in result.stdout
    assert session.task_details_requests == [TaskDetailsRequest(task="ACME-1")]
    assert session.task_submit_requests == [
        TaskSubmitRequest(
            task="ACME-1",
            expected_version=1,
            comment="Done",
        )
    ]


def test_task_submit_interactive_decline_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined Human submission reads once and performs no mutation."""
    monkeypatch.setattr(
        "workaholic.cli.task_mutations._is_interactive_terminal",
        lambda: True,
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["task", "submit", "ACME-1"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert result.stdout.endswith("No changes made.\n")
    assert session.task_details_requests == [TaskDetailsRequest(task="ACME-1")]
    assert session.task_submit_requests == []


def test_task_submit_result_error_is_preserved_without_retry() -> None:
    """Criterion mismatch reaches the caller once without refresh or retry."""
    session = RecordingSession()
    session.failures["submit_human_result"] = ApplicationError(
        ApplicationErrorCode.RESULT_INVALID,
        "Result criteria do not match the Task acceptance criteria.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="RESULT_INVALID")
    assert detail["message"] == (
        "Result criteria do not match the Task acceptance criteria."
    )
    assert len(session.task_submit_requests) == 1
    assert session.task_details_requests == []


def test_task_submit_redacts_invalid_session_result_contract() -> None:
    """A Session returning the wrong result category fails without leakage."""
    session = RecordingSession()
    session.task_submit_result = cast("TaskSubmissionResult", object())

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--expected-version",
            "1",
            "--json",
            "--non-interactive",
        ],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert len(session.task_submit_requests) == 1


def test_task_submit_has_no_attempt_option() -> None:
    """The Human command does not expose the deferred Agent Attempt concept."""
    provider = SessionProviderSpy(RecordingSession())
    help_result = _RUNNER.invoke(
        create_app(provider),
        ["task", "submit", "--help"],
    )
    invalid_result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "submit",
            "ACME-1",
            "--attempt",
            "atm_forbidden",
            "--expected-version",
            "1",
        ],
    )

    assert help_result.exit_code == 0
    assert "--attempt" not in help_result.stdout
    assert invalid_result.exit_code == 2
    assert provider.call_count == 0
