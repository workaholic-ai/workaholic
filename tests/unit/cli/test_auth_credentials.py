"""Unit tests for local credential and current-identity CLI commands."""

from __future__ import annotations

import base64
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.auth import RawToken
from workaholic.cli.main import create_app
from workaholic.domain import InstanceId
from workaholic.session import (
    LoginRequest,
    LogoutRequest,
    RecoverLocalRequest,
    WhoAmIRequest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_RUNNER = CliRunner()
_TOKEN_TEXT = "tok_login." + base64.urlsafe_b64encode(bytes(range(32))).decode(
    "ascii"
).rstrip("=")


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "auth"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _identity_data(result: Result) -> Mapping[str, object]:
    """Extract the exact current-identity data object from a successful result."""
    return require_object(
        require_success(_completed(result)),
        context="current identity data",
    )


def test_whoami_json_emits_complete_non_secret_identity() -> None:
    """Whoami forwards profile and returns only public Subject and Token data."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["auth", "whoami", "--profile", "team", "--json", "--non-interactive"],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert _identity_data(result) == {
        "subject": {
            "id": "sub_local",
            "instance_id": "ins_local",
            "kind": "human",
            "handle": "local-operator",
            "display_name": "Local operator",
            "enabled": True,
            "is_instance_admin": True,
            "version": 1,
            "created_by": "sub_local",
            "created_at": "2026-07-30T12:30:00Z",
            "updated_at": "2026-07-30T12:30:00Z",
        },
        "token": {
            "id": "tok_cli",
            "subject_id": "sub_local",
            "status": "active",
            "created_by": "sub_local",
            "created_at": "2026-07-30T12:30:00Z",
            "activated_at": "2026-07-30T12:30:00Z",
            "expires_at": "2026-10-28T12:30:00Z",
            "revoked_at": None,
            "revoked_by": None,
        },
    }
    assert session.whoami_requests == [WhoAmIRequest(profile="team")]
    assert provider.call_count == 1
    assert _TOKEN_TEXT not in result.stdout


def test_whoami_human_output_is_concise_and_non_secret() -> None:
    """Human whoami output identifies metadata without credential material."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["auth", "whoami"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Subject: local-operator (sub_local)\n"
        "Kind: human\n"
        "Token: tok_cli\tstatus=active\n"
    )
    assert result.stderr == ""


def test_login_reads_explicit_protected_file_and_never_modifies_it(
    tmp_path: Path,
) -> None:
    """Login authenticates one bounded file Token before returning metadata."""
    token_file = tmp_path / "human.token"
    token_file.write_text(f"{_TOKEN_TEXT}\n", encoding="utf-8")
    token_file.chmod(0o600)
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "login", "--token-file", str(token_file), "--json"],
    )

    assert result.exit_code == 0
    assert _identity_data(result)["subject"] is not None
    assert session.login_requests == [
        LoginRequest(raw_token=RawToken(_TOKEN_TEXT), profile=None)
    ]
    assert token_file.read_text(encoding="utf-8") == f"{_TOKEN_TEXT}\n"
    assert _TOKEN_TEXT not in result.stdout
    assert _TOKEN_TEXT not in result.stderr


def test_login_reads_stdin_only_when_dash_is_explicit() -> None:
    """Explicit dash selects bounded stdin and omitted source never reads it."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    success = _RUNNER.invoke(
        create_app(provider),
        ["auth", "login", "--token-file", "-", "--profile", "team", "--json"],
        input=f"{_TOKEN_TEXT}\n",
    )
    omitted = _RUNNER.invoke(
        create_app(provider),
        ["auth", "login"],
        input=f"{_TOKEN_TEXT}\n",
    )

    assert success.exit_code == 0
    assert session.login_requests == [
        LoginRequest(raw_token=RawToken(_TOKEN_TEXT), profile="team")
    ]
    assert omitted.exit_code == 2
    assert len(session.login_requests) == 1
    assert _TOKEN_TEXT not in success.stdout + success.stderr
    assert _TOKEN_TEXT not in omitted.stdout + omitted.stderr


@pytest.mark.parametrize(
    "input_text",
    ["", "not-a-token\n", "\ufeff" + _TOKEN_TEXT, "A" * 513],
)
def test_login_rejects_malformed_stdin_without_echo(
    input_text: str,
) -> None:
    """Empty, malformed, BOM-prefixed, and oversized credentials stay private."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "login", "--token-file", "-", "--json"],
        input=input_text,
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Login Token input is invalid."
    assert session.login_requests == []
    if input_text:
        assert input_text.rstrip("\n") not in result.stdout
        assert input_text.rstrip("\n") not in result.stderr


def test_login_replaces_credential_through_same_explicit_contract() -> None:
    """Repeated login delegates replacement atomically to the Session boundary."""
    session = RecordingSession()
    application = create_app(SessionProviderSpy(session))

    first = _RUNNER.invoke(
        application,
        ["auth", "login", "--token-file", "-"],
        input=f"{_TOKEN_TEXT}\n",
    )
    second = _RUNNER.invoke(
        application,
        ["auth", "login", "--token-file", "-"],
        input=f"{_TOKEN_TEXT}\n",
    )

    assert first.exit_code == second.exit_code == 0
    assert len(session.login_requests) == 2


def test_login_preserves_agent_denial_and_redacts_hostile_failure() -> None:
    """Session-enforced Human-only login and unknown diagnostics remain safe."""
    session = RecordingSession()
    session.failures["login"] = ApplicationError(
        ApplicationErrorCode.AUTHENTICATION_FAILED,
        "The Token cannot be used for Human login.",
    )
    denied = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "login", "--token-file", "-", "--json"],
        input=f"{_TOKEN_TEXT}\n",
    )
    assert (
        require_error(
            _completed(denied),
            expected_code="AUTHENTICATION_FAILED",
        )["message"]
        == "The Token cannot be used for Human login."
    )

    private_detail = "credential-private-diagnostic"
    hostile = RecordingSession()
    hostile.failures["login"] = RuntimeError(f"failure contained {private_detail}")
    failed = _RUNNER.invoke(
        create_app(SessionProviderSpy(hostile)),
        ["auth", "login", "--token-file", "-", "--json"],
        input=f"{_TOKEN_TEXT}\n",
    )
    assert require_error(_completed(failed), expected_code="INTERNAL_ERROR")
    assert private_detail not in failed.stdout + failed.stderr
    assert _TOKEN_TEXT not in failed.stdout + failed.stderr


def test_logout_is_idempotent_unauthenticated_local_operation() -> None:
    """Logout forwards profile and returns stable credential absence."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "logout", "--profile", "team", "--json", "--non-interactive"],
    )

    assert result.exit_code == 0
    assert require_object(
        require_success(_completed(result)),
        context="logout data",
    ) == {"profile": "local", "credential_stored": False}
    assert session.logout_requests == [LogoutRequest(profile="team")]


def test_recovery_noninteractive_requires_and_forwards_exact_confirmation() -> None:
    """Both exact operands confirm recovery in automation without prompting."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "recover-local",
            "--instance",
            "ins_local",
            "--subject",
            "local-operator",
            "--profile",
            "team",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert _identity_data(result)["token"] is not None
    assert session.recover_local_requests == [
        RecoverLocalRequest(
            instance_id=InstanceId("ins_local"),
            subject="local-operator",
            profile="team",
        )
    ]


def test_recovery_rejects_wrong_subject_before_session_and_redacts_it() -> None:
    """A mismatched recovery selector is safe and causes no mutation."""
    hostile_subject = "private-operator-selector"
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "recover-local",
            "--instance",
            "ins_local",
            "--subject",
            hostile_subject,
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Local recovery input is invalid."
    assert hostile_subject not in result.stdout + result.stderr
    assert session.recover_local_requests == []


def test_recovery_interactive_confirmation_accepts_or_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive recovery mutates only after an explicit affirmative answer."""
    monkeypatch.setattr("workaholic.cli.auth._is_interactive_terminal", lambda: True)
    accepted = RecordingSession()
    accepted_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(accepted)),
        [
            "auth",
            "recover-local",
            "--instance",
            "ins_local",
            "--subject",
            "local-operator",
        ],
        input="y\n",
    )
    cancelled = RecordingSession()
    cancelled_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(cancelled)),
        [
            "auth",
            "recover-local",
            "--instance",
            "ins_local",
            "--subject",
            "local-operator",
        ],
        input="n\n",
    )

    assert accepted_result.exit_code == 0
    assert len(accepted.recover_local_requests) == 1
    assert cancelled_result.exit_code == 0
    assert cancelled_result.stdout.endswith("No changes made.\n")
    assert cancelled.recover_local_requests == []


def test_recovery_non_tty_requires_explicit_noninteractive_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human-mode recovery never treats redirected input as confirmation."""
    monkeypatch.setattr("workaholic.cli.auth._is_interactive_terminal", lambda: False)
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "recover-local",
            "--instance",
            "ins_local",
            "--subject",
            "local-operator",
        ],
        input="y\n",
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "interactive terminal or --non-interactive" in result.stderr
    assert session.recover_local_requests == []


def test_auth_help_is_side_effect_free_and_documents_safe_inputs() -> None:
    """Auth help exposes explicit credential and confirmation contracts."""
    provider = SessionProviderSpy(RecordingSession())
    application = create_app(provider)

    group = _RUNNER.invoke(application, ["auth", "--help"])
    login = _RUNNER.invoke(application, ["auth", "login", "--help"])
    recovery = _RUNNER.invoke(application, ["auth", "recover-local", "--help"])

    assert group.exit_code == login.exit_code == recovery.exit_code == 0
    assert all(
        command in unstyle(group.stdout)
        for command in ("whoami", "login", "logout", "recover-local")
    )
    assert "--token-file" in unstyle(login.stdout)
    assert "PATH|-" in unstyle(login.stdout)
    assert "--instance" in unstyle(recovery.stdout)
    assert "--subject" in unstyle(recovery.stdout)
    assert provider.call_count == 0
