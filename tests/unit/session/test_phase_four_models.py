"""Unit tests for strict transport-neutral Phase 4 Session requests."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, cast

import pytest
from pydantic import BaseModel, ValidationError

from workaholic.application import TaskResultInput
from workaholic.domain import (
    AttemptId,
    ObservationKind,
    ProgressObservation,
    TaskProgress,
)
from workaholic.session import (
    AgentHeartbeatRequest,
    AgentProgressRequest,
    AgentReleaseRequest,
    AgentSubmitRequest,
    AgentTaskClaimRequest,
    HumanClaimReleaseRequest,
    HumanClaimRenewRequest,
    HumanTaskClaimRequest,
)

_ATTEMPT_ID = AttemptId("atm_current")


class _LeaseRequestAccess(Protocol):
    """Typed test access to fields shared by Claim request models."""

    lease: timedelta | None
    project: str | None


@pytest.mark.parametrize(
    ("request_type", "values"),
    [
        (HumanTaskClaimRequest, {"task": "ACME-1"}),
        (AgentTaskClaimRequest, {}),
        (HumanClaimRenewRequest, {"task": "ACME-1"}),
        (
            AgentHeartbeatRequest,
            {"task": "ACME-1", "attempt": _ATTEMPT_ID},
        ),
        (HumanClaimReleaseRequest, {"task": "ACME-1"}),
        (
            AgentReleaseRequest,
            {"task": "ACME-1", "attempt": _ATTEMPT_ID},
        ),
        (
            AgentProgressRequest,
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "progress": TaskProgress(message="Working."),
            },
        ),
        (
            AgentSubmitRequest,
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "expected_version": 1,
                "result": TaskResultInput(summary="Implemented."),
            },
        ),
    ],
)
def test_phase_four_requests_are_frozen_and_closed(
    request_type: type[BaseModel],
    values: dict[str, object],
) -> None:
    """Every command path rejects unknown input and later mutation."""
    request = request_type.model_validate(values)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        request_type.model_validate({**values, "unknown": True})
    with pytest.raises(ValidationError, match="frozen"):
        cast("_LeaseRequestAccess", request).project = "OTHER"


@pytest.mark.parametrize(
    ("request_type", "values", "valid_duration"),
    [
        (
            HumanTaskClaimRequest,
            {"task": "ACME-1"},
            timedelta(minutes=1),
        ),
        (AgentTaskClaimRequest, {}, timedelta(seconds=1)),
        (
            HumanClaimRenewRequest,
            {"task": "ACME-1"},
            timedelta(days=30),
        ),
        (
            AgentHeartbeatRequest,
            {"task": "ACME-1", "attempt": _ATTEMPT_ID},
            timedelta(days=1),
        ),
    ],
)
def test_claim_requests_preserve_validated_whole_second_durations(
    request_type: type[BaseModel],
    values: dict[str, object],
    valid_duration: timedelta,
) -> None:
    """Session validation preserves duration precision and ownership bounds."""
    request = request_type.model_validate({**values, "lease": valid_duration})
    lease = cast("_LeaseRequestAccess", request).lease

    assert lease == valid_duration
    assert lease.total_seconds().is_integer()


@pytest.mark.parametrize(
    ("request_type", "values", "invalid_duration"),
    [
        (HumanTaskClaimRequest, {"task": "ACME-1"}, timedelta(seconds=59)),
        (AgentTaskClaimRequest, {}, timedelta(days=1, seconds=1)),
        (
            HumanClaimRenewRequest,
            {"task": "ACME-1"},
            timedelta(microseconds=1),
        ),
        (
            AgentHeartbeatRequest,
            {"task": "ACME-1", "attempt": _ATTEMPT_ID},
            900,
        ),
    ],
)
def test_claim_requests_reject_wrong_bounds_precision_and_coercion(
    request_type: type[BaseModel],
    values: dict[str, object],
    invalid_duration: object,
) -> None:
    """Invalid Lease values fail before any Session service can run."""
    with pytest.raises(ValidationError):
        request_type.model_validate({**values, "lease": invalid_duration})


def test_agent_owner_requests_require_typed_attempt_ids() -> None:
    """Agent command paths cannot omit or coerce their owner token."""
    with pytest.raises(ValidationError):
        AgentHeartbeatRequest.model_validate({"task": "ACME-1"})
    with pytest.raises(ValidationError):
        AgentReleaseRequest.model_validate({"task": "ACME-1", "attempt": "atm_current"})


def test_agent_progress_revalidates_closed_mapping_input() -> None:
    """Structured progress accepts only bounded domain-owned fields."""
    request = AgentProgressRequest.model_validate(
        {
            "task": "ACME-1",
            "attempt": _ATTEMPT_ID,
            "progress": {
                "message": "Running checks.",
                "percent_complete": 80,
                "observations": ({"kind": "risk", "text": "A retry may be needed."},),
            },
        }
    )

    assert request.progress == TaskProgress(
        message="Running checks.",
        percent_complete=80,
        observations=(
            ProgressObservation(
                kind=ObservationKind.RISK,
                text="A retry may be needed.",
            ),
        ),
    )
    with pytest.raises(ValidationError, match="closed progress shape"):
        AgentProgressRequest.model_validate(
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "progress": {
                    "message": "Working.",
                    "subject_id": "sub_forged",
                },
            }
        )


@pytest.mark.parametrize(
    "progress",
    [
        {},
        {"percent_complete": True},
        {"observations": ({"kind": "unknown", "text": "No."},)},
        {"observations": ({"kind": "risk", "text": "No.", "extra": 1},)},
    ],
)
def test_agent_progress_rejects_empty_coerced_or_unknown_content(
    progress: dict[str, object],
) -> None:
    """Malformed progress stays outside the domain and application layers."""
    with pytest.raises(ValidationError):
        AgentProgressRequest.model_validate(
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "progress": progress,
            }
        )


def test_agent_submit_requires_version_and_revalidates_result_input() -> None:
    """Agent submission preserves optimistic versioning and closed Result data."""
    request = AgentSubmitRequest.model_validate(
        {
            "task": "ACME-1",
            "attempt": _ATTEMPT_ID,
            "expected_version": 4,
            "result": {"summary": "Implemented."},
            "project": "ACME",
            "idempotency_key": "submit-1",
        }
    )

    assert request.expected_version == 4
    assert request.result == TaskResultInput(summary="Implemented.")
    with pytest.raises(ValidationError):
        AgentSubmitRequest.model_validate(
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "expected_version": 0,
                "result": {},
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentSubmitRequest.model_validate(
            {
                "task": "ACME-1",
                "attempt": _ATTEMPT_ID,
                "expected_version": 1,
                "result": {"subject_id": "sub_forged"},
            }
        )


def test_human_and_agent_paths_cannot_accept_each_others_fields() -> None:
    """Path-specific models make nullable Attempt dispatch unambiguous."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HumanTaskClaimRequest.model_validate({"task": "ACME-1", "attempt": _ATTEMPT_ID})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentTaskClaimRequest.model_validate({"task": "ACME-1"})
