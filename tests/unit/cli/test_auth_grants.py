"""Unit tests for cumulative ProjectGrant CLI administration."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ProjectGrantPage,
    SubjectPage,
)
from workaholic.cli.auth_admin import (
    _find_grant,
    _find_subject,
    _is_interactive_mode,
    _require_grant_page,
    _require_subject_page,
)
from workaholic.cli.main import create_app
from workaholic.domain import ProjectId, ProjectRole, SubjectId
from workaholic.session import (
    GrantAssignRequest,
    GrantListRequest,
    GrantRevokeRequest,
    SubjectListRequest,
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


def test_automation_grant_without_version_asserts_absent_creation() -> None:
    """Omitted automation version creates only when no current grant exists."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "grant",
            "build-agent",
            "agent",
            "--project",
            "ACME",
            "--idempotency-key",
            "grant-1",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert session.grant_assign_requests == [
        GrantAssignRequest(
            subject="build-agent",
            project="ACME",
            role=ProjectRole.AGENT,
            expected_version=None,
            profile=None,
            idempotency_key="grant-1",
        )
    ]
    data = require_object(require_success(_completed(result)), context="grant")
    assert data == {
        "subject_id": "sub_local",
        "project_id": "prj_acme",
        "role": "owner",
        "version": 1,
        "granted_by": "sub_local",
        "created_at": "2026-07-30T12:30:00Z",
        "updated_at": "2026-07-30T12:30:00Z",
    }


def test_grant_replacement_accepts_public_ids_and_exact_version() -> None:
    """Opaque Subject and Project IDs remain typed at the Session boundary."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "grant",
            "sub_local",
            "operator",
            "--project",
            "prj_acme",
            "--expected-version",
            "3",
            "--json",
        ],
    )

    assert result.exit_code == 0
    request = session.grant_assign_requests[0]
    assert request.subject == SubjectId("sub_local")
    assert request.project == ProjectId("prj_acme")
    assert request.role is ProjectRole.OPERATOR
    assert request.expected_version == 3


def test_list_grants_forwards_project_cursor_limit_and_profile() -> None:
    """Grant listing preserves its target Project and stable pagination inputs."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "list-grants",
            "--project",
            "ACME",
            "--cursor",
            "v5.grants",
            "--limit",
            "20",
            "--profile",
            "team",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert session.grant_list_requests == [
        GrantListRequest(
            project="ACME",
            cursor="v5.grants",
            limit=20,
            profile="team",
        )
    ]
    data = require_object(require_success(_completed(result)), context="grant page")
    assert isinstance(data["grants"], list)
    assert data["next_cursor"] is None


def test_revoke_grant_requires_version_for_automation() -> None:
    """Automated revocation cannot race from an unversioned observation."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    result = _RUNNER.invoke(
        create_app(provider),
        [
            "auth",
            "revoke-grant",
            "local-operator",
            "--project",
            "ACME",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "--expected-version" in result.stdout
    assert provider.call_count == 0
    assert session.grant_revoke_requests == []


def test_revoke_grant_forwards_exact_version_once() -> None:
    """Explicit revoke sends one optimistic mutation with no convenience reads."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "revoke-grant",
            "sub_local",
            "--project",
            "prj_acme",
            "--expected-version",
            "4",
            "--idempotency-key",
            "revoke-1",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert session.grant_list_requests == []
    assert session.subject_list_requests == []
    assert session.grant_revoke_requests == [
        GrantRevokeRequest(
            subject=SubjectId("sub_local"),
            project=ProjectId("prj_acme"),
            expected_version=4,
            profile=None,
            idempotency_key="revoke-1",
        )
    ]


@pytest.mark.parametrize("command", ["grant", "revoke-grant"])
def test_interactive_grant_omission_reads_current_version_and_confirms_once(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal convenience resolves handle and current grant before one write."""
    monkeypatch.setattr(
        "workaholic.cli.auth_admin._is_interactive_mode",
        lambda **_kwargs: True,
    )
    session = RecordingSession()
    arguments = ["auth", command, "local-operator"]
    if command == "grant":
        arguments.append("viewer")
    arguments.extend(("--project", "ACME"))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
        input="y\n",
    )

    assert result.exit_code == 0
    assert session.subject_list_requests == [
        SubjectListRequest(cursor=None, limit=500, profile=None)
    ]
    assert session.grant_list_requests == [
        GrantListRequest(project="ACME", cursor=None, limit=500, profile=None)
    ]
    request = (
        session.grant_assign_requests[0]
        if command == "grant"
        else session.grant_revoke_requests[0]
    )
    assert request.expected_version == 1
    assert "version=1" in result.stdout


def test_grant_rejects_unknown_role_before_session_acquisition() -> None:
    """Role parsing is closed and does not disclose caller input."""
    hostile_role = "private-custom-role"
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    result = _RUNNER.invoke(
        create_app(provider),
        [
            "auth",
            "grant",
            "local-operator",
            hostile_role,
            "--project",
            "ACME",
            "--json",
        ],
    )

    require_error(_completed(result), expected_code="INVALID_INPUT")
    assert hostile_role not in result.stdout + result.stderr
    assert provider.call_count == 0


def test_grant_preserves_last_owner_conflict() -> None:
    """Last-enabled-Owner protection remains one documented conflict."""
    session = RecordingSession()
    session.failures["revoke_grant"] = ApplicationError(
        ApplicationErrorCode.LAST_PROJECT_OWNER,
        "The Project must retain an enabled Owner.",
    )
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "revoke-grant",
            "local-operator",
            "--project",
            "ACME",
            "--expected-version",
            "1",
            "--json",
        ],
    )

    detail = require_error(_completed(result), expected_code="LAST_PROJECT_OWNER")
    assert detail["message"] == "The Project must retain an enabled Owner."


def test_grant_help_is_complete_and_side_effect_free() -> None:
    """Grant commands document roles, Project scope, and optimistic versions."""
    provider = SessionProviderSpy(RecordingSession())
    app = create_app(provider)
    grant = _RUNNER.invoke(app, ["auth", "grant", "--help"])
    listing = _RUNNER.invoke(app, ["auth", "list-grants", "--help"])
    revoke = _RUNNER.invoke(app, ["auth", "revoke-grant", "--help"])

    assert grant.exit_code == listing.exit_code == revoke.exit_code == 0
    assert "viewer" in unstyle(grant.stdout)
    assert "--project" in unstyle(grant.stdout)
    assert "--expected-version" in unstyle(grant.stdout)
    assert "--cursor" in unstyle(listing.stdout)
    assert "--expected-version" in unstyle(revoke.stdout)
    assert provider.call_count == 0


@pytest.mark.parametrize(
    "arguments",
    [
        ["grant", "sub_local", "viewer", "--project", "ACME"],
        ["revoke-grant", "sub_local", "--project", "ACME", "--expected-version", "1"],
    ],
)
def test_grant_mutations_reject_invalid_session_results(arguments: list[str]) -> None:
    """Grant renderers reject output that did not cross the typed Session boundary."""
    session = RecordingSession()
    session.project_grant_result = object()  # type: ignore[assignment]

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", *arguments, "--json", "--non-interactive"],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")


def test_grant_listing_rejects_invalid_session_page() -> None:
    """Grant pagination fails closed on an unvalidated Session page."""
    session = RecordingSession()
    session.project_grant_page_result = object()  # type: ignore[assignment]

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "list-grants", "--project", "ACME", "--json"],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")


@pytest.mark.parametrize(
    "arguments",
    [
        ["grant", "sub_local", "viewer"],
        ["list-grants"],
        ["revoke-grant", "sub_local", "--expected-version", "1"],
    ],
)
def test_grant_commands_reject_missing_project_before_session(
    arguments: list[str],
) -> None:
    """Every grant command requires an explicit Project scope."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["auth", *arguments, "--json", "--non-interactive"],
    )

    assert result.exit_code == 2
    assert "--project" in result.stderr
    assert provider.call_count == 0


@pytest.mark.parametrize("command", ["grant", "revoke-grant"])
def test_interactive_grant_change_can_be_cancelled(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive grant convenience performs no mutation after cancellation."""
    monkeypatch.setattr(
        "workaholic.cli.auth_admin._is_interactive_mode",
        lambda **_kwargs: True,
    )
    session = RecordingSession()
    arguments = ["auth", command, "local-operator"]
    if command == "grant":
        arguments.append("viewer")
    arguments.extend(("--project", "ACME"))

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        arguments,
        input="n\n",
    )

    assert result.exit_code == 0
    assert result.stdout.endswith("No changes made.\n")
    assert session.grant_assign_requests == []
    assert session.grant_revoke_requests == []


def test_interactive_revoke_reports_missing_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive revocation maps an absent current grant to its stable error."""
    monkeypatch.setattr(
        "workaholic.cli.auth_admin._is_interactive_mode",
        lambda **_kwargs: True,
    )
    session = RecordingSession()
    session.project_grant_page_result = ProjectGrantPage(
        grants=(),
        next_cursor=None,
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "revoke-grant", "local-operator", "--project", "ACME", "--json"],
    )

    require_error(_completed(result), expected_code="GRANT_NOT_FOUND")
    assert session.grant_revoke_requests == []


def test_interactive_lookup_helpers_reject_invalid_and_cyclic_pages() -> None:
    """Bounded convenience lookup terminates on malformed or repeated cursors."""
    session = RecordingSession()
    session.subject_page_result = SubjectPage(subjects=(), next_cursor=None)
    with pytest.raises(ApplicationError) as missing_subject:
        _find_subject(session, subject="missing", profile=None)
    assert missing_subject.value.code is ApplicationErrorCode.SUBJECT_NOT_FOUND

    session.subject_page_result = SubjectPage(subjects=(), next_cursor="v5.repeat")
    with pytest.raises(TypeError):
        _find_subject(session, subject="missing", profile=None)

    session.project_grant_page_result = ProjectGrantPage(
        grants=(),
        next_cursor="v5.repeat",
    )
    with pytest.raises(TypeError):
        _find_grant(
            session,
            subject_id=SubjectId("sub_missing"),
            project="ACME",
            profile=None,
        )

    with pytest.raises(TypeError):
        _require_subject_page(object())
    with pytest.raises(TypeError):
        _require_grant_page(object())


def test_interactive_mode_maps_terminal_detection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human convenience remains disabled when terminal inspection fails."""

    class _Input:
        """Input stream whose terminal inspection fails."""

        def isatty(self) -> bool:
            """Raise a simulated terminal inspection failure."""
            message = "private terminal failure"
            raise OSError(message)

    monkeypatch.setattr("workaholic.cli.auth_admin.sys.stdin", _Input())
    assert not _is_interactive_mode(json_mode=False, non_interactive=False)
