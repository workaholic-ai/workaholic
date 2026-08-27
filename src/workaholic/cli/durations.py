"""Strict CLI parsing for bounded Claim Lease durations."""

from __future__ import annotations

import re
from datetime import timedelta
from enum import StrEnum

from workaholic.domain import (
    AGENT_LEASE_MAXIMUM,
    AGENT_LEASE_MINIMUM,
    HUMAN_LEASE_MAXIMUM,
    HUMAN_LEASE_MINIMUM,
)

_DURATION_PATTERN = re.compile(r"^[1-9][0-9]*(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
_MAX_MAGNITUDE_DIGITS = 10


class LeaseOwner(StrEnum):
    """Ownership paths with distinct Lease duration bounds."""

    HUMAN = "human"
    AGENT = "agent"


class LeaseDurationError(ValueError):
    """A Lease duration is malformed or outside its ownership-path bounds."""


def parse_lease_duration(value: object, *, owner: LeaseOwner) -> timedelta:
    """Parse one exact positive duration using the Phase 4 grammar.

    The accepted grammar is one positive integer followed by exactly one of
    ``s``, ``m``, ``h``, or ``d``. Compound, fractional, signed, whitespace,
    and unitless forms are deliberately rejected so automation has one stable
    representation.

    Args:
        value: Candidate CLI duration text.
        owner: Human or Agent Lease path selecting the applicable bounds.

    Returns:
        Validated whole-second duration.

    Raises:
        TypeError: If ``owner`` is not a LeaseOwner.
        LeaseDurationError: If the text is malformed or outside its bounds.

    """
    candidate_owner: object = owner
    if not isinstance(candidate_owner, LeaseOwner):
        message = "Lease duration owner must be a LeaseOwner."
        raise TypeError(message)
    if not isinstance(value, str):
        message = "Lease duration must be text."
        raise LeaseDurationError(message)
    matched = _DURATION_PATTERN.fullmatch(value)
    if matched is None or len(value) - 1 > _MAX_MAGNITUDE_DIGITS:
        message = "Lease duration must be a positive integer followed by s, m, h, or d."
        raise LeaseDurationError(message)

    unit = matched.group(1)
    seconds = int(value[:-1]) * _UNIT_SECONDS[unit]
    minimum, maximum = _lease_bounds(owner)
    if not int(minimum.total_seconds()) <= seconds <= int(maximum.total_seconds()):
        message = f"Lease duration is outside the {owner.value} bounds."
        raise LeaseDurationError(message)
    return timedelta(seconds=seconds)


def _lease_bounds(owner: LeaseOwner) -> tuple[timedelta, timedelta]:
    """Return the domain-owned bounds for one Lease path.

    Args:
        owner: Validated Human or Agent owner path.

    Returns:
        Inclusive minimum and maximum duration.

    """
    if owner is LeaseOwner.HUMAN:
        return HUMAN_LEASE_MINIMUM, HUMAN_LEASE_MAXIMUM
    return AGENT_LEASE_MINIMUM, AGENT_LEASE_MAXIMUM
