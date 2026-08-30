"""Unit tests for safe CLI failure rendering and exit mapping."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
import typer
from tests.golden import require_error
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
)
from workaholic.cli.errors import normalize_failure, write_failure

_RUNNER = CliRunner()
_EXPECTED_EXITS = {
    ApplicationErrorCode.INVALID_INPUT: ExitCategory.INPUT_USAGE,
    ApplicationErrorCode.RESULT_INVALID: ExitCategory.INPUT_USAGE,
    ApplicationErrorCode.CONTEXT_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.CONTEXT_INVALID: ExitCategory.MISSING,
    ApplicationErrorCode.PROFILE_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.PROFILE_INVALID: ExitCategory.MISSING,
    ApplicationErrorCode.PROFILE_UNSUPPORTED: ExitCategory.MISSING,
    ApplicationErrorCode.NOT_INITIALIZED: ExitCategory.MISSING,
    ApplicationErrorCode.PROJECT_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.TASK_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.NO_TASK_AVAILABLE: ExitCategory.MISSING,
    ApplicationErrorCode.SUBJECT_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.TOKEN_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.GRANT_NOT_FOUND: ExitCategory.MISSING,
    ApplicationErrorCode.PROJECT_KEY_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.VERSION_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.INVALID_TRANSITION: ExitCategory.CONFLICT,
    ApplicationErrorCode.DEPENDENCY_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.DEPENDENCY_CYCLE: ExitCategory.CONFLICT,
    ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY: ExitCategory.CONFLICT,
    ApplicationErrorCode.TASK_LOCKED: ExitCategory.CONFLICT,
    ApplicationErrorCode.LEASE_LOST: ExitCategory.CONFLICT,
    ApplicationErrorCode.SUBJECT_HANDLE_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.IDENTITY_VERSION_CONFLICT: ExitCategory.CONFLICT,
    ApplicationErrorCode.LAST_INSTANCE_ADMIN: ExitCategory.CONFLICT,
    ApplicationErrorCode.LAST_PROJECT_OWNER: ExitCategory.CONFLICT,
    ApplicationErrorCode.AUTHENTICATION_REQUIRED: ExitCategory.AUTHORIZATION,
    ApplicationErrorCode.AUTHENTICATION_FAILED: ExitCategory.AUTHORIZATION,
    ApplicationErrorCode.PERMISSION_DENIED: ExitCategory.AUTHORIZATION,
    ApplicationErrorCode.CREDENTIAL_UNAVAILABLE: ExitCategory.OPERATIONAL,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED: ExitCategory.OPERATIONAL,
    ApplicationErrorCode.STORAGE_BUSY: ExitCategory.OPERATIONAL,
    ApplicationErrorCode.STORAGE_UNAVAILABLE: ExitCategory.OPERATIONAL,
    ApplicationErrorCode.INTERNAL_ERROR: ExitCategory.OPERATIONAL,
}
_RETRYABLE_CODES = {
    ApplicationErrorCode.NO_TASK_AVAILABLE,
    ApplicationErrorCode.TASK_LOCKED,
    ApplicationErrorCode.STORAGE_BUSY,
}

assert set(_EXPECTED_EXITS) == set(ApplicationErrorCode)


def _invoke_failure(error: Exception, *, json_mode: bool) -> Result:
    """Invoke the failure writer inside a real Typer command boundary.

    Args:
        error: Failure to normalize and render.
        json_mode: Whether to select the public JSON envelope.

    Returns:
        Captured Typer command result.

    """
    application = typer.Typer(add_completion=False)

    @application.command()
    def fail() -> None:
        """Render the configured failure and terminate."""
        write_failure(error, json_mode=json_mode)

    return _RUNNER.invoke(application)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for the shared golden assertions.

    Args:
        result: Captured Typer invocation result.

    Returns:
        Equivalent completed-process value.

    """
    return CompletedProcess(
        args=("workaholic", "--json", "--non-interactive"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@pytest.mark.parametrize(
    ("code", "exit_category"),
    tuple(_EXPECTED_EXITS.items()),
)
def test_every_application_error_maps_to_exact_json_exit(
    code: ApplicationErrorCode,
    exit_category: ExitCategory,
) -> None:
    """Known errors retain code, safe message, retryability, and exit category."""
    error = ApplicationError(code, f"Safe message for {code.value}.")

    result = _invoke_failure(error, json_mode=True)

    assert result.exit_code == int(exit_category)
    assert result.stderr == ""
    detail = require_error(_completed(result), expected_code=code.value)
    assert detail == {
        "code": code.value,
        "message": f"Safe message for {code.value}.",
        "retryable": code in _RETRYABLE_CODES,
    }


def test_human_failure_writes_only_safe_stderr() -> None:
    """Human mode keeps diagnostics off stdout and uses the same stable exit."""
    error = ApplicationError(
        ApplicationErrorCode.PERMISSION_DENIED,
        "The selected Subject is not authorized.",
    )

    result = _invoke_failure(error, json_mode=False)

    assert result.exit_code == 5
    assert result.stdout == ""
    assert result.stderr == "The selected Subject is not authorized.\n"


def test_unknown_error_is_fully_redacted_to_internal_failure() -> None:
    """Unknown type, message, and cause details never reach either stream."""
    private_detail = "sensitive database connection diagnostic"
    error = RuntimeError(private_detail)

    result = _invoke_failure(error, json_mode=True)

    assert result.exit_code == 10
    assert result.stderr == ""
    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail == {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected internal error occurred.",
        "retryable": False,
    }
    assert private_detail not in result.stdout
    assert "RuntimeError" not in result.stdout


def test_known_error_never_renders_its_private_cause() -> None:
    """A safe application failure remains redacted when chained from a secret."""
    private_detail = "sensitive credential diagnostic"
    cause = RuntimeError(private_detail)
    error = ApplicationError(
        ApplicationErrorCode.STORAGE_UNAVAILABLE,
        "Local storage is unavailable.",
    )
    error.__cause__ = cause

    result = _invoke_failure(error, json_mode=True)

    assert result.exit_code == 10
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr
    assert "Local storage is unavailable." in result.stdout


@pytest.mark.parametrize("code", tuple(ApplicationErrorCode))
def test_retryability_matches_the_complete_public_error_contract(
    code: ApplicationErrorCode,
) -> None:
    """Only bounded contention or temporary availability failures are retryable."""
    detail = require_error(
        _completed(
            _invoke_failure(
                ApplicationError(code, f"Safe {code.value} message."),
                json_mode=True,
            )
        ),
        expected_code=code.value,
    )

    assert detail["retryable"] is (code in _RETRYABLE_CODES)


def test_normalize_failure_preserves_known_instance() -> None:
    """Known application failures are not copied or remapped."""
    error = ApplicationError(
        ApplicationErrorCode.TASK_NOT_FOUND,
        "The Task was not found.",
    )

    assert normalize_failure(error) is error


def test_failure_writer_runtime_validates_json_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Integer lookalikes cannot choose an output stream or exit contract."""
    with pytest.raises(TypeError, match="json_mode"):
        write_failure(
            RuntimeError("private"),
            json_mode=1,  # type: ignore[arg-type]
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_failure_writer_always_terminates_after_one_envelope() -> None:
    """A caller cannot continue and accidentally append stdout after failure."""
    application = typer.Typer(add_completion=False)
    continued: list[bool] = []

    def render_terminal_failure() -> None:
        """Wrap the terminal boundary for runtime continuation verification."""
        write_failure(
            ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Input is invalid.",
            ),
            json_mode=True,
        )

    @application.command()
    def fail() -> None:
        """Attempt to continue after the terminal failure boundary."""
        render_terminal_failure()
        continued.append(True)

    result = _RUNNER.invoke(application)

    assert result.exit_code == 2
    assert continued == []
    require_error(_completed(result), expected_code="INVALID_INPUT")
