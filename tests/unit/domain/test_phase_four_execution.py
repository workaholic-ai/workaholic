"""Exhaustive pure-domain tests for Phase 4 Claim and Agent execution rules."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path

import pytest

from workaholic.domain import (
    AGENT_LEASE_DEFAULT,
    AGENT_LEASE_MAXIMUM,
    AGENT_LEASE_MINIMUM,
    HUMAN_LEASE_DEFAULT,
    HUMAN_LEASE_MAXIMUM,
    HUMAN_LEASE_MINIMUM,
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
    DomainValidationError,
    ObservationKind,
    ProgressObservation,
    ProjectId,
    ReadinessReason,
    SubjectId,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEventType,
    TaskId,
    TaskProgress,
    TaskState,
    claim_owner_matches,
    derive_task_readiness,
    is_lease_current,
    is_task_claimable,
    resolve_lease_duration,
    transition_attempt_status,
    validate_claim_attempt_consistency,
)

_NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
_DOMAIN_DIRECTORY = Path(__file__).parents[3] / "src" / "workaholic" / "domain"


def _task() -> Task:
    """Build one valid ready Task.

    Returns:
        A valid open Task with no dependencies.

    """
    return Task(
        uid=TaskId("tsk_claimed"),
        project_id=ProjectId("prj_main"),
        number=1,
        key="PRJ-1",
        title="Implement claims",
        objective="Implement the accepted Claim contract.",
        state=TaskState.OPEN,
        priority=50,
        version=4,
        created_by=SubjectId("sub_local"),
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
        approval=ApprovalRequirement.NONE,
    )


def _claim(
    *,
    attempt_id: AttemptId | None = None,
    expires_at: datetime | None = None,
) -> TaskClaim:
    """Build a Human or Agent Claim.

    Args:
        attempt_id: Optional Agent Attempt identity.
        expires_at: Optional Lease expiry override.

    Returns:
        A valid immutable Claim.

    """
    task = _task()
    return TaskClaim(
        task_uid=task.uid,
        task_key=task.key,
        subject_id=SubjectId("sub_local"),
        attempt_id=attempt_id,
        claimed_at=_NOW - timedelta(minutes=1),
        lease_expires_at=expires_at or _NOW + timedelta(minutes=15),
    )


def _attempt(
    *,
    status: AttemptStatus = AttemptStatus.ACTIVE,
    ended_at: datetime | None = None,
) -> TaskAttempt:
    """Build one valid Agent Attempt.

    Args:
        status: Attempt lifecycle status.
        ended_at: Terminal timestamp, when applicable.

    Returns:
        A valid immutable Attempt.

    """
    expiry = _NOW + timedelta(minutes=15)
    if status is AttemptStatus.EXPIRED:
        ended_at = expiry
    return TaskAttempt(
        id=AttemptId("atm_current"),
        task_uid=_task().uid,
        subject_id=SubjectId("sub_local"),
        status=status,
        lease_expires_at=expiry,
        started_at=_NOW - timedelta(minutes=1),
        ended_at=ended_at,
    )


def test_claims_distinguish_human_and_agent_ownership_and_are_frozen() -> None:
    """Nullable Attempt identity is the explicit immutable owner-path marker."""
    human = _claim()
    agent = _claim(attempt_id=AttemptId("atm_current"))

    assert human.attempt_id is None
    assert agent.attempt_id == AttemptId("atm_current")
    with pytest.raises(FrozenInstanceError):
        agent.attempt_id = None  # type: ignore[misc]


def test_phase_four_event_types_are_exact_and_additive() -> None:
    """Claim and execution add exactly the six accepted event values."""
    assert tuple(event_type.value for event_type in TaskEventType)[-6:] == (
        "task_claimed",
        "claim_renewed",
        "claim_released",
        "claim_expired",
        "progress_reported",
        "observation_added",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_uid", "tsk_claimed", "task_uid"),
        ("task_key", "bad", "task_key"),
        ("subject_id", "sub_local", "subject_id"),
        ("attempt_id", "atm_current", "attempt_id"),
        ("claimed_at", _NOW.replace(tzinfo=None), "claimed_at"),
        ("lease_expires_at", _NOW - timedelta(minutes=1), "must follow"),
    ],
)
def test_claim_rejects_invalid_runtime_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    """Claim construction validates types, key shape, UTC, and positive Lease."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_claim(), **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("status", tuple(AttemptStatus))
def test_attempt_accepts_every_exact_status_with_valid_timestamps(
    status: AttemptStatus,
) -> None:
    """Every accepted Attempt status has one valid timestamp shape."""
    ended_at = None if status is AttemptStatus.ACTIVE else _NOW + timedelta(minutes=1)
    attempt = _attempt(status=status, ended_at=ended_at)

    assert attempt.status is status
    assert (attempt.ended_at is None) is (status is AttemptStatus.ACTIVE)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "atm_current"}, "Attempt id"),
        ({"status": "active"}, "Attempt status"),
        ({"lease_expires_at": _NOW - timedelta(minutes=1)}, "must follow"),
        ({"ended_at": _NOW}, "active Attempt"),
    ],
)
def test_active_attempt_rejects_invalid_identity_status_and_timestamps(
    changes: dict[str, object],
    message: str,
) -> None:
    """Active Attempt invariants are enforced at runtime."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_attempt(), **changes)  # type: ignore[arg-type]


def test_terminal_attempts_require_valid_end_time_for_their_reason() -> None:
    """Expiry ends at the Lease boundary; other terminal states end before it."""
    with pytest.raises(DomainValidationError, match="requires ended_at"):
        _attempt(status=AttemptStatus.RELEASED)
    with pytest.raises(DomainValidationError, match="lease_expires_at"):
        replace(
            _attempt(status=AttemptStatus.EXPIRED),
            ended_at=_NOW + timedelta(minutes=14),
        )
    with pytest.raises(DomainValidationError, match="before Lease expiry"):
        _attempt(
            status=AttemptStatus.SUBMITTED,
            ended_at=_NOW + timedelta(minutes=15),
        )


def test_progress_normalizes_text_and_defensively_copies_observations() -> None:
    """Progress preserves order while detaching accepted mutable inputs."""
    observations = [
        ProgressObservation(ObservationKind.NOTE, "  Started  "),
        ProgressObservation(ObservationKind.RISK, "  Needs review  "),
    ]
    progress = TaskProgress(
        message="  Halfway  ",
        percent_complete=50,
        observations=observations,  # type: ignore[arg-type]
    )
    observations.clear()

    assert progress.message == "Halfway"
    assert progress.percent_complete == 50
    assert tuple(item.kind for item in progress.observations or ()) == (
        ObservationKind.NOTE,
        ObservationKind.RISK,
    )
    assert (progress.observations or ())[0].text == "Started"


@pytest.mark.parametrize(
    ("progress", "message"),
    [
        ({}, "at least one field"),
        ({"message": " "}, "Progress message"),
        ({"message": "x" * 4_001}, "Progress message"),
        ({"percent_complete": True}, "percent_complete"),
        ({"percent_complete": -1}, "percent_complete"),
        ({"percent_complete": 101}, "percent_complete"),
        (
            {
                "observations": tuple(
                    ProgressObservation(ObservationKind.NOTE, str(index))
                    for index in range(51)
                )
            },
            "50 items",
        ),
    ],
)
def test_progress_rejects_missing_and_out_of_bounds_fields(
    progress: dict[str, object],
    message: str,
) -> None:
    """Progress applies exact presence, text, percent, and collection bounds."""
    with pytest.raises(DomainValidationError, match=message):
        TaskProgress(**progress)  # type: ignore[arg-type]


def test_progress_accepts_each_observation_kind_and_empty_present_collection() -> None:
    """All four inert kinds and an explicitly present empty list are valid."""
    assert TaskProgress(observations=()).observations == ()
    for kind in ObservationKind:
        assert ProgressObservation(kind=kind, text="Observed").kind is kind
    with pytest.raises(DomainValidationError, match="Observation kind"):
        ProgressObservation(kind="note", text="Observed")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("attempt_id", "expected"),
    [
        (None, HUMAN_LEASE_DEFAULT),
        (AttemptId("atm_current"), AGENT_LEASE_DEFAULT),
    ],
)
def test_lease_defaults_are_selected_by_nullable_attempt(
    attempt_id: AttemptId | None,
    expected: timedelta,
) -> None:
    """Human and Agent owner paths receive their documented defaults."""
    assert resolve_lease_duration(None, attempt_id=attempt_id) == expected


@pytest.mark.parametrize(
    ("attempt_id", "duration"),
    [
        (None, HUMAN_LEASE_MINIMUM),
        (None, HUMAN_LEASE_MAXIMUM),
        (AttemptId("atm_current"), AGENT_LEASE_MINIMUM),
        (AttemptId("atm_current"), AGENT_LEASE_MAXIMUM),
    ],
)
def test_lease_duration_accepts_inclusive_bounds(
    attempt_id: AttemptId | None,
    duration: timedelta,
) -> None:
    """Every Human and Agent minimum and maximum is inclusive."""
    assert resolve_lease_duration(duration, attempt_id=attempt_id) == duration


@pytest.mark.parametrize(
    ("attempt_id", "duration"),
    [
        (None, HUMAN_LEASE_MINIMUM - timedelta(microseconds=1)),
        (None, HUMAN_LEASE_MAXIMUM + timedelta(microseconds=1)),
        (AttemptId("atm_current"), timedelta(0)),
        (AttemptId("atm_current"), AGENT_LEASE_MAXIMUM + timedelta(seconds=1)),
    ],
)
def test_lease_duration_rejects_values_outside_owner_bounds(
    attempt_id: AttemptId | None,
    duration: timedelta,
) -> None:
    """Sub-unit precision cannot bypass inclusive Lease limits."""
    with pytest.raises(DomainValidationError, match="Lease duration"):
        resolve_lease_duration(duration, attempt_id=attempt_id)


def test_lease_duration_rejects_coercion_and_untyped_attempt() -> None:
    """Lease rules do not rely on annotations or parse presentation text."""
    with pytest.raises(DomainValidationError, match="timedelta"):
        resolve_lease_duration(60, attempt_id=None)
    with pytest.raises(DomainValidationError, match="AttemptId"):
        resolve_lease_duration(None, attempt_id="atm_current")


def test_lease_current_uses_exact_half_open_utc_boundary() -> None:
    """A Lease is current before, but never at, its expiry instant."""
    expiry = _NOW + timedelta(seconds=1)

    assert is_lease_current(lease_expires_at=expiry, now=_NOW)
    assert not is_lease_current(lease_expires_at=expiry, now=expiry)
    with pytest.raises(DomainValidationError, match="Lease time"):
        is_lease_current(lease_expires_at=expiry, now=_NOW.replace(tzinfo=None))


def test_owner_tokens_compare_subject_and_nullable_attempt_exactly() -> None:
    """A shared bootstrap Subject cannot substitute for Agent Attempt identity."""
    agent_claim = _claim(attempt_id=AttemptId("atm_current"))

    assert claim_owner_matches(
        claim=agent_claim,
        subject_id=SubjectId("sub_local"),
        attempt_id=AttemptId("atm_current"),
    )
    assert not claim_owner_matches(
        claim=agent_claim,
        subject_id=SubjectId("sub_local"),
        attempt_id=AttemptId("atm_other"),
    )
    assert not claim_owner_matches(
        claim=agent_claim,
        subject_id=SubjectId("sub_other"),
        attempt_id=AttemptId("atm_current"),
    )
    assert claim_owner_matches(
        claim=_claim(),
        subject_id=SubjectId("sub_local"),
        attempt_id=None,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    tuple(product(AttemptStatus, AttemptStatus)),
)
def test_attempt_transition_matrix_is_explicit(
    current: AttemptStatus,
    target: AttemptStatus,
) -> None:
    """Only active-to-terminal Attempt transitions are accepted."""
    if current is AttemptStatus.ACTIVE and target is not AttemptStatus.ACTIVE:
        assert transition_attempt_status(current, target) is target
    else:
        with pytest.raises(DomainValidationError, match="only once"):
            transition_attempt_status(current, target)


def test_claim_attempt_pair_requires_exact_active_agent_attempt() -> None:
    """Human Claims have no Attempt; Agent Claims match one active Attempt."""
    validate_claim_attempt_consistency(claim=_claim(), attempt=None)
    agent_claim = _claim(attempt_id=AttemptId("atm_current"))
    validate_claim_attempt_consistency(claim=agent_claim, attempt=_attempt())

    invalid_pairs = (
        (_claim(), _attempt()),
        (agent_claim, None),
        (agent_claim, replace(_attempt(), id=AttemptId("atm_other"))),
        (
            agent_claim,
            _attempt(
                status=AttemptStatus.RELEASED,
                ended_at=_NOW + timedelta(minutes=1),
            ),
        ),
    )
    for claim, attempt in invalid_pairs:
        with pytest.raises(DomainValidationError):
            validate_claim_attempt_consistency(claim=claim, attempt=attempt)


def test_readiness_uses_current_claim_and_allows_stale_plus_ready() -> None:
    """Current Claims lock readiness while expired Claims are informational."""
    task = _task()
    active = derive_task_readiness(
        task=task,
        prerequisites=(),
        now=_NOW,
        claim=_claim(expires_at=_NOW + timedelta(seconds=1)),
    )
    stale = derive_task_readiness(
        task=task,
        prerequisites=(),
        now=_NOW,
        claim=_claim(expires_at=_NOW),
    )

    assert not active.ready
    assert active.running
    assert active.reasons == (ReadinessReason.ACTIVE_CLAIM,)
    assert stale.ready
    assert stale.stale
    assert stale.reasons == (ReadinessReason.STALE_CLAIM,)
    assert not is_task_claimable(
        task=task,
        prerequisites=(),
        now=_NOW,
        claim=_claim(expires_at=_NOW + timedelta(seconds=1)),
    )
    assert is_task_claimable(
        task=task,
        prerequisites=(),
        now=_NOW,
        claim=_claim(expires_at=_NOW),
    )


def test_domain_execution_rules_have_no_hidden_clock_or_boundary_dependency() -> None:
    """The domain remains pure and requires authoritative time from callers."""
    forbidden_import_roots = {"pydantic", "sqlite3", "typer"}
    forbidden_workaholic_roots = {
        "workaholic.application",
        "workaholic.cli",
        "workaholic.persistence",
        "workaholic.session",
    }
    for path in sorted(_DOMAIN_DIRECTORY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = {node.module}
            else:
                names = set()
            assert not {name.partition(".")[0] for name in names} & (
                forbidden_import_roots
            ), path
            assert not names & forbidden_workaholic_roots, path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "today", "utcnow"}, path
