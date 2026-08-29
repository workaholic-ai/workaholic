"""Unit tests for safe Token lifecycle CLI commands."""

from __future__ import annotations

from datetime import timedelta
from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.auth_tokens import TokenDurationError, parse_token_duration
from workaholic.cli.main import create_app
from workaholic.domain import SubjectId, TokenId
from workaholic.session import (
    TokenCreateRequest,
    TokenListRequest,
    TokenRevokeRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    ("text", "expected"),
    [
        ("5m", timedelta(minutes=5)),
        ("24h", timedelta(hours=24)),
        ("30d", timedelta(days=30)),
        ("31536000s", timedelta(days=365)),
    ],
)
def test_token_duration_parser_accepts_exact_single_unit_grammar(
    text: str,
    expected: timedelta,
) -> None:
    """Supported durations normalize to positive whole seconds."""
    assert parse_token_duration(text) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", "0s", "1", "1.5h", "+1h", " 1h", "1H", "366d", "9" * 11 + "s"],
)
def test_token_duration_parser_rejects_malformed_or_broadly_unsafe_values(
    value: object,
) -> None:
    """Duration syntax is bounded before any target-specific Session lookup."""
    with pytest.raises(TokenDurationError):
        parse_token_duration(value)


def test_create_token_forwards_absolute_output_and_never_renders_path(
    tmp_path: Path,
) -> None:
    """Provisioning passes a new absolute sink and emits metadata only."""
    output = tmp_path / "agent.token"
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "create-token",
            "sub_local",
            "--token-file",
            str(output),
            "--expires-in",
            "24h",
            "--idempotency-key",
            "token-1",
            "--profile",
            "team",
            "--json",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    assert session.token_create_requests == [
        TokenCreateRequest(
            subject=SubjectId("sub_local"),
            token_file=output,
            expires_in=timedelta(hours=24),
            profile="team",
            idempotency_key="token-1",
        )
    ]
    data = require_object(require_success(_completed(result)), context="Token")
    assert data["id"] == "tok_cli"
    assert data["status"] == "active"
    assert "token_hash" not in result.stdout
    assert "raw_token" not in result.stdout
    assert str(output) not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("path", "duration"),
    [
        ("relative.token", None),
        ("relative.token", "1h"),
        ("/protected/output", "bad"),
    ],
)
def test_create_token_rejects_invalid_path_or_duration_without_session(
    path: str,
    duration: str | None,
) -> None:
    """Malformed output selection fails before credential provisioning."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    arguments = ["auth", "create-token", "local-operator", "--token-file", path]
    if duration is not None:
        arguments.extend(("--expires-in", duration))
    arguments.append("--json")

    result = _RUNNER.invoke(create_app(provider), arguments)

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Token administration input is invalid."
    assert path not in result.stdout + result.stderr
    assert provider.call_count == 0


def test_create_token_redacts_path_and_private_activation_failure(
    tmp_path: Path,
) -> None:
    """Unexpected provisioning diagnostics cannot disclose the sink or secret."""
    output = tmp_path / "private-agent-token"
    private_detail = "generated credential value"
    session = RecordingSession()
    session.failures["create_token"] = RuntimeError(f"{output}: {private_detail}")
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "create-token",
            "local-operator",
            "--token-file",
            str(output),
            "--json",
        ],
    )

    require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert str(output) not in result.stdout + result.stderr
    assert private_detail not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        (None, None),
        ("local-operator", "local-operator"),
        ("sub_local", SubjectId("sub_local")),
    ],
)
def test_list_tokens_supports_self_handle_and_id_scopes(
    subject: str | None,
    expected: SubjectId | str | None,
) -> None:
    """Token listing preserves explicit target scope and pagination."""
    session = RecordingSession()
    arguments = ["auth", "list-tokens"]
    if subject is not None:
        arguments.append(subject)
    arguments.extend(("--cursor", "v5.tokens", "--limit", "12", "--json"))
    result = _RUNNER.invoke(create_app(SessionProviderSpy(session)), arguments)

    assert result.exit_code == 0
    assert session.token_list_requests == [
        TokenListRequest(
            subject=expected,
            cursor="v5.tokens",
            limit=12,
            profile=None,
        )
    ]
    data = require_object(require_success(_completed(result)), context="Token page")
    assert isinstance(data["tokens"], list)
    assert data["next_cursor"] is None
    assert "token_hash" not in result.stdout


def test_revoke_token_accepts_only_public_id_and_forwards_idempotency() -> None:
    """Revocation never accepts or renders a raw bearer Token."""
    session = RecordingSession()
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        [
            "auth",
            "revoke-token",
            "tok_cli",
            "--idempotency-key",
            "revoke-token-1",
            "--profile",
            "team",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert session.token_revoke_requests == [
        TokenRevokeRequest(
            token_id=TokenId("tok_cli"),
            profile="team",
            idempotency_key="revoke-token-1",
        )
    ]

    raw_candidate = "tok_cli." + "A" * 43
    rejected = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["auth", "revoke-token", raw_candidate, "--json"],
    )
    require_error(_completed(rejected), expected_code="INVALID_INPUT")
    assert raw_candidate not in rejected.stdout + rejected.stderr


def test_token_permission_failure_is_preserved_without_target_disclosure() -> None:
    """Unauthorized issuance uses the shared non-disclosing permission error."""
    session = RecordingSession()
    session.failures["list_tokens"] = ApplicationError(
        ApplicationErrorCode.PERMISSION_DENIED,
        "The selected operation is not permitted.",
    )
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["auth", "list-tokens", "private-target", "--json"],
    )

    detail = require_error(_completed(result), expected_code="PERMISSION_DENIED")
    assert detail["message"] == "The selected operation is not permitted."
    assert "private-target" not in result.stdout + result.stderr


def test_token_help_documents_protected_output_and_is_side_effect_free() -> None:
    """Token command help makes the one-time output boundary discoverable."""
    provider = SessionProviderSpy(RecordingSession())
    app = create_app(provider)
    create = _RUNNER.invoke(app, ["auth", "create-token", "--help"])
    listing = _RUNNER.invoke(app, ["auth", "list-tokens", "--help"])
    revoke = _RUNNER.invoke(app, ["auth", "revoke-token", "--help"])

    assert create.exit_code == listing.exit_code == revoke.exit_code == 0
    assert "--token-file" in unstyle(create.stdout)
    assert "ABSOLUTE_PATH" in unstyle(create.stdout)
    assert "--expires-in" in unstyle(create.stdout)
    assert "--cursor" in unstyle(listing.stdout)
    assert "raw bearer Tokens" in unstyle(revoke.stdout)
    assert "accepted" in unstyle(revoke.stdout)
    assert provider.call_count == 0
