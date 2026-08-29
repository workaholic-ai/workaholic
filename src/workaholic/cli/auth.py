"""Credential and identity commands for authenticated local operation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, cast

import typer
from pydantic import ValidationError

from workaholic.auth import RawToken, parse_token, read_token_file
from workaholic.auth.errors import TokenFormatError
from workaholic.cli.errors import (
    write_failure,
    write_invalid_input,
    write_recovery_confirmation_required,
)
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases.
    InstanceOption,
    JsonOption,
    NonInteractiveOption,
    ProfileOption,
    RecoverySubjectOption,
    TokenInputFileOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    credential_logout_data,
    current_identity_data,
    current_identity_summary,
)
from workaholic.domain import InstanceId
from workaholic.session import (
    CurrentIdentityResult,
    LoginRequest,
    LogoutRequest,
    RecoverLocalRequest,
    WhoAmIRequest,
)

_TOKEN_INPUT_MAX_BYTES = 512
_LOGIN_INPUT_INVALID_MESSAGE = "Login Token input is invalid."
_RECOVERY_INPUT_INVALID_MESSAGE = "Local recovery input is invalid."


class TokenInputError(ValueError):
    """Signal malformed explicit Token input without retaining its value."""


def register_auth_commands(  # noqa: PLR0915 - explicit CLI command registration.
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register credential lifecycle commands against one Session provider.

    Args:
        application: ``auth`` Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("whoami")
    def whoami(
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option.
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Show the freshly authenticated Subject and active Token metadata."""
        del non_interactive
        try:
            request = WhoAmIRequest(profile=profile)
        except ValidationError:
            write_invalid_input(
                "Current identity input is invalid.",
                json_mode=json_mode,
            )
        try:
            result = acquire_session(session_provider).whoami(request)
            _write_identity(result, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure.
            write_failure(error, json_mode=json_mode)

    @application.command("login")
    def login(
        token_file: TokenInputFileOption,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option.
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Validate and store one explicit Human Token without echoing it."""
        del non_interactive
        raw_token: RawToken
        try:
            raw_token = _load_login_token(token_file)
            request = LoginRequest(raw_token=raw_token, profile=profile)
        except TokenInputError, TokenFormatError, ValidationError:
            write_invalid_input(_LOGIN_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every input failure.
            write_failure(error, json_mode=json_mode)
        try:
            result = acquire_session(session_provider).login(request)
            _write_identity(result, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure.
            write_failure(error, json_mode=json_mode)

    @application.command("logout")
    def logout(
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option.
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Remove only the selected profile's stored Human credential."""
        del non_interactive
        try:
            request = LogoutRequest(profile=profile)
        except ValidationError:
            write_invalid_input("Logout input is invalid.", json_mode=json_mode)
        try:
            result = acquire_session(session_provider).logout(request)
            write_success(
                credential_logout_data(result)
                if json_mode
                else f"Profile {result.profile} is logged out.",
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure.
            write_failure(error, json_mode=json_mode)

    @application.command("recover-local")
    def recover_local(
        instance: InstanceOption,
        subject: RecoverySubjectOption,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option.
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Confirm and replace the bootstrap Human's local credential."""
        try:
            request = RecoverLocalRequest(
                instance_id=InstanceId(instance),
                subject=subject,
                profile=profile,
            )
        except ValidationError, ValueError:
            write_invalid_input(_RECOVERY_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        if not (json_mode or non_interactive):
            if not _is_interactive_terminal():
                write_recovery_confirmation_required(json_mode=json_mode)
            if not typer.confirm(
                "Revoke all bootstrap Human Tokens and continue?",
                default=False,
            ):
                typer.echo("No changes made.")
                return
        try:
            result = acquire_session(session_provider).recover_local(request)
            _write_identity(result, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure.
            write_failure(error, json_mode=json_mode)


def _write_identity(result: CurrentIdentityResult, *, json_mode: bool) -> None:
    """Render one validated current-identity result.

    Args:
        result: Authenticated Subject and active Token metadata.
        json_mode: Whether to emit the JSON envelope.

    """
    if not isinstance(result, CurrentIdentityResult):
        raise TypeError
    write_success(
        current_identity_data(result)
        if json_mode
        else current_identity_summary(result),
        json_mode=json_mode,
    )


def _load_login_token(source: str) -> RawToken:
    """Read and parse one explicit bounded Token source.

    Args:
        source: Explicit file path or exact ``-`` stdin marker.

    Returns:
        Canonical opaque raw Token.

    Raises:
        TokenInputError: If stdin content or source syntax is malformed.
        ApplicationError: If the selected protected file cannot be read safely.

    """
    candidate: object = source
    if not isinstance(candidate, str) or not candidate or "\x00" in candidate:
        raise TokenInputError
    if candidate != "-":
        try:
            path = Path(candidate).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise TokenInputError from error
        return read_token_file(path)
    payload = _read_bounded_stdin()
    if not payload or payload.startswith(b"\xef\xbb\xbf"):
        raise TokenInputError
    try:
        text = payload.decode("utf-8", errors="strict").removesuffix("\n")
        return parse_token(text).raw_token
    except (TokenFormatError, UnicodeDecodeError) as error:
        raise TokenInputError from error


def _read_bounded_stdin() -> bytes:
    """Read at most one bounded Token only after explicit ``-`` selection."""
    binary_stream: object = getattr(sys.stdin, "buffer", None)
    if binary_stream is not None and callable(getattr(binary_stream, "read", None)):
        reader = cast("IO[bytes]", binary_stream)
        try:
            payload = reader.read(_TOKEN_INPUT_MAX_BYTES + 1)
        except (OSError, UnicodeError, ValueError) as error:
            raise TokenInputError from error
        if not isinstance(payload, bytes):
            raise TokenInputError
    else:
        try:
            value = sys.stdin.read(_TOKEN_INPUT_MAX_BYTES + 1)
        except (OSError, UnicodeError, ValueError) as error:
            raise TokenInputError from error
        if not isinstance(value, str):
            raise TokenInputError
        payload = value.encode("utf-8")
    if len(payload) > _TOKEN_INPUT_MAX_BYTES:
        raise TokenInputError
    return payload


def _is_interactive_terminal() -> bool:
    """Return whether recovery input and confirmation output are terminals."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except AttributeError, OSError:
        return False
