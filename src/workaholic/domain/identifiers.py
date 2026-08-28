"""Opaque, prefixed identifier value objects for cumulative domain entities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from workaholic.domain.errors import DomainValidationError

_IDENTIFIER_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _validate_identifier(value: object, *, prefix: str, label: str) -> str:
    """Validate one opaque identifier without interpreting its suffix.

    Args:
        value: Candidate identifier.
        prefix: Required type-specific prefix, including the underscore.
        label: Human-readable identifier kind for safe error messages.

    Returns:
        The validated identifier string.

    Raises:
        DomainValidationError: If the value has the wrong type or format.

    """
    if not isinstance(value, str):
        message = f"{label} must be a string."
        raise DomainValidationError(message)
    if not value.startswith(prefix):
        message = f"{label} must start with {prefix!r}."
        raise DomainValidationError(message)
    suffix = value.removeprefix(prefix)
    if _IDENTIFIER_SUFFIX_PATTERN.fullmatch(suffix) is None:
        message = (
            f"{label} must have a 1-128 character opaque suffix containing only "
            "ASCII letters, digits, underscores, or hyphens."
        )
        raise DomainValidationError(message)
    return value


@dataclass(frozen=True, slots=True)
class InstanceId:
    """Opaque identity of one Workaholic Instance."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Instance identifier at construction."""
        _validate_identifier(self.value, prefix="ins_", label="Instance ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class ProjectId:
    """Opaque identity of one Project."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Project identifier at construction."""
        _validate_identifier(self.value, prefix="prj_", label="Project ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class SubjectId:
    """Opaque identity of one Human or Agent Subject."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Subject identifier at construction."""
        _validate_identifier(self.value, prefix="sub_", label="Subject ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class TokenId:
    """Opaque identity of one revocable bearer Token."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Token identifier at construction."""
        _validate_identifier(self.value, prefix="tok_", label="Token ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class TaskId:
    """Opaque canonical identity of one Task."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Task identifier at construction."""
        _validate_identifier(self.value, prefix="tsk_", label="Task ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class TaskEventId:
    """Opaque identity of one TaskEvent."""

    value: str

    def __post_init__(self) -> None:
        """Validate the TaskEvent identifier at construction."""
        _validate_identifier(self.value, prefix="evt_", label="TaskEvent ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class AuditEventId:
    """Opaque identity of one administrative AuditEvent."""

    value: str

    def __post_init__(self) -> None:
        """Validate the AuditEvent identifier at construction."""
        _validate_identifier(self.value, prefix="aev_", label="AuditEvent ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class ResultId:
    """Opaque identity of one submitted Task Result."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Result identifier at construction."""
        _validate_identifier(self.value, prefix="res_", label="Result ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class RequestId:
    """Opaque identity of one application request."""

    value: str

    def __post_init__(self) -> None:
        """Validate the request identifier at construction."""
        _validate_identifier(self.value, prefix="req_", label="Request ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value


@dataclass(frozen=True, slots=True)
class AttemptId:
    """Opaque identity of one Agent execution Attempt."""

    value: str

    def __post_init__(self) -> None:
        """Validate the Attempt identifier at construction."""
        _validate_identifier(self.value, prefix="atm_", label="Attempt ID")

    def __str__(self) -> str:
        """Return the serialized identifier.

        Returns:
            The opaque prefixed identifier.

        """
        return self.value
