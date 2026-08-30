"""Token provisioning, lifecycle metadata, and identity audit CLI commands."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases.
    CursorOption,
    IdempotencyKeyOption,
    JsonOption,
    LimitOption,
    NonInteractiveOption,
    ProfileOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    audit_event_page_data,
    token_page_data,
    token_result_data,
)
from workaholic.domain import SubjectId, TokenId
from workaholic.session import (
    AuditEventPage,
    AuditEventsRequest,
    TokenCreateRequest,
    TokenListRequest,
    TokenPage,
    TokenResult,
    TokenRevokeRequest,
)

SubjectArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Exact immutable Subject handle or public Subject ID.",
        metavar="SUBJECT",
    ),
]
OptionalSubjectArgument = Annotated[
    str | None,
    typer.Argument(
        help="Optional exact Subject handle or ID; omission selects self.",
        metavar="SUBJECT",
    ),
]
TokenArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Public Token ID; raw bearer Tokens are never accepted.",
        metavar="TOKEN",
    ),
]
TokenOutputFileOption = Annotated[
    str,
    typer.Option(
        ...,
        "--token-file",
        help="Write the one-time Token to a new protected absolute path.",
        metavar="ABSOLUTE_PATH",
        prompt=False,
    ),
]
ExpiresInOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--expires-in",
        help="Set a positive single-unit Token lifetime such as 24h or 30d.",
        metavar="DURATION",
        prompt=False,
    ),
]
AuditAfterOption = Annotated[
    int,
    typer.Option(
        ...,
        "--after",
        help="Read AuditEvents strictly after this nonnegative cursor.",
        min=0,
        metavar="INTEGER",
        prompt=False,
        show_default=True,
    ),
]

_TOKEN_INPUT_INVALID_MESSAGE = "Token administration input is invalid."  # noqa: S105
_AUDIT_INPUT_INVALID_MESSAGE = "AuditEvent input is invalid."
_DURATION_PATTERN = re.compile(r"^[1-9][0-9]*(s|m|h|d)$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
_MAX_DURATION_DIGITS = 10
_MAX_TOKEN_LIFETIME = timedelta(days=365)


class TokenDurationError(ValueError):
    """Signal malformed or broadly out-of-range Token duration input."""


def register_token_admin_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Token lifecycle and administrative audit commands.

    Args:
        application: Shared ``auth`` Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("create-token")
    def create_token(  # noqa: PLR0913
        subject: SubjectArgument,
        token_file: TokenOutputFileOption,
        expires_in: ExpiresInOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Provision one Token to an exclusive protected output file."""
        del non_interactive
        try:
            output = _absolute_output_path(token_file)
            request = TokenCreateRequest(
                subject=_subject_selector(subject),
                token_file=output,
                expires_in=(
                    None if expires_in is None else parse_token_duration(expires_in)
                ),
                profile=profile,
                idempotency_key=idempotency_key,
            )
            result = acquire_session(session_provider).create_token(request)
            _write_token_result(result, json_mode=json_mode)
        except TokenDurationError, ValidationError, ValueError:
            write_invalid_input(_TOKEN_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact path/Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("list-tokens")
    def list_tokens(  # noqa: PLR0913 - explicit public CLI contract.
        subject: OptionalSubjectArgument = None,
        cursor: CursorOption = None,
        limit: LimitOption = 100,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List one stable page of self- or administrator-visible Tokens."""
        del non_interactive
        try:
            request = TokenListRequest(
                subject=None if subject is None else _subject_selector(subject),
                cursor=cursor,
                limit=limit,
                profile=profile,
            )
            result = acquire_session(session_provider).list_tokens(request)
            _write_token_page(result, json_mode=json_mode)
        except ValidationError, ValueError:
            write_invalid_input(_TOKEN_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("revoke-token")
    def revoke_token(
        token: TokenArgument,
        idempotency_key: IdempotencyKeyOption = None,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Monotonically revoke one public Token identity."""
        del non_interactive
        try:
            request = TokenRevokeRequest(
                token_id=TokenId(token),
                profile=profile,
                idempotency_key=idempotency_key,
            )
            result = acquire_session(session_provider).revoke_token(request)
            _write_token_result(result, json_mode=json_mode)
        except ValidationError, ValueError:
            write_invalid_input(_TOKEN_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)

    @application.command("events")
    def events(
        after: AuditAfterOption = 0,
        limit: LimitOption = 100,
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Read one ascending Instance-administrator AuditEvent page."""
        del non_interactive
        try:
            request = AuditEventsRequest(
                after=after,
                limit=limit,
                profile=profile,
            )
            result = acquire_session(session_provider).read_audit_events(request)
            _write_audit_page(result, json_mode=json_mode)
        except ValidationError:
            write_invalid_input(_AUDIT_INPUT_INVALID_MESSAGE, json_mode=json_mode)
        except Exception as error:  # noqa: BLE001 - redact Session failures.
            write_failure(error, json_mode=json_mode)


def parse_token_duration(value: object) -> timedelta:
    """Parse one exact positive single-unit Token lifetime.

    Subject-kind-specific minimum and maximum validation remains authoritative
    in the Session after it resolves the target Subject. This parser enforces
    the public grammar and broad maximum before any Session acquisition.

    Args:
        value: Candidate duration text.

    Returns:
        Positive whole-second duration no greater than 365 days.

    Raises:
        TokenDurationError: If the text or broad bound is invalid.

    """
    if not isinstance(value, str):
        raise TokenDurationError
    matched = _DURATION_PATTERN.fullmatch(value)
    if matched is None or len(value) - 1 > _MAX_DURATION_DIGITS:
        raise TokenDurationError
    seconds = int(value[:-1]) * _DURATION_SECONDS[matched.group(1)]
    duration = timedelta(seconds=seconds)
    if duration > _MAX_TOKEN_LIFETIME:
        raise TokenDurationError
    return duration


def _absolute_output_path(value: str) -> Path:
    """Return one absolute unresolved Token output path.

    Args:
        value: Caller-selected output path text.

    Returns:
        Absolute path preserved without resolving symlinks.

    Raises:
        ValueError: If the path is relative or malformed.

    """
    if not value or "\x00" in value:
        raise ValueError
    output = Path(value)
    if not output.is_absolute():
        raise ValueError
    return output


def _subject_selector(value: str) -> SubjectId | str:
    """Parse an opaque Subject ID while preserving handles as exact text."""
    return SubjectId(value) if value.startswith("sub_") else value


def _write_token_result(value: object, *, json_mode: bool) -> None:
    """Validate and render one non-secret Token lifecycle result."""
    if not isinstance(value, TokenResult):
        raise TypeError
    write_success(token_result_data(value), json_mode=json_mode)


def _write_token_page(value: object, *, json_mode: bool) -> None:
    """Validate and render one non-secret Token page."""
    if not isinstance(value, TokenPage):
        raise TypeError
    write_success(token_page_data(value), json_mode=json_mode)


def _write_audit_page(value: object, *, json_mode: bool) -> None:
    """Validate and render one attributable administrative event page."""
    if not isinstance(value, AuditEventPage):
        raise TypeError
    write_success(audit_event_page_data(value), json_mode=json_mode)
