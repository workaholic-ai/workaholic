"""Dependency-inversion ports owned by the cumulative application layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.commands import (
        AddTaskDependencyMutation,
        ApproveResultMutation,
        BootstrapMutation,
        GetLocalStatus,
        GetProjectByKey,
        GetTask,
        GetTaskDetails,
        ListInstanceTasks,
        ListProjects,
        ListTasks,
        ListTasksByView,
        ProjectCreationMutation,
        ReadTaskEvents,
        RejectResultMutation,
        RemoveTaskDependencyMutation,
        SubmitHumanResultMutation,
        TaskBlockMutation,
        TaskCancelMutation,
        TaskCreationMutation,
        TaskUnblockMutation,
        TaskUpdateMutation,
    )
    from workaholic.application.results import (
        BootstrapResult,
        ProjectCreationResult,
        StatusResult,
        TaskDetails,
        TaskEventPage,
        TaskMutationResult,
        TaskPage,
        TaskSubmissionResult,
    )
    from workaholic.domain import (
        InstanceId,
        Project,
        ProjectId,
        RequestId,
        ResultId,
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


class ResultIdentifierFactory(Protocol):
    """Generate only the identities required by Human Result operations."""

    def new_result_id(self) -> ResultId:
        """Create a candidate Result identifier.

        Returns:
            A new opaque ResultId.

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


class TaskCreationRepository(Protocol):
    """Persist attributable Task creation through one semantic operation."""

    def create_task(self, mutation: TaskCreationMutation) -> Task:
        """Atomically allocate, create, and record one Task.

        Args:
            mutation: Validated Task creation mutation.

        Returns:
            The committed Task.

        """
        ...


class TaskRepository(TaskCreationRepository, Protocol):
    """Persist the cumulative Task mutation surface."""

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Atomically update Task definition fields at an expected version.

        Args:
            mutation: Validated optimistic Task patch.

        Returns:
            The committed Task and its attributable event.

        """
        ...

    def block_task(self, mutation: TaskBlockMutation) -> TaskMutationResult:
        """Atomically block one Task at an expected version.

        Args:
            mutation: Validated block transition.

        Returns:
            The committed Task and blocking event.

        """
        ...

    def unblock_task(self, mutation: TaskUnblockMutation) -> TaskMutationResult:
        """Atomically unblock one Task at an expected version.

        Args:
            mutation: Validated unblock transition.

        Returns:
            The committed Task and unblocking event.

        """
        ...

    def cancel_task(self, mutation: TaskCancelMutation) -> TaskMutationResult:
        """Atomically cancel one Task at an expected version.

        Args:
            mutation: Validated cancellation transition.

        Returns:
            The committed Task and cancellation event.

        """
        ...

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically add one same-Project Task prerequisite.

        Args:
            mutation: Validated dependency addition.

        Returns:
            The committed dependant Task and update event.

        """
        ...

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically remove one existing Task prerequisite.

        Args:
            mutation: Validated dependency removal.

        Returns:
            The committed dependant Task and update event.

        """
        ...

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically submit one Human Result without an Attempt.

        Args:
            mutation: Validated Human submission and candidate identities.

        Returns:
            The committed Task, Result, and ordered events.

        """
        ...

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically approve and complete the current pending Result.

        Args:
            mutation: Validated approval and event identities.

        Returns:
            The committed Task, Result, and ordered events.

        """
        ...

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically reject and deselect the current pending Result.

        Args:
            mutation: Validated rejection and event identity.

        Returns:
            The reopened Task, retained Result, and rejection event.

        """
        ...


class ProjectRepository(Protocol):
    """Persist atomic Project creation through one semantic operation."""

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


class CoreQueryRepository(Protocol):
    """Read the currently composed status, Project, and Task query surface."""

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

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Read one authorized Project by immutable key.

        Args:
            command: Validated Instance, Subject, and key query.

        Returns:
            The matching authorized Project.

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

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Read Tasks across authorized Projects in one Instance.

        Args:
            command: Validated Instance-scoped pagination query.

        Returns:
            Tasks ordered by Project key and Project-local number.

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


class TaskViewQueryRepository(CoreQueryRepository, Protocol):
    """Read cumulative core queries plus Phase 3 Task readiness views."""

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Read complete Task definition, readiness, and selected Result details.

        Args:
            command: Validated scoped Task detail query.

        Returns:
            Complete internally consistent Task details.

        """
        ...

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Read one deterministic persisted or derived Task view page.

        Args:
            command: Validated view, scope, and pagination query.

        Returns:
            A view-bound deterministic Task page.

        """
        ...


class QueryRepository(TaskViewQueryRepository, Protocol):
    """Read the complete cumulative query surface without mutation."""

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Read one bounded TaskEvent snapshot after an Instance cursor.

        Args:
            command: Validated Task scope and cursor query.

        Returns:
            A polling-safe ascending TaskEvent page.

        """
        ...


class WorkaholicRepository(
    BootstrapRepository,
    ProjectRepository,
    TaskRepository,
    QueryRepository,
    Protocol,
):
    """Persist cumulative operations through explicit semantic methods."""
