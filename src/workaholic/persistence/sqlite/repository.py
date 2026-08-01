"""Stable SQLite repository façade over focused semantic operations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite import _queries as sqlite_queries
from workaholic.persistence.sqlite import _task_views as sqlite_task_views
from workaholic.persistence.sqlite._bootstrap import (
    bootstrap_local_project as _bootstrap_local_project,
)
from workaholic.persistence.sqlite._projects import create_project as _create_project
from workaholic.persistence.sqlite._task_dependencies import (
    add_task_dependency as _add_task_dependency,
)
from workaholic.persistence.sqlite._task_dependencies import (
    remove_task_dependency as _remove_task_dependency,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    block_task as _block_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    cancel_task as _cancel_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    unblock_task as _unblock_task,
)
from workaholic.persistence.sqlite._task_lifecycle import (
    update_task_if_version as _update_task_if_version,
)
from workaholic.persistence.sqlite._task_results import (
    approve_result as _approve_result,
)
from workaholic.persistence.sqlite._task_results import reject_result as _reject_result
from workaholic.persistence.sqlite._task_results import (
    submit_human_result as _submit_human_result,
)
from workaholic.persistence.sqlite._tasks import create_task as _create_task
from workaholic.persistence.sqlite.schema import initialize_empty_store

if TYPE_CHECKING:
    from workaholic.application import (
        AddTaskDependencyMutation,
        ApproveResultMutation,
        BootstrapMutation,
        BootstrapResult,
        Clock,
        GetLocalStatus,
        GetProjectByKey,
        GetTask,
        GetTaskDetails,
        ListInstanceTasks,
        ListProjects,
        ListTasks,
        ListTasksByView,
        ProjectCreationMutation,
        ProjectCreationResult,
        ReadTaskEvents,
        RejectResultMutation,
        RemoveTaskDependencyMutation,
        StatusResult,
        SubmitHumanResultMutation,
        TaskBlockMutation,
        TaskCancelMutation,
        TaskCreationMutation,
        TaskDetails,
        TaskEventPage,
        TaskMutationResult,
        TaskPage,
        TaskSubmissionResult,
        TaskUnblockMutation,
        TaskUpdateMutation,
    )
    from workaholic.domain import Project, Task


class _UtcSystemClock:
    """Supply authoritative UTC time for direct repository query use."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC timestamp."""
        return datetime.now(UTC)


class SQLiteRepository:
    """Cumulative SQLite implementation of atomic semantic operations."""

    def __init__(self, database_path: Path, *, clock: Clock | None = None) -> None:
        """Bind the adapter to one absolute local database path.

        Args:
            database_path: Absolute path to a schema-version-3 SQLite store.
            clock: Optional authoritative clock for time-derived read views.

        Raises:
            TypeError: If the value is not an absolute Path.

        """
        candidate_path: object = database_path
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            message = "SQLite repository database_path must be an absolute Path."
            raise TypeError(message)
        selected_clock = _UtcSystemClock() if clock is None else clock
        if not callable(getattr(selected_clock, "now", None)):
            message = "SQLite repository clock must provide now()."
            raise TypeError(message)
        self._database_path = candidate_path
        self._clock = selected_clock

    @property
    def database_path(self) -> Path:
        """Return the immutable configured database path.

        Returns:
            Absolute SQLite database path.

        """
        return self._database_path

    def bootstrap_local_project(
        self,
        mutation: BootstrapMutation,
    ) -> BootstrapResult:
        """Atomically bootstrap or locate the single local Project.

        Args:
            mutation: Validated candidate identities and semantic input.

        Returns:
            The committed local identity and Owner authorization graph.

        """
        initialize_empty_store(self._database_path)
        return _bootstrap_local_project(self._database_path, mutation)

    def create_task(self, mutation: TaskCreationMutation) -> Task:
        """Atomically allocate, create, and attribute one initial Task.

        Args:
            mutation: Validated Task creation mutation.

        Returns:
            The new or idempotently replayed Task.

        """
        return _create_task(self._database_path, mutation)

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Atomically update Task definition fields at an expected version.

        Args:
            mutation: Validated optimistic Task update mutation.

        Returns:
            The committed Task and its attributable update event.

        """
        return _update_task_if_version(self._database_path, mutation)

    def block_task(self, mutation: TaskBlockMutation) -> TaskMutationResult:
        """Atomically block an open Task at an expected version.

        Args:
            mutation: Validated optimistic blocking mutation.

        Returns:
            The committed blocked Task and its attributable event.

        """
        return _block_task(self._database_path, mutation)

    def unblock_task(self, mutation: TaskUnblockMutation) -> TaskMutationResult:
        """Atomically return a blocked Task to open.

        Args:
            mutation: Validated optimistic unblocking mutation.

        Returns:
            The committed open Task and its attributable event.

        """
        return _unblock_task(self._database_path, mutation)

    def cancel_task(self, mutation: TaskCancelMutation) -> TaskMutationResult:
        """Atomically cancel a mutable Task at an expected version.

        Args:
            mutation: Validated optimistic cancellation mutation.

        Returns:
            The committed cancelled Task and its attributable event.

        """
        return _cancel_task(self._database_path, mutation)

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically add one same-Project prerequisite edge.

        Args:
            mutation: Validated optimistic dependency addition.

        Returns:
            The committed dependant Task and attributable update event.

        """
        return _add_task_dependency(self._database_path, mutation)

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Atomically remove one existing prerequisite edge.

        Args:
            mutation: Validated optimistic dependency removal.

        Returns:
            The committed dependant Task and attributable update event.

        """
        return _remove_task_dependency(self._database_path, mutation)

    def submit_human_result(
        self,
        mutation: SubmitHumanResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically submit one Human Result without an Agent Attempt.

        Args:
            mutation: Validated optimistic Human submission mutation.

        Returns:
            Committed Task, Result, and ordered semantic events.

        """
        return _submit_human_result(self._database_path, mutation)

    def approve_result(
        self,
        mutation: ApproveResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically approve the current pending Result.

        Args:
            mutation: Validated optimistic approval mutation.

        Returns:
            Committed Task, approved Result, and ordered events.

        """
        return _approve_result(self._database_path, mutation)

    def reject_result(
        self,
        mutation: RejectResultMutation,
    ) -> TaskSubmissionResult:
        """Atomically reject and deselect the current pending Result.

        Args:
            mutation: Validated optimistic rejection mutation.

        Returns:
            Reopened Task, retained Result, and rejection event.

        """
        return _reject_result(self._database_path, mutation)

    def create_project(
        self,
        mutation: ProjectCreationMutation,
    ) -> ProjectCreationResult:
        """Atomically create one Project and grant its creator Owner access.

        Args:
            mutation: Validated Project creation mutation.

        Returns:
            The new or idempotently replayed Project and Owner grant.

        """
        return _create_project(self._database_path, mutation)

    def get_local_status(self, command: GetLocalStatus) -> StatusResult:
        """Read authorized local status without mutating storage.

        Args:
            command: Validated exact identity selection.

        Returns:
            Current local status.

        """
        return sqlite_queries.get_local_status(self._database_path, command)

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """List authorized Projects by immutable key.

        Args:
            command: Validated Instance and Subject selection.

        Returns:
            Authorized Projects ordered by key ascending.

        """
        return sqlite_queries.list_projects(self._database_path, command)

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Read one authorized Project by immutable key.

        Args:
            command: Validated Instance-, Subject-, and key-bound query.

        Returns:
            Matching authorized Project.

        """
        return sqlite_queries.get_project_by_key(self._database_path, command)

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Read one deterministic Project-bound Task page.

        Args:
            command: Validated pagination query.

        Returns:
            Tasks ordered by Project-local number.

        """
        return sqlite_queries.list_tasks(self._database_path, command)

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Read one Task page across authorized Projects in an Instance.

        Args:
            command: Validated Instance-scoped pagination query.

        Returns:
            Tasks ordered by Project key and Project-local number.

        """
        return sqlite_queries.list_tasks_for_instance(
            self._database_path,
            command,
        )

    def get_task(self, command: GetTask) -> Task:
        """Read one Task by exact UID or stable Human key.

        Args:
            command: Validated Project-scoped selector.

        Returns:
            Matching immutable Task.

        """
        return sqlite_queries.get_task(self._database_path, command)

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Read complete Task details with authoritative derived readiness.

        Args:
            command: Validated Project-scoped detail query.

        Returns:
            Complete Task details at one clock snapshot.

        """
        return sqlite_task_views.get_task_details(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Read one view-bound deterministic Phase 3 Task page.

        Args:
            command: Validated view, scope, and pagination query.

        Returns:
            Tasks and aligned readiness using a version-3 cursor.

        """
        return sqlite_task_views.list_tasks_by_view(
            self._database_path,
            command,
            now=self._clock.now(),
        )

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Read one authorized bounded TaskEvent snapshot.

        Args:
            command: Validated Task, Project, actor, cursor, and limit query.

        Returns:
            Polling-safe attributable events in cursor order.

        """
        return sqlite_queries.read_task_events_after(self._database_path, command)
