"""Integration tests for atomic SQLite Agent Result submission semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from workaholic.application import (
    ApproveResultMutation,
    BootstrapMutation,
    ClaimNextTaskMutation,
    IdempotencyConflictError,
    InvalidTransitionError,
    LeaseLostError,
    ReadTaskEvents,
    RejectResultMutation,
    ReleaseClaimMutation,
    ResultInvalidError,
    SubmitAgentResultMutation,
    TaskCreationMutation,
    TaskResultInput,
    UnsatisfiableDependencyError,
    VersionConflictError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    AttemptId,
    AttemptStatus,
    CriterionOutcome,
    CriterionStatus,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite._records import serialize_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import TaskClaimResult

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 24, 9, tzinfo=UTC)
_CLAIMED_AT = datetime(2026, 8, 24, 10, tzinfo=UTC)
_SUBMITTED_AT = datetime(2026, 8, 24, 10, 5, tzinfo=UTC)
_REVIEWED_AT = datetime(2026, 8, 24, 10, 6, tzinfo=UTC)
_RECLAIMED_AT = datetime(2026, 8, 24, 10, 7, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_agent_results")
_SUBJECT_ID = SubjectId("sub_local")
_ATTEMPT_ID = AttemptId("atm_agent_result")
_NEW_ATTEMPT_ID = AttemptId("atm_agent_reclaimed")
_REQUIRED_ACCEPTANCE = (
    AcceptanceCriterion("ac_done", "Implementation is complete.", required=True),
)


class _Clock:
    """Return one fixed time inside the initial Agent Lease."""

    def now(self) -> datetime:
        """Return the fixed authoritative query time.

        Returns:
            UTC time used by read projections.

        """
        return _SUBMITTED_AT


def _repository(
    tmp_path: Path,
    *,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    acceptance: tuple[AcceptanceCriterion, ...] = (),
) -> tuple[SQLiteRepository, Task]:
    """Bootstrap one Project and Agent submission target.

    Args:
        tmp_path: Isolated test directory.
        approval: Target approval requirement.
        acceptance: Target acceptance definition.

    Returns:
        Initialized repository and authoritative open Task.

    """
    repository = SQLiteRepository(tmp_path / "local.db", clock=_Clock())
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_agent_results"),
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            request_id=RequestId("req_bootstrap_agent_results"),
            occurred_at=_CREATED_AT - timedelta(minutes=1),
            project_key="ACME",
        )
    )
    task = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_agent_result"),
            event_id=TaskEventId("evt_create_agent_result"),
            request_id=RequestId("req_create_agent_result"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT,
            title="Submit Agent work",
            objective="Persist and review one attributable Agent Result.",
            priority=60,
            approval=approval,
            acceptance=acceptance,
        )
    )
    return repository, task


def _claim_agent(
    repository: SQLiteRepository,
    *,
    attempt_id: AttemptId = _ATTEMPT_ID,
    claimed_at: datetime = _CLAIMED_AT,
    duration: int = 900,
    suffix: str = "initial",
) -> TaskClaimResult:
    """Claim the highest-ranked ready Task through one Agent Attempt.

    Args:
        repository: Initialized repository.
        attempt_id: Generated Agent owner token.
        claimed_at: Authoritative acquisition time.
        duration: Agent Lease duration in seconds.
        suffix: Generated request and event identity suffix.

    Returns:
        Active Agent Claim and Attempt.

    """
    return repository.claim_next_task(
        ClaimNextTaskMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            request_id=RequestId(f"req_claim_agent_{suffix}"),
            occurred_at=claimed_at,
            attempt_id=attempt_id,
            lease_duration_seconds=duration,
            task_claimed_event_id=TaskEventId(f"evt_claim_agent_{suffix}"),
            claim_expired_event_id=TaskEventId(f"evt_expire_agent_{suffix}"),
        )
    )


def _structured_result() -> TaskResultInput:
    """Build complete bounded Agent Result content.

    Returns:
        Valid Result body satisfying the required acceptance criterion.

    """
    return TaskResultInput(
        summary="Implemented and verified the requested behavior.",
        criteria=(
            CriterionOutcome(
                criterion_id="ac_done",
                status=CriterionStatus.PASSED,
                evidence="Focused and regression suites pass.",
            ),
        ),
        artifacts=(
            ArtifactReference(
                uri="workspace://repo/reports/agent-result.md",
                media_type="text/markdown",
                sha256="a" * 64,
            ),
        ),
    )


def _submit(  # noqa: PLR0913 - exact optimistic mutation fixture boundary.
    task: Task,
    *,
    suffix: str = "submit",
    attempt_id: AttemptId = _ATTEMPT_ID,
    expected_version: int | None = None,
    occurred_at: datetime = _SUBMITTED_AT,
    result: TaskResultInput | None = None,
    idempotency_key: str | None = None,
    submitted_event_id: TaskEventId | None = None,
    completed_event_id: TaskEventId | None = None,
) -> SubmitAgentResultMutation:
    """Build one exact Agent Result submission mutation.

    Args:
        task: Claimed target Task snapshot.
        suffix: Generated identity suffix.
        attempt_id: Exact current Agent owner token.
        expected_version: Optional optimistic version override.
        occurred_at: Authoritative submission time.
        result: Optional structured Result body.
        idempotency_key: Optional caller replay key.
        submitted_event_id: Optional submission event identity override.
        completed_event_id: Optional completion event identity override.

    Returns:
        Validated Agent submission mutation.

    """
    completion = completed_event_id
    if completion is None and task.approval is ApprovalRequirement.NONE:
        completion = TaskEventId(f"evt_agent_completed_{suffix}")
    return SubmitAgentResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId(f"req_agent_submit_{suffix}"),
        occurred_at=occurred_at,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        attempt_id=attempt_id,
        result_id=ResultId(f"res_agent_{suffix}"),
        result_submitted_event_id=(
            submitted_event_id
            if submitted_event_id is not None
            else TaskEventId(f"evt_agent_submitted_{suffix}")
        ),
        task_completed_event_id=completion,
        result=TaskResultInput() if result is None else result,
        idempotency_key=idempotency_key,
    )


def _approve(task: Task) -> ApproveResultMutation:
    """Build one exact Human approval mutation.

    Args:
        task: Task currently awaiting review.

    Returns:
        Validated approval mutation.

    """
    return ApproveResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_approve_agent_result"),
        occurred_at=_REVIEWED_AT,
        expected_version=task.version,
        review_approved_event_id=TaskEventId("evt_approve_agent_result"),
        task_completed_event_id=TaskEventId("evt_complete_agent_result_review"),
        comment="Agent evidence accepted.",
    )


def _reject(task: Task) -> RejectResultMutation:
    """Build one exact Human rejection mutation.

    Args:
        task: Task currently awaiting review.

    Returns:
        Validated rejection mutation.

    """
    return RejectResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=_SUBJECT_ID,
        request_id=RequestId("req_reject_agent_result"),
        occurred_at=_REVIEWED_AT,
        expected_version=task.version,
        review_rejected_event_id=TaskEventId("evt_reject_agent_result"),
        reason="Add stronger evidence.",
    )


def _snapshot(repository: SQLiteRepository) -> tuple[object, ...]:
    """Read all Agent-submission-owned state for rollback comparisons.

    Args:
        repository: Initialized SQLite repository.

    Returns:
        Stable Task, Result, Claim, Attempt, event, and replay rows.

    """
    with open_read_connection(repository.database_path) as connection:
        return (
            connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall(),
            connection.execute("SELECT * FROM task_results ORDER BY id").fetchall(),
            connection.execute(
                "SELECT * FROM task_claims ORDER BY task_uid"
            ).fetchall(),
            connection.execute("SELECT * FROM task_attempts ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM task_events ORDER BY cursor").fetchall(),
            connection.execute(
                "SELECT * FROM idempotency_records ORDER BY operation, caller_key"
            ).fetchall(),
            connection.execute(
                "SELECT * FROM task_dependencies ORDER BY task_uid, prerequisite_uid"
            ).fetchall(),
        )


def test_agent_submission_completes_and_persists_terminal_history(
    tmp_path: Path,
) -> None:
    """A no-review submission ends ownership and changes Task version once."""
    repository, task = _repository(tmp_path, acceptance=_REQUIRED_ACCEPTANCE)
    claimed = _claim_agent(repository)
    mutation = _submit(task, result=_structured_result())

    outcome = repository.submit_agent_result(mutation)

    assert outcome.task.state is TaskState.DONE
    assert outcome.task.version == task.version + 1
    assert outcome.task.updated_at == _SUBMITTED_AT
    assert outcome.task.current_result_id == outcome.result.id
    assert outcome.result.attempt_id == _ATTEMPT_ID
    assert outcome.result.submitted_by == _SUBJECT_ID
    assert outcome.result.comment is None
    assert outcome.result.summary == _structured_result().summary
    assert outcome.result.review.status is ResultReviewStatus.NOT_REQUIRED
    assert outcome.attempt is not None
    assert claimed.attempt is not None
    assert outcome.attempt.status is AttemptStatus.SUBMITTED
    assert outcome.attempt.ended_at == _SUBMITTED_AT
    assert outcome.attempt.lease_expires_at == claimed.attempt.lease_expires_at
    assert tuple(event.event_type for event in outcome.events) == (
        TaskEventType.RESULT_SUBMITTED,
        TaskEventType.TASK_COMPLETED,
    )
    assert all(event.attempt_id == _ATTEMPT_ID for event in outcome.events)
    assert tuple(dict(event.payload) for event in outcome.events) == (
        {
            "result_id": str(outcome.result.id),
            "review_status": "not_required",
            "version": task.version + 1,
        },
        {"result_id": str(outcome.result.id), "version": task.version + 1},
    )
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM task_claims").fetchone() == (0,)
        assert connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = ?",
            (str(_ATTEMPT_ID),),
        ).fetchone() == ("submitted", serialize_timestamp(_SUBMITTED_AT))
        assert connection.execute(
            "SELECT attempt_id FROM task_results WHERE id = ?",
            (str(outcome.result.id),),
        ).fetchone() == (str(_ATTEMPT_ID),)

    restarted = SQLiteRepository(repository.database_path, clock=_Clock())
    page = restarted.read_task_events_after(
        ReadTaskEvents(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=task.uid,
            after=outcome.events[0].cursor - 1,
            limit=10,
        )
    )
    assert tuple(event.id for event in page.events) == tuple(
        event.id for event in outcome.events
    )
    assert all(event.attempt_id == _ATTEMPT_ID for event in page.events)


def test_agent_review_approval_preserves_submitted_attempt(
    tmp_path: Path,
) -> None:
    """Human review changes Result disposition but never terminal Attempt state."""
    repository, task = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
        acceptance=_REQUIRED_ACCEPTANCE,
    )
    _claim_agent(repository)
    submitted = repository.submit_agent_result(
        _submit(task, result=_structured_result())
    )

    approved = repository.approve_result(_approve(submitted.task))

    assert submitted.task.state is TaskState.REVIEW
    assert submitted.result.review.status is ResultReviewStatus.PENDING
    assert submitted.attempt is not None
    assert submitted.attempt.status is AttemptStatus.SUBMITTED
    assert approved.task.state is TaskState.DONE
    assert approved.task.version == task.version + 2
    assert approved.result.attempt_id == _ATTEMPT_ID
    assert approved.result.review.status is ResultReviewStatus.APPROVED
    assert approved.attempt is None
    assert all(event.attempt_id is None for event in approved.events)
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = ?",
            (str(_ATTEMPT_ID),),
        ).fetchone() == ("submitted", serialize_timestamp(_SUBMITTED_AT))


def test_agent_rejection_reopens_for_a_new_attempt_only(tmp_path: Path) -> None:
    """Rejection retains terminal history and requires a fresh Claim Attempt."""
    repository, task = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    _claim_agent(repository)
    submitted = repository.submit_agent_result(_submit(task))

    rejected = repository.reject_result(_reject(submitted.task))
    reclaimed = _claim_agent(
        repository,
        attempt_id=_NEW_ATTEMPT_ID,
        claimed_at=_RECLAIMED_AT,
        suffix="reclaimed",
    )

    assert rejected.task.state is TaskState.OPEN
    assert rejected.task.current_result_id is None
    assert rejected.result.attempt_id == _ATTEMPT_ID
    assert rejected.result.review.status is ResultReviewStatus.REJECTED
    assert rejected.attempt is None
    assert reclaimed.attempt is not None
    assert reclaimed.attempt.id == _NEW_ATTEMPT_ID
    before = _snapshot(repository)
    with pytest.raises(LeaseLostError):
        repository.submit_agent_result(
            _submit(
                rejected.task,
                suffix="old_attempt",
                attempt_id=_ATTEMPT_ID,
                occurred_at=_RECLAIMED_AT + timedelta(seconds=1),
            )
        )
    assert _snapshot(repository) == before


@pytest.mark.parametrize("failure", ["version", "criteria", "transition"])
def test_failed_agent_submission_retains_active_ownership(
    failure: str,
    tmp_path: Path,
) -> None:
    """Version, Result, and transition failures leave Claim and Attempt active."""
    acceptance = _REQUIRED_ACCEPTANCE if failure == "criteria" else ()
    repository, task = _repository(tmp_path, acceptance=acceptance)
    claimed = _claim_agent(repository)
    if failure == "transition":
        with open_write_transaction(repository.database_path) as connection:
            connection.execute(
                """
                UPDATE tasks
                SET state = 'blocked', blocking_reason = 'Direct fixture block'
                WHERE uid = ?
                """,
                (str(task.uid),),
            )
    before = _snapshot(repository)
    mutation = _submit(
        task,
        suffix=failure,
        expected_version=task.version + 1 if failure == "version" else task.version,
    )
    expected_error = {
        "version": VersionConflictError,
        "criteria": ResultInvalidError,
        "transition": InvalidTransitionError,
    }[failure]

    with pytest.raises(expected_error):
        repository.submit_agent_result(mutation)

    assert _snapshot(repository) == before
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT attempt_id FROM task_claims WHERE task_uid = ?",
            (str(task.uid),),
        ).fetchone() == (str(_ATTEMPT_ID),)
        assert connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = ?",
            (str(_ATTEMPT_ID),),
        ).fetchone() == ("active", None)
    assert claimed.attempt is not None


@pytest.mark.parametrize("prerequisite_state", ["open", "cancelled"])
def test_agent_submission_revalidates_dependencies_inside_transaction(
    prerequisite_state: Literal["open", "cancelled"],
    tmp_path: Path,
) -> None:
    """A newly unfinished or cancelled prerequisite cannot consume ownership."""
    repository, task = _repository(tmp_path)
    prerequisite = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_agent_prerequisite"),
            event_id=TaskEventId("evt_create_agent_prerequisite"),
            request_id=RequestId("req_create_agent_prerequisite"),
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            title="Agent prerequisite",
            objective="Exercise submission dependency revalidation.",
            priority=10,
        )
    )
    _claim_agent(repository)
    with open_write_transaction(repository.database_path) as connection:
        if prerequisite_state == "cancelled":
            connection.execute(
                """
                UPDATE tasks
                SET state = 'cancelled', version = version + 1,
                    updated_at = ?, blocking_reason = NULL
                WHERE uid = ?
                """,
                (serialize_timestamp(_CLAIMED_AT), str(prerequisite.uid)),
            )
        connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (str(task.uid), str(prerequisite.uid), str(_PROJECT_ID)),
        )
    before = _snapshot(repository)
    expected_error = (
        UnsatisfiableDependencyError
        if prerequisite_state == "cancelled"
        else InvalidTransitionError
    )

    with pytest.raises(expected_error):
        repository.submit_agent_result(
            _submit(task, suffix=f"dependency_{prerequisite_state}")
        )

    assert _snapshot(repository) == before


@pytest.mark.parametrize("mode", ["foreign", "expired", "released", "superseded"])
def test_agent_submission_rejects_every_lost_lease_mode(
    mode: Literal["foreign", "expired", "released", "superseded"],
    tmp_path: Path,
) -> None:
    """Foreign, expired, released, and superseded Attempts all fail identically."""
    repository, task = _repository(tmp_path)
    duration = 300 if mode == "expired" else 900
    _claim_agent(repository, duration=duration)
    attempt_id = _ATTEMPT_ID
    occurred_at = _SUBMITTED_AT
    if mode == "foreign":
        attempt_id = AttemptId("atm_foreign_result")
    elif mode == "expired":
        occurred_at = _CLAIMED_AT + timedelta(seconds=duration)
    elif mode in ("released", "superseded"):
        repository.release_claim(
            ReleaseClaimMutation(
                project_id=_PROJECT_ID,
                task_uid=task.uid,
                actor_subject_id=_SUBJECT_ID,
                request_id=RequestId(f"req_release_{mode}"),
                occurred_at=_CLAIMED_AT + timedelta(minutes=1),
                attempt_id=_ATTEMPT_ID,
                claim_released_event_id=TaskEventId(f"evt_release_{mode}"),
            )
        )
        if mode == "superseded":
            _claim_agent(
                repository,
                attempt_id=_NEW_ATTEMPT_ID,
                claimed_at=_CLAIMED_AT + timedelta(minutes=2),
                suffix="superseding",
            )
            occurred_at = _CLAIMED_AT + timedelta(minutes=3)
    before = _snapshot(repository)

    with pytest.raises(LeaseLostError):
        repository.submit_agent_result(
            _submit(
                task,
                suffix=f"lost_{mode}",
                attempt_id=attempt_id,
                occurred_at=occurred_at,
                idempotency_key=f"lost-{mode}",
            )
        )

    assert _snapshot(repository) == before


def test_agent_submission_idempotency_replays_terminal_outcome(tmp_path: Path) -> None:
    """Equivalent replay survives Claim deletion while semantic key reuse conflicts."""
    repository, task = _repository(tmp_path)
    _claim_agent(repository)
    content = TaskResultInput(summary="Completed by Agent")
    original = repository.submit_agent_result(
        _submit(
            task,
            suffix="idempotent",
            result=content,
            idempotency_key="agent-submit-run",
        )
    )
    before_replay = _snapshot(repository)

    replay = repository.submit_agent_result(
        _submit(
            task,
            suffix="replay",
            occurred_at=_SUBMITTED_AT + timedelta(minutes=1),
            result=content,
            idempotency_key="agent-submit-run",
        )
    )

    assert replay == original
    assert _snapshot(repository) == before_replay
    with pytest.raises(IdempotencyConflictError):
        repository.submit_agent_result(
            _submit(
                task,
                suffix="conflict",
                result=content.model_copy(update={"summary": "Different Result"}),
                idempotency_key="agent-submit-run",
            )
        )
    assert _snapshot(repository) == before_replay


@pytest.mark.parametrize("collision_index", [0, 1])
def test_agent_submission_rolls_back_at_each_event_boundary(
    collision_index: int,
    tmp_path: Path,
) -> None:
    """Event collisions roll back Result, Attempt, Claim, Task, and replay writes."""
    repository, task = _repository(tmp_path)
    claimed = _claim_agent(repository)
    submitted_event = TaskEventId("evt_agent_submitted_rollback")
    completed_event = TaskEventId("evt_agent_completed_rollback")
    if collision_index == 0:
        submitted_event = TaskEventId("evt_create_agent_result")
    else:
        completed_event = TaskEventId("evt_create_agent_result")
    before = _snapshot(repository)

    with pytest.raises(StorageUnavailableError):
        repository.submit_agent_result(
            _submit(
                task,
                suffix=f"rollback_{collision_index}",
                idempotency_key=f"agent-rollback-{collision_index}",
                submitted_event_id=submitted_event,
                completed_event_id=completed_event,
            )
        )

    assert _snapshot(repository) == before
    assert claimed.attempt is not None
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = ?",
            (str(_ATTEMPT_ID),),
        ).fetchone() == ("active", None)
