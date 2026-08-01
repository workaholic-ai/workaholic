"""Unit tests for Phase 3 application boundary and repository contracts."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest
from pydantic import ValidationError

from workaholic.application import (
    AddTaskDependencyInput,
    AddTaskDependencyMutation,
    ApproveResultInput,
    ApproveResultMutation,
    BlockTaskInput,
    CancelTaskInput,
    GetTaskDetails,
    ListTasksByView,
    ReadTaskEvents,
    RejectResultInput,
    RejectResultMutation,
    RemoveTaskDependencyInput,
    RemoveTaskDependencyMutation,
    SubmitHumanResultInput,
    SubmitHumanResultMutation,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskCreationMutation,
    TaskDetails,
    TaskEventPage,
    TaskListView,
    TaskMutationResult,
    TaskPage,
    TaskRepository,
    TaskResultInput,
    TaskSubmissionResult,
    TaskUnblockMutation,
    TaskUpdateMutation,
    TaskUpdatePatch,
    UnblockTaskInput,
    UpdateTaskInput,
    WorkaholicRepository,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    ContextReference,
    CriterionOutcome,
    CriterionStatus,
    InstanceId,
    ProjectId,
    ProposedFollowUp,
    ReadinessReason,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskReadiness,
    TaskResult,
    TaskState,
)

_NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _task(  # noqa: PLR0913 - explicit Task fixture dimensions
    *,
    state: TaskState = TaskState.OPEN,
    current_result_id: ResultId | None = None,
    depends_on: tuple[TaskId, ...] = (),
    number: int = 1,
    priority: int = 50,
    available_at: datetime | None = None,
) -> Task:
    """Build one valid Phase 3 Task for boundary tests.

    Args:
        state: Persisted lifecycle state.
        current_result_id: Optional selected Result identity.
        depends_on: Ordered prerequisite identities.
        number: Project-local Task number.
        priority: Task scheduling priority.
        available_at: Optional scheduling boundary.

    Returns:
        A valid immutable Task.

    """
    return Task(
        uid=TaskId(f"tsk_{number}"),
        project_id=ProjectId("prj_acme"),
        number=number,
        key=f"ACME-{number}",
        title=f"Task {number}",
        objective="Complete the requested work.",
        state=state,
        priority=priority,
        version=2,
        created_by=SubjectId("sub_human"),
        created_at=_NOW,
        updated_at=_NOW,
        depends_on=depends_on,
        blocking_reason="Paused" if state is TaskState.BLOCKED else None,
        current_result_id=current_result_id,
        available_at=available_at,
    )


def _event(
    task: Task,
    event_type: TaskEventType,
    *,
    cursor: int,
    suffix: str | None = None,
) -> TaskEvent:
    """Build one attributable Task event.

    Args:
        task: Task affected by the event.
        event_type: Semantic event type.
        cursor: Instance event cursor.
        suffix: Optional event ID suffix.

    Returns:
        A valid immutable TaskEvent.

    """
    return TaskEvent(
        id=TaskEventId(f"evt_{suffix or cursor}"),
        cursor=cursor,
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_human"),
        request_id=RequestId("req_operation"),
        event_type=event_type,
        occurred_at=_NOW,
        payload={},
    )


def _result(task: Task, status: ResultReviewStatus) -> TaskResult:
    """Build one valid Task Result at a requested review disposition.

    Args:
        task: Owning Task.
        status: Review status to model.

    Returns:
        A valid immutable Result.

    """
    reviewed = status in (ResultReviewStatus.APPROVED, ResultReviewStatus.REJECTED)
    return TaskResult(
        id=ResultId("res_current"),
        task_uid=task.uid,
        submitted_by=SubjectId("sub_human"),
        attempt_id=None,
        submitted_at=_NOW,
        comment=None,
        summary=None,
        criteria=(),
        artifacts=(),
        proposed_follow_ups=(),
        review=ResultReview(
            status=status,
            reviewed_by=SubjectId("sub_reviewer") if reviewed else None,
            reviewed_at=_NOW if reviewed else None,
            reason="Needs evidence" if status is ResultReviewStatus.REJECTED else None,
        ),
    )


def _intent_data() -> dict[str, object]:
    """Return shared valid existing-Task Human intent fields."""
    return {
        "project_id": ProjectId("prj_acme"),
        "subject_id": SubjectId("sub_human"),
        "task": "ACME-1",
        "expected_version": 2,
        "idempotency_key": "operation-1",
    }


def _mutation_data() -> dict[str, object]:
    """Return shared valid attributable optimistic mutation fields."""
    return {
        "task_uid": TaskId("tsk_1"),
        "project_id": ProjectId("prj_acme"),
        "actor_subject_id": SubjectId("sub_human"),
        "request_id": RequestId("req_operation"),
        "occurred_at": _NOW,
        "expected_version": 2,
        "idempotency_key": "operation-1",
    }


def test_task_update_patch_accepts_complete_closed_structured_input() -> None:
    """Editable fields normalize into typed immutable domain values."""
    patch = TaskUpdatePatch.model_validate(
        {
            "title": "  Updated task  ",
            "objective": "  Updated objective.  ",
            "priority": 80,
            "available_at": _NOW + timedelta(days=1),
            "approval": "human",
            "acceptance": [{"id": "ac_done", "text": "Done", "required": True}],
            "context": [{"uri": "workspace://repo/spec.md", "version": "git:abc"}],
        }
    )

    assert patch.title == "Updated task"
    assert patch.objective == "Updated objective."
    assert patch.priority == 80
    assert patch.approval is ApprovalRequirement.HUMAN
    assert patch.acceptance == (AcceptanceCriterion("ac_done", "Done", required=True),)
    assert patch.context is not None
    assert patch.context[0].uri == "workspace://repo/spec.md"

    typed = TaskUpdatePatch(
        acceptance=(AcceptanceCriterion("ac_typed", "Typed", required=False),),
        context=(ContextReference("workspace://repo/typed.md"),),
    )
    assert typed.acceptance is not None
    assert typed.context is not None
    assert typed.acceptance[0].id == "ac_typed"
    assert typed.context[0].uri == "workspace://repo/typed.md"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"title": None},
        {"objective": None},
        {"priority": None},
        {"approval": None},
        {"acceptance": None},
        {"context": None},
        {"state": "done"},
        {"priority": True},
        {"available_at": _NOW.replace(tzinfo=None)},
        {"approval": "agent"},
        {"acceptance": [{"id": "ac_done", "text": "Done"}]},
        {
            "acceptance": [
                {"id": "ac_same", "text": "One", "required": True},
                {"id": "ac_same", "text": "Two", "required": False},
            ]
        },
        {"context": [{"uri": "relative/path"}]},
        {"context": [{"uri": "workspace://repo/a", "extra": True}]},
        {
            "context": [
                {"uri": "workspace://repo/a"},
                {"uri": "workspace://repo/a"},
            ]
        },
        {"acceptance": [object()] * 101},
    ],
)
def test_task_update_patch_rejects_empty_raw_state_null_and_bad_structures(
    data: dict[str, object],
) -> None:
    """Generic update cannot cross semantic boundaries or accept malformed data."""
    with pytest.raises(ValidationError):
        TaskUpdatePatch.model_validate(data)


def test_task_update_patch_accepts_explicit_availability_clear_and_empty_sets() -> None:
    """Only available_at may be null; empty ordered sets intentionally clear data."""
    assert TaskUpdatePatch(available_at=None).model_fields_set == {"available_at"}
    assert TaskUpdatePatch(acceptance=()).acceptance == ()
    assert TaskUpdatePatch(context=()).context == ()


@pytest.mark.parametrize(
    ("model", "specific"),
    [
        (UpdateTaskInput, {"patch": TaskUpdatePatch(title="Changed")}),
        (BlockTaskInput, {"reason": "Waiting"}),
        (UnblockTaskInput, {}),
        (CancelTaskInput, {"reason": None}),
        (AddTaskDependencyInput, {"prerequisite": "ACME-2"}),
        (RemoveTaskDependencyInput, {"prerequisite": TaskId("tsk_2")}),
        (SubmitHumanResultInput, {}),
        (ApproveResultInput, {"comment": "Approved"}),
        (RejectResultInput, {"reason": "Needs evidence"}),
    ],
)
@pytest.mark.parametrize("version", [None, 0, -1, True, "2"])
def test_every_existing_task_intent_requires_explicit_positive_version(
    model: type[object],
    specific: dict[str, object],
    version: object,
) -> None:
    """Every Human mutation intent rejects missing or ambiguous versions."""
    data = {**_intent_data(), **specific}
    if version is None:
        data.pop("expected_version")
    else:
        data["expected_version"] = version

    with pytest.raises(ValidationError):
        model.model_validate(data)  # type: ignore[attr-defined]


def test_existing_task_intents_normalize_selectors_reasons_and_comments() -> None:
    """Operation-specific intent models preserve only their documented fields."""
    block = BlockTaskInput.model_validate({**_intent_data(), "reason": "  Waiting  "})
    cancel = CancelTaskInput.model_validate(
        {**_intent_data(), "reason": "  No longer needed  "}
    )
    submit = SubmitHumanResultInput.model_validate(
        {**_intent_data(), "comment": "  Manual work  "}
    )
    approve = ApproveResultInput.model_validate(
        {**_intent_data(), "comment": "  Approved  "}
    )
    reject = RejectResultInput.model_validate(
        {**_intent_data(), "reason": "  Needs evidence  "}
    )

    assert block.reason == "Waiting"
    assert cancel.reason == "No longer needed"
    assert submit.comment == "Manual work"
    assert approve.comment == "Approved"
    assert reject.reason == "Needs evidence"
    assert UnblockTaskInput.model_validate(_intent_data()).task == "ACME-1"


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (BlockTaskInput, "reason", ""),
        (BlockTaskInput, "reason", "x" * 1001),
        (CancelTaskInput, "reason", "line\nbreak"),
        (SubmitHumanResultInput, "comment", "line\nbreak"),
        (ApproveResultInput, "comment", "x" * 4001),
        (RejectResultInput, "reason", None),
        (AddTaskDependencyInput, "prerequisite", "bad"),
    ],
)
def test_operation_specific_intents_reject_invalid_bounded_values(
    model: type[object],
    field: str,
    value: object,
) -> None:
    """Reason, comment, and prerequisite fields validate before use."""
    with pytest.raises(ValidationError):
        model.model_validate({**_intent_data(), field: value})  # type: ignore[attr-defined]


def test_result_input_accepts_closed_structured_content_and_defaults() -> None:
    """Result input converts JSON-like structures into immutable domain values."""
    empty = TaskResultInput()
    result = TaskResultInput.model_validate(
        {
            "summary": "  Completed.  ",
            "criteria": [
                {
                    "criterion_id": "ac_done",
                    "status": "passed",
                    "evidence": "Verified",
                }
            ],
            "artifacts": [
                {
                    "uri": "workspace://repo/report.md",
                    "media_type": "text/markdown",
                    "sha256": "a" * 64,
                }
            ],
            "proposed_follow_ups": [{"title": "Add regression coverage"}],
        }
    )

    assert empty.criteria == ()
    assert empty.artifacts == ()
    assert empty.proposed_follow_ups == ()
    assert result.summary == "Completed."
    assert result.criteria[0].status is CriterionStatus.PASSED
    assert isinstance(result.artifacts[0], ArtifactReference)
    assert result.proposed_follow_ups == (ProposedFollowUp("Add regression coverage"),)

    typed = TaskResultInput(
        criteria=(CriterionOutcome("ac_done", CriterionStatus.PASSED),),
        artifacts=(ArtifactReference("workspace://repo/typed.md"),),
        proposed_follow_ups=(ProposedFollowUp("Typed follow-up"),),
    )
    assert typed.criteria[0].criterion_id == "ac_done"
    assert typed.artifacts[0].uri == "workspace://repo/typed.md"
    assert typed.proposed_follow_ups[0].title == "Typed follow-up"


@pytest.mark.parametrize(
    "data",
    [
        {"summary": "line\nbreak"},
        {"unexpected": True},
        {"criteria": "not-an-array"},
        {"criteria": [{"criterion_id": "ac_done", "status": "unknown"}]},
        {"criteria": [{"criterion_id": "ac_done", "status": True}]},
        {"criteria": [{"criterion_id": "ac_done", "status": "passed", "x": 1}]},
        {
            "criteria": [
                {"criterion_id": "ac_done", "status": "passed"},
                {"criterion_id": "ac_done", "status": "failed"},
            ]
        },
        {"artifacts": [{"uri": "relative/path"}]},
        {"artifacts": [{"uri": "workspace://repo/a", "extra": True}]},
        {"proposed_follow_ups": [{"title": "Next", "create": True}]},
        {"artifacts": [object()] * 101},
    ],
)
def test_result_input_rejects_open_malformed_or_duplicate_structures(
    data: dict[str, object],
) -> None:
    """Result content cannot inject identity, state, or unsupported fields."""
    with pytest.raises(ValidationError):
        TaskResultInput.model_validate(data)


def test_view_and_event_queries_validate_scope_view_cursor_and_bounds() -> None:
    """Phase 3 queries bind exact selection, view, and bounded pagination input."""
    project_query = ListTasksByView.model_validate(
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "view": "ready",
            "cursor": "v3.opaque",
            "limit": 500,
        }
    )
    instance_query = ListTasksByView(
        subject_id=SubjectId("sub_human"),
        instance_id=InstanceId("ins_local"),
    )

    assert project_query.view is TaskListView.READY
    assert project_query.limit == 500
    assert instance_query.instance_id == InstanceId("ins_local")
    details = GetTaskDetails(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_human"),
        task="tsk_1",
    )
    events = ReadTaskEvents(
        project_id=details.project_id,
        subject_id=details.subject_id,
        task=details.task,
        after=0,
        limit=1,
    )
    assert events.after == 0
    assert events.limit == 1


@pytest.mark.parametrize(
    "data",
    [
        {"subject_id": SubjectId("sub_human")},
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "instance_id": ProjectId("prj_wrong"),
        },
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "view": "unknown",
        },
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "view": True,
        },
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "cursor": "bad cursor",
        },
        {
            "subject_id": SubjectId("sub_human"),
            "project_id": ProjectId("prj_acme"),
            "limit": True,
        },
    ],
)
def test_view_query_rejects_ambiguous_or_malformed_scope(
    data: dict[str, object],
) -> None:
    """View queries reject missing/dual scopes, bad views, cursors, and limits."""
    with pytest.raises(ValidationError):
        ListTasksByView.model_validate(data)


@pytest.mark.parametrize(("after", "limit"), [(-1, 100), (True, 100), (0, 0), (0, 501)])
def test_event_query_rejects_invalid_numeric_bounds(
    after: object, limit: object
) -> None:
    """Event snapshots reject negative, boolean, and out-of-range pagination."""
    with pytest.raises(ValidationError):
        ReadTaskEvents.model_validate(
            {
                "project_id": ProjectId("prj_acme"),
                "subject_id": SubjectId("sub_human"),
                "task": "ACME-1",
                "after": after,
                "limit": limit,
            }
        )


def test_task_queries_reject_non_string_non_identifier_selectors() -> None:
    """Task detail and event selectors reject coercible scalar values."""
    common = {
        "project_id": ProjectId("prj_acme"),
        "subject_id": SubjectId("sub_human"),
        "task": True,
    }

    with pytest.raises(ValidationError):
        GetTaskDetails.model_validate(common)
    with pytest.raises(ValidationError):
        ReadTaskEvents.model_validate(common)


@pytest.mark.parametrize(
    ("model", "specific"),
    [
        (
            TaskUpdateMutation,
            {
                "event_id": TaskEventId("evt_update"),
                "patch": TaskUpdatePatch(title="Changed"),
            },
        ),
        (
            TaskBlockMutation,
            {"event_id": TaskEventId("evt_block"), "reason": "Waiting"},
        ),
        (TaskUnblockMutation, {"event_id": TaskEventId("evt_unblock")}),
        (TaskCancelMutation, {"event_id": TaskEventId("evt_cancel"), "reason": None}),
        (
            AddTaskDependencyMutation,
            {"event_id": TaskEventId("evt_add"), "prerequisite_uid": TaskId("tsk_2")},
        ),
        (
            RemoveTaskDependencyMutation,
            {
                "event_id": TaskEventId("evt_remove"),
                "prerequisite_uid": TaskId("tsk_2"),
            },
        ),
        (
            SubmitHumanResultMutation,
            {
                "result_id": ResultId("res_new"),
                "result_submitted_event_id": TaskEventId("evt_submitted"),
                "task_completed_event_id": TaskEventId("evt_completed"),
            },
        ),
        (
            ApproveResultMutation,
            {
                "review_approved_event_id": TaskEventId("evt_approved"),
                "task_completed_event_id": TaskEventId("evt_completed"),
            },
        ),
        (
            RejectResultMutation,
            {
                "review_rejected_event_id": TaskEventId("evt_rejected"),
                "reason": "Needs evidence",
            },
        ),
    ],
)
def test_every_repository_mutation_requires_attribution_time_and_version(
    model: type[object],
    specific: dict[str, object],
) -> None:
    """Repository operations require typed actor/request/time/version boundaries."""
    valid = {**_mutation_data(), **specific}
    assert model.model_validate(valid).expected_version == 2  # type: ignore[attr-defined]
    for field, value in (
        ("actor_subject_id", "sub_human"),
        ("request_id", "req_operation"),
        ("occurred_at", _NOW.replace(tzinfo=None)),
        ("expected_version", 0),
        ("expected_version", True),
        ("idempotency_key", "bad key"),
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**valid, field: value})  # type: ignore[attr-defined]


def test_multi_event_mutations_require_distinct_exact_event_id_slots() -> None:
    """Submission and approval cannot reuse one TaskEvent identity."""
    common = _mutation_data()
    with pytest.raises(ValidationError, match="distinct"):
        SubmitHumanResultMutation.model_validate(
            {
                **common,
                "result_id": ResultId("res_new"),
                "result_submitted_event_id": TaskEventId("evt_same"),
                "task_completed_event_id": TaskEventId("evt_same"),
            }
        )
    with pytest.raises(ValidationError, match="distinct"):
        ApproveResultMutation.model_validate(
            {
                **common,
                "review_approved_event_id": TaskEventId("evt_same"),
                "task_completed_event_id": TaskEventId("evt_same"),
            }
        )


def test_task_details_validate_prerequisite_and_current_result_relationships() -> None:
    """Task details cannot combine foreign, unordered, or mismatched objects."""
    prerequisite = _task(number=2)
    task = replace(
        _task(),
        depends_on=(prerequisite.uid,),
        current_result_id=ResultId("res_current"),
    )
    result = _result(task, ResultReviewStatus.PENDING)
    readiness = TaskReadiness(
        ready=False,
        running=False,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=(ReadinessReason.UNSATISFIED_DEPENDENCY,),
    )
    details = TaskDetails(
        task=task,
        readiness=readiness,
        prerequisites=(prerequisite,),
        current_result=result,
    )

    assert details.current_result is result
    with pytest.raises(ValidationError, match="depends_on"):
        TaskDetails(
            task=replace(task, depends_on=()),
            readiness=readiness,
            prerequisites=(prerequisite,),
            current_result=result,
        )
    with pytest.raises(ValidationError, match="absent"):
        TaskDetails(
            task=replace(task, current_result_id=None),
            readiness=readiness,
            prerequisites=(prerequisite,),
            current_result=result,
        )
    later_prerequisite = _task(number=3)
    with pytest.raises(ValidationError, match="ordered"):
        TaskDetails(
            task=replace(
                task,
                depends_on=(later_prerequisite.uid, prerequisite.uid),
            ),
            readiness=readiness,
            prerequisites=(later_prerequisite, prerequisite),
            current_result=result,
        )
    with pytest.raises(ValidationError, match="Project"):
        TaskDetails(
            task=task,
            readiness=readiness,
            prerequisites=(replace(prerequisite, project_id=ProjectId("prj_other")),),
            current_result=result,
        )
    with pytest.raises(ValidationError, match="match"):
        TaskDetails(
            task=task,
            readiness=readiness,
            prerequisites=(prerequisite,),
            current_result=replace(result, id=ResultId("res_other")),
        )


def test_task_mutation_result_requires_one_matching_event() -> None:
    """Simple mutations return exactly one matching attributable TaskEvent."""
    task = _task()
    event = _event(task, TaskEventType.TASK_UPDATED, cursor=1)
    assert TaskMutationResult(task=task, events=(event,)).events == (event,)

    with pytest.raises(ValidationError, match="exactly one"):
        TaskMutationResult(task=task, events=())
    with pytest.raises(ValidationError, match="identities"):
        TaskMutationResult(
            task=task,
            events=(replace(event, task_uid=TaskId("tsk_other")),),
        )


@pytest.mark.parametrize(
    ("status", "state", "event_types"),
    [
        (
            ResultReviewStatus.NOT_REQUIRED,
            TaskState.DONE,
            (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED),
        ),
        (
            ResultReviewStatus.PENDING,
            TaskState.REVIEW,
            (TaskEventType.RESULT_SUBMITTED,),
        ),
        (
            ResultReviewStatus.APPROVED,
            TaskState.DONE,
            (TaskEventType.REVIEW_APPROVED, TaskEventType.TASK_COMPLETED),
        ),
        (
            ResultReviewStatus.REJECTED,
            TaskState.OPEN,
            (TaskEventType.REVIEW_REJECTED,),
        ),
    ],
)
def test_submission_result_accepts_each_exact_review_event_sequence(
    status: ResultReviewStatus,
    state: TaskState,
    event_types: tuple[TaskEventType, ...],
) -> None:
    """Result disposition, Task state, selection, and events remain consistent."""
    selected = (
        None if status is ResultReviewStatus.REJECTED else ResultId("res_current")
    )
    task = _task(state=state, current_result_id=selected)
    result = _result(task, status)
    events = tuple(
        _event(task, event_type, cursor=index + 1)
        for index, event_type in enumerate(event_types)
    )

    assert (
        TaskSubmissionResult(task=task, result=result, events=events).events == events
    )


def test_submission_result_rejects_wrong_result_state_and_event_sequences() -> None:
    """Submission results reject cross-Task Results and mismatched event batches."""
    task = _task(state=TaskState.REVIEW, current_result_id=ResultId("res_current"))
    result = _result(task, ResultReviewStatus.PENDING)
    submitted = _event(task, TaskEventType.RESULT_SUBMITTED, cursor=1)

    with pytest.raises(ValidationError, match="belong"):
        TaskSubmissionResult(
            task=task,
            result=replace(result, task_uid=TaskId("tsk_other")),
            events=(submitted,),
        )
    with pytest.raises(ValidationError, match="events"):
        TaskSubmissionResult(
            task=task,
            result=result,
            events=(replace(submitted, event_type=TaskEventType.TASK_UPDATED),),
        )
    with pytest.raises(ValidationError, match="state"):
        TaskSubmissionResult(
            task=replace(task, state=TaskState.OPEN),
            result=result,
            events=(submitted,),
        )
    with pytest.raises(ValidationError, match="at least one"):
        TaskSubmissionResult(task=task, result=result, events=())


def test_submission_result_rejects_unordered_or_mixed_attribution_events() -> None:
    """Multi-event Result outcomes form one ordered attributable transaction."""
    task = _task(state=TaskState.DONE, current_result_id=ResultId("res_current"))
    result = _result(task, ResultReviewStatus.APPROVED)
    approved = _event(task, TaskEventType.REVIEW_APPROVED, cursor=1)
    completed = _event(task, TaskEventType.TASK_COMPLETED, cursor=2)

    with pytest.raises(ValidationError, match="ascending"):
        TaskSubmissionResult(
            task=task,
            result=result,
            events=(replace(approved, cursor=2), replace(completed, cursor=1)),
        )
    with pytest.raises(ValidationError, match="attribution"):
        TaskSubmissionResult(
            task=task,
            result=result,
            events=(
                approved,
                replace(completed, actor_subject_id=SubjectId("sub_other")),
            ),
        )


def test_task_event_page_requires_strict_scope_order_and_terminal_cursor() -> None:
    """Event pages are polling-safe for empty and nonempty snapshots."""
    task = _task()
    first = _event(task, TaskEventType.TASK_UPDATED, cursor=3)
    second = _event(task, TaskEventType.TASK_BLOCKED, cursor=4)

    assert TaskEventPage(events=(), next_cursor=2).next_cursor == 2
    assert TaskEventPage(events=(first, second), next_cursor=4).events[-1] is second
    for events, cursor in (
        ((second, first), 3),
        ((first, second), 3),
        ((first, replace(second, task_uid=TaskId("tsk_other"))), 4),
    ):
        with pytest.raises(ValidationError):
            TaskEventPage(events=events, next_cursor=cursor)
    for cursor in (-1, True):
        with pytest.raises(ValidationError):
            TaskEventPage.model_validate({"events": (), "next_cursor": cursor})


def test_task_page_binds_view_and_uses_ready_ordering() -> None:
    """Ready pages enforce priority and null-first availability ordering."""
    high = _task(number=2, priority=90)
    scheduled = _task(
        number=3,
        priority=90,
        available_at=_NOW + timedelta(hours=1),
    )
    low = _task(number=1, priority=10)
    ready_projection = TaskReadiness(
        ready=True,
        running=False,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=(),
    )

    page = TaskPage(
        tasks=(high, scheduled, low),
        readiness=(ready_projection, ready_projection, ready_projection),
        next_cursor="v3.ready",
        view=TaskListView.READY,
    )
    assert page.view is TaskListView.READY
    with pytest.raises(ValidationError, match="ordered"):
        TaskPage(
            tasks=(scheduled, high),
            next_cursor=None,
            view=TaskListView.READY,
        )
    with pytest.raises(ValidationError, match="one-for-one"):
        TaskPage(
            tasks=(high, low),
            readiness=(ready_projection,),
            next_cursor=None,
            view=TaskListView.READY,
        )


def test_repository_protocol_exposes_only_explicit_phase_three_semantics() -> None:
    """Repository contracts use semantic operations instead of generic CRUD handles."""
    phase_three_methods = {
        "update_task_if_version",
        "block_task",
        "unblock_task",
        "cancel_task",
        "add_task_dependency",
        "remove_task_dependency",
        "submit_human_result",
        "approve_result",
        "reject_result",
        "get_task_details",
        "list_tasks_by_view",
        "read_task_events_after",
    }
    prohibited = {"update", "save", "delete", "execute", "transaction"}

    assert phase_three_methods <= set(dir(WorkaholicRepository))
    assert prohibited.isdisjoint(dir(WorkaholicRepository))
    for method in phase_three_methods:
        signature = inspect.signature(getattr(WorkaholicRepository, method))
        assert next(iter(signature.parameters)) == "self"
        assert len(signature.parameters) == 2


def _accept_task_repository(repository: TaskRepository) -> TaskRepository:
    """Type-check one adapter-independent Phase 3 Task repository fake.

    Args:
        repository: Structurally compatible semantic repository.

    Returns:
        The same repository typed through the application-owned port.

    """
    return repository


def _unimplemented_fake_operation() -> NoReturn:
    """Fail if a type-only repository fake is invoked at runtime."""
    message = "The Phase 3 contract fake has no persistence behavior."
    raise NotImplementedError(message)


class _PhaseThreeTaskRepositoryFake:
    """Prove the application Task port is structurally adapter-independent."""

    def create_task(self, mutation: TaskCreationMutation) -> Task:
        """Satisfy the cumulative Task creation contract."""
        del mutation
        return _unimplemented_fake_operation()

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Satisfy the optimistic field-update contract."""
        del mutation
        return _unimplemented_fake_operation()

    def block_task(self, mutation: TaskBlockMutation) -> TaskMutationResult:
        """Satisfy the Task-blocking contract."""
        del mutation
        return _unimplemented_fake_operation()

    def unblock_task(self, mutation: TaskUnblockMutation) -> TaskMutationResult:
        """Satisfy the Task-unblocking contract."""
        del mutation
        return _unimplemented_fake_operation()

    def cancel_task(self, mutation: TaskCancelMutation) -> TaskMutationResult:
        """Satisfy the Task-cancellation contract."""
        del mutation
        return _unimplemented_fake_operation()

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Satisfy the dependency-addition contract."""
        del mutation
        return _unimplemented_fake_operation()

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Satisfy the dependency-removal contract."""
        del mutation
        return _unimplemented_fake_operation()

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Satisfy the Human Result submission contract."""
        del mutation
        return _unimplemented_fake_operation()

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Satisfy the Result approval contract."""
        del mutation
        return _unimplemented_fake_operation()

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Satisfy the Result rejection contract."""
        del mutation
        return _unimplemented_fake_operation()


_TYPED_PHASE_THREE_FAKE: TaskRepository = _PhaseThreeTaskRepositoryFake()


def test_application_contracts_import_without_adapter_or_session_modules() -> None:
    """Phase 3 contracts remain usable without importing any concrete adapter."""
    assert TaskRepository.__module__ == "workaholic.application.ports"
    assert WorkaholicRepository.__module__ == "workaholic.application.ports"
    assert _accept_task_repository(_TYPED_PHASE_THREE_FAKE) is _TYPED_PHASE_THREE_FAKE
