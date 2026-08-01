"""Stable SQLite repository façade over focused semantic operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite import _queries as sqlite_queries
from workaholic.persistence.sqlite._bootstrap import (
    bootstrap_local_project as _bootstrap_local_project,
)
from workaholic.persistence.sqlite._projects import create_project as _create_project
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
from workaholic.persistence.sqlite._tasks import create_task as _create_task
from workaholic.persistence.sqlite.schema import initialize_empty_store

if TYPE_CHECKING:
    from workaholic.application import (
        BootstrapMutation,
        BootstrapResult,
        GetLocalStatus,
        GetProjectByKey,
        GetTask,
        ListInstanceTasks,
        ListProjects,
        ListTasks,
        ProjectCreationMutation,
        ProjectCreationResult,
        StatusResult,
        TaskBlockMutation,
        TaskCancelMutation,
        TaskCreationMutation,
        TaskMutationResult,
        TaskPage,
        TaskUnblockMutation,
        TaskUpdateMutation,
    )
    from workaholic.domain import Project, Task


class SQLiteRepository:
    """Cumulative SQLite implementation of atomic semantic operations."""

    def __init__(self, database_path: Path) -> None:
        """Bind the adapter to one absolute local database path.

        Args:
            database_path: Absolute path to a schema-version-3 SQLite store.

        Raises:
            TypeError: If the value is not an absolute Path.

        """
        candidate_path: object = database_path
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            message = "SQLite repository database_path must be an absolute Path."
            raise TypeError(message)
        self._database_path = candidate_path

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
