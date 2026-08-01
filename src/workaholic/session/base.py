"""Transport-neutral cumulative Session and local boundary ports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from workaholic.domain import InstanceId, SubjectId, WorkspaceBinding

if TYPE_CHECKING:
    from workaholic.application import (
        BootstrapResult,
        ContextResult,
        ProjectCreationResult,
        StatusResult,
        TaskDetails,
        TaskEventPage,
        TaskMutationResult,
        TaskPage,
        TaskSubmissionResult,
    )
    from workaholic.domain import (
        Project,
        Task,
    )
    from workaholic.session.local import LocalRuntime
    from workaholic.session.models import (
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
    )


@dataclass(frozen=True, slots=True)
class LocalIdentity:
    """Trusted Instance and bootstrap-Human identities selected from one runtime."""

    instance_id: InstanceId
    subject_id: SubjectId

    def __post_init__(self) -> None:
        """Validate both strongly typed identity values."""
        instance_value: object = self.instance_id
        subject_value: object = self.subject_id
        if not isinstance(instance_value, InstanceId) or not isinstance(
            subject_value, SubjectId
        ):
            message = "Local identity requires typed Instance and Subject IDs."
            raise TypeError(message)


@dataclass(frozen=True, slots=True)
class WorkspaceContextSelection:
    """Discovered binding plus canonical safe filesystem locations."""

    binding: WorkspaceBinding
    context_source: Path
    workspace_root: Path

    def __post_init__(self) -> None:
        """Validate the discovered context and physical path relationship."""
        binding_value: object = self.binding
        context_source_value: object = self.context_source
        workspace_root_value: object = self.workspace_root
        if not isinstance(binding_value, WorkspaceBinding):
            message = "Workspace selection requires a WorkspaceBinding."
            raise TypeError(message)
        if not isinstance(context_source_value, Path) or not isinstance(
            workspace_root_value,
            Path,
        ):
            message = "Workspace selection paths must be pathlib Paths."
            raise TypeError(message)
        if (
            not self.context_source.is_absolute()
            or not self.workspace_root.is_absolute()
            or self.context_source.name != ".workaholic.env"
        ):
            message = "Workspace selection paths must be canonical and absolute."
            raise ValueError(message)
        context_directory = self.context_source.parent
        if self.workspace_root != context_directory and (
            context_directory not in self.workspace_root.parents
        ):
            message = "Workspace selection root must remain under its context."
            raise ValueError(message)


class WorkspaceContextGateway(Protocol):
    """Discover and durably write Workspace bindings."""

    def discover(self) -> WorkspaceContextSelection | None:
        """Discover the nearest valid context through physical ancestors.

        Returns:
            Nearest validated selection, or ``None`` when no context exists.

        """
        ...

    def write_current(self, binding: WorkspaceBinding) -> Path:
        """Durably write or verify the exact current Workspace binding.

        Args:
            binding: Validated binding produced by committed bootstrap.

        Returns:
            Exact context-file path.

        """
        ...

    def bind(
        self,
        directory: Path | None,
        binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Durably bind an explicit or current Workspace directory.

        Args:
            directory: Explicit target, or ``None`` for the current directory.
            binding: Authoritative Project binding.
            replace: Whether valid conflicting context may be replaced.

        Returns:
            Canonical physical context-file path.

        """
        ...


class ProfileResolver(Protocol):
    """Resolve one trusted profile name using configured precedence."""

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Resolve one profile without accepting repository-controlled paths.

        Args:
            explicit_profile: Validated caller selector when supplied.
            discovered_profile: Validated nearest-context selector when present.

        Returns:
            Trusted configured profile name.

        """
        ...


class LocalRuntimeOpener(Protocol):
    """Open one application runtime selected by trusted profile name."""

    def open(self, profile: str) -> LocalRuntime:
        """Open one profile-selected local application runtime.

        Args:
            profile: Trusted profile name returned by ProfileResolver.

        Returns:
            Runtime containing semantic services and local identity selection.

        """
        ...


class LocalActorSelector(Protocol):
    """Select the trusted bootstrap Human from local state."""

    def select(self, binding: WorkspaceBinding) -> SubjectId:
        """Select the actor associated with one authoritative local binding.

        Args:
            binding: Untrusted context identities used to scope selection.

        Returns:
            Selected local bootstrap Human identity.

        """
        ...


class TaskSession(Protocol):
    """Presentation-independent established bootstrap and Task operations."""

    def up(self, request: UpRequest) -> BootstrapResult:
        """Bootstrap or locate the local Project and bind the Workspace.

        Args:
            request: Validated bootstrap and optional profile request.

        Returns:
            Committed bootstrap graph after durable context binding.

        """
        ...

    def status(self, request: StatusRequest) -> StatusResult:
        """Return authorized status for the current Workspace.

        Args:
            request: Validated optional profile and Project selectors.

        Returns:
            Status matching authoritative context and actor identities.

        """
        ...

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """Return Projects authorized for the selected local Human.

        Args:
            request: Validated optional profile selector.

        Returns:
            Authorized Projects ordered by immutable key.

        """
        ...

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Create one attributable Task in the selected Project.

        Args:
            request: Validated Task input and optional Project selector.

        Returns:
            Atomically committed Task.

        """
        ...

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """Return one deterministic page from the selected Project.

        Args:
            request: Validated pagination and Project-scope selection.

        Returns:
            Stable Project-bound Task page.

        """
        ...

    def get_task(self, request: TaskGetRequest) -> Task:
        """Return one selected-Project Task by UID or Human key.

        Args:
            request: Validated Task and optional Project selectors.

        Returns:
            Matching immutable Task.

        """
        ...


class WorkaholicSession(TaskSession, Protocol):
    """Complete presentation-independent cumulative product operations."""

    def context(self, request: ContextRequest) -> ContextResult:
        """Return the effective trusted profile and Workspace selection.

        Args:
            request: Validated optional profile and Project selectors.

        Returns:
            Effective identity and safe context paths.

        """
        ...

    def create_project(
        self,
        request: ProjectCreateRequest,
    ) -> ProjectCreationResult:
        """Create one named Project in the selected initialized profile.

        Args:
            request: Validated Project creation request.

        Returns:
            Atomically committed Project and creator Owner grant.

        """
        ...

    def bind_project(self, request: ProjectBindRequest) -> ContextResult:
        """Bind one existing Project to a local Workspace directory.

        Args:
            request: Validated Project, path, profile, and replacement intent.

        Returns:
            Effective context after the durable binding.

        """
        ...

    def update_task(self, request: TaskUpdateRequest) -> TaskMutationResult:
        """Update editable Task fields at an expected version.

        Args:
            request: Validated optimistic Task patch intent.

        Returns:
            Committed Task and its attributable update event.

        """
        ...

    def block_task(self, request: TaskBlockRequest) -> TaskMutationResult:
        """Block one open Task at an expected version.

        Args:
            request: Validated optimistic block intent.

        Returns:
            Committed blocked Task and event.

        """
        ...

    def unblock_task(self, request: TaskUnblockRequest) -> TaskMutationResult:
        """Return one blocked Task to open.

        Args:
            request: Validated optimistic unblock intent.

        Returns:
            Committed open Task and event.

        """
        ...

    def cancel_task(self, request: TaskCancelRequest) -> TaskMutationResult:
        """Cancel one mutable Task at an expected version.

        Args:
            request: Validated optimistic cancellation intent.

        Returns:
            Committed cancelled Task and event.

        """
        ...

    def add_task_dependency(
        self,
        request: TaskAddDependencyRequest,
    ) -> TaskMutationResult:
        """Add one same-Project prerequisite.

        Args:
            request: Validated optimistic dependency intent.

        Returns:
            Committed dependent Task and event.

        """
        ...

    def remove_task_dependency(
        self,
        request: TaskRemoveDependencyRequest,
    ) -> TaskMutationResult:
        """Remove one same-Project prerequisite.

        Args:
            request: Validated optimistic dependency intent.

        Returns:
            Committed dependent Task and event.

        """
        ...

    def submit_human_result(
        self,
        request: TaskSubmitRequest,
    ) -> TaskSubmissionResult:
        """Submit direct Human work without an Agent Attempt.

        Args:
            request: Validated optimistic Human submission intent.

        Returns:
            Committed Task, Result, and ordered events.

        """
        ...

    def approve_result(self, request: TaskApproveRequest) -> TaskSubmissionResult:
        """Approve the current pending Result.

        Args:
            request: Validated optimistic approval intent.

        Returns:
            Completed Task, approved Result, and ordered events.

        """
        ...

    def reject_result(self, request: TaskRejectRequest) -> TaskSubmissionResult:
        """Reject the current pending Result.

        Args:
            request: Validated optimistic rejection intent.

        Returns:
            Reopened Task, rejected Result, and event.

        """
        ...

    def get_task_details(self, request: TaskDetailsRequest) -> TaskDetails:
        """Return complete Task definition and derived details.

        Args:
            request: Validated Task detail selection.

        Returns:
            Complete Task, dependencies, readiness, and current Result.

        """
        ...

    def list_tasks_by_view(self, request: TaskListByViewRequest) -> TaskPage:
        """Return one deterministic Task readiness or lifecycle view.

        Args:
            request: Validated view, selection, and pagination intent.

        Returns:
            View-bound Task page with aligned readiness.

        """
        ...

    def read_task_events(self, request: TaskEventsRequest) -> TaskEventPage:
        """Return one bounded attributable TaskEvent history page.

        Args:
            request: Validated Task selection and cursor intent.

        Returns:
            Polling-safe TaskEvent page.

        """
        ...
