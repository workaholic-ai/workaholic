"""Transport-neutral Phase 1 Session and local boundary ports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult, StatusResult, TaskPage
    from workaholic.domain import (
        Project,
        SubjectId,
        Task,
        WorkspaceBinding,
    )
    from workaholic.session.models import (
        ProjectListRequest,
        StatusRequest,
        TaskCreateRequest,
        TaskGetRequest,
        TaskListRequest,
        UpRequest,
    )


class WorkspaceContextGateway(Protocol):
    """Read and durably write exact-directory Workspace bindings."""

    def read_current(self) -> WorkspaceBinding:
        """Read the exact current directory's Workspace binding.

        Returns:
            Validated current Workspace binding.

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


class LocalActorSelector(Protocol):
    """Select the sole trusted Phase 1 bootstrap Human from local state."""

    def select(self, binding: WorkspaceBinding) -> SubjectId:
        """Select the actor associated with one authoritative local binding.

        Args:
            binding: Untrusted context identities used to scope selection.

        Returns:
            Selected local bootstrap Human identity.

        """
        ...


class WorkaholicSession(Protocol):
    """Presentation-independent Phase 1 product operations."""

    def up(self, request: UpRequest) -> BootstrapResult:
        """Bootstrap or locate the local Project and bind the Workspace.

        Args:
            request: Validated context-free bootstrap request.

        Returns:
            Committed bootstrap graph after durable context binding.

        """
        ...

    def status(self, request: StatusRequest) -> StatusResult:
        """Return authorized status for the current Workspace.

        Args:
            request: Validated empty status request.

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
            request: Validated empty Project-list request.

        Returns:
            Authorized Projects ordered by immutable key.

        """
        ...

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Create one attributable Task in the selected Project.

        Args:
            request: Validated context-free Task creation request.

        Returns:
            Atomically committed Task.

        """
        ...

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """Return one deterministic page from the selected Project.

        Args:
            request: Validated context-free pagination request.

        Returns:
            Stable Project-bound Task page.

        """
        ...

    def get_task(self, request: TaskGetRequest) -> Task:
        """Return one selected-Project Task by UID or Human key.

        Args:
            request: Validated context-free Task selector.

        Returns:
            Matching immutable Task.

        """
        ...
