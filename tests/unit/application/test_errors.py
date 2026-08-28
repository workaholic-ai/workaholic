"""Unit tests for stable cumulative application errors."""

from __future__ import annotations

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AuthenticationFailedError,
    AuthenticationRequiredError,
    CredentialUnavailableError,
    DependencyConflictError,
    DependencyCycleError,
    ExitCategory,
    GrantNotFoundError,
    IdentityVersionConflictError,
    InvalidTransitionError,
    LastInstanceAdminError,
    LastProjectOwnerError,
    LeaseLostError,
    NoTaskAvailableError,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProfileUnsupportedError,
    ProjectNotFoundError,
    ResultInvalidError,
    SubjectHandleConflictError,
    SubjectNotFoundError,
    TaskLockedError,
    TokenNotFoundError,
    UnsatisfiableDependencyError,
    VersionConflictError,
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
    ApplicationErrorCode.NO_TASK_AVAILABLE: (ExitCategory.MISSING, True),
    ApplicationErrorCode.PROJECT_KEY_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT: (
        ExitCategory.CONFLICT,
        False,
    ),
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.VERSION_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.INVALID_TRANSITION: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.DEPENDENCY_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.DEPENDENCY_CYCLE: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY: (
        ExitCategory.CONFLICT,
        False,
    ),
    ApplicationErrorCode.RESULT_INVALID: (ExitCategory.INPUT_USAGE, False),
    ApplicationErrorCode.TASK_LOCKED: (ExitCategory.CONFLICT, True),
    ApplicationErrorCode.LEASE_LOST: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.AUTHENTICATION_REQUIRED: (
        ExitCategory.AUTHORIZATION,
        False,
    ),
    ApplicationErrorCode.AUTHENTICATION_FAILED: (
        ExitCategory.AUTHORIZATION,
        False,
    ),
    ApplicationErrorCode.SUBJECT_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.SUBJECT_HANDLE_CONFLICT: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.TOKEN_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.GRANT_NOT_FOUND: (ExitCategory.MISSING, False),
    ApplicationErrorCode.IDENTITY_VERSION_CONFLICT: (
        ExitCategory.CONFLICT,
        False,
    ),
    ApplicationErrorCode.LAST_INSTANCE_ADMIN: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.LAST_PROJECT_OWNER: (ExitCategory.CONFLICT, False),
    ApplicationErrorCode.CREDENTIAL_UNAVAILABLE: (ExitCategory.OPERATIONAL, False),
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


def test_phase_three_errors_have_exact_safe_public_messages() -> None:
    """Lifecycle failures expose only the six fixed redacted Phase 3 messages."""
    expected = (
        (
            VersionConflictError(),
            ApplicationErrorCode.VERSION_CONFLICT,
            "The Task changed after the expected version.",
        ),
        (
            InvalidTransitionError(),
            ApplicationErrorCode.INVALID_TRANSITION,
            "The Task cannot perform the requested lifecycle transition.",
        ),
        (
            DependencyConflictError(),
            ApplicationErrorCode.DEPENDENCY_CONFLICT,
            "The dependency change conflicts with the current Task graph.",
        ),
        (
            DependencyCycleError(),
            ApplicationErrorCode.DEPENDENCY_CYCLE,
            "The dependency change would create a cycle.",
        ),
        (
            UnsatisfiableDependencyError(),
            ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY,
            "The Task has a cancelled prerequisite and cannot be completed.",
        ),
        (
            ResultInvalidError(),
            ApplicationErrorCode.RESULT_INVALID,
            "The submitted Result is invalid.",
        ),
    )

    for error, code, message in expected:
        assert error.code is code
        assert error.safe_message == message
        assert not error.retryable


def test_phase_four_errors_have_exact_safe_public_messages() -> None:
    """Claim failures expose fixed redacted messages and retry semantics."""
    expected = (
        (
            NoTaskAvailableError(),
            ApplicationErrorCode.NO_TASK_AVAILABLE,
            "No ready Task is available to claim.",
            True,
        ),
        (
            TaskLockedError(),
            ApplicationErrorCode.TASK_LOCKED,
            "The Task has a current Claim owned by another execution.",
            True,
        ),
        (
            LeaseLostError(),
            ApplicationErrorCode.LEASE_LOST,
            "The Claim is no longer current.",
            False,
        ),
    )

    for error, code, message, retryable in expected:
        assert error.code is code
        assert error.safe_message == message
        assert error.retryable is retryable


def test_phase_five_errors_have_exact_safe_public_messages() -> None:
    """Identity failures expose only their fixed non-secret Phase 5 messages."""
    expected = (
        (
            AuthenticationRequiredError(),
            ApplicationErrorCode.AUTHENTICATION_REQUIRED,
            "Authentication is required.",
        ),
        (
            AuthenticationFailedError(),
            ApplicationErrorCode.AUTHENTICATION_FAILED,
            "The supplied credential is not valid.",
        ),
        (
            SubjectNotFoundError(),
            ApplicationErrorCode.SUBJECT_NOT_FOUND,
            "The Subject was not found.",
        ),
        (
            SubjectHandleConflictError(),
            ApplicationErrorCode.SUBJECT_HANDLE_CONFLICT,
            "The Subject handle is already in use.",
        ),
        (
            TokenNotFoundError(),
            ApplicationErrorCode.TOKEN_NOT_FOUND,
            "The Token was not found.",
        ),
        (
            GrantNotFoundError(),
            ApplicationErrorCode.GRANT_NOT_FOUND,
            "The ProjectGrant was not found.",
        ),
        (
            IdentityVersionConflictError(),
            ApplicationErrorCode.IDENTITY_VERSION_CONFLICT,
            "The identity or grant changed after the expected version.",
        ),
        (
            LastInstanceAdminError(),
            ApplicationErrorCode.LAST_INSTANCE_ADMIN,
            "The Instance must retain an enabled administrator.",
        ),
        (
            LastProjectOwnerError(),
            ApplicationErrorCode.LAST_PROJECT_OWNER,
            "The Project must retain an enabled Owner.",
        ),
        (
            CredentialUnavailableError(),
            ApplicationErrorCode.CREDENTIAL_UNAVAILABLE,
            "The credential store is unavailable.",
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
