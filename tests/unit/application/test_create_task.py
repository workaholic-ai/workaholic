"""Unit tests for attributable Task creation application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    CreateTaskInput,
    TaskApplication,
    TaskCreationMutation,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskEventId,
    TaskId,
    TaskState,
)

if TYPE_CHECKING:
    from workaholic.application import TaskRepository

_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
_AVAILABLE_AT = datetime(2026, 8, 2, 9, 0, 0, 123456, tzinfo=UTC)
_ACCEPTANCE = (
    AcceptanceCriterion(
        id="ac_evidence",
        text="Attach the categorized evidence.",
        required=True,
    ),
)
_CONTEXT = (
    ContextReference(
        uri="workspace://repo/data/cancellations.csv",
        version="git:8f31c12",
    ),
)


def _task() -> Task:
    """Build one valid Task application result.

    Returns:
        Valid initial Task.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="First task",
        objective="First task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Clock:
    """Deterministic Task application-test clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic complete IdentifierFactory test implementation."""

    def new_instance_id(self) -> InstanceId:
        """Return an unused candidate Instance identity."""
        return InstanceId("ins_candidate")

    def new_project_id(self) -> ProjectId:
        """Return an unused candidate Project identity."""
        return ProjectId("prj_candidate")

    def new_subject_id(self) -> SubjectId:
        """Return an unused candidate Subject identity."""
        return SubjectId("sub_candidate")

    def new_task_id(self) -> TaskId:
        """Return the candidate Task identity."""
        return TaskId("tsk_candidate")

    def new_event_id(self) -> TaskEventId:
        """Return the candidate event identity."""
        return TaskEventId("evt_candidate")

    def new_request_id(self) -> RequestId:
        """Return the candidate request identity."""
        return RequestId("req_candidate")


class _RecordingRepository:
    """Minimal Task repository spy."""

    def __init__(self, result: object) -> None:
        """Initialize the configured result.

        Args:
            result: Value returned by create_task.

        """
        self.result = result
        self.mutations: list[TaskCreationMutation] = []

    def create_task(self, mutation: TaskCreationMutation) -> object:
        """Record the mutation and return the configured value."""
        self.mutations.append(mutation)
        return self.result


def test_create_builds_one_attributable_mutation_with_command_defaults() -> None:
    """Application orchestration allocates identities once and preserves defaults."""
    expected = _task()
    recording = _RecordingRepository(expected)
    application = TaskApplication(
        repository=cast("TaskRepository", recording),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )
    command = CreateTaskInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        title="  First task  ",
        idempotency_key="task-add-1",
    )

    actual = application.create(command)

    assert actual is expected
    assert command.objective == "First task"
    assert command.priority == 50
    assert recording.mutations == [
        TaskCreationMutation(
            task_id=TaskId("tsk_candidate"),
            event_id=TaskEventId("evt_candidate"),
            request_id=RequestId("req_candidate"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_local"),
            occurred_at=_NOW,
            title="First task",
            objective="First task",
            priority=50,
            idempotency_key="task-add-1",
        )
    ]


def test_create_preserves_complete_structured_task_definition() -> None:
    """Application orchestration forwards every validated Phase 3 field exactly."""
    expected = replace(
        _task(),
        available_at=_AVAILABLE_AT,
        approval=ApprovalRequirement.HUMAN,
        acceptance=_ACCEPTANCE,
        context=_CONTEXT,
    )
    recording = _RecordingRepository(expected)
    application = TaskApplication(
        repository=cast("TaskRepository", recording),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )
    command = CreateTaskInput.model_validate(
        {
            "project_id": ProjectId("prj_acme"),
            "subject_id": SubjectId("sub_local"),
            "title": "First task",
            "available_at": _AVAILABLE_AT,
            "approval": "human",
            "acceptance": [
                {
                    "id": "ac_evidence",
                    "text": "Attach the categorized evidence.",
                    "required": True,
                }
            ],
            "context": [
                {
                    "uri": "workspace://repo/data/cancellations.csv",
                    "version": "git:8f31c12",
                }
            ],
        }
    )

    actual = application.create(command)

    assert actual is expected
    assert recording.mutations == [
        TaskCreationMutation(
            task_id=TaskId("tsk_candidate"),
            event_id=TaskEventId("evt_candidate"),
            request_id=RequestId("req_candidate"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_local"),
            occurred_at=_NOW,
            title="First task",
            objective="First task",
            priority=50,
            available_at=_AVAILABLE_AT,
            approval=ApprovalRequirement.HUMAN,
            acceptance=_ACCEPTANCE,
            context=_CONTEXT,
        )
    ]


@pytest.mark.parametrize(
    ("repository", "clock", "identifiers"),
    [
        (object(), _Clock(), _Identifiers()),
        (_RecordingRepository(_task()), object(), _Identifiers()),
        (_RecordingRepository(_task()), _Clock(), object()),
    ],
)
def test_constructor_runtime_validates_dependencies(
    repository: object,
    clock: object,
    identifiers: object,
) -> None:
    """Missing dependency methods fail at composition time."""
    with pytest.raises(TypeError, match="Task"):
        TaskApplication(
            repository=cast("TaskRepository", repository),
            clock=cast("_Clock", clock),
            identifiers=cast("_Identifiers", identifiers),
        )


def test_create_runtime_validates_command_type() -> None:
    """The use case rejects bypasses of the validated command boundary."""
    application = TaskApplication(
        repository=cast("TaskRepository", _RecordingRepository(_task())),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.create(cast("CreateTaskInput", object()))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_invalid_dependency_output_is_a_safe_internal_error() -> None:
    """Malformed clock output never leaks boundary validation details."""

    class _InvalidClock:
        """Clock that violates its timezone contract."""

        def now(self) -> datetime:
            """Return a deliberately naive timestamp."""
            return _NOW.replace(tzinfo=None)

    application = TaskApplication(
        repository=cast("TaskRepository", _RecordingRepository(_task())),
        clock=_InvalidClock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                title="First task",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_invalid_repository_result_is_a_safe_internal_error() -> None:
    """The application verifies concrete Task output at runtime."""
    application = TaskApplication(
        repository=cast("TaskRepository", _RecordingRepository(object())),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                title="First task",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    "result",
    [
        replace(_task(), available_at=_AVAILABLE_AT),
        replace(_task(), approval=ApprovalRequirement.HUMAN),
        replace(_task(), acceptance=_ACCEPTANCE),
        replace(_task(), context=_CONTEXT),
        replace(_task(), state=TaskState.DONE),
        replace(_task(), version=2),
        replace(_task(), created_by=SubjectId("sub_other")),
    ],
)
def test_create_rejects_semantically_mismatched_repository_task(
    result: Task,
) -> None:
    """Repository output must match caller semantics and initial Task invariants."""
    application = TaskApplication(
        repository=cast("TaskRepository", _RecordingRepository(result)),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                title="First task",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
