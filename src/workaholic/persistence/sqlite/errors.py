"""Safe application failures emitted by the SQLite adapter boundary."""

from __future__ import annotations

from workaholic.application import ApplicationError, ApplicationErrorCode


class SchemaUnsupportedError(ApplicationError):
    """Report a missing, malformed, older, or newer store schema."""

    def __init__(self) -> None:
        """Initialize the stable unsupported-schema failure."""
        super().__init__(
            ApplicationErrorCode.SCHEMA_UNSUPPORTED,
            "Store schema is missing or unsupported.",
        )


class StorageBusyError(ApplicationError):
    """Report exhaustion of bounded SQLite lock acquisition."""

    def __init__(self) -> None:
        """Initialize the stable retryable storage-contention failure."""
        super().__init__(
            ApplicationErrorCode.STORAGE_BUSY,
            "Local Workaholic storage remained busy past its retry window.",
        )


class StorageUnavailableError(ApplicationError):
    """Report an unexpected or unavailable SQLite storage operation."""

    def __init__(self) -> None:
        """Initialize the stable redacted storage failure."""
        super().__init__(
            ApplicationErrorCode.STORAGE_UNAVAILABLE,
            "Local Workaholic storage is unavailable.",
        )
