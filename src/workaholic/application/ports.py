"""Dependency-inversion ports owned by the cumulative application layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.commands import (
        BootstrapMutation,
        GetLocalStatus,
        GetProjectByKey,
        GetTask,
        ListInstanceTasks,
        ListProjects,
        ListTasks,
        ProjectCreationMutation,
        TaskCreationMutation,
    )
    from workaholic.application.results import (
        BootstrapResult,
        ProjectCreationResult,
        StatusResult,
        TaskPage,
    )
    from workaholic.domain import (
        InstanceId,
        Project,
        ProjectId,
        RequestId,
        SubjectId,
        Task,
        TaskEventId,
        TaskId,
    )


class Clock(Protocol):
    """Supply the authoritative UTC time for one application operation."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime.

        Returns:
            An authoritative timezone-aware UTC datetime.

        """
        ...


class IdentifierFactory(Protocol):
    """Generate opaque candidate identifiers outside the domain core."""

    def new_instance_id(self) -> InstanceId:
        """Create a candidate Instance identifier.

        Returns:
            A new opaque InstanceId.

        """
        ...

    def new_project_id(self) -> ProjectId:
        """Create a candidate Project identifier.

        Returns:
            A new opaque ProjectId.

        """
        ...

    def new_subject_id(self) -> SubjectId:
        """Create a candidate Subject identifier.

        Returns:
            A new opaque SubjectId.

        """
        ...

    def new_task_id(self) -> TaskId:
        """Create a candidate Task identifier.

        Returns:
            A new opaque TaskId.

        """
        ...

    def new_event_id(self) -> TaskEventId:
        """Create a candidate TaskEvent identifier.

        Returns:
            A new opaque TaskEventId.

        """
        ...

    def new_request_id(self) -> RequestId:
        """Create a candidate request identifier.

        Returns:
            A new opaque RequestId.

        """
        ...


class BootstrapRepository(Protocol):
    """Persist the atomic local bootstrap semantic operation."""

    def bootstrap_local_project(
        self,
        mutation: BootstrapMutation,
    ) -> BootstrapResult:
        """Atomically bootstrap or locate the local Instance and Project.

        Args:
            mutation: Validated candidate identities and bootstrap data.

        Returns:
            The committed bootstrap entities and binding.

        """
        ...


class TaskRepository(Protocol):
    """Persist attributable Task mutations through semantic operations."""

    def create_task(self, mutation: TaskCreationMutation) -> Task:
        """Atomically allocate, create, and record one Task.

        Args:
            mutation: Validated Task creation mutation.

        Returns:
            The committed Task.

        """
        ...


class QueryRepository(Protocol):
    """Read existing local status, Projects, and Tasks without mutation."""

    def get_local_status(self, command: GetLocalStatus) -> StatusResult:
        """Read the selected local status without mutating state.

        Args:
            command: Validated status query.

        Returns:
            Current authorized local status.

        """
        ...

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """Read authorized Projects without mutating state.

        Args:
            command: Validated Project query.

        Returns:
            Projects ordered by immutable key.

        """
        ...

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Read one deterministic Task page without mutating state.

        Args:
            command: Validated page query.

        Returns:
            Tasks ordered by Project-local number.

        """
        ...

    def get_task(self, command: GetTask) -> Task:
        """Read one Task without mutating state.

        Args:
            command: Validated Task selector query.

        Returns:
            The matching Task.

        """
        ...


class WorkaholicRepository(
    BootstrapRepository,
    TaskRepository,
    QueryRepository,
    Protocol,
):
    """Persist cumulative operations through explicit semantic methods."""

    def create_project(
        self,
        mutation: ProjectCreationMutation,
    ) -> ProjectCreationResult:
        """Atomically create one Project and its creator Owner grant.

        Args:
            mutation: Validated Project creation mutation.

        Returns:
            The committed Project and grant.

        """
        ...

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Read one authorized Project by immutable key.

        Args:
            command: Validated Instance, Subject, and key query.

        Returns:
            The matching authorized Project.

        """
        ...

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Read Tasks across authorized Projects in one Instance.

        Args:
            command: Validated Instance-scoped pagination query.

        Returns:
            Tasks ordered by Project key and Project-local number.

        """
        ...
