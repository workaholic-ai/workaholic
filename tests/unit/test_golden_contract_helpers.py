"""Tests for golden-journey CLI contract assertions."""

from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest
from tests.golden import require_error, require_object, require_success


def _completed(*, returncode: int, stdout: str) -> CompletedProcess[str]:
    """Build a completed command result without invoking a process.

    Args:
        returncode: Process exit status.
        stdout: Captured standard output.

    Returns:
        A deterministic completed-process value.

    """
    return CompletedProcess(
        args=("workaholic", "--json", "--non-interactive"),
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_require_success_returns_validated_data() -> None:
    """A valid successful envelope exposes its JSON data."""
    result = _completed(
        returncode=0,
        stdout=(
            '{"schema":"workaholic.cli/v1","ok":true,'
            '"data":{"task":{"key":"ACME-1"}}}\n'
        ),
    )

    data = require_object(require_success(result), context="success data")

    assert data == {"task": {"key": "ACME-1"}}


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        pytest.param(
            1,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\n',
            id="nonzero-success",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v2","ok":true,"data":{}}\n',
            id="wrong-schema",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\nnoise',
            id="mixed-stdout",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":NaN}\n',
            id="nonstandard-number",
        ),
        pytest.param(
            0,
            '{"schema":"workaholic.cli/v1","ok":true,"data":{}}\n\n',
            id="extra-newline",
        ),
    ],
)
def test_require_success_rejects_contract_violations(
    returncode: int,
    stdout: str,
) -> None:
    """Malformed success output cannot satisfy a golden journey."""
    with pytest.raises(AssertionError):
        require_success(_completed(returncode=returncode, stdout=stdout))


def test_require_error_validates_machine_readable_failure() -> None:
    """A valid failure envelope exposes its structured error."""
    result = _completed(
        returncode=3,
        stdout=json.dumps(
            {
                "schema": "workaholic.cli/v1",
                "ok": False,
                "error": {
                    "code": "LEASE_LOST",
                    "message": "The Attempt is no longer active.",
                    "retryable": False,
                },
            },
            separators=(",", ":"),
        )
        + "\n",
    )

    error = require_error(result, expected_code="LEASE_LOST")

    assert error["retryable"] is False


@pytest.mark.parametrize(
    ("returncode", "error"),
    [
        pytest.param(0, {}, id="zero-exit"),
        pytest.param(
            3,
            {"code": "OTHER", "message": "Wrong code.", "retryable": False},
            id="wrong-code",
        ),
        pytest.param(
            3,
            {"code": "LEASE_LOST", "message": "", "retryable": False},
            id="empty-message",
        ),
        pytest.param(
            3,
            {"code": "LEASE_LOST", "message": "Lost.", "retryable": "false"},
            id="invalid-retryable",
        ),
    ],
)
def test_require_error_rejects_contract_violations(
    returncode: int,
    error: dict[str, object],
) -> None:
    """Malformed error output cannot satisfy a golden journey."""
    stdout = (
        json.dumps(
            {
                "schema": "workaholic.cli/v1",
                "ok": False,
                "error": error,
            },
            separators=(",", ":"),
        )
        + "\n"
    )

    with pytest.raises(AssertionError):
        require_error(
            _completed(returncode=returncode, stdout=stdout),
            expected_code="LEASE_LOST",
        )
