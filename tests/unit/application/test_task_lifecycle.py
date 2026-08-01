"""Unit tests for optimistic Task lifecycle application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetTask,
    TaskLifecycleApplication,
    TaskMutationResult,
    TaskUpdateMutation,
    TaskUpdatePatch,
    UpdateTaskInput,
    VersionConflictError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, IdentifierFactory

_NOW = datetime(2026, 8, 1, 9, 30, 0, 123456, tzinfo=UTC)
_CREATED_AT = _NOW - timedelta(days=1)
_AVAILABLE_AT = _NOW + timedelta(days=1)
_ACTOR_ID = SubjectId("sub_local")
_EVENT_ID = TaskEventId("evt_update")
_REQUEST_ID = RequestId("req_update")
_ACCEPTANCE = (AcceptanceCriterion("ac_evidence", "Attach evidence.", required=True),)
_CONTEXT = (ContextReference("workspace://repo/spec.md", "git:abc123"),)


def _task(**changes: object) -> Task:
    """Build one valid Task snapshot with optional test changes.

    Args:
        **changes: Fields to replace on the canonical snapshot.

    Returns:
        Valid Task snapshot.

    """
    task = Task(
        uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="First task",
        objective="First objective.",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    # Test cases deliberately exercise independent dataclass fields dynamically.
    return replace(task, **changes)  # type: ignore[arg-type]


def _result(  # noqa: PLR0913 - explicit mismatch controls keep cases readable.
    patch: TaskUpdatePatch,
    *,
    occurred_at: datetime = _NOW,
    event_id: TaskEventId = _EVENT_ID,
    request_id: RequestId = _REQUEST_ID,
    actor_subject_id: SubjectId = _ACTOR_ID,
    event_type: TaskEventType = TaskEventType.TASK_UPDATED,
    **task_changes: object,
) -> TaskMutationResult:
    """Build one internally valid mutation result for an update patch.

    Args:
        patch: Caller patch represented by the event.
        occurred_at: Authoritative update and event timestamp.
        event_id: Event identity returned by persistence.
        request_id: Request correlation identity returned by persistence.
        actor_subject_id: Event actor.
        event_type: Event semantic type.
        **task_changes: Additional updated Task fields.

    Returns:
        Valid mutation result whose Task is at version two.

    """
    patched = {name: getattr(patch, name) for name in patch.model_fields_set}
    patched.update({"updated_at": occurred_at, "version": 2})
    patched.update(task_changes)
    task = _task(**patched)
    event = TaskEvent(
        id=event_id,
        cursor=2,
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=actor_subject_id,
        request_id=request_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload={
            "changes": tuple(sorted(patch.model_fields_set)),
            "version": task.version,
        },
    )
    return TaskMutationResult(task=task, events=(event,))


class _Clock:
    """Deterministic lifecycle-test clock."""

    def now(self) -> datetime:
        """Return the authoritative update timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic subset of the application IdentifierFactory contract."""

    def new_event_id(self) -> TaskEventId:
        """Return the update event identity."""
        return TaskEventId("evt_update")

    def new_request_id(self) -> RequestId:
        """Return the update request identity."""
        return RequestId("req_update")


class _RecordingRepository:
    """Minimal lifecycle repository spy with configurable results."""

    def __init__(
        self,
        result: object,
        *,
        resolved: object | None = None,
        error: ApplicationError | None = None,
    ) -> None:
        """Initialize configured resolution and update behavior.

        Args:
            result: Value returned by the update operation.
            resolved: Optional Human-key lookup result.
            error: Optional application error raised by update persistence.

        """
        self.result = result
        self.resolved = _task() if resolved is None else resolved
        self.error = error
        self.queries: list[GetTask] = []
        self.mutations: list[TaskUpdateMutation] = []

    def get_task(self, command: GetTask) -> Task:
        """Record a Human-key lookup and return its configured value."""
        self.queries.append(command)
        return cast("Task", self.resolved)

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Record a mutation and return or raise configured behavior."""
        self.mutations.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskMutationResult", self.result)


def _application(repository: _RecordingRepository) -> TaskLifecycleApplication:
    """Compose the lifecycle use case around deterministic test dependencies."""
    return TaskLifecycleApplication(
        repository,
        cast("Clock", _Clock()),
        cast("IdentifierFactory", _Identifiers()),
    )


def test_update_by_task_id_builds_one_exact_attributable_mutation() -> None:
    """Canonical identity avoids a read and preserves optimistic input exactly."""
    patch = TaskUpdatePatch(title="Updated task")
    expected = _result(patch)
    repository = _RecordingRepository(expected)

    actual = _application(repository).update(
        UpdateTaskInput(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task=TaskId("tsk_first"),
            expected_version=1,
            patch=patch,
        )
    )

    assert actual is expected
    assert repository.queries == []
    assert repository.mutations == [
        TaskUpdateMutation(
            task_uid=TaskId("tsk_first"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_local"),
            event_id=TaskEventId("evt_update"),
            request_id=RequestId("req_update"),
            occurred_at=_NOW,
            expected_version=1,
            patch=patch,
        )
    ]


def test_update_resolves_human_key_and_forwards_complete_definition_patch() -> None:
    """Human keys resolve once and every editable structured field is retained."""
    patch = TaskUpdatePatch(
        title="Updated task",
        objective="Updated objective.",
        priority=80,
        available_at=_AVAILABLE_AT,
        approval=ApprovalRequirement.HUMAN,
        acceptance=_ACCEPTANCE,
        context=_CONTEXT,
    )
    expected = _result(patch)
    repository = _RecordingRepository(expected)

    actual = _application(repository).update(
        UpdateTaskInput(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-1",
            expected_version=1,
            patch=patch,
            idempotency_key="update-1",
        )
    )

    assert actual is expected
    assert repository.queries == [
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-1",
        )
    ]
    assert repository.mutations[0].task_uid == TaskId("tsk_first")
    assert repository.mutations[0].patch is patch
    assert repository.mutations[0].idempotency_key == "update-1"


def test_update_accepts_matching_historic_idempotency_replay() -> None:
    """A retry may return original attribution instead of newly allocated values."""
    patch = TaskUpdatePatch(priority=80)
    historical_time = _NOW - timedelta(hours=1)
    replay = _result(
        patch,
        occurred_at=historical_time,
        event_id=TaskEventId("evt_historic"),
        request_id=RequestId("req_historic"),
    )
    repository = _RecordingRepository(replay)

    actual = _application(repository).update(
        UpdateTaskInput(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task=TaskId("tsk_first"),
            expected_version=1,
            patch=patch,
            idempotency_key="update-1",
        )
    )

    assert actual is replay


@pytest.mark.parametrize(
    ("repository", "clock", "identifiers"),
    [
        (object(), _Clock(), _Identifiers()),
        (_RecordingRepository(object()), object(), _Identifiers()),
        (_RecordingRepository(object()), _Clock(), object()),
    ],
)
def test_constructor_runtime_validates_dependencies(
    repository: object,
    clock: object,
    identifiers: object,
) -> None:
    """Missing explicit dependency methods fail during composition."""
    with pytest.raises(TypeError, match="Task lifecycle"):
        TaskLifecycleApplication(
            cast("_RecordingRepository", repository),
            cast("Clock", clock),
            cast("IdentifierFactory", identifiers),
        )


def test_update_rejects_runtime_command_bypass() -> None:
    """Only the validated Human update input can enter orchestration."""
    with pytest.raises(ApplicationError) as captured:
        _application(_RecordingRepository(object())).update(
            cast("UpdateTaskInput", object())
        )

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    "resolved",
    [
        object(),
        _task(project_id=ProjectId("prj_other")),
        _task(key="ACME-2", number=2),
    ],
)
def test_update_rejects_inconsistent_human_key_resolution(resolved: object) -> None:
    """A selector lookup cannot silently cross Task or Project scope."""
    repository = _RecordingRepository(object(), resolved=resolved)

    with pytest.raises(ApplicationError) as captured:
        _application(repository).update(
            UpdateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task="ACME-1",
                expected_version=1,
                patch=TaskUpdatePatch(title="Updated task"),
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.mutations == []


def test_update_converts_invalid_generated_values_to_safe_internal_error() -> None:
    """Malformed clocks or identifiers do not leak validation internals."""

    class _InvalidClock:
        """Clock that violates the required timezone contract."""

        def now(self) -> datetime:
            """Return a deliberately naive timestamp."""
            return _NOW.replace(tzinfo=None)

    repository = _RecordingRepository(object())
    application = TaskLifecycleApplication(
        repository,
        cast("Clock", _InvalidClock()),
        cast("IdentifierFactory", _Identifiers()),
    )

    with pytest.raises(ApplicationError) as captured:
        application.update(
            UpdateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task=TaskId("tsk_first"),
                expected_version=1,
                patch=TaskUpdatePatch(title="Updated task"),
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.mutations == []


def test_update_propagates_stable_application_errors() -> None:
    """Expected persistence failures retain their public error code and message."""
    repository = _RecordingRepository(object(), error=VersionConflictError())

    with pytest.raises(VersionConflictError) as captured:
        _application(repository).update(
            UpdateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task=TaskId("tsk_first"),
                expected_version=1,
                patch=TaskUpdatePatch(title="Updated task"),
            )
        )

    assert captured.value.code is ApplicationErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "result",
    [
        object(),
        _result(TaskUpdatePatch(title="Wrong title")),
        _result(TaskUpdatePatch(title="Updated task"), version=3),
        _result(
            TaskUpdatePatch(title="Updated task"),
            state=TaskState.DONE,
        ),
        _result(
            TaskUpdatePatch(title="Updated task"),
            actor_subject_id=SubjectId("sub_other"),
        ),
        _result(
            TaskUpdatePatch(title="Updated task"),
            event_type=TaskEventType.TASK_BLOCKED,
        ),
        _result(
            TaskUpdatePatch(title="Updated task"),
            event_id=TaskEventId("evt_other"),
        ),
        _result(
            TaskUpdatePatch(title="Updated task"),
            request_id=RequestId("req_other"),
        ),
    ],
)
def test_update_rejects_semantically_mismatched_repository_result(
    result: object,
) -> None:
    """Persistence output must prove the requested patch and exact attribution."""
    patch = TaskUpdatePatch(title="Updated task")

    with pytest.raises(ApplicationError) as captured:
        _application(_RecordingRepository(result)).update(
            UpdateTaskInput(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task=TaskId("tsk_first"),
                expected_version=1,
                patch=patch,
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
