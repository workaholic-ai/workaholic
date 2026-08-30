"""Unit tests for administrative AuditEvent CLI reads."""

from __future__ import annotations

from subprocess import CompletedProcess

from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AuditEventPage,
)
from workaholic.cli.main import create_app
from workaholic.session import AuditEventsRequest

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one invocation for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "auth", "events"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_events_emits_exact_attributable_page_and_forwards_cursor() -> None:
    """Audit reads preserve attribution, payload, pagination, and profile."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "events",
            "--after",
            "12",
            "--limit",
            "50",
            "--profile",
            "team",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert session.audit_event_requests == [
        AuditEventsRequest(after=12, limit=50, profile="team")
    ]
    data = require_object(require_success(_completed(result)), context="audit page")
    assert data == {
        "events": [
            {
                "cursor": 42,
                "id": "aev_cli",
                "instance_id": "ins_local",
                "actor_subject_id": "sub_local",
                "actor_kind": "human",
                "actor_token_id": "tok_cli",
                "request_id": "req_audit",
                "event_type": "project_grant_assigned",
                "occurred_at": "2026-07-30T12:30:00Z",
                "payload": {
                    "project_id": "prj_acme",
                    "subject_id": "sub_local",
                    "role": "owner",
                    "version": 1,
                },
            }
        ],
        "next_cursor": 42,
    }
    assert "token_hash" not in result.stdout
    assert "raw_token" not in result.stdout


def test_events_empty_page_retains_supplied_after_cursor() -> None:
    """Empty polling pages succeed and preserve their exclusive cursor."""
    session = RecordingSession()
    session.audit_event_page_result = AuditEventPage(events=(), next_cursor=7)
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "events", "--after", "7", "--json"],
    )

    data = require_object(require_success(_completed(result)), context="audit page")
    assert data == {"events": [], "next_cursor": 7}


def test_events_preserves_instance_administrator_denial() -> None:
    """Non-administrators receive one non-disclosing authorization failure."""
    session = RecordingSession()
    session.failures["read_audit_events"] = ApplicationError(
        ApplicationErrorCode.PERMISSION_DENIED,
        "The selected operation is not permitted.",
    )
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "events", "--json"],
    )

    detail = require_error(_completed(result), expected_code="PERMISSION_DENIED")
    assert detail["message"] == "The selected operation is not permitted."


def test_events_rejects_invalid_cursor_before_session_acquisition() -> None:
    """Negative audit cursors are CLI usage errors with no persistence read."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    result = _RUNNER.invoke(
        create_app(provider),
        ["auth", "events", "--after", "-1", "--json"],
    )

    assert result.exit_code == 2
    assert provider.call_count == 0
    assert session.audit_event_requests == []


def test_events_help_is_side_effect_free_and_documents_polling_options() -> None:
    """Audit pagination controls remain discoverable without authentication."""
    provider = SessionProviderSpy(RecordingSession())
    result = _RUNNER.invoke(
        create_app(provider),
        ["auth", "events", "--help"],
    )

    assert result.exit_code == 0
    rendered = unstyle(result.stdout)
    assert "--after" in rendered
    assert "--limit" in rendered
    assert "--json" in rendered
    assert "--non-interactive" in rendered
    assert provider.call_count == 0
