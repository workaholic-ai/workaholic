"""Explicit Phase 1 Session fakes shared by CLI unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from workaholic.application import BootstrapResult, StatusResult, TaskPage
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
    WorkspaceBinding,
)

if TYPE_CHECKING:
    from workaholic.session import (
        ProjectListRequest,
        StatusRequest,
        TaskCreateRequest,
        TaskGetRequest,
        TaskListRequest,
        UpRequest,
        WorkaholicSession,
    )

_NOW = datetime(2026, 7, 30, 12, 30, tzinfo=UTC)


def instance() -> Instance:
    """Build the deterministic CLI-test Instance.

    Returns:
        Validated Instance.

    """
    return Instance(id=InstanceId("ins_local"), created_at=_NOW)


def subject() -> Subject:
    """Build the deterministic CLI-test local Human.

    Returns:
        Validated enabled Human administrator.

    """
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def project(
    *,
    key: str = "ACME",
    identifier: str = "prj_acme",
) -> Project:
    """Build one deterministic CLI-test Project.

    Args:
        key: Immutable Project key.
        identifier: Canonical Project identifier text.

    Returns:
        Validated Project.

    """
    return Project(
        id=ProjectId(identifier),
        instance_id=instance().id,
        key=key,
        created_at=_NOW,
    )


def grant(selected_project: Project | None = None) -> ProjectGrant:
    """Build the deterministic Owner grant.

    Args:
        selected_project: Optional Project to authorize.

    Returns:
        Validated local Human Owner grant.

    """
    authorized_project = project() if selected_project is None else selected_project
    return ProjectGrant(
        subject_id=subject().id,
        project_id=authorized_project.id,
        role=ProjectRole.OWNER,
    )


def bootstrap_result() -> BootstrapResult:
    """Build one internally consistent local bootstrap result.

    Returns:
        Validated deterministic bootstrap result.

    """
    selected_project = project()
    return BootstrapResult(
        instance=instance(),
        project=selected_project,
        subject=subject(),
        grant=grant(selected_project),
        workspace=WorkspaceBinding(
            context_version=1,
            profile="local",
            instance_id=instance().id,
            project_id=selected_project.id,
            project_key=selected_project.key,
            workspace_root=".",
        ),
    )


def status_result() -> StatusResult:
    """Build one internally consistent local status result.

    Returns:
        Validated deterministic status result.

    """
    selected_project = project()
    return StatusResult(
        instance=instance(),
        project=selected_project,
        subject=subject(),
        grant=grant(selected_project),
    )


def task() -> Task:
    """Build one deterministic CLI-test Task.

    Returns:
        Validated initial Task.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=project().id,
        number=1,
        key="ACME-1",
        title="First persistent task",
        objective="First persistent task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=subject().id,
        created_at=_NOW,
        updated_at=_NOW,
    )


class RecordingSession:
    """Configurable explicit fake for the complete Phase 1 Session boundary."""

    def __init__(self) -> None:
        """Initialize deterministic results, failures, and call logs."""
        first_task = task()
        self.up_result = bootstrap_result()
        self.status_result = status_result()
        self.projects_result: tuple[Project, ...] = (project(),)
        self.create_task_result = first_task
        self.task_page_result = TaskPage(tasks=(first_task,), next_cursor=None)
        self.get_task_result = first_task
        self.failures: dict[str, Exception] = {}
        self.up_requests: list[UpRequest] = []
        self.status_requests: list[StatusRequest] = []
        self.project_list_requests: list[ProjectListRequest] = []
        self.task_create_requests: list[TaskCreateRequest] = []
        self.task_list_requests: list[TaskListRequest] = []
        self.task_get_requests: list[TaskGetRequest] = []

    def up(self, request: UpRequest) -> BootstrapResult:
        """Record and answer one bootstrap request."""
        self.up_requests.append(request)
        self._raise_failure("up")
        return self.up_result

    def status(self, request: StatusRequest) -> StatusResult:
        """Record and answer one status request."""
        self.status_requests.append(request)
        self._raise_failure("status")
        return self.status_result

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """Record and answer one Project-list request."""
        self.project_list_requests.append(request)
        self._raise_failure("list_projects")
        return self.projects_result

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Record and answer one Task-create request."""
        self.task_create_requests.append(request)
        self._raise_failure("create_task")
        return self.create_task_result

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """Record and answer one Task-list request."""
        self.task_list_requests.append(request)
        self._raise_failure("list_tasks")
        return self.task_page_result

    def get_task(self, request: TaskGetRequest) -> Task:
        """Record and answer one Task-get request."""
        self.task_get_requests.append(request)
        self._raise_failure("get_task")
        return self.get_task_result

    def _raise_failure(self, operation: str) -> None:
        """Raise the configured failure for one operation, if present.

        Args:
            operation: Stable fake-operation name.

        Raises:
            Exception: Configured safe or unexpected failure.

        """
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure


class SessionProviderSpy:
    """Callable provider that records command-scoped Session acquisition."""

    def __init__(self, session: RecordingSession) -> None:
        """Initialize the provider with one explicit fake Session.

        Args:
            session: Session returned for every test invocation.

        """
        self.session = session
        self.call_count = 0

    def __call__(self) -> WorkaholicSession:
        """Return the configured Session and record acquisition."""
        self.call_count += 1
        return self.session
