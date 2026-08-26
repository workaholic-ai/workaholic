"""Exhaustive unit tests for Phase 3 lifecycle and readiness rules."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    AttemptId,
    CriterionOutcome,
    CriterionStatus,
    DomainValidationError,
    ProjectId,
    ReadinessReason,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskClaim,
    TaskId,
    TaskOperationalView,
    TaskReadiness,
    TaskResult,
    TaskState,
    TaskTransition,
    derive_task_readiness,
    ready_task_ordering_key,
    transition_task_state,
    validate_dependency_addition,
    validate_dependency_removal,
    validate_human_submission,
    validate_task_result_consistency,
)

_NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_main")


def _task(
    *,
    suffix: str = "main",
    number: int = 1,
    state: TaskState = TaskState.OPEN,
    project_id: ProjectId = _PROJECT_ID,
) -> Task:
    """Build one valid Phase 3 Task projection.

    Args:
        suffix: Opaque Task ID suffix.
        number: Project-local Task number.
        state: Initial lifecycle state.
        project_id: Owning Project identity.

    Returns:
        A valid immutable Task.

    """
    return Task(
        uid=TaskId(f"tsk_{suffix}"),
        project_id=project_id,
        number=number,
        key=f"PRJ-{number}",
        title=f"Task {number}",
        objective="Produce the requested outcome.",
        state=state,
        priority=50,
        version=1,
        created_by=SubjectId("sub_human"),
        created_at=_NOW,
        updated_at=_NOW,
        blocking_reason="Paused" if state is TaskState.BLOCKED else None,
    )


def _result(
    task: Task,
    *,
    criterion_id: str = "ac_done",
    attempt_id: AttemptId | None = None,
) -> TaskResult:
    """Build one Human Result attributed to a Task.

    Args:
        task: Task receiving the Result.
        criterion_id: Criterion identity reported by the Result.
        attempt_id: Optional Agent Attempt identity for negative tests.

    Returns:
        A valid structured Task Result.

    """
    return TaskResult(
        id=ResultId("res_submission"),
        task_uid=task.uid,
        submitted_by=SubjectId("sub_human"),
        attempt_id=attempt_id,
        submitted_at=_NOW,
        comment=None,
        summary=None,
        criteria=(
            CriterionOutcome(
                criterion_id=criterion_id,
                status=CriterionStatus.PASSED,
            ),
        ),
        artifacts=(),
        proposed_follow_ups=(),
        review=ResultReview(status=ResultReviewStatus.NOT_REQUIRED),
    )


_STATIC_TRANSITIONS: dict[tuple[TaskState, TaskTransition], TaskState] = {
    (TaskState.OPEN, TaskTransition.UPDATE): TaskState.OPEN,
    (TaskState.BLOCKED, TaskTransition.UPDATE): TaskState.BLOCKED,
    (TaskState.OPEN, TaskTransition.BLOCK): TaskState.BLOCKED,
    (TaskState.BLOCKED, TaskTransition.UNBLOCK): TaskState.OPEN,
    (TaskState.OPEN, TaskTransition.CANCEL): TaskState.CANCELLED,
    (TaskState.BLOCKED, TaskTransition.CANCEL): TaskState.CANCELLED,
    (TaskState.REVIEW, TaskTransition.CANCEL): TaskState.CANCELLED,
    (TaskState.OPEN, TaskTransition.ADD_DEPENDENCY): TaskState.OPEN,
    (TaskState.BLOCKED, TaskTransition.ADD_DEPENDENCY): TaskState.BLOCKED,
    (TaskState.OPEN, TaskTransition.REMOVE_DEPENDENCY): TaskState.OPEN,
    (TaskState.BLOCKED, TaskTransition.REMOVE_DEPENDENCY): TaskState.BLOCKED,
    (TaskState.REVIEW, TaskTransition.APPROVE): TaskState.DONE,
    (TaskState.REVIEW, TaskTransition.REJECT): TaskState.OPEN,
}


@pytest.mark.parametrize(
    ("state", "transition"),
    tuple(product(TaskState, TaskTransition)),
)
def test_every_phase_three_transition_is_explicitly_allowed_or_rejected(
    state: TaskState,
    transition: TaskTransition,
) -> None:
    """The lifecycle matrix has no implicit or untested state-operation pair."""
    if transition is TaskTransition.SUBMIT and state is TaskState.OPEN:
        assert (
            transition_task_state(
                state,
                transition,
                approval=ApprovalRequirement.NONE,
            )
            is TaskState.DONE
        )
        assert (
            transition_task_state(
                state,
                transition,
                approval=ApprovalRequirement.HUMAN,
            )
            is TaskState.REVIEW
        )
        return

    expected = _STATIC_TRANSITIONS.get((state, transition))
    if expected is None:
        with pytest.raises(DomainValidationError, match="cannot perform"):
            transition_task_state(state, transition)
    else:
        assert transition_task_state(state, transition) is expected


@pytest.mark.parametrize(
    ("state", "transition"),
    tuple(product((TaskState.DONE, TaskState.CANCELLED), TaskTransition)),
)
def test_terminal_states_reject_every_mutation(
    state: TaskState,
    transition: TaskTransition,
) -> None:
    """Done and cancelled Tasks are terminal for all semantic operations."""
    with pytest.raises(DomainValidationError, match="cannot perform"):
        transition_task_state(state, transition)


@pytest.mark.parametrize(
    ("state", "transition", "approval", "message"),
    [
        ("open", TaskTransition.UPDATE, ApprovalRequirement.NONE, "Current state"),
        (TaskState.OPEN, "update", ApprovalRequirement.NONE, "Transition"),
        (TaskState.OPEN, TaskTransition.UPDATE, "none", "Approval"),
    ],
)
def test_transition_rule_rejects_unvalidated_enum_values(
    state: object,
    transition: object,
    approval: object,
    message: str,
) -> None:
    """Runtime enum validation does not rely on static annotations alone."""
    with pytest.raises(DomainValidationError, match=message):
        transition_task_state(state, transition, approval=approval)


def test_dependency_addition_accepts_same_project_acyclic_edge() -> None:
    """An open Task may add one absent same-Project prerequisite."""
    dependant = _task()
    prerequisite = _task(suffix="prerequisite", number=2)

    validate_dependency_addition(
        dependant=dependant,
        prerequisite=prerequisite,
        dependency_graph={dependant.uid: (), prerequisite.uid: ()},
    )


@pytest.mark.parametrize(
    "state",
    [TaskState.REVIEW, TaskState.DONE, TaskState.CANCELLED],
)
def test_dependency_addition_rejects_noneditable_dependant(state: TaskState) -> None:
    """Only open or blocked dependant Tasks may change dependency edges."""
    dependant = _task(state=state)
    prerequisite = _task(suffix="prerequisite", number=2)

    with pytest.raises(DomainValidationError, match="cannot perform"):
        validate_dependency_addition(
            dependant=dependant,
            prerequisite=prerequisite,
            dependency_graph={},
        )


def test_dependency_addition_rejects_self_duplicate_foreign_and_cycle_edges() -> None:
    """Dependency graph validation rejects every documented conflict category."""
    dependant = _task()
    prerequisite = _task(suffix="prerequisite", number=2)
    foreign = _task(
        suffix="foreign",
        number=3,
        project_id=ProjectId("prj_foreign"),
    )

    with pytest.raises(DomainValidationError, match="itself"):
        validate_dependency_addition(
            dependant=dependant,
            prerequisite=dependant,
            dependency_graph={},
        )
    with pytest.raises(DomainValidationError, match="same Project"):
        validate_dependency_addition(
            dependant=dependant,
            prerequisite=foreign,
            dependency_graph={},
        )
    with pytest.raises(DomainValidationError, match="already exists"):
        validate_dependency_addition(
            dependant=replace(dependant, depends_on=(prerequisite.uid,)),
            prerequisite=prerequisite,
            dependency_graph={dependant.uid: ()},
        )
    with pytest.raises(DomainValidationError, match="cycle"):
        validate_dependency_addition(
            dependant=dependant,
            prerequisite=prerequisite,
            dependency_graph={prerequisite.uid: (dependant.uid,)},
        )


@pytest.mark.parametrize(
    "graph",
    [
        [],
        {"tsk_main": ()},
        {TaskId("tsk_main"): ("tsk_other",)},
        {TaskId("tsk_main"): (TaskId("tsk_other"), TaskId("tsk_other"))},
    ],
)
def test_dependency_addition_rejects_invalid_graph_shapes(graph: object) -> None:
    """Cycle checks validate graph identities and adjacency collections at runtime."""
    with pytest.raises(DomainValidationError, match="Dependency graph"):
        validate_dependency_addition(
            dependant=_task(),
            prerequisite=_task(suffix="prerequisite", number=2),
            dependency_graph=graph,  # type: ignore[arg-type]
        )


def test_dependency_removal_accepts_existing_edge_and_rejects_conflicts() -> None:
    """Removal requires an editable dependant and an existing same-Project edge."""
    prerequisite = _task(suffix="prerequisite", number=2)
    dependant = replace(_task(), depends_on=(prerequisite.uid,))

    validate_dependency_removal(dependant=dependant, prerequisite=prerequisite)
    with pytest.raises(DomainValidationError, match="does not exist"):
        validate_dependency_removal(
            dependant=_task(),
            prerequisite=prerequisite,
        )
    with pytest.raises(DomainValidationError, match="same Project"):
        validate_dependency_removal(
            dependant=dependant,
            prerequisite=_task(
                suffix="foreign",
                number=2,
                project_id=ProjectId("prj_foreign"),
            ),
        )


def test_ready_task_has_no_reasons_and_available_boundary_is_inclusive() -> None:
    """An open Task becomes ready exactly at its authoritative availability time."""
    readiness = derive_task_readiness(
        task=replace(_task(), available_at=_NOW),
        prerequisites=(),
        now=_NOW,
    )

    assert readiness == TaskReadiness(
        ready=True,
        running=False,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=(),
    )
    assert readiness.includes(TaskOperationalView.READY)


def test_future_availability_schedules_but_does_not_enable_task() -> None:
    """A future available_at produces one availability reason and scheduled view."""
    readiness = derive_task_readiness(
        task=replace(_task(), available_at=_NOW + timedelta(seconds=1)),
        prerequisites=(),
        now=_NOW,
    )

    assert not readiness.ready
    assert readiness.scheduled
    assert readiness.reasons == (ReadinessReason.NOT_YET_AVAILABLE,)


@pytest.mark.parametrize(
    ("state", "reason", "awaiting_review"),
    [
        (TaskState.BLOCKED, ReadinessReason.TASK_BLOCKED, False),
        (TaskState.REVIEW, ReadinessReason.TASK_AWAITING_REVIEW, True),
        (TaskState.DONE, ReadinessReason.TASK_DONE, False),
        (TaskState.CANCELLED, ReadinessReason.TASK_CANCELLED, False),
    ],
)
def test_nonopen_states_have_stable_readiness_reasons(
    state: TaskState,
    reason: ReadinessReason,
    awaiting_review: object,
) -> None:
    """Stored non-open states map to deterministic derived readiness values."""
    readiness = derive_task_readiness(
        task=_task(state=state),
        prerequisites=(),
        now=_NOW,
    )

    assert readiness.reasons == (reason,)
    assert readiness.awaiting_review is awaiting_review


def test_dependency_reasons_order_by_task_key_and_distinguish_cancelled() -> None:
    """Prerequisite reasons are stable and cancelled prerequisites are unsatisfiable."""
    later_key = _task(suffix="later", number=3, state=TaskState.CANCELLED)
    earlier_key = _task(suffix="earlier", number=2)
    done = _task(suffix="done", number=4, state=TaskState.DONE)
    task = replace(
        _task(),
        depends_on=(later_key.uid, done.uid, earlier_key.uid),
    )

    readiness = derive_task_readiness(
        task=task,
        prerequisites=(later_key, done, earlier_key),
        now=_NOW,
    )

    assert readiness.reasons == (
        ReadinessReason.UNSATISFIED_DEPENDENCY,
        ReadinessReason.UNSATISFIABLE_DEPENDENCY,
    )


@pytest.mark.parametrize(
    ("expires_at", "reason", "view"),
    [
        (
            _NOW + timedelta(seconds=1),
            ReadinessReason.ACTIVE_CLAIM,
            TaskOperationalView.RUNNING,
        ),
        (
            _NOW,
            ReadinessReason.STALE_CLAIM,
            TaskOperationalView.STALE,
        ),
    ],
)
def test_claim_derives_running_or_stale_operational_views(
    expires_at: datetime,
    reason: ReadinessReason,
    view: TaskOperationalView,
) -> None:
    """One explicit Claim maps to running or stale views without I/O."""
    task = _task()
    readiness = derive_task_readiness(
        task=task,
        prerequisites=(),
        now=_NOW,
        claim=TaskClaim(
            task_uid=task.uid,
            task_key=task.key,
            subject_id=SubjectId("sub_human"),
            attempt_id=None,
            claimed_at=_NOW - timedelta(minutes=1),
            lease_expires_at=expires_at,
        ),
    )

    assert readiness.reasons == (reason,)
    assert readiness.includes(view)
    assert readiness.ready is (reason is ReadinessReason.STALE_CLAIM)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"now": _NOW.replace(tzinfo=None)}, "Readiness time"),
        ({"claim": object()}, "Claim"),
    ],
)
def test_readiness_rejects_invalid_time_and_claim_projection(
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Readiness uses explicit UTC time and a validated Claim projection."""
    arguments: dict[str, object] = {
        "task": _task(),
        "prerequisites": (),
        "now": _NOW,
    }
    arguments.update(kwargs)
    with pytest.raises(DomainValidationError, match=message):
        derive_task_readiness(**arguments)  # type: ignore[arg-type]


def test_readiness_requires_exact_unique_same_project_prerequisite_projection() -> None:
    """Callers must supply complete authoritative prerequisite data."""
    prerequisite = _task(suffix="prerequisite", number=2)
    task = replace(_task(), depends_on=(prerequisite.uid,))
    foreign = replace(prerequisite, project_id=ProjectId("prj_foreign"))

    for values in ((), (prerequisite, prerequisite), (foreign,)):
        with pytest.raises(DomainValidationError):
            derive_task_readiness(task=task, prerequisites=values, now=_NOW)


def test_human_submission_ignores_availability_and_applies_approval() -> None:
    """A deliberate Human may submit future work when dependencies are done."""
    prerequisite = _task(suffix="prerequisite", number=2, state=TaskState.DONE)
    task = replace(
        _task(),
        available_at=_NOW + timedelta(days=1),
        approval=ApprovalRequirement.HUMAN,
        depends_on=(prerequisite.uid,),
        acceptance=(AcceptanceCriterion("ac_done", "Done", required=True),),
    )

    assert (
        validate_human_submission(
            task=task,
            prerequisites=(prerequisite,),
            result=_result(task),
        )
        is TaskState.REVIEW
    )
    assert (
        validate_human_submission(
            task=replace(task, approval=ApprovalRequirement.NONE),
            prerequisites=(prerequisite,),
            result=_result(task),
        )
        is TaskState.DONE
    )


def test_human_submission_rejects_unfinished_dependency_and_agent_attempt() -> None:
    """Human submission requires done prerequisites and null Attempt attribution."""
    prerequisite = _task(suffix="prerequisite", number=2)
    task = replace(
        _task(),
        depends_on=(prerequisite.uid,),
        acceptance=(AcceptanceCriterion("ac_done", "Done", required=True),),
    )

    with pytest.raises(DomainValidationError, match="prerequisite"):
        validate_human_submission(
            task=task,
            prerequisites=(prerequisite,),
            result=_result(task),
        )
    with pytest.raises(DomainValidationError, match="Attempt"):
        validate_human_submission(
            task=replace(task, depends_on=()),
            prerequisites=(),
            result=_result(task, attempt_id=AttemptId("atm_agent")),
        )


def test_result_consistency_rejects_wrong_task_unknown_and_missing_criteria() -> None:
    """Result attribution must exactly match Task identity and acceptance contracts."""
    task = replace(
        _task(),
        acceptance=(
            AcceptanceCriterion("ac_done", "Done", required=True),
            AcceptanceCriterion("ac_optional", "Optional", required=False),
        ),
    )
    wrong_task = replace(_result(task), task_uid=TaskId("tsk_other"))
    unknown = _result(task, criterion_id="ac_unknown")
    missing = replace(_result(task), criteria=())

    for result in (wrong_task, unknown, missing):
        with pytest.raises(DomainValidationError):
            validate_task_result_consistency(
                task=task,
                result=result,
                human_submission=True,
            )
    validate_task_result_consistency(
        task=task,
        result=_result(task),
        human_submission=True,
    )


def test_ready_ordering_is_priority_availability_project_and_number_stable() -> None:
    """Ready-view keys implement the exact null-first deterministic ordering."""
    low_priority = replace(_task(number=1), priority=10)
    high_scheduled = replace(
        _task(suffix="scheduled", number=2),
        priority=90,
        available_at=_NOW + timedelta(hours=2),
    )
    high_unscheduled = replace(
        _task(suffix="unscheduled", number=3),
        priority=90,
    )

    ordered = sorted(
        (low_priority, high_scheduled, high_unscheduled),
        key=ready_task_ordering_key,
    )
    assert ordered == [high_unscheduled, high_scheduled, low_priority]
    assert ready_task_ordering_key(high_unscheduled, project_key="PRJ")[-2:] == (
        "PRJ",
        3,
    )
    with pytest.raises(DomainValidationError, match="Project key"):
        ready_task_ordering_key(high_unscheduled, project_key="bad")


def test_task_readiness_is_immutable_and_runtime_validated() -> None:
    """Readiness projections defensively copy reasons and reject fake bools/views."""
    source = [ReadinessReason.TASK_BLOCKED]
    readiness = TaskReadiness(
        ready=False,
        running=False,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=source,  # type: ignore[arg-type]
    )
    source.clear()

    assert readiness.reasons == (ReadinessReason.TASK_BLOCKED,)
    with pytest.raises(FrozenInstanceError):
        readiness.ready = True  # type: ignore[misc]
    with pytest.raises(DomainValidationError, match="boolean"):
        replace(readiness, ready=1)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="reasons"):
        replace(readiness, reasons=("task_blocked",))  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="view"):
        readiness.includes("ready")
