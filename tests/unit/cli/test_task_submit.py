"""Unit tests for Human and Agent Task Result submission."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, cast

import pytest
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import (
    RecordingSession,
    SessionProviderSpy,
    task_submission_result,
)
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    LeaseLostError,
    TaskResultInput,
    VersionConflictError,
)
from workaholic.cli.main import create_app
from workaholic.domain import (
    ArtifactReference,
    AttemptId,
    CriterionOutcome,
    CriterionStatus,
    ProposedFollowUp,
    ResultReviewStatus,
)
from workaholic.session import (
    AgentSubmitRequest,
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


def test_task_submit_attempt_dispatches_closed_agent_result(tmp_path: Path) -> None:
    """An Attempt selects explicit Agent submission and returns terminal ownership."""
    source = tmp_path / "agent-result.json"
    source.write_text(
        json.dumps({"summary": "Implemented and verified."}),
        encoding="utf-8",
    )
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--expected-version",
            "1",
            "--result-file",
            str(source),
            "--project",
            "ACME",
            "--idempotency-key",
            "agent-submit-1",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="submission")
    assert set(data) == {"task", "result", "claim", "attempt", "events"}
    assert data["claim"] is None
    submitted = require_object(data["result"], context="Result")
    assert submitted["attempt_id"] == "atm_cli"
    attempt = require_object(data["attempt"], context="Attempt")
    assert attempt["id"] == "atm_cli"
    assert attempt["status"] == "submitted"
    assert attempt["ended_at"] is not None
    events = data["events"]
    assert isinstance(events, list)
    assert [require_object(event, context="event")["type"] for event in events] == [
        "result_submitted",
        "task_completed",
    ]
    assert all(
        require_object(event, context="event")["attempt_id"] == "atm_cli"
        for event in events
    )
    assert session.agent_submit_requests == [
        AgentSubmitRequest(
            task="ACME-1",
            attempt=AttemptId("atm_cli"),
            expected_version=1,
            result=TaskResultInput(summary="Implemented and verified."),
            project="ACME",
            idempotency_key="agent-submit-1",
        )
    ]
    assert session.task_submit_requests == []
    assert session.task_details_requests == []


def test_task_submit_agent_stdin_can_enter_review_with_terminal_attempt() -> None:
    """Review-required Agent work still ends its Attempt on successful submission."""
    session = RecordingSession()
    session.agent_submit_result = task_submission_result(
        ResultReviewStatus.PENDING,
        agent=True,
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--expected-version",
            "1",
            "--result-file",
            "-",
            "--json",
        ],
        input='{"summary":"Ready for review."}\n',
    )

    data = require_object(require_success(_completed(result)), context="submission")
    task = require_object(data["task"], context="Task")
    result_data = require_object(data["result"], context="Result")
    review = require_object(result_data["review"], context="review")
    attempt = require_object(data["attempt"], context="Attempt")
    assert task["state"] == "review"
    assert review["status"] == "pending"
    assert attempt["status"] == "submitted"
    assert session.agent_submit_requests[0].result == TaskResultInput(
        summary="Ready for review."
    )


def test_task_submit_agent_replay_forwards_identical_fingerprint(
    tmp_path: Path,
) -> None:
    """Equivalent Agent retries preserve Attempt, version, Result, and replay key."""
    source = tmp_path / "replay-result.json"
    source.write_text('{"summary":"Done."}', encoding="utf-8")
    session = RecordingSession()
    application = create_app(SessionProviderSpy(session))
    arguments = [
        "task",
        "submit",
        "ACME-1",
        "--attempt",
        "atm_cli",
        "--expected-version",
        "1",
        "--result-file",
        str(source),
        "--idempotency-key",
        "submit-replay",
        "--json",
    ]

    first = _RUNNER.invoke(application, arguments)
    second = _RUNNER.invoke(application, arguments)

    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    assert len(session.agent_submit_requests) == 2
    assert session.agent_submit_requests[0] == session.agent_submit_requests[1]


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        (
            ["--attempt", "atm_cli", "--result-file", "-"],
            "Agent submission requires --expected-version.",
        ),
        (
            ["--attempt", "atm_cli", "--expected-version", "1"],
            "Task-submission input is invalid.",
        ),
        (
            [
                "--attempt",
                "atm_cli",
                "--expected-version",
                "1",
                "--result-file",
                "-",
                "--comment",
                "Human-only",
            ],
            "Task-submission input is invalid.",
        ),
        (
            [
                "--attempt",
                "invalid",
                "--expected-version",
                "1",
                "--result-file",
                "-",
            ],
            "Task-submission input is invalid.",
        ),
    ],
)
def test_task_submit_agent_requires_result_version_and_consistent_operands(
    arguments: list[str],
    expected_message: str,
) -> None:
    """Agent submission never falls back to prompting or Human-only operands."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["task", "submit", "ACME-1", *arguments, "--json"],
        input='{"summary":"Done."}\n',
    )

    error = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert error["message"] == expected_message
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"attempt_id": "atm_forged"},
        {"submitted_by": "sub_forged"},
        {"summary": "Done.", "unknown": {}},
        {"artifacts": [{"uri": "file:///tmp/private", "nested": {}}]},
    ],
)
def test_task_submit_agent_rejects_unknown_identity_or_nested_result(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    """Agent Result input uses the same closed, identity-free Result schema."""
    source = tmp_path / "invalid-agent-result.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "task",
            "submit",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--expected-version",
            "1",
            "--result-file",
            str(source),
            "--json",
        ],
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (LeaseLostError(), "LEASE_LOST", False),
        (VersionConflictError(), "VERSION_CONFLICT", False),
    ],
)
def test_task_submit_agent_preserves_lease_and_version_failures(
    failure: Exception,
    code: str,
    retryable: bool,  # noqa: FBT001 - parameterized public contract
) -> None:
    """Agent ownership and optimistic races retain their distinct errors."""
    session = RecordingSession()
    session.failures["submit_agent_result"] = failure

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "task",
            "submit",
            "ACME-1",
            "--attempt",
            "atm_cli",
            "--expected-version",
            "1",
            "--result-file",
            "-",
            "--json",
        ],
        input='{"summary":"Done."}\n',
    )

    error = require_error(_completed(result), expected_code=code)
    assert error["retryable"] is retryable
    assert len(session.agent_submit_requests) == 1


def test_task_submit_help_exposes_both_human_and_agent_paths() -> None:
    """Submission help documents Attempt and structured Result controls."""
    provider = SessionProviderSpy(RecordingSession())
    help_result = _RUNNER.invoke(
        create_app(provider),
        ["task", "submit", "--help"],
    )

    assert help_result.exit_code == 0
    assert "--attempt" in help_result.stdout
    assert "--result-file" in help_result.stdout
    assert "--comment" in help_result.stdout
    assert "--expected-version" in help_result.stdout
    assert provider.call_count == 0
