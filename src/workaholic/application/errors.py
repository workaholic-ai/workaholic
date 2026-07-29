"""Stable application failures shared by Sessions and presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from workaholic.domain import DomainValidationError

_MAX_SAFE_MESSAGE_LENGTH = 500


class ApplicationErrorCode(StrEnum):
    """Machine-readable Phase 1 failure identifiers."""

    INVALID_INPUT = "INVALID_INPUT"
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    CONTEXT_INVALID = "CONTEXT_INVALID"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    PROJECT_KEY_CONFLICT = "PROJECT_KEY_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
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
        ApplicationErrorCode.NOT_INITIALIZED: _ErrorSpec(
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
        ApplicationErrorCode.IDEMPOTENCY_CONFLICT: _ErrorSpec(
            ExitCategory.CONFLICT,
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
