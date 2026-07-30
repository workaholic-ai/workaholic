"""Typed safe failures for local data-path and Workspace context operations."""

from __future__ import annotations

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProfileUnsupportedError,
)

__all__ = [
    "ContextInvalidError",
    "ContextNotFoundError",
    "ContextStorageError",
    "ProfileInvalidError",
    "ProfileNotFoundError",
    "ProfileUnsupportedError",
]


class ContextNotFoundError(ApplicationError):
    """Report that the exact current directory has no context file."""

    def __init__(self) -> None:
        """Initialize the stable missing-context failure."""
        super().__init__(
            ApplicationErrorCode.CONTEXT_NOT_FOUND,
            "No .workaholic.env exists in the current directory.",
        )


class ContextInvalidError(ApplicationError):
    """Report malformed or untrusted local context data."""

    def __init__(
        self,
        safe_message: str = "The .workaholic.env context is invalid.",
    ) -> None:
        """Initialize a stable invalid-context failure.

        Args:
            safe_message: Redacted explanation safe for public presentation.

        """
        super().__init__(ApplicationErrorCode.CONTEXT_INVALID, safe_message)


class ContextStorageError(ApplicationError):
    """Report an unavailable or non-durable local filesystem operation."""

    def __init__(
        self,
        safe_message: str = "Local Workaholic storage is unavailable.",
    ) -> None:
        """Initialize a stable storage failure.

        Args:
            safe_message: Redacted explanation safe for public presentation.

        """
        super().__init__(ApplicationErrorCode.STORAGE_UNAVAILABLE, safe_message)
