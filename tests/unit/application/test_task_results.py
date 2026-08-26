"""Unit tests for Human Result application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ApproveResultInput,
    ApproveResultMutation,
    GetTask,
    InvalidTransitionError,
    RejectResultInput,
    RejectResultMutation,
    SubmitHumanResultInput,
    SubmitHumanResultMutation,
    TaskResultApplication,
    TaskResultInput,
    TaskSubmissionResult,
)
from workaholic.domain import (
    ApprovalRequirement,
    ArtifactReference,
    CriterionOutcome,
    CriterionStatus,
    JsonValue,
    ProjectId,
    ProposedFollowUp,
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
    TaskResult,
    TaskState,
)

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, ResultIdentifierFactory

_NOW = datetime(2026, 8, 1, 12, 0, 0, 123456, tzinfo=UTC)
_CREATED_AT = _NOW - timedelta(hours=1)


class _IntentData(TypedDict):
    """Exact common fields shared by Result intent constructors."""

    project_id: ProjectId
    subject_id: SubjectId
    task: TaskId | str
    expected_version: int


def _task(
    *,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    state: TaskState = TaskState.OPEN,
    current_result_id: ResultId | None = None,
) -> Task:
    """Build one deterministic Result-operation Task fixture."""
    return Task(
        uid=TaskId("tsk_target"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="Implement the task",
        objective="Produce the requested outcome.",
        state=state,
        priority=50,
        approval=approval,
        version=1,
        current_result_id=current_result_id,
        created_by=SubjectId("sub_local"),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _content() -> TaskResultInput:
    """Build complete caller-controlled structured Result content."""
    return TaskResultInput(
        summary="Implemented and verified.",
        criteria=(
            CriterionOutcome(
                criterion_id="ac_done",
                status=CriterionStatus.PASSED,
                evidence="All checks passed.",
            ),
        ),
        artifacts=(
            ArtifactReference(
                uri="workspace://repo/report.md",
                media_type="text/markdown",
                sha256="a" * 64,
            ),
        ),
        proposed_follow_ups=(ProposedFollowUp("Add another regression test"),),
    )


def _outcome(
    status: ResultReviewStatus,
    *,
    historical: bool = False,
    content: TaskResultInput | None = None,
) -> TaskSubmissionResult:
    """Build one internally consistent submission or review outcome."""
    occurred_at = _NOW - timedelta(minutes=5) if historical else _NOW
    result_id = ResultId("res_historic" if historical else "res_new")
    selected = None if status is ResultReviewStatus.REJECTED else result_id
    state = {
        ResultReviewStatus.NOT_REQUIRED: TaskState.DONE,
        ResultReviewStatus.PENDING: TaskState.REVIEW,
        ResultReviewStatus.APPROVED: TaskState.DONE,
        ResultReviewStatus.REJECTED: TaskState.OPEN,
    }[status]
    approval = (
        ApprovalRequirement.NONE
        if status is ResultReviewStatus.NOT_REQUIRED
        else ApprovalRequirement.HUMAN
    )
    task = replace(
        _task(approval=approval),
        state=state,
        current_result_id=selected,
        version=2,
        updated_at=occurred_at,
    )
    values = TaskResultInput() if content is None else content
    reviewed = status in (ResultReviewStatus.APPROVED, ResultReviewStatus.REJECTED)
    result = TaskResult(
        id=result_id,
        task_uid=task.uid,
        submitted_by=SubjectId("sub_local"),
        attempt_id=None,
        submitted_at=_CREATED_AT,
        comment="Manual result" if not reviewed else "Original result",
        summary=values.summary,
        criteria=values.criteria,
        artifacts=values.artifacts,
        proposed_follow_ups=values.proposed_follow_ups,
        review=ResultReview(
            status=status,
            reviewed_by=SubjectId("sub_local") if reviewed else None,
            reviewed_at=occurred_at if reviewed else None,
            comment="Looks good" if status is ResultReviewStatus.APPROVED else None,
            reason="Needs evidence" if status is ResultReviewStatus.REJECTED else None,
        ),
    )
    event_types = {
        ResultReviewStatus.NOT_REQUIRED: (
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.TASK_COMPLETED,
        ),
        ResultReviewStatus.PENDING: (TaskEventType.RESULT_SUBMITTED,),
        ResultReviewStatus.APPROVED: (
            TaskEventType.REVIEW_APPROVED,
            TaskEventType.TASK_COMPLETED,
        ),
        ResultReviewStatus.REJECTED: (TaskEventType.REVIEW_REJECTED,),
    }[status]
    events: list[TaskEvent] = []
    for index, event_type in enumerate(event_types):
        payload: dict[str, JsonValue] = {
            "result_id": str(result.id),
            "version": task.version,
        }
        if event_type is TaskEventType.RESULT_SUBMITTED:
            payload["review_status"] = status.value
        elif event_type is TaskEventType.REVIEW_APPROVED:
            payload["comment"] = result.review.comment
        elif event_type is TaskEventType.REVIEW_REJECTED:
            payload["reason"] = result.review.reason
        events.append(
            TaskEvent(
                id=TaskEventId(
                    f"evt_historic_{index}" if historical else f"evt_{index + 1}"
                ),
                cursor=index + 3,
                task_uid=task.uid,
                project_id=task.project_id,
                actor_subject_id=SubjectId("sub_local"),
                request_id=RequestId("req_historic" if historical else "req_result"),
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            )
        )
    if not reviewed:
        result = replace(result, submitted_at=occurred_at)
    return TaskSubmissionResult(task=task, result=result, events=tuple(events))


class _Clock:
    """Deterministic Result-test clock."""

    def now(self) -> datetime:
        """Return the authoritative mutation time."""
        return _NOW


class _Identifiers:
    """Deterministic Result-test identity factory subset."""

    def __init__(self) -> None:
        """Initialize deterministic event sequencing."""
        self._event_number = 0

    def new_result_id(self) -> ResultId:
        """Return the candidate Result identity."""
        return ResultId("res_new")

    def new_event_id(self) -> TaskEventId:
        """Return the next candidate event identity."""
        self._event_number += 1
        return TaskEventId(f"evt_{self._event_number}")

    def new_request_id(self) -> RequestId:
        """Return the candidate request identity."""
        return RequestId("req_result")


class _BrokenIdentifiers:
    """Identity dependency returning one invalid event value."""

    def new_result_id(self) -> ResultId:
        """Return a valid Result identity before the invalid event value."""
        return ResultId("res_new")

    def new_event_id(self) -> object:
        """Return a value rejected by every Result mutation model."""
        return object()

    def new_request_id(self) -> RequestId:
        """Return a valid request identity."""
        return RequestId("req_result")


class _Repository:
    """Result repository spy with independently configurable outcomes."""

    def __init__(self) -> None:
        """Initialize valid Task and outcome defaults."""
        self.task: object = _task()
        self.submission: object = _outcome(ResultReviewStatus.NOT_REQUIRED)
        self.approval: object = _outcome(ResultReviewStatus.APPROVED)
        self.rejection: object = _outcome(ResultReviewStatus.REJECTED)
        self.queries: list[GetTask] = []
        self.submissions: list[SubmitHumanResultMutation] = []
        self.approvals: list[ApproveResultMutation] = []
        self.rejections: list[RejectResultMutation] = []
        self.error: ApplicationError | None = None

    def get_task(self, command: GetTask) -> Task:
        """Record one authoritative Task lookup."""
        self.queries.append(command)
        return cast("Task", self.task)

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Record and return one submission result."""
        self.submissions.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskSubmissionResult", self.submission)

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Record and return one approval result."""
        self.approvals.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskSubmissionResult", self.approval)

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Record and return one rejection result."""
        self.rejections.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskSubmissionResult", self.rejection)


def _application(repository: _Repository) -> TaskResultApplication:
    """Compose Result orchestration with deterministic collaborators."""
    return TaskResultApplication(
        repository,
        cast("Clock", _Clock()),
        cast("ResultIdentifierFactory", _Identifiers()),
    )


def _input_data() -> _IntentData:
    """Return common optimistic Result intent fields."""
    return {
        "project_id": ProjectId("prj_acme"),
        "subject_id": SubjectId("sub_local"),
        "task": "ACME-1",
        "expected_version": 1,
    }


def test_submit_without_approval_builds_exact_multi_event_mutation() -> None:
    """No-approval submission allocates Result, request, and two event IDs."""
    repository = _Repository()
    command = SubmitHumanResultInput(
        **_input_data(),
        comment="Manual result",
        idempotency_key="submit-1",
    )

    assert _application(repository).submit(command) is repository.submission
    assert repository.queries == [
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-1",
        )
    ]
    assert repository.submissions == [
        SubmitHumanResultMutation(
            task_uid=TaskId("tsk_target"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_local"),
            result_id=ResultId("res_new"),
            result_submitted_event_id=TaskEventId("evt_1"),
            task_completed_event_id=TaskEventId("evt_2"),
            claim_expired_event_id=TaskEventId("evt_3"),
            request_id=RequestId("req_result"),
            occurred_at=_NOW,
            expected_version=1,
            idempotency_key="submit-1",
            comment="Manual result",
        )
    ]


def test_submit_accepts_one_fresh_lazy_expiry_prefix() -> None:
    """Human submission validates its exact conditional expiry event."""
    base = _outcome(ResultReviewStatus.NOT_REQUIRED)
    expired = replace(
        base.events[0],
        id=TaskEventId("evt_3"),
        cursor=2,
        event_type=TaskEventType.CLAIM_EXPIRED,
        payload={"lease_expires_at": "2026-08-01T11:59:00Z"},
    )
    expected = TaskSubmissionResult(
        task=base.task,
        result=base.result,
        events=(expired, *base.events),
    )
    repository = _Repository()
    repository.submission = expected

    actual = _application(repository).submit(
        SubmitHumanResultInput(
            **_input_data(),
            comment="Manual result",
        )
    )

    assert actual is expected


def test_submit_for_human_review_preserves_complete_content_and_one_event() -> None:
    """Approval-required submission carries structured content without completion."""
    repository = _Repository()
    repository.task = _task(approval=ApprovalRequirement.HUMAN)
    repository.submission = _outcome(
        ResultReviewStatus.PENDING,
        content=_content(),
    )
    command = SubmitHumanResultInput(
        **_input_data(),
        comment="Manual result",
        result=_content(),
    )

    _application(repository).submit(command)

    mutation = repository.submissions[0]
    assert mutation.task_completed_event_id is None
    assert mutation.result == _content()


def test_approve_and_reject_build_exact_review_mutations() -> None:
    """Review commands allocate only their operation-specific event slots."""
    repository = _Repository()
    repository.task = _task(
        approval=ApprovalRequirement.HUMAN,
        state=TaskState.REVIEW,
        current_result_id=ResultId("res_new"),
    )

    _application(repository).approve(
        ApproveResultInput(**_input_data(), comment="Looks good")
    )
    _application(repository).reject(
        RejectResultInput(**_input_data(), reason="Needs evidence")
    )

    approval = repository.approvals[0]
    assert approval.review_approved_event_id == TaskEventId("evt_1")
    assert approval.task_completed_event_id == TaskEventId("evt_2")
    assert approval.comment == "Looks good"
    rejection = repository.rejections[0]
    assert rejection.review_rejected_event_id == TaskEventId("evt_1")
    assert rejection.reason == "Needs evidence"


@pytest.mark.parametrize(
    ("method_name", "command"),
    [
        (
            "submit",
            SubmitHumanResultInput(
                **_input_data(),
                comment="Manual result",
                idempotency_key="replay-1",
            ),
        ),
        (
            "approve",
            ApproveResultInput(
                **_input_data(),
                comment="Looks good",
                idempotency_key="replay-1",
            ),
        ),
        (
            "reject",
            RejectResultInput(
                **_input_data(),
                reason="Needs evidence",
                idempotency_key="replay-1",
            ),
        ),
    ],
)
def test_idempotent_replay_accepts_historic_generated_attribution(
    method_name: str,
    command: object,
) -> None:
    """Equivalent replay returns its original identities and timestamp."""
    repository = _Repository()
    repository.task = _task(
        approval=ApprovalRequirement.HUMAN,
        state=TaskState.REVIEW,
        current_result_id=ResultId("res_historic"),
    )
    repository.submission = _outcome(
        ResultReviewStatus.NOT_REQUIRED,
        historical=True,
    )
    repository.approval = _outcome(ResultReviewStatus.APPROVED, historical=True)
    repository.rejection = _outcome(ResultReviewStatus.REJECTED, historical=True)

    method = getattr(_application(repository), method_name)
    attribute = {
        "approve": "approval",
        "reject": "rejection",
        "submit": "submission",
    }[method_name]
    assert method(command) is getattr(repository, attribute)


@pytest.mark.parametrize("method_name", ["submit", "approve", "reject"])
def test_runtime_command_type_is_validated(method_name: str) -> None:
    """Bypassing Pydantic intents returns a stable invalid-input failure."""
    method = getattr(_application(_Repository()), method_name)

    with pytest.raises(ApplicationError) as captured:
        method(object())

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_inconsistent_task_resolution_prevents_mutation() -> None:
    """A repository cannot substitute a foreign or differently keyed Task."""
    repository = _Repository()
    repository.task = replace(
        _task(),
        project_id=ProjectId("prj_other"),
        key="OTHER-1",
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).submit(SubmitHumanResultInput(**_input_data()))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.submissions == []


@pytest.mark.parametrize(
    ("method_name", "attribute", "command"),
    [
        ("submit", "submission", SubmitHumanResultInput(**_input_data())),
        ("approve", "approval", ApproveResultInput(**_input_data())),
        (
            "reject",
            "rejection",
            RejectResultInput(**_input_data(), reason="Needs evidence"),
        ),
    ],
)
def test_wrong_persistence_result_type_is_rejected(
    method_name: str,
    attribute: str,
    command: object,
) -> None:
    """Malformed semantic outputs never cross the application boundary."""
    repository = _Repository()
    setattr(repository, attribute, object())

    with pytest.raises(ApplicationError) as captured:
        getattr(_application(repository), method_name)(command)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    ("method_name", "command"),
    [
        (
            "submit",
            SubmitHumanResultInput(**_input_data(), comment="Different comment"),
        ),
        (
            "approve",
            ApproveResultInput(**_input_data(), comment="Different comment"),
        ),
        (
            "reject",
            RejectResultInput(**_input_data(), reason="Different reason"),
        ),
    ],
)
def test_semantically_mismatched_persistence_result_is_rejected(
    method_name: str,
    command: object,
) -> None:
    """A well-shaped outcome still must match exact caller-controlled input."""
    repository = _Repository()

    with pytest.raises(ApplicationError) as captured:
        getattr(_application(repository), method_name)(command)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    ("method_name", "command"),
    [
        ("submit", SubmitHumanResultInput(**_input_data())),
        ("approve", ApproveResultInput(**_input_data())),
        (
            "reject",
            RejectResultInput(**_input_data(), reason="Needs evidence"),
        ),
    ],
)
def test_invalid_generated_identity_is_mapped_to_internal_error(
    method_name: str,
    command: object,
) -> None:
    """Invalid trusted identity output cannot escape as raw validation failure."""
    repository = _Repository()
    application = TaskResultApplication(
        repository,
        cast("Clock", _Clock()),
        cast("ResultIdentifierFactory", _BrokenIdentifiers()),
    )

    with pytest.raises(ApplicationError) as captured:
        getattr(application, method_name)(command)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_mismatched_submission_payload_is_rejected() -> None:
    """Persistence cannot return an event payload for another Result."""
    repository = _Repository()
    outcome = cast("TaskSubmissionResult", repository.submission)
    bad_event = replace(
        outcome.events[0],
        payload={
            "result_id": "res_other",
            "review_status": "not_required",
            "version": 2,
        },
    )
    repository.submission = TaskSubmissionResult(
        task=outcome.task,
        result=outcome.result,
        events=(bad_event, outcome.events[1]),
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).submit(
            SubmitHumanResultInput(**_input_data(), comment="Manual result")
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_typed_repository_failure_propagates_unchanged() -> None:
    """Expected Result-operation errors retain their public meaning."""
    repository = _Repository()
    repository.error = InvalidTransitionError()

    with pytest.raises(InvalidTransitionError):
        _application(repository).submit(SubmitHumanResultInput(**_input_data()))


@pytest.mark.parametrize(
    ("repository", "clock", "identifiers"),
    [
        (object(), _Clock(), _Identifiers()),
        (_Repository(), object(), _Identifiers()),
        (_Repository(), _Clock(), object()),
    ],
)
def test_constructor_requires_explicit_callable_dependencies(
    repository: object,
    clock: object,
    identifiers: object,
) -> None:
    """Missing collaborator methods fail at composition rather than invocation."""
    with pytest.raises(TypeError):
        TaskResultApplication(
            cast("_Repository", repository),
            cast("Clock", clock),
            cast("ResultIdentifierFactory", identifiers),
        )
