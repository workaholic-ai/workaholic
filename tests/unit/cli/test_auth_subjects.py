"""Unit tests for Subject and Instance-administrator CLI commands."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.domain import SubjectId, SubjectKind
from workaholic.session import (
    SubjectAdminRequest,
    SubjectCreateRequest,
    SubjectEnabledRequest,
    SubjectListRequest,
    SubjectUpdateRequest,
)

_RUNNER = CliRunner()


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one invocation for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "auth"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@pytest.mark.parametrize(
    ("command", "kind"),
    [("create-human", SubjectKind.HUMAN), ("create-agent", SubjectKind.AGENT)],
)
def test_create_subject_commands_forward_exact_kind_and_metadata(
    command: str,
    kind: SubjectKind,
) -> None:
    """Human and Agent creation are explicit wrappers over one Session request."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            command,
            "build-agent",
            "--display-name",
            "Build agent",
            "--idempotency-key",
            "create-1",
            "--profile",
            "team",
            "--json",
        ],
    )

    assert result.exit_code == 0
    data = require_object(require_success(_completed(result)), context="Subject")
    assert data["id"] == "sub_local"
    assert data["handle"] == "local-operator"
    assert "raw_token" not in result.stdout
    assert session.subject_create_requests == [
        SubjectCreateRequest(
            kind=kind,
            handle="build-agent",
            display_name="Build agent",
            profile="team",
            idempotency_key="create-1",
        )
    ]


def test_list_subjects_emits_exact_page_and_forwards_pagination() -> None:
    """Subject pages preserve closed objects, cursor, limit, and profile."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "list-subjects",
            "--cursor",
            "v5.subjects",
            "--limit",
            "25",
            "--profile",
            "team",
            "--json",
            "--non-interactive",
        ],
    )

    data = require_object(require_success(_completed(result)), context="Subject page")
    subjects = data["subjects"]
    assert isinstance(subjects, list)
    first = subjects[0]
    assert isinstance(first, dict)
    assert first["handle"] == "local-operator"
    assert data["next_cursor"] is None
    assert session.subject_list_requests == [
        SubjectListRequest(
            cursor="v5.subjects",
            limit=25,
            profile="team",
        )
    ]


@pytest.mark.parametrize(
    ("arguments", "log_name", "expected"),
    [
        (
            ["update-subject", "sub_local", "--display-name", "Operator"],
            "subject_update_requests",
            SubjectUpdateRequest(
                subject=SubjectId("sub_local"),
                expected_version=7,
                display_name="Operator",
                idempotency_key="change-1",
            ),
        ),
        (
            ["enable-subject", "sub_local"],
            "subject_enabled_requests",
            SubjectEnabledRequest(
                subject=SubjectId("sub_local"),
                expected_version=7,
                enabled=True,
                idempotency_key="change-1",
            ),
        ),
        (
            ["disable-subject", "sub_local"],
            "subject_enabled_requests",
            SubjectEnabledRequest(
                subject=SubjectId("sub_local"),
                expected_version=7,
                enabled=False,
                idempotency_key="change-1",
            ),
        ),
        (
            ["grant-admin", "sub_local"],
            "subject_admin_requests",
            SubjectAdminRequest(
                subject=SubjectId("sub_local"),
                expected_version=7,
                is_instance_admin=True,
                idempotency_key="change-1",
            ),
        ),
        (
            ["revoke-admin", "sub_local"],
            "subject_admin_requests",
            SubjectAdminRequest(
                subject=SubjectId("sub_local"),
                expected_version=7,
                is_instance_admin=False,
                idempotency_key="change-1",
            ),
        ),
    ],
)
def test_subject_mutations_forward_exact_optimistic_version(
    arguments: list[str],
    log_name: str,
    expected: object,
) -> None:
    """Every existing-Subject mutation carries one explicit exact version."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            *arguments,
            "--expected-version",
            "7",
            "--idempotency-key",
            "change-1",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert getattr(session, log_name) == [expected]


def test_automation_requires_subject_version_before_session_acquisition() -> None:
    """JSON, noninteractive, and non-TTY mutation omission fail safely."""
    for mode in (["--json"], ["--non-interactive"]):
        session = RecordingSession()
        provider = SessionProviderSpy(session)
        result = _RUNNER.invoke(
            create_app(provider),
            ["auth", "disable-subject", "local-operator", *mode],
        )

        assert result.exit_code == 2
        assert "--expected-version" in result.stdout + result.stderr
        assert provider.call_count == 0
        assert session.subject_enabled_requests == []


def test_interactive_subject_omission_reads_once_confirms_and_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal convenience submits exactly the observed Subject version once."""
    monkeypatch.setattr(
        "workaholic.cli.auth_admin._is_interactive_mode",
        lambda **_kwargs: True,
    )
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "update-subject", "local-operator", "--display-name", "Operator"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert session.subject_list_requests == [
        SubjectListRequest(cursor=None, limit=500, profile=None)
    ]
    assert session.subject_update_requests[0].expected_version == 1
    assert len(session.subject_update_requests) == 1
    assert "version=1" in result.stdout


def test_subject_mutation_preserves_last_admin_failure() -> None:
    """Invariant failures retain their public conflict code and safe message."""
    session = RecordingSession()
    session.failures["set_instance_admin"] = ApplicationError(
        ApplicationErrorCode.LAST_INSTANCE_ADMIN,
        "The Instance must retain an enabled administrator.",
    )
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "revoke-admin",
            "local-operator",
            "--expected-version",
            "1",
            "--json",
        ],
    )

    detail = require_error(_completed(result), expected_code="LAST_INSTANCE_ADMIN")
    assert detail["message"] == "The Instance must retain an enabled administrator."


def test_subject_help_inventory_is_complete_and_side_effect_free() -> None:
    """All public Subject commands and optimistic options remain discoverable."""
    provider = SessionProviderSpy(RecordingSession())
    application = create_app(provider)
    group = _RUNNER.invoke(application, ["auth", "--help"])
    update = _RUNNER.invoke(application, ["auth", "update-subject", "--help"])

    assert group.exit_code == update.exit_code == 0
    rendered = unstyle(group.stdout)
    for command in (
        "create-human",
        "create-agent",
        "list-subjects",
        "update-subject",
        "enable-subject",
        "disable-subject",
        "grant-admin",
        "revoke-admin",
    ):
        assert command in rendered
    assert "--expected-version" in unstyle(update.stdout)
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["create-agent", "build-agent"],
        [
            "update-subject",
            "sub_local",
            "--display-name",
            "Operator",
            "--expected-version",
            "1",
        ],
        ["disable-subject", "sub_local", "--expected-version", "1"],
    ],
)
def test_subject_commands_reject_invalid_session_results(arguments: list[str]) -> None:
    """Every Subject renderer fails closed on a Session result type violation."""
    session = RecordingSession()
    session.subject_result = object()  # type: ignore[assignment]

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", *arguments, "--json", "--non-interactive"],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")


def test_subject_listing_rejects_invalid_session_page() -> None:
    """Subject pagination never serializes an unvalidated Session object."""
    session = RecordingSession()
    session.subject_page_result = object()  # type: ignore[assignment]

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "list-subjects", "--json"],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")
