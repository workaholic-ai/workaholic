"""Explicit Phase 1 Session fakes shared by CLI unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Never

from workaholic.application import (
    BootstrapResult,
    ContextResult,
    ProjectCreationResult,
    StatusResult,
    TaskDetails,
    TaskListView,
    TaskMutationResult,
    TaskPage,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskReadiness,
    TaskState,
    WorkspaceBinding,
)

if TYPE_CHECKING:
    from workaholic.application import (
        TaskEventPage,
        TaskSubmissionResult,
    )
    from workaholic.session import (
        ContextRequest,
        ProjectBindRequest,
        ProjectCreateRequest,
        ProjectListRequest,
        StatusRequest,
        TaskAddDependencyRequest,
        TaskApproveRequest,
        TaskBlockRequest,
        TaskCancelRequest,
        TaskCreateRequest,
        TaskDetailsRequest,
        TaskEventsRequest,
        TaskGetRequest,
        TaskListByViewRequest,
        TaskListRequest,
        TaskRejectRequest,
        TaskRemoveDependencyRequest,
        TaskSubmitRequest,
        TaskUnblockRequest,
        TaskUpdateRequest,
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
    name: str | None = None,
    identifier: str = "prj_acme",
) -> Project:
    """Build one deterministic CLI-test Project.

    Args:
        key: Immutable Project key.
        name: Optional Human-readable Project name.
        identifier: Canonical Project identifier text.

    Returns:
        Validated Project.

    """
    return Project(
        id=ProjectId(identifier),
        instance_id=instance().id,
        key=key,
        name=key if name is None else name,
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


def context_result(
    *,
    selected_project: Project | None = None,
    profile: str = "local",
    workspace_root: Path | None = Path("/work/acme"),
) -> ContextResult:
    """Build one internally consistent effective-context result.

    Args:
        selected_project: Optional Project selected by the result.
        profile: Trusted profile name.
        workspace_root: Optional absolute Workspace root.

    Returns:
        Validated deterministic effective-context result.

    """
    effective_project = project() if selected_project is None else selected_project
    context_source = (
        None if workspace_root is None else workspace_root / ".workaholic.env"
    )
    return ContextResult(
        profile=profile,
        instance=instance(),
        project=effective_project,
        subject=subject(),
        grant=grant(effective_project),
        workspace_root=workspace_root,
        context_source=context_source,
    )


def project_creation_result() -> ProjectCreationResult:
    """Build one internally consistent Project-creation result.

    Returns:
        Validated deterministic Project and Owner grant.

    """
    created_project = project(
        key="DOCS",
        name="Documentation",
        identifier="prj_docs",
    )
    return ProjectCreationResult(
        project=created_project,
        grant=grant(created_project),
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
    """Configurable explicit fake for the cumulative Session boundary."""

    def __init__(self) -> None:
        """Initialize deterministic results, failures, and call logs."""
        first_task = task()
        self.up_result = bootstrap_result()
        self.status_result = status_result()
        self.context_result = context_result()
        self.projects_result: tuple[Project, ...] = (project(),)
        self.project_creation_result = project_creation_result()
        self.project_binding_result = context_result()
        self.create_task_result = first_task
        self.task_page_result = TaskPage(tasks=(first_task,), next_cursor=None)
        self.get_task_result = first_task
        ready = TaskReadiness(
            ready=True,
            running=False,
            scheduled=False,
            stale=False,
            awaiting_review=False,
            reasons=(),
        )
        mutation_task = Task(
            uid=first_task.uid,
            project_id=first_task.project_id,
            number=first_task.number,
            key=first_task.key,
            title=first_task.title,
            objective=first_task.objective,
            state=first_task.state,
            priority=first_task.priority,
            version=2,
            created_by=first_task.created_by,
            created_at=first_task.created_at,
            updated_at=_NOW,
        )
        mutation_event = TaskEvent(
            id=TaskEventId("evt_updated"),
            cursor=2,
            task_uid=mutation_task.uid,
            project_id=mutation_task.project_id,
            actor_subject_id=subject().id,
            request_id=RequestId("req_updated"),
            event_type=TaskEventType.TASK_UPDATED,
            occurred_at=_NOW,
            payload={},
        )
        self.task_mutation_result = TaskMutationResult(
            task=mutation_task,
            events=(mutation_event,),
        )
        self.task_details_result = TaskDetails(
            task=first_task,
            readiness=ready,
            prerequisites=(),
            current_result=None,
        )
        self.task_view_page_result = TaskPage(
            tasks=(first_task,),
            readiness=(ready,),
            next_cursor=None,
            view=TaskListView.ALL,
        )
        self.failures: dict[str, Exception] = {}
        self.up_requests: list[UpRequest] = []
        self.status_requests: list[StatusRequest] = []
        self.context_requests: list[ContextRequest] = []
        self.project_list_requests: list[ProjectListRequest] = []
        self.project_create_requests: list[ProjectCreateRequest] = []
        self.project_bind_requests: list[ProjectBindRequest] = []
        self.task_create_requests: list[TaskCreateRequest] = []
        self.task_list_requests: list[TaskListRequest] = []
        self.task_get_requests: list[TaskGetRequest] = []
        self.task_update_requests: list[TaskUpdateRequest] = []
        self.task_block_requests: list[TaskBlockRequest] = []
        self.task_unblock_requests: list[TaskUnblockRequest] = []
        self.task_cancel_requests: list[TaskCancelRequest] = []
        self.task_add_dependency_requests: list[TaskAddDependencyRequest] = []
        self.task_remove_dependency_requests: list[TaskRemoveDependencyRequest] = []
        self.task_details_requests: list[TaskDetailsRequest] = []
        self.task_view_requests: list[TaskListByViewRequest] = []

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

    def context(self, request: ContextRequest) -> ContextResult:
        """Record and answer one effective-context request."""
        self.context_requests.append(request)
        self._raise_failure("context")
        return self.context_result

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """Record and answer one Project-list request."""
        self.project_list_requests.append(request)
        self._raise_failure("list_projects")
        return self.projects_result

    def create_project(
        self,
        request: ProjectCreateRequest,
    ) -> ProjectCreationResult:
        """Record and answer one Project-create request."""
        self.project_create_requests.append(request)
        self._raise_failure("create_project")
        return self.project_creation_result

    def bind_project(self, request: ProjectBindRequest) -> ContextResult:
        """Record and answer one Project-bind request."""
        self.project_bind_requests.append(request)
        self._raise_failure("bind_project")
        return self.project_binding_result

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

    def update_task(self, request: TaskUpdateRequest) -> TaskMutationResult:
        """Record and answer one Task-definition update."""
        self.task_update_requests.append(request)
        self._raise_failure("update_task")
        return self.task_mutation_result

    def block_task(self, request: TaskBlockRequest) -> TaskMutationResult:
        """Record and answer one Task block."""
        self.task_block_requests.append(request)
        self._raise_failure("block_task")
        return self.task_mutation_result

    def unblock_task(self, request: TaskUnblockRequest) -> TaskMutationResult:
        """Record and answer one Task unblock."""
        self.task_unblock_requests.append(request)
        self._raise_failure("unblock_task")
        return self.task_mutation_result

    def cancel_task(self, request: TaskCancelRequest) -> TaskMutationResult:
        """Record and answer one Task cancellation."""
        self.task_cancel_requests.append(request)
        self._raise_failure("cancel_task")
        return self.task_mutation_result

    def add_task_dependency(
        self,
        request: TaskAddDependencyRequest,
    ) -> TaskMutationResult:
        """Record and answer one dependency addition."""
        self.task_add_dependency_requests.append(request)
        self._raise_failure("add_task_dependency")
        return self.task_mutation_result

    def remove_task_dependency(
        self,
        request: TaskRemoveDependencyRequest,
    ) -> TaskMutationResult:
        """Record and answer one dependency removal."""
        self.task_remove_dependency_requests.append(request)
        self._raise_failure("remove_task_dependency")
        return self.task_mutation_result

    def submit_human_result(
        self,
        _request: TaskSubmitRequest,
    ) -> TaskSubmissionResult:
        """Fail an unexpected Phase 3 Human submission."""
        self._unexpected_phase_three("submit Results")

    def approve_result(
        self,
        _request: TaskApproveRequest,
    ) -> TaskSubmissionResult:
        """Fail an unexpected Phase 3 Result approval."""
        self._unexpected_phase_three("approve Results")

    def reject_result(
        self,
        _request: TaskRejectRequest,
    ) -> TaskSubmissionResult:
        """Fail an unexpected Phase 3 Result rejection."""
        self._unexpected_phase_three("reject Results")

    def get_task_details(self, request: TaskDetailsRequest) -> TaskDetails:
        """Record and answer one complete Task-details query."""
        self.task_details_requests.append(request)
        self._raise_failure("get_task_details")
        return self.task_details_result

    def list_tasks_by_view(self, request: TaskListByViewRequest) -> TaskPage:
        """Record and answer one view-bound Task page query."""
        self.task_view_requests.append(request)
        self._raise_failure("list_tasks_by_view")
        return self.task_view_page_result

    def read_task_events(self, _request: TaskEventsRequest) -> TaskEventPage:
        """Fail an unexpected Phase 3 Task-event query."""
        self._unexpected_phase_three("query Task events")

    @staticmethod
    def _unexpected_phase_three(operation: str) -> Never:
        """Fail when a pre-Phase-3 CLI test crosses the newer Session surface.

        Args:
            operation: Short semantic operation description.

        Raises:
            AssertionError: Always, because the call is outside test scope.

        """
        message = f"Phase 2 CLI tests must not {operation}."
        raise AssertionError(message)

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
