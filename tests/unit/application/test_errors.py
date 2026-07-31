"""Unit tests for stable cumulative application errors."""

from __future__ import annotations

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ExitCategory,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProfileUnsupportedError,
    ProjectNotFoundError,
    WorkspaceBindingConflictError,
)
from workaholic.domain import DomainValidationError

_EXPECTED_ERROR_SEMANTICS = {
    ApplicationErrorCode.INVALID_INPUT: (ExitCategory.INPUT_USAGE, False),
    ApplicationErrorCode.CONTEXT_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.CONTEXT_INVALID: (ExitCategory.MISSING, False),
    ApplicationErrorCode.PROFILE_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.PROFILE_INVALID: (ExitCategory.MISSING, False),
    ApplicationErrorCode.PROFILE_UNSUPPORTED: (ExitCategory.MISSING, False),
    ApplicationErrorCode.NOT_INITIALIZED: (ExitCategory.MISSING, False),
    ApplicationErrorCode.PROJECT_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.TASK_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.PROJECT_KEY_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT: (
        ExitCategory.CONFLICT,
        False,
    ),
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.PERMISSION_DENIED: (ExitCategory.AUTHORIZATION, False),
    ApplicationErrorCode.SCHEMA_UNSUPPORTED: (ExitCategory.OPERATIONAL, False),
    ApplicationErrorCode.STORAGE_BUSY: (ExitCategory.OPERATIONAL, True),
    ApplicationErrorCode.STORAGE_UNAVAILABLE: (ExitCategory.OPERATIONAL, False),
    ApplicationErrorCode.INTERNAL_ERROR: (ExitCategory.OPERATIONAL, False),
}


def test_every_documented_error_has_fixed_exit_and_retry_semantics() -> None:
    """Every public code maps to exactly one documented behavior."""
    assert set(ApplicationErrorCode) == set(_EXPECTED_ERROR_SEMANTICS)

    for code, (exit_category, retryable) in _EXPECTED_ERROR_SEMANTICS.items():
        error = ApplicationError(code, f"Safe message for {code.value}.")

        assert error.code is code
        assert error.exit_category is exit_category
        assert error.retryable is retryable
        assert error.safe_message == str(error)
        assert error.args == (error.safe_message,)


def test_phase_two_errors_have_exact_safe_public_messages() -> None:
    """Phase 2 failures cannot disclose configuration or storage details."""
    expected = (
        (
            ProfileNotFoundError(),
            ApplicationErrorCode.PROFILE_NOT_FOUND,
            "The selected profile was not found.",
        ),
        (
            ProfileInvalidError(),
            ApplicationErrorCode.PROFILE_INVALID,
            "The trusted profile configuration is invalid.",
        ),
        (
            ProfileUnsupportedError(),
            ApplicationErrorCode.PROFILE_UNSUPPORTED,
            ("The selected profile mode or configuration version is not supported."),
        ),
        (
            ProjectNotFoundError(),
            ApplicationErrorCode.PROJECT_NOT_FOUND,
            "The selected Project was not found.",
        ),
        (
            WorkspaceBindingConflictError(),
            ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT,
            (
                "The Workspace is already bound to a different Project, "
                "Instance, or profile."
            ),
        ),
    )

    for error, code, message in expected:
        assert error.code is code
        assert error.safe_message == message
        assert not error.retryable


@pytest.mark.parametrize(
    "message",
    [
        "",
        " ",
        " padded",
        "padded ",
        "line\nbreak",
        "tab\tvalue",
        "delete\x7f",
        "x" * 501,
        123,
    ],
)
def test_application_error_rejects_unsafe_public_message(message: object) -> None:
    """Public failures reject blank, control-bearing, oversized, and coerced prose."""
    with pytest.raises(DomainValidationError):
        ApplicationError(
            ApplicationErrorCode.INVALID_INPUT,
            message,  # type: ignore[arg-type]
        )


def test_application_error_rejects_untyped_code() -> None:
    """Application failures never accept a free-form machine code."""
    with pytest.raises(DomainValidationError, match="ApplicationErrorCode"):
        ApplicationError(
            "INVALID_INPUT",  # type: ignore[arg-type]
            "Input is invalid.",
        )


def test_exit_categories_have_exact_documented_values() -> None:
    """CLI exit categories remain stable and nonzero."""
    assert {category.name: category.value for category in ExitCategory} == {
        "INPUT_USAGE": 2,
        "MISSING": 3,
        "CONFLICT": 4,
        "AUTHORIZATION": 5,
        "OPERATIONAL": 10,
    }
