"""Stable application failures shared by Sessions and presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from workaholic.domain import DomainValidationError

_MAX_SAFE_MESSAGE_LENGTH = 500


class ApplicationErrorCode(StrEnum):
    """Machine-readable cumulative failure identifiers."""

    INVALID_INPUT = "INVALID_INPUT"
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    CONTEXT_INVALID = "CONTEXT_INVALID"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    PROFILE_INVALID = "PROFILE_INVALID"
    PROFILE_UNSUPPORTED = "PROFILE_UNSUPPORTED"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    PROJECT_KEY_CONFLICT = "PROJECT_KEY_CONFLICT"
    WORKSPACE_BINDING_CONFLICT = "WORKSPACE_BINDING_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    DEPENDENCY_CONFLICT = "DEPENDENCY_CONFLICT"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    UNSATISFIABLE_DEPENDENCY = "UNSATISFIABLE_DEPENDENCY"
    RESULT_INVALID = "RESULT_INVALID"
    NO_TASK_AVAILABLE = "NO_TASK_AVAILABLE"
    TASK_LOCKED = "TASK_LOCKED"
    LEASE_LOST = "LEASE_LOST"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    SUBJECT_NOT_FOUND = "SUBJECT_NOT_FOUND"
    SUBJECT_HANDLE_CONFLICT = "SUBJECT_HANDLE_CONFLICT"
    TOKEN_NOT_FOUND = "TOKEN_NOT_FOUND"  # noqa: S105 - error code, not a credential
    GRANT_NOT_FOUND = "GRANT_NOT_FOUND"
    IDENTITY_VERSION_CONFLICT = "IDENTITY_VERSION_CONFLICT"
    LAST_INSTANCE_ADMIN = "LAST_INSTANCE_ADMIN"
    LAST_PROJECT_OWNER = "LAST_PROJECT_OWNER"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"
    STORAGE_BUSY = "STORAGE_BUSY"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ExitCategory(IntEnum):
    """Stable nonzero CLI exit categories established in Phase 1."""

    INPUT_USAGE = 2
    MISSING = 3
    CONFLICT = 4
    AUTHORIZATION = 5
    OPERATIONAL = 10


@dataclass(frozen=True, slots=True)
class _ErrorSpec:
    """Fixed exit and retry semantics for one public error code."""

    exit_category: ExitCategory
    retryable: bool


_ERROR_SPECS: Final = MappingProxyType(
    {
        ApplicationErrorCode.INVALID_INPUT: _ErrorSpec(
            ExitCategory.INPUT_USAGE,
            retryable=False,
        ),
        ApplicationErrorCode.CONTEXT_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.CONTEXT_INVALID: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.PROFILE_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.PROFILE_INVALID: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.PROFILE_UNSUPPORTED: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.NOT_INITIALIZED: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.PROJECT_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.TASK_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.PROJECT_KEY_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.IDEMPOTENCY_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.VERSION_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.INVALID_TRANSITION: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.DEPENDENCY_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.DEPENDENCY_CYCLE: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.RESULT_INVALID: _ErrorSpec(
            ExitCategory.INPUT_USAGE,
            retryable=False,
        ),
        ApplicationErrorCode.NO_TASK_AVAILABLE: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=True,
        ),
        ApplicationErrorCode.TASK_LOCKED: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=True,
        ),
        ApplicationErrorCode.LEASE_LOST: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.AUTHENTICATION_REQUIRED: _ErrorSpec(
            ExitCategory.AUTHORIZATION,
            retryable=False,
        ),
        ApplicationErrorCode.AUTHENTICATION_FAILED: _ErrorSpec(
            ExitCategory.AUTHORIZATION,
            retryable=False,
        ),
        ApplicationErrorCode.SUBJECT_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.SUBJECT_HANDLE_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.TOKEN_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.GRANT_NOT_FOUND: _ErrorSpec(
            ExitCategory.MISSING,
            retryable=False,
        ),
        ApplicationErrorCode.IDENTITY_VERSION_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.LAST_INSTANCE_ADMIN: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.LAST_PROJECT_OWNER: _ErrorSpec(
            ExitCategory.CONFLICT,
            retryable=False,
        ),
        ApplicationErrorCode.CREDENTIAL_UNAVAILABLE: _ErrorSpec(
            ExitCategory.OPERATIONAL,
            retryable=False,
        ),
        ApplicationErrorCode.PERMISSION_DENIED: _ErrorSpec(
            ExitCategory.AUTHORIZATION,
            retryable=False,
        ),
        ApplicationErrorCode.SCHEMA_UNSUPPORTED: _ErrorSpec(
            ExitCategory.OPERATIONAL,
            retryable=False,
        ),
        ApplicationErrorCode.STORAGE_BUSY: _ErrorSpec(
            ExitCategory.OPERATIONAL,
            retryable=True,
        ),
        ApplicationErrorCode.STORAGE_UNAVAILABLE: _ErrorSpec(
            ExitCategory.OPERATIONAL,
            retryable=False,
        ),
        ApplicationErrorCode.INTERNAL_ERROR: _ErrorSpec(
            ExitCategory.OPERATIONAL,
            retryable=False,
        ),
    }
)


class ApplicationError(Exception):
    """A safe, typed failure suitable for public error mapping."""

    __slots__ = ("_code", "_exit_category", "_retryable", "_safe_message")

    def __init__(
        self,
        code: ApplicationErrorCode,
        safe_message: str,
    ) -> None:
        """Initialize a failure with fixed code semantics.

        Args:
            code: Stable machine-readable failure identifier.
            safe_message: Redacted Human-readable explanation.

        Raises:
            DomainValidationError: If code or message is not safe and valid.

        """
        candidate_code: object = code
        if not isinstance(candidate_code, ApplicationErrorCode):
            message = "Application error code must be an ApplicationErrorCode."
            raise DomainValidationError(message)
        validated_message = _validate_safe_message(safe_message)
        spec = _ERROR_SPECS[candidate_code]
        self._code = candidate_code
        self._safe_message = validated_message
        self._retryable = spec.retryable
        self._exit_category = spec.exit_category
        super().__init__(validated_message)

    @property
    def code(self) -> ApplicationErrorCode:
        """Return the stable machine-readable code.

        Returns:
            The public application error code.

        """
        return self._code

    @property
    def safe_message(self) -> str:
        """Return the redacted Human-readable message.

        Returns:
            A bounded message containing no control characters.

        """
        return self._safe_message

    @property
    def retryable(self) -> bool:
        """Return whether a bounded retry can be appropriate.

        Returns:
            The fixed retry guidance for this error code.

        """
        return self._retryable

    @property
    def exit_category(self) -> ExitCategory:
        """Return the stable CLI exit category.

        Returns:
            The fixed nonzero exit category for this error code.

        """
        return self._exit_category


class ProjectKeyConflictError(ApplicationError):
    """Report a second Project key in the single-Project local runtime."""

    def __init__(self) -> None:
        """Initialize the stable Project-key conflict failure."""
        super().__init__(
            ApplicationErrorCode.PROJECT_KEY_CONFLICT,
            "The local Instance is already initialized with another Project key.",
        )


class ProfileNotFoundError(ApplicationError):
    """Report that the selected trusted profile does not exist."""

    def __init__(self) -> None:
        """Initialize the fixed missing-profile failure."""
        super().__init__(
            ApplicationErrorCode.PROFILE_NOT_FOUND,
            "The selected profile was not found.",
        )


class ProfileInvalidError(ApplicationError):
    """Report malformed or unsafe trusted profile configuration."""

    def __init__(self) -> None:
        """Initialize the fixed invalid-profile failure."""
        super().__init__(
            ApplicationErrorCode.PROFILE_INVALID,
            "The trusted profile configuration is invalid.",
        )


class ProfileUnsupportedError(ApplicationError):
    """Report an unsupported trusted profile version or mode."""

    def __init__(self) -> None:
        """Initialize the fixed unsupported-profile failure."""
        super().__init__(
            ApplicationErrorCode.PROFILE_UNSUPPORTED,
            "The selected profile mode or configuration version is not supported.",
        )


class IdempotencyConflictError(ApplicationError):
    """Report reuse of one caller key for different semantic input."""

    def __init__(self) -> None:
        """Initialize the stable idempotency conflict failure."""
        super().__init__(
            ApplicationErrorCode.IDEMPOTENCY_CONFLICT,
            "The idempotency key was already used for a different request.",
        )


class PermissionDeniedError(ApplicationError):
    """Report a missing or disabled Phase 1 Owner authorization."""

    def __init__(self) -> None:
        """Initialize the stable authorization failure."""
        super().__init__(
            ApplicationErrorCode.PERMISSION_DENIED,
            "The selected local Subject is not authorized for this Project.",
        )


class InvalidInputError(ApplicationError):
    """Report malformed validated-boundary input such as an opaque cursor."""

    def __init__(self) -> None:
        """Initialize the stable invalid-input failure."""
        super().__init__(
            ApplicationErrorCode.INVALID_INPUT,
            "The supplied input is invalid.",
        )


class NotInitializedError(ApplicationError):
    """Report a valid store without the referenced local identity state."""

    def __init__(self) -> None:
        """Initialize the stable missing-initialization failure."""
        super().__init__(
            ApplicationErrorCode.NOT_INITIALIZED,
            "The referenced local Instance is not initialized.",
        )


class ProjectNotFoundError(ApplicationError):
    """Report that an authorized Project key does not resolve."""

    def __init__(self) -> None:
        """Initialize the fixed missing-Project failure."""
        super().__init__(
            ApplicationErrorCode.PROJECT_NOT_FOUND,
            "The selected Project was not found.",
        )


class TaskNotFoundError(ApplicationError):
    """Report that a scoped Task UID or Human key does not resolve."""

    def __init__(self) -> None:
        """Initialize the stable missing-Task failure."""
        super().__init__(
            ApplicationErrorCode.TASK_NOT_FOUND,
            "The requested Task was not found in the selected Project.",
        )


class WorkspaceBindingConflictError(ApplicationError):
    """Report a valid Workspace binding that requires explicit replacement."""

    def __init__(self) -> None:
        """Initialize the fixed Workspace-binding conflict failure."""
        super().__init__(
            ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT,
            (
                "The Workspace is already bound to a different Project, "
                "Instance, or profile."
            ),
        )


class VersionConflictError(ApplicationError):
    """Report a stale optimistic Task version without disclosing current state."""

    def __init__(self) -> None:
        """Initialize the fixed version-conflict failure."""
        super().__init__(
            ApplicationErrorCode.VERSION_CONFLICT,
            "The Task changed after the expected version.",
        )


class InvalidTransitionError(ApplicationError):
    """Report an illegal semantic Task transition."""

    def __init__(self) -> None:
        """Initialize the fixed invalid-transition failure."""
        super().__init__(
            ApplicationErrorCode.INVALID_TRANSITION,
            "The Task cannot perform the requested lifecycle transition.",
        )


class DependencyConflictError(ApplicationError):
    """Report a dependency-edge conflict without disclosing graph contents."""

    def __init__(self) -> None:
        """Initialize the fixed dependency-conflict failure."""
        super().__init__(
            ApplicationErrorCode.DEPENDENCY_CONFLICT,
            "The dependency change conflicts with the current Task graph.",
        )


class DependencyCycleError(ApplicationError):
    """Report that a proposed dependency edge would create a cycle."""

    def __init__(self) -> None:
        """Initialize the fixed dependency-cycle failure."""
        super().__init__(
            ApplicationErrorCode.DEPENDENCY_CYCLE,
            "The dependency change would create a cycle.",
        )


class UnsatisfiableDependencyError(ApplicationError):
    """Report that a cancelled prerequisite prevents completion."""

    def __init__(self) -> None:
        """Initialize the fixed unsatisfiable-dependency failure."""
        super().__init__(
            ApplicationErrorCode.UNSATISFIABLE_DEPENDENCY,
            "The Task has a cancelled prerequisite and cannot be completed.",
        )


class ResultInvalidError(ApplicationError):
    """Report invalid structured Result input without echoing its content."""

    def __init__(self) -> None:
        """Initialize the fixed invalid-Result failure."""
        super().__init__(
            ApplicationErrorCode.RESULT_INVALID,
            "The submitted Result is invalid.",
        )


class NoTaskAvailableError(ApplicationError):
    """Report that one Project currently has no ready Task to claim."""

    def __init__(self) -> None:
        """Initialize the fixed retryable no-Task failure."""
        super().__init__(
            ApplicationErrorCode.NO_TASK_AVAILABLE,
            "No ready Task is available to claim.",
        )


class TaskLockedError(ApplicationError):
    """Report a current Claim owned by another execution."""

    def __init__(self) -> None:
        """Initialize the fixed retryable Claim-lock failure."""
        super().__init__(
            ApplicationErrorCode.TASK_LOCKED,
            "The Task has a current Claim owned by another execution.",
        )


class LeaseLostError(ApplicationError):
    """Report a missing, expired, terminal, or superseded Claim owner token."""

    def __init__(self) -> None:
        """Initialize the fixed non-retryable Lease-lost failure."""
        super().__init__(
            ApplicationErrorCode.LEASE_LOST,
            "The Claim is no longer current.",
        )


class AuthenticationRequiredError(ApplicationError):
    """Report that no credential source was available."""

    def __init__(self) -> None:
        """Initialize the exact missing-authentication failure."""
        super().__init__(
            ApplicationErrorCode.AUTHENTICATION_REQUIRED,
            "Authentication is required.",
        )


class AuthenticationFailedError(ApplicationError):
    """Collapse all invalid credential and Subject states safely."""

    def __init__(self) -> None:
        """Initialize the non-disclosing authentication failure."""
        super().__init__(
            ApplicationErrorCode.AUTHENTICATION_FAILED,
            "The supplied credential is not valid.",
        )


class SubjectNotFoundError(ApplicationError):
    """Report an administratively visible missing Subject."""

    def __init__(self) -> None:
        """Initialize the exact missing-Subject failure."""
        super().__init__(
            ApplicationErrorCode.SUBJECT_NOT_FOUND,
            "The Subject was not found.",
        )


class SubjectHandleConflictError(ApplicationError):
    """Report reuse of one immutable Instance-scoped Subject handle."""

    def __init__(self) -> None:
        """Initialize the exact Subject-handle conflict failure."""
        super().__init__(
            ApplicationErrorCode.SUBJECT_HANDLE_CONFLICT,
            "The Subject handle is already in use.",
        )


class TokenNotFoundError(ApplicationError):
    """Report an administratively visible missing Token metadata record."""

    def __init__(self) -> None:
        """Initialize the exact missing-Token failure."""
        super().__init__(
            ApplicationErrorCode.TOKEN_NOT_FOUND,
            "The Token was not found.",
        )


class GrantNotFoundError(ApplicationError):
    """Report an administratively visible missing ProjectGrant."""

    def __init__(self) -> None:
        """Initialize the exact missing-grant failure."""
        super().__init__(
            ApplicationErrorCode.GRANT_NOT_FOUND,
            "The ProjectGrant was not found.",
        )


class IdentityVersionConflictError(ApplicationError):
    """Report stale optimistic Subject or ProjectGrant metadata."""

    def __init__(self) -> None:
        """Initialize the exact identity-version conflict failure."""
        super().__init__(
            ApplicationErrorCode.IDENTITY_VERSION_CONFLICT,
            "The identity or grant changed after the expected version.",
        )


class LastInstanceAdminError(ApplicationError):
    """Reject a mutation that removes the final enabled administrator."""

    def __init__(self) -> None:
        """Initialize the exact last-administrator guard failure."""
        super().__init__(
            ApplicationErrorCode.LAST_INSTANCE_ADMIN,
            "The Instance must retain an enabled administrator.",
        )


class LastProjectOwnerError(ApplicationError):
    """Reject a mutation that removes the final enabled Project Owner."""

    def __init__(self) -> None:
        """Initialize the exact last-Owner guard failure."""
        super().__init__(
            ApplicationErrorCode.LAST_PROJECT_OWNER,
            "The Project must retain an enabled Owner.",
        )


class CredentialUnavailableError(ApplicationError):
    """Report a protected credential boundary that cannot be used safely."""

    def __init__(self) -> None:
        """Initialize the exact credential-store operational failure."""
        super().__init__(
            ApplicationErrorCode.CREDENTIAL_UNAVAILABLE,
            "The credential store is unavailable.",
        )


def _validate_safe_message(value: object) -> str:
    """Validate one bounded message without terminal control characters.

    Args:
        value: Candidate public error message.

    Returns:
        The validated message.

    Raises:
        DomainValidationError: If the message is unsafe or malformed.

    """
    if not isinstance(value, str):
        message = "Application error message must be a string."
        raise DomainValidationError(message)
    if value != value.strip() or not value:
        message = "Application error message must be nonempty and trimmed."
        raise DomainValidationError(message)
    if len(value) > _MAX_SAFE_MESSAGE_LENGTH:
        message = "Application error message must not exceed 500 characters."
        raise DomainValidationError(message)
    if any(not character.isprintable() for character in value):
        message = "Application error message must not contain control characters."
        raise DomainValidationError(message)
    return value
