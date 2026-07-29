"""Typed failures raised when domain values violate Workaholic invariants."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for failures owned by the dependency-free domain layer."""


class DomainValidationError(DomainError, ValueError):
    """Report invalid data supplied to a domain value or rule."""


class DomainPermissionError(DomainError, PermissionError):
    """Report a rejected domain authorization rule."""
