"""Application-boundary tests for Phase 4 Claim and Agent execution contracts."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import NoReturn, TypedDict

import pytest
from pydantic import ValidationError

from workaholic.application import (
    ClaimExecutionRepository,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    ReleaseClaimMutation,
    RenewClaimMutation,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskClaimResult,
    TaskDetails,
    TaskProgressResult,
    TaskResultInput,
    TaskSubmissionResult,
    WorkaholicRepository,
)
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
    ObservationKind,
    ProgressObservation,
    ProjectId,
    ReadinessReason,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskProgress,
    TaskReadiness,
    TaskResult,
    TaskState,
)

_NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_main")
_SUBJECT_ID = SubjectId("sub_local")
_ATTEMPT_ID = AttemptId("atm_current")


class _ClaimMutationArgs(TypedDict):
    """Shared exact attribution passed to Phase 4 mutation constructors."""

    project_id: ProjectId
    actor_subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str


def _task(
    *,
    uid: TaskId | None = None,
    state: TaskState = TaskState.OPEN,
    version: int = 4,
    updated_at: datetime | None = None,
    current_result_id: ResultId | None = None,
) -> Task:
    """Build one valid Task for application result contracts.

    Args:
        uid: Optional Task identity override.
        state: Stored lifecycle state.
        version: Optimistic version.
        updated_at: Optional authoritative update time.
        current_result_id: Optional selected Result.

    Returns:
        A valid Task.

    """
    return Task(
        uid=uid or TaskId("tsk_claimed"),
        project_id=_PROJECT_ID,
        number=1,
        key="PRJ-1",
        title="Implement claims",
        objective="Implement the accepted Claim contract.",
        state=state,
        priority=50,
        version=version,
        created_by=_SUBJECT_ID,
        created_at=_NOW - timedelta(days=1),
        updated_at=updated_at or _NOW - timedelta(days=1),
        approval=ApprovalRequirement.NONE,
        current_result_id=current_result_id,
    )


def _claim(*, attempt_id: AttemptId | None = _ATTEMPT_ID) -> TaskClaim:
    """Build one current Human or Agent Claim.

    Args:
        attempt_id: Null for Human ownership or one Agent identity.

    Returns:
        A valid current Claim.

    """
    return TaskClaim(
        task_uid=TaskId("tsk_claimed"),
        task_key="PRJ-1",
        subject_id=_SUBJECT_ID,
        attempt_id=attempt_id,
        claimed_at=_NOW - timedelta(minutes=1),
        lease_expires_at=_NOW + timedelta(minutes=14),
    )


def _attempt(
    *,
    status: AttemptStatus = AttemptStatus.ACTIVE,
    ended_at: datetime | None = None,
) -> TaskAttempt:
    """Build one current or terminal Attempt.

    Args:
        status: Attempt lifecycle status.
        ended_at: Terminal timestamp.

    Returns:
        A valid Attempt matching ``_claim``.

    """
    return TaskAttempt(
        id=_ATTEMPT_ID,
        task_uid=TaskId("tsk_claimed"),
        subject_id=_SUBJECT_ID,
        status=status,
        lease_expires_at=_NOW + timedelta(minutes=14),
        started_at=_NOW - timedelta(minutes=1),
        ended_at=ended_at,
    )


def _event(
    event_type: TaskEventType,
    *,
    cursor: int = 1,
    attempt_id: AttemptId | None = _ATTEMPT_ID,
    event_id: str | None = None,
) -> TaskEvent:
    """Build one attributable TaskEvent.

    Args:
        event_type: Semantic event kind.
        cursor: Instance event cursor.
        attempt_id: Nullable execution attribution.
        event_id: Optional serialized event identity.

    Returns:
        A valid immutable event.

    """
    return TaskEvent(
        id=TaskEventId(event_id or f"evt_{event_type.value}_{cursor}"),
        cursor=cursor,
        task_uid=TaskId("tsk_claimed"),
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_operation"),
        event_type=event_type,
        occurred_at=_NOW,
        payload={},
        attempt_id=attempt_id,
    )


def _base_mutation() -> _ClaimMutationArgs:
    """Build shared trusted Claim mutation attribution.

    Returns:
        Mutable keyword data for strict command construction.

    """
    return {
        "project_id": _PROJECT_ID,
        "actor_subject_id": _SUBJECT_ID,
        "request_id": RequestId("req_operation"),
        "occurred_at": _NOW,
        "idempotency_key": "run-1",
    }


def test_all_phase_four_mutations_accept_exact_valid_contracts() -> None:
    """Every semantic operation has one strict adapter-neutral mutation shape."""
    common = _base_mutation()
    human = ClaimTaskMutation(
        **common,
        task_uid=TaskId("tsk_claimed"),
        lease_duration_seconds=28_800,
        task_claimed_event_id=TaskEventId("evt_claimed"),
        claim_expired_event_id=TaskEventId("evt_expired"),
    )
    agent = ClaimNextTaskMutation(
        **common,
        attempt_id=_ATTEMPT_ID,
        lease_duration_seconds=900,
        task_claimed_event_id=TaskEventId("evt_claimed"),
        claim_expired_event_id=TaskEventId("evt_expired"),
    )
    renewal = RenewClaimMutation(
        **common,
        task_uid=TaskId("tsk_claimed"),
        attempt_id=_ATTEMPT_ID,
        lease_duration_seconds=900,
        claim_renewed_event_id=TaskEventId("evt_renewed"),
    )
    release = ReleaseClaimMutation(
        **common,
        task_uid=TaskId("tsk_claimed"),
        attempt_id=None,
        claim_released_event_id=TaskEventId("evt_released"),
    )
    progress = ReportTaskProgressMutation(
        **common,
        task_uid=TaskId("tsk_claimed"),
        attempt_id=_ATTEMPT_ID,
        progress=TaskProgress(
            message="Working",
            observations=(ProgressObservation(ObservationKind.NOTE, "Tests added"),),
        ),
        progress_reported_event_id=TaskEventId("evt_progress"),
        observation_event_ids=(TaskEventId("evt_observation"),),
    )
    submission = SubmitAgentResultMutation(
        **common,
        task_uid=TaskId("tsk_claimed"),
        expected_version=4,
        attempt_id=_ATTEMPT_ID,
        result_id=ResultId("res_agent"),
        result_submitted_event_id=TaskEventId("evt_submitted"),
        task_completed_event_id=TaskEventId("evt_completed"),
        result=TaskResultInput(summary="Completed"),
    )

    assert human.lease_duration_seconds == 28_800
    assert agent.attempt_id == _ATTEMPT_ID
    assert renewal.attempt_id == _ATTEMPT_ID
    assert release.attempt_id is None
    assert progress.observation_event_ids == (TaskEventId("evt_observation"),)
    assert submission.expected_version == 4


@pytest.mark.parametrize(
    ("mutation_type", "changes"),
    [
        (ClaimTaskMutation, {"lease_duration_seconds": 59}),
        (ClaimTaskMutation, {"lease_duration_seconds": 2_592_001}),
        (ClaimTaskMutation, {"lease_duration_seconds": True}),
        (ClaimNextTaskMutation, {"lease_duration_seconds": 0}),
        (ClaimNextTaskMutation, {"lease_duration_seconds": 86_401}),
        (ClaimNextTaskMutation, {"attempt_id": "atm_current"}),
        (RenewClaimMutation, {"lease_duration_seconds": 0}),
        (RenewClaimMutation, {"occurred_at": _NOW.replace(tzinfo=None)}),
    ],
)
def test_claim_mutations_reject_invalid_duration_identity_and_time(
    mutation_type: type[object],
    changes: dict[str, object],
) -> None:
    """Strict commands reject coercion and owner-specific Lease violations."""
    common = _base_mutation()
    defaults: dict[type[object], dict[str, object]] = {
        ClaimTaskMutation: {
            "task_uid": TaskId("tsk_claimed"),
            "lease_duration_seconds": 28_800,
            "task_claimed_event_id": TaskEventId("evt_claimed"),
            "claim_expired_event_id": TaskEventId("evt_expired"),
        },
        ClaimNextTaskMutation: {
            "attempt_id": _ATTEMPT_ID,
            "lease_duration_seconds": 900,
            "task_claimed_event_id": TaskEventId("evt_claimed"),
            "claim_expired_event_id": TaskEventId("evt_expired"),
        },
        RenewClaimMutation: {
            "task_uid": TaskId("tsk_claimed"),
            "attempt_id": _ATTEMPT_ID,
            "lease_duration_seconds": 900,
            "claim_renewed_event_id": TaskEventId("evt_renewed"),
        },
    }
    values = {**common, **defaults[mutation_type], **changes}

    with pytest.raises(ValidationError):
        mutation_type.model_validate(values)  # type: ignore[attr-defined]


def test_phase_four_mutations_reject_unknown_and_reused_event_fields() -> None:
    """Closed commands never accept forged fields or ambiguous event identity."""
    common = _base_mutation()
    with pytest.raises(ValidationError, match="Extra inputs"):
        ClaimTaskMutation.model_validate(
            {
                **common,
                "task_uid": TaskId("tsk_claimed"),
                "lease_duration_seconds": 28_800,
                "task_claimed_event_id": TaskEventId("evt_claimed"),
                "claim_expired_event_id": TaskEventId("evt_expired"),
                "database_path": "/unsafe/local.db",
            }
        )
    with pytest.raises(ValidationError, match="distinct"):
        ClaimNextTaskMutation(
            **common,
            attempt_id=_ATTEMPT_ID,
            lease_duration_seconds=900,
            task_claimed_event_id=TaskEventId("evt_same"),
            claim_expired_event_id=TaskEventId("evt_same"),
        )
    with pytest.raises(ValidationError, match="distinct"):
        SubmitAgentResultMutation(
            **common,
            task_uid=TaskId("tsk_claimed"),
            expected_version=4,
            attempt_id=_ATTEMPT_ID,
            result_id=ResultId("res_agent"),
            result_submitted_event_id=TaskEventId("evt_same"),
            task_completed_event_id=TaskEventId("evt_same"),
            result=TaskResultInput(),
        )


def test_progress_mutation_binds_exact_ordered_event_identity_count() -> None:
    """Progress cannot lose, add, or alias an observation event identity."""
    common = _base_mutation()
    progress = TaskProgress(
        observations=(
            ProgressObservation(ObservationKind.NOTE, "One"),
            ProgressObservation(ObservationKind.RISK, "Two"),
        )
    )
    base = {
        **common,
        "task_uid": TaskId("tsk_claimed"),
        "attempt_id": _ATTEMPT_ID,
        "progress": progress,
        "progress_reported_event_id": TaskEventId("evt_progress"),
    }
    with pytest.raises(ValidationError, match="one-for-one"):
        ReportTaskProgressMutation.model_validate(
            {**base, "observation_event_ids": (TaskEventId("evt_one"),)}
        )
    with pytest.raises(ValidationError, match="distinct"):
        ReportTaskProgressMutation.model_validate(
            {
                **base,
                "observation_event_ids": (
                    TaskEventId("evt_same"),
                    TaskEventId("evt_same"),
                ),
            }
        )


def test_task_details_return_only_consistent_current_claim_ownership() -> None:
    """Task details align active ownership with running readiness."""
    task = _task()
    running = TaskReadiness(
        ready=False,
        running=True,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=(ReadinessReason.ACTIVE_CLAIM,),
    )
    details = TaskDetails(
        task=task,
        readiness=running,
        prerequisites=(),
        current_result=None,
        claim=_claim(),
        attempt=_attempt(),
    )

    assert details.claim == _claim()
    with pytest.raises(ValidationError, match="running"):
        TaskDetails(
            task=task,
            readiness=running,
            prerequisites=(),
            current_result=None,
        )
    with pytest.raises(ValidationError, match="Task identities"):
        TaskDetails(
            task=task,
            readiness=running,
            prerequisites=(),
            current_result=None,
            claim=replace(details.claim, task_uid=TaskId("tsk_other")),
            attempt=_attempt(),
        )


def test_claim_result_accepts_claim_noop_reclaim_and_release_shapes() -> None:
    """Claim results allow only accepted ordered Claim lifecycle sequences."""
    task = _task()
    current = TaskClaimResult(
        task=task,
        claim=_claim(),
        attempt=_attempt(),
        events=(_event(TaskEventType.TASK_CLAIMED),),
    )
    noop = TaskClaimResult(
        task=task,
        claim=_claim(attempt_id=None),
        attempt=None,
        events=(),
    )
    released_attempt = _attempt(
        status=AttemptStatus.RELEASED,
        ended_at=_NOW,
    )
    released = TaskClaimResult(
        task=task,
        claim=None,
        attempt=released_attempt,
        events=(_event(TaskEventType.CLAIM_RELEASED),),
    )

    assert current.attempt == _attempt()
    assert noop.events == ()
    assert released.attempt is not None
    with pytest.raises(ValidationError, match="invalid event sequence"):
        TaskClaimResult(
            task=task,
            claim=_claim(),
            attempt=_attempt(),
            events=(_event(TaskEventType.PROGRESS_REPORTED),),
        )
    with pytest.raises(ValidationError, match="owner attribution"):
        TaskClaimResult(
            task=task,
            claim=_claim(),
            attempt=_attempt(),
            events=(
                replace(
                    _event(TaskEventType.TASK_CLAIMED),
                    actor_subject_id=SubjectId("sub_other"),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="claim_released"):
        TaskClaimResult(task=task, claim=None, attempt=None, events=())


def test_progress_result_requires_active_agent_and_ordered_event_batch() -> None:
    """Progress results reject Human ownership and malformed event ordering."""
    result = TaskProgressResult(
        task=_task(),
        claim=_claim(),
        attempt=_attempt(),
        events=(
            _event(TaskEventType.PROGRESS_REPORTED, cursor=1),
            _event(TaskEventType.OBSERVATION_ADDED, cursor=2),
        ),
    )

    assert len(result.events) == 2
    with pytest.raises(ValidationError, match="begin"):
        TaskProgressResult(
            task=_task(),
            claim=_claim(),
            attempt=_attempt(),
            events=(_event(TaskEventType.OBSERVATION_ADDED),),
        )
    with pytest.raises(ValidationError, match="Human Claim"):
        TaskProgressResult(
            task=_task(),
            claim=_claim(attempt_id=None),
            attempt=_attempt(),
            events=(_event(TaskEventType.PROGRESS_REPORTED),),
        )
    with pytest.raises(ValidationError, match="Attempt attribution"):
        TaskProgressResult(
            task=_task(),
            claim=_claim(),
            attempt=_attempt(),
            events=(
                replace(
                    _event(TaskEventType.PROGRESS_REPORTED),
                    occurred_at=_NOW + timedelta(minutes=14),
                ),
            ),
        )


def test_agent_submission_result_requires_matching_terminal_attempt() -> None:
    """Agent Result, submission events, and terminal Attempt agree exactly."""
    result_id = ResultId("res_agent")
    task = _task(
        state=TaskState.DONE,
        version=5,
        updated_at=_NOW,
        current_result_id=result_id,
    )
    result = TaskResult(
        id=result_id,
        task_uid=task.uid,
        submitted_by=_SUBJECT_ID,
        attempt_id=_ATTEMPT_ID,
        submitted_at=_NOW,
        comment=None,
        summary="Completed",
        criteria=(),
        artifacts=(),
        proposed_follow_ups=(),
        review=ResultReview(status=ResultReviewStatus.NOT_REQUIRED),
    )
    attempt = _attempt(status=AttemptStatus.SUBMITTED, ended_at=_NOW)
    events = (
        _event(TaskEventType.RESULT_SUBMITTED, cursor=1),
        _event(TaskEventType.TASK_COMPLETED, cursor=2),
    )
    submission = TaskSubmissionResult(
        task=task,
        result=result,
        events=events,
        attempt=attempt,
    )

    assert submission.attempt == attempt
    with pytest.raises(ValidationError, match="must return"):
        TaskSubmissionResult(task=task, result=result, events=events)


def test_claim_execution_port_exposes_only_semantic_operations() -> None:
    """Adapters receive explicit use cases instead of CRUD or driver handles."""
    methods = {
        "claim_task",
        "claim_next_task",
        "renew_claim",
        "release_claim",
        "report_task_progress",
        "submit_agent_result",
    }
    prohibited = {"save", "update", "delete", "execute", "cursor", "transaction"}

    assert methods <= set(dir(ClaimExecutionRepository))
    assert methods <= set(dir(WorkaholicRepository))
    assert prohibited.isdisjoint(dir(ClaimExecutionRepository))
    for method in methods:
        signature = inspect.signature(getattr(ClaimExecutionRepository, method))
        assert tuple(signature.parameters) == ("self", "mutation")


def _accept_claim_repository(
    repository: ClaimExecutionRepository,
) -> ClaimExecutionRepository:
    """Type-check one adapter-neutral Claim repository fake.

    Args:
        repository: Structurally compatible semantic repository.

    Returns:
        The same typed repository.

    """
    return repository


def _unimplemented() -> NoReturn:
    """Fail if a type-only fake operation is called."""
    message = "Type-only Phase 4 repository fake has no behavior."
    raise NotImplementedError(message)


class _ClaimRepositoryFake:
    """Statically prove the Phase 4 repository surface is adapter-neutral."""

    def claim_task(self, mutation: ClaimTaskMutation) -> TaskClaimResult:
        """Satisfy targeted Human Claim persistence."""
        del mutation
        return _unimplemented()

    def claim_next_task(self, mutation: ClaimNextTaskMutation) -> TaskClaimResult:
        """Satisfy Agent pull persistence."""
        del mutation
        return _unimplemented()

    def renew_claim(self, mutation: RenewClaimMutation) -> TaskClaimResult:
        """Satisfy shared renewal persistence."""
        del mutation
        return _unimplemented()

    def release_claim(self, mutation: ReleaseClaimMutation) -> TaskClaimResult:
        """Satisfy shared release persistence."""
        del mutation
        return _unimplemented()

    def report_task_progress(
        self,
        mutation: ReportTaskProgressMutation,
    ) -> TaskProgressResult:
        """Satisfy Agent progress persistence."""
        del mutation
        return _unimplemented()

    def submit_agent_result(
        self,
        mutation: SubmitAgentResultMutation,
    ) -> TaskSubmissionResult:
        """Satisfy Agent Result persistence."""
        del mutation
        return _unimplemented()


def test_fake_repository_type_checks_without_adapter_imports() -> None:
    """A plain fake structurally satisfies the application-owned Phase 4 port."""
    fake = _ClaimRepositoryFake()

    assert _accept_claim_repository(fake) is fake
