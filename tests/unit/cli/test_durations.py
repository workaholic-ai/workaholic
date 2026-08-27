"""Unit tests for strict CLI Claim Lease duration parsing."""

from __future__ import annotations

from datetime import timedelta

import pytest

from workaholic.cli.durations import (
    LeaseDurationError,
    LeaseOwner,
    parse_lease_duration,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1s", timedelta(seconds=1)),
        ("59s", timedelta(seconds=59)),
        ("1m", timedelta(minutes=1)),
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("1d", timedelta(days=1)),
    ],
)
def test_agent_duration_accepts_every_unit_with_inclusive_bounds(
    text: str,
    expected: timedelta,
) -> None:
    """Agent durations use the exact grammar from one second through 24 hours."""
    assert parse_lease_duration(text, owner=LeaseOwner.AGENT) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("60s", timedelta(minutes=1)),
        ("1m", timedelta(minutes=1)),
        ("8h", timedelta(hours=8)),
        ("1d", timedelta(days=1)),
        ("30d", timedelta(days=30)),
    ],
)
def test_human_duration_accepts_every_unit_with_inclusive_bounds(
    text: str,
    expected: timedelta,
) -> None:
    """Human durations use the same grammar from one minute through 30 days."""
    assert parse_lease_duration(text, owner=LeaseOwner.HUMAN) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0s",
        "01s",
        "+1s",
        "-1s",
        "1.5h",
        "1h30m",
        "1",
        "1S",
        " 1h",
        "1h ",
        "1w",
        "١h",  # noqa: RUF001 - non-ASCII numeral rejection fixture
        "9" * 10_000,
    ],
)
def test_duration_rejects_every_form_outside_the_exact_grammar(text: str) -> None:
    """Ambiguous, padded, compound, and unbounded numeric forms are rejected."""
    with pytest.raises(LeaseDurationError):
        parse_lease_duration(text, owner=LeaseOwner.AGENT)


@pytest.mark.parametrize(
    ("text", "owner"),
    [
        ("59s", LeaseOwner.HUMAN),
        ("31d", LeaseOwner.HUMAN),
        ("1441m", LeaseOwner.AGENT),
        ("2d", LeaseOwner.AGENT),
    ],
)
def test_duration_rejects_values_outside_owner_specific_bounds(
    text: str,
    owner: LeaseOwner,
) -> None:
    """Grammar-valid durations still respect the selected ownership path."""
    with pytest.raises(LeaseDurationError):
        parse_lease_duration(text, owner=owner)


def test_duration_runtime_validates_text_and_owner_types() -> None:
    """Direct callers cannot bypass parsing with lookalike runtime values."""
    with pytest.raises(LeaseDurationError):
        parse_lease_duration(60, owner=LeaseOwner.HUMAN)
    with pytest.raises(TypeError, match="LeaseOwner"):
        parse_lease_duration("1h", owner="human")  # type: ignore[arg-type]
