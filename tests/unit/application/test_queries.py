"""Unit tests for read-only Phase 1 query application orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetLocalStatus,
    GetTask,
    ListProjects,
    ListTasks,
    QueryApplication,
    StatusResult,
    TaskNotFoundError,
    TaskPage,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskId,
    TaskState,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workaholic.application import PhaseOneRepository

_NOW = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)


def _instance() -> Instance:
    """Build one valid local Instance.

    Returns:
        Deterministic Instance fixture.

    """
    return Instance(id=InstanceId("ins_local"), created_at=_NOW)


def _subject() -> Subject:
    """Build one valid local Human Owner.

    Returns:
        Deterministic Subject fixture.

    """
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _project(*, key: str = "ACME", suffix: str = "acme") -> Project:
    """Build one valid Project.

    Args:
        key: Immutable Human-facing Project key.
        suffix: Opaque Project identifier suffix.

    Returns:
        Deterministic Project fixture.

    """
    return Project(
        id=ProjectId(f"prj_{suffix}"),
        instance_id=InstanceId("ins_local"),
        key=key,
        name=key,
        created_at=_NOW,
    )


def _task() -> Task:
    """Build one valid initial Task.

    Returns:
        Deterministic Task fixture.

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


def _other_task() -> Task:
    """Build one valid Task belonging to another Project.

    Returns:
        Deterministic cross-Project Task fixture.

    """
    return Task(
        uid=TaskId("tsk_other"),
        project_id=ProjectId("prj_beta"),
        number=1,
        key="BETA-1",
        title="Other task",
        objective="Other task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_other"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _status() -> StatusResult:
    """Build one internally consistent local status.

    Returns:
        Deterministic StatusResult fixture.

    """
    return StatusResult(
        instance=_instance(),
        project=_project(),
        subject=_subject(),
        grant=ProjectGrant(
            subject_id=SubjectId("sub_local"),
            project_id=ProjectId("prj_acme"),
            role=ProjectRole.OWNER,
        ),
    )


class _RecordingRepository:
    """Complete query repository spy with configurable raw outputs."""

    def __init__(self) -> None:
        """Initialize valid defaults and empty command recordings."""
        task = _task()
        self.status_result: object = _status()
        self.projects_result: object = (_project(),)
        self.tasks_result: object = TaskPage(tasks=(task,), next_cursor=None)
        self.task_result: object = task
        self.commands: list[object] = []

    def get_local_status(self, command: GetLocalStatus) -> object:
        """Record and return the configured status output."""
        self.commands.append(command)
        return self.status_result

    def list_projects(self, command: ListProjects) -> object:
        """Record and return the configured Project output."""
        self.commands.append(command)
        return self.projects_result

    def list_tasks(self, command: ListTasks) -> object:
        """Record and return the configured Task-page output."""
        self.commands.append(command)
        return self.tasks_result

    def get_task(self, command: GetTask) -> object:
        """Record and return the configured Task output."""
        self.commands.append(command)
        return self.task_result


class _MissingGetTaskRepository:
    """Near-complete dependency deliberately missing ``get_task``."""

    def get_local_status(self, _command: GetLocalStatus) -> StatusResult:
        """Return a valid status."""
        return _status()

    def list_projects(self, _command: ListProjects) -> tuple[Project, ...]:
        """Return an empty authorized Project list."""
        return ()

    def list_tasks(self, _command: ListTasks) -> TaskPage:
        """Return an empty Task page."""
        return TaskPage(tasks=(), next_cursor=None)


def test_queries_delegate_exact_validated_commands() -> None:
    """Each query delegates once and preserves valid repository results."""
    repository = _RecordingRepository()
    application = QueryApplication(cast("PhaseOneRepository", repository))
    status_command = GetLocalStatus(
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )
    projects_command = ListProjects(
        instance_id=InstanceId("ins_local"),
        subject_id=SubjectId("sub_local"),
    )
    tasks_command = ListTasks(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        limit=25,
    )
    task_command = GetTask(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_first"),
    )

    assert application.status(status_command) is repository.status_result
    assert application.list_projects(projects_command) is repository.projects_result
    assert application.list_tasks(tasks_command) is repository.tasks_result
    assert application.get_task(task_command) is repository.task_result
    assert repository.commands == [
        status_command,
        projects_command,
        tasks_command,
        task_command,
    ]


@pytest.mark.parametrize(
    "repository",
    [object(), _MissingGetTaskRepository()],
)
def test_constructor_requires_every_query_method(repository: object) -> None:
    """Missing repository capabilities fail during composition."""
    with pytest.raises(TypeError, match="Query repository"):
        QueryApplication(cast("PhaseOneRepository", repository))


def test_each_method_runtime_validates_its_command_type() -> None:
    """Bypassing any validated command boundary returns stable INVALID_INPUT."""
    application = QueryApplication(cast("PhaseOneRepository", _RecordingRepository()))
    invocations: tuple[Callable[[], object], ...] = (
        lambda: application.status(cast("GetLocalStatus", object())),
        lambda: application.list_projects(cast("ListProjects", object())),
        lambda: application.list_tasks(cast("ListTasks", object())),
        lambda: application.get_task(cast("GetTask", object())),
    )

    for invoke in invocations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_invalid_repository_results_map_to_safe_internal_errors() -> None:
    """No malformed adapter value crosses the application result boundary."""
    repository = _RecordingRepository()
    application = QueryApplication(cast("PhaseOneRepository", repository))
    status_command = GetLocalStatus(
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )
    projects_command = ListProjects(
        instance_id=InstanceId("ins_local"),
        subject_id=SubjectId("sub_local"),
    )
    tasks_command = ListTasks(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )
    task_command = GetTask(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task="ACME-1",
    )
    repository.status_result = object()
    repository.projects_result = [_project()]
    repository.tasks_result = object()
    repository.task_result = object()
    invocations: tuple[Callable[[], object], ...] = (
        lambda: application.status(status_command),
        lambda: application.list_projects(projects_command),
        lambda: application.list_tasks(tasks_command),
        lambda: application.get_task(task_command),
    )

    for invoke in invocations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_unsorted_project_result_is_rejected() -> None:
    """The application enforces stable Project ordering at its output boundary."""
    repository = _RecordingRepository()
    repository.projects_result = (
        _project(key="BETA", suffix="beta"),
        _project(key="ACME", suffix="acme"),
    )
    application = QueryApplication(cast("PhaseOneRepository", repository))

    with pytest.raises(ApplicationError) as captured:
        application.list_projects(
            ListProjects(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_type_correct_cross_selection_results_are_rejected() -> None:
    """Repository results must match every exact identity in their query."""
    repository = _RecordingRepository()
    application = QueryApplication(cast("PhaseOneRepository", repository))
    other_instance = Instance(id=InstanceId("ins_other"), created_at=_NOW)
    other_project = Project(
        id=ProjectId("prj_other"),
        instance_id=other_instance.id,
        key="OTHER",
        name="Other",
        created_at=_NOW,
    )
    other_subject = Subject(
        id=SubjectId("sub_other"),
        kind=SubjectKind.HUMAN,
        display_name="Other operator",
        enabled=True,
        is_instance_admin=True,
    )
    repository.status_result = StatusResult(
        instance=other_instance,
        project=other_project,
        subject=other_subject,
        grant=ProjectGrant(
            subject_id=other_subject.id,
            project_id=other_project.id,
            role=ProjectRole.OWNER,
        ),
    )
    repository.projects_result = (other_project,)
    repository.tasks_result = TaskPage(
        tasks=(_other_task(),),
        next_cursor=None,
    )
    repository.task_result = _task()
    invocations: tuple[Callable[[], object], ...] = (
        lambda: application.status(
            GetLocalStatus(
                instance_id=InstanceId("ins_local"),
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
            )
        ),
        lambda: application.list_projects(
            ListProjects(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
            )
        ),
        lambda: application.list_tasks(
            ListTasks(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
            )
        ),
        lambda: application.get_task(
            GetTask(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task="ACME-404",
            )
        ),
    )

    for invoke in invocations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_typed_repository_error_propagates_unchanged() -> None:
    """Expected query failures retain their stable code and safe message."""

    class _MissingTaskRepository(_RecordingRepository):
        """Repository spy that reports an expected missing Task."""

        def get_task(self, _command: GetTask) -> object:
            """Raise the stable missing-Task error."""
            raise TaskNotFoundError

    application = QueryApplication(cast("PhaseOneRepository", _MissingTaskRepository()))

    with pytest.raises(TaskNotFoundError) as captured:
        application.get_task(
            GetTask(
                project_id=ProjectId("prj_acme"),
                subject_id=SubjectId("sub_local"),
                task="ACME-404",
            )
        )

    assert captured.value.code is ApplicationErrorCode.TASK_NOT_FOUND
