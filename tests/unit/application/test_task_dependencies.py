"""Unit tests for optimistic Task dependency application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    AddTaskDependencyInput,
    AddTaskDependencyMutation,
    ApplicationError,
    ApplicationErrorCode,
    DependencyConflictError,
    GetTask,
    RemoveTaskDependencyInput,
    RemoveTaskDependencyMutation,
    TaskDependencyApplication,
    TaskMutationResult,
)
from workaholic.domain import (
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

_NOW = datetime(2026, 8, 1, 10, 0, 0, 123456, tzinfo=UTC)
_CREATED_AT = _NOW - timedelta(hours=1)


def _task(number: int = 1, *, depends_on: tuple[TaskId, ...] = ()) -> Task:
    """Build one deterministic Task fixture.

    Args:
        number: Project-local Task number.
        depends_on: Ordered prerequisite identities.

    Returns:
        Valid open Task.

    """
    return Task(
        uid=TaskId(f"tsk_{number}"),
        project_id=ProjectId("prj_acme"),
        number=number,
        key=f"ACME-{number}",
        title=f"Task {number}",
        objective=f"Task {number}",
        state=TaskState.OPEN,
        priority=50,
        depends_on=depends_on,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _result(*, add: bool, historical: bool = False) -> TaskMutationResult:
    """Build one valid dependency mutation result.

    Args:
        add: Whether the prerequisite is present after the operation.
        historical: Whether replay attribution predates the current invocation.

    Returns:
        Internally consistent mutation result.

    """
    occurred_at = _NOW - timedelta(minutes=5) if historical else _NOW
    task = replace(
        _task(),
        depends_on=(TaskId("tsk_2"),) if add else (),
        version=2,
        updated_at=occurred_at,
    )
    event = TaskEvent(
        id=TaskEventId("evt_historic" if historical else "evt_dependency"),
        cursor=3,
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        request_id=RequestId("req_historic" if historical else "req_dependency"),
        event_type=TaskEventType.TASK_UPDATED,
        occurred_at=occurred_at,
        payload={
            "dependency": "added" if add else "removed",
            "prerequisite_uid": "tsk_2",
            "version": 2,
        },
    )
    return TaskMutationResult(task=task, events=(event,))


class _Clock:
    """Deterministic dependency-test clock."""

    def now(self) -> datetime:
        """Return the authoritative mutation time."""
        return _NOW


class _Identifiers:
    """Deterministic dependency-test identity factory subset."""

    def new_event_id(self) -> TaskEventId:
        """Return the candidate dependency event identity."""
        return TaskEventId("evt_dependency")

    def new_request_id(self) -> RequestId:
        """Return the candidate dependency request identity."""
        return RequestId("req_dependency")


class _Repository:
    """Dependency repository spy with configurable semantic output."""

    def __init__(self, *, result: object | None = None) -> None:
        """Initialize valid defaults and empty recordings.

        Args:
            result: Optional raw mutation result override.

        """
        self.result = _result(add=True) if result is None else result
        self.queries: list[GetTask] = []
        self.additions: list[AddTaskDependencyMutation] = []
        self.removals: list[RemoveTaskDependencyMutation] = []
        self.lookup_results: dict[str, object] = {
            "ACME-1": _task(),
            "ACME-2": _task(2),
        }
        self.error: ApplicationError | None = None

    def get_task(self, command: GetTask) -> Task:
        """Record and resolve one Human Task key."""
        self.queries.append(command)
        return cast("Task", self.lookup_results[cast("str", command.task)])

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Record and return one dependency addition."""
        self.additions.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskMutationResult", self.result)

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Record and return one dependency removal."""
        self.removals.append(mutation)
        if self.error is not None:
            raise self.error
        return cast("TaskMutationResult", self.result)


def _application(repository: _Repository) -> TaskDependencyApplication:
    """Compose dependency orchestration with deterministic collaborators."""
    return TaskDependencyApplication(
        repository,
        cast("Clock", _Clock()),
        cast("IdentifierFactory", _Identifiers()),
    )


def _invoke_invalid_command(
    application: TaskDependencyApplication,
    operation: str,
) -> None:
    """Invoke one operation with a deliberately invalid runtime command.

    Args:
        application: Dependency application under test.
        operation: Exact add or remove operation label.

    """
    if operation == "add":
        application.add(cast("AddTaskDependencyInput", object()))
    else:
        application.remove(cast("RemoveTaskDependencyInput", object()))


def test_add_by_canonical_ids_builds_exact_attributable_mutation() -> None:
    """Canonical selectors avoid reads and preserve optimistic intent exactly."""
    repository = _Repository()
    command = AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_1"),
        prerequisite=TaskId("tsk_2"),
        expected_version=1,
        idempotency_key="dependency-add-1",
    )

    result = _application(repository).add(command)

    assert result is repository.result
    assert repository.queries == []
    assert repository.additions == [
        AddTaskDependencyMutation(
            task_uid=TaskId("tsk_1"),
            prerequisite_uid=TaskId("tsk_2"),
            project_id=ProjectId("prj_acme"),
            actor_subject_id=SubjectId("sub_local"),
            event_id=TaskEventId("evt_dependency"),
            request_id=RequestId("req_dependency"),
            occurred_at=_NOW,
            expected_version=1,
            idempotency_key="dependency-add-1",
        )
    ]


def test_remove_resolves_both_human_keys_once_and_dispatches_remove() -> None:
    """Human selectors resolve inside the exact selected Project."""
    repository = _Repository(result=_result(add=False))
    command = RemoveTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task="ACME-1",
        prerequisite="ACME-2",
        expected_version=1,
    )

    result = _application(repository).remove(command)

    assert result is repository.result
    assert repository.queries == [
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-1",
        ),
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-2",
        ),
    ]
    assert repository.removals[0].task_uid == TaskId("tsk_1")
    assert repository.removals[0].prerequisite_uid == TaskId("tsk_2")


def test_idempotent_replay_accepts_historic_attribution() -> None:
    """A matching replay may retain its originally committed identities and time."""
    repository = _Repository(result=_result(add=True, historical=True))
    command = AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_1"),
        prerequisite=TaskId("tsk_2"),
        expected_version=1,
        idempotency_key="dependency-add-1",
    )

    assert _application(repository).add(command) is repository.result


@pytest.mark.parametrize("operation", ["add", "remove"])
def test_runtime_command_type_is_validated(operation: str) -> None:
    """Bypassing Pydantic commands returns a stable input failure."""
    application = _application(_Repository())

    with pytest.raises(ApplicationError) as captured:
        _invoke_invalid_command(application, operation)

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_invalid_human_key_resolution_is_rejected_before_mutation() -> None:
    """A repository cannot substitute another Project's Task during resolution."""
    repository = _Repository()
    repository.lookup_results["ACME-1"] = replace(
        _task(),
        project_id=ProjectId("prj_other"),
        key="OTHER-1",
    )
    command = AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task="ACME-1",
        prerequisite=TaskId("tsk_2"),
        expected_version=1,
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).add(command)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.additions == []


@pytest.mark.parametrize(
    "result",
    [
        object(),
        _result(add=False),
        TaskMutationResult(
            task=replace(_result(add=True).task, version=3),
            events=_result(add=True).events,
        ),
        TaskMutationResult(
            task=_result(add=True).task,
            events=(
                replace(
                    _result(add=True).events[0],
                    payload={
                        "dependency": "added",
                        "prerequisite_uid": "tsk_other",
                        "version": 2,
                    },
                ),
            ),
        ),
    ],
    ids=["wrong-type", "edge-absent", "wrong-version", "wrong-payload"],
)
def test_invalid_persistence_results_are_rejected(result: object) -> None:
    """Malformed semantic outputs never cross the application boundary."""
    repository = _Repository(result=result)
    command = AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_1"),
        prerequisite=TaskId("tsk_2"),
        expected_version=1,
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).add(command)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_typed_repository_failure_propagates_unchanged() -> None:
    """Expected dependency outcomes retain their stable public meaning."""
    repository = _Repository()
    repository.error = DependencyConflictError()
    command = AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_1"),
        prerequisite=TaskId("tsk_2"),
        expected_version=1,
    )

    with pytest.raises(DependencyConflictError) as captured:
        _application(repository).add(command)

    assert captured.value.code is ApplicationErrorCode.DEPENDENCY_CONFLICT


@pytest.mark.parametrize(
    ("dependency", "missing"),
    [
        (object(), "get_task"),
        (_Repository(), "clock"),
        (_Repository(), "identifiers"),
    ],
)
def test_constructor_validates_explicit_collaborators(
    dependency: object,
    missing: str,
) -> None:
    """Composition fails immediately for incomplete collaborators."""
    repository: object = dependency if missing == "get_task" else _Repository()
    clock: object = object() if missing == "clock" else _Clock()
    identifiers: object = object() if missing == "identifiers" else _Identifiers()

    with pytest.raises(TypeError, match="Task dependency"):
        TaskDependencyApplication(
            cast("_Repository", repository),
            cast("Clock", clock),
            cast("IdentifierFactory", identifiers),
        )
