"""Safe CLI failure normalization, rendering, and exit mapping."""

from __future__ import annotations

from typing import Never

import typer

from workaholic.cli.envelopes import JsonError, JsonErrorDetail
from workaholic.session import (
    ApplicationError,
    ApplicationErrorCode,
)

_UNKNOWN_ERROR_MESSAGE = "An unexpected internal error occurred."
TASK_EXPECTED_VERSION_REQUIRED_MESSAGE = (
    "Task mutation requires --expected-version for automation."
)


def write_expected_task_version_required(*, json_mode: bool) -> Never:
    """Write the shared unsafe Task-version-omission failure and terminate.

    Args:
        json_mode: Whether to emit the public automation envelope.

    Raises:
        typer.Exit: Always, after rendering the stable input failure.

    """
    write_invalid_input(
        TASK_EXPECTED_VERSION_REQUIRED_MESSAGE,
        json_mode=json_mode,
    )


def write_invalid_input(message: str, *, json_mode: bool) -> Never:
    """Write one safe CLI-boundary input failure and terminate.

    Args:
        message: Stable public diagnostic without input echoing.
        json_mode: Whether to emit the public automation envelope.

    Raises:
        TypeError: If ``json_mode`` is not a real boolean.
        DomainValidationError: If ``message`` is not a safe public diagnostic.
        typer.Exit: Always, after rendering the input failure.

    """
    write_failure(
        ApplicationError(ApplicationErrorCode.INVALID_INPUT, message),
        json_mode=json_mode,
    )


def write_failure(error: Exception, *, json_mode: bool) -> Never:
    """Write one safe failure and terminate with its stable exit category.

    Known application errors retain their safe code, message, retryability, and
    exit mapping. Unknown failures are fully redacted to ``INTERNAL_ERROR``;
    their exception type and message are never rendered.

    Args:
        error: Known application failure or unexpected exception.
        json_mode: Whether to emit the public automation envelope.

    Raises:
        TypeError: If ``json_mode`` is not a real boolean.
        typer.Exit: Always, after rendering the safe failure.

    """
    candidate_json_mode: object = json_mode
    if type(candidate_json_mode) is not bool:
        message = "json_mode must be a boolean."
        raise TypeError(message)
    public_error = normalize_failure(error)
    if candidate_json_mode:
        envelope = JsonError(
            error=JsonErrorDetail(
                code=public_error.code.value,
                message=public_error.safe_message,
                retryable=public_error.retryable,
            )
        )
        typer.echo(
            envelope.model_dump_json(
                by_alias=True,
                exclude_none=False,
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(public_error.safe_message, err=True)
    raise typer.Exit(code=int(public_error.exit_category))


def normalize_failure(error: Exception) -> ApplicationError:
    """Map one exception to a safe public application failure.

    Args:
        error: Candidate known or unexpected exception.

    Returns:
        Original typed application error or a redacted internal failure.

    """
    if isinstance(error, ApplicationError):
        return error
    return ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        _UNKNOWN_ERROR_MESSAGE,
    )
