"""Read-only application orchestration for cumulative query use cases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never, cast

from workaholic.application.commands import (
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    GetTaskDetails,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ListTasksByView,
    ReadTaskEvents,
)
from workaholic.application.errors import (
    ApplicationError,
    ApplicationErrorCode,
    InvalidInputError,
)
from workaholic.application.results import (
    StatusResult,
    TaskDetails,
    TaskEventPage,
    TaskPage,
)
from workaholic.domain import Project, Task, TaskId

if TYPE_CHECKING:
    from workaholic.application.ports import QueryRepository


class QueryApplication:
    """Validate query boundaries and delegate read-only repository operations."""

    def __init__(self, repository: QueryRepository) -> None:
        """Initialize the query service with one semantic repository.

        Args:
            repository: Read-only semantic persistence boundary.

        Raises:
            TypeError: If the dependency lacks a required query method.

        """
        for method_name in (
            "get_local_status",
            "list_projects",
            "get_project_by_key",
            "list_tasks",
            "list_tasks_for_instance",
            "get_task",
            "get_task_details",
            "list_tasks_by_view",
            "read_task_events_after",
        ):
            _require_callable(repository, method_name)
        self._repository = repository

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Return authorized status for an exact local selection.

        Args:
            command: Validated local status query.

        Returns:
            Current local status without storage-specific details.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, GetLocalStatus):
            raise InvalidInputError
        result: object = self._repository.get_local_status(candidate)
        if not isinstance(result, StatusResult):
            _raise_invalid_result("Status")
        if (
            result.profile != candidate.profile
            or result.instance.id != candidate.instance_id
            or result.project.id != candidate.project_id
            or result.subject.id != candidate.subject_id
        ):
            _raise_invalid_result("Status")
        return result

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """Return authorized Projects ordered by immutable key.

        Args:
            command: Validated Project-list query.

        Returns:
            Exact tuple of Projects ordered by key ascending.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, ListProjects):
            raise InvalidInputError
        result: object = self._repository.list_projects(candidate)
        if type(result) is not tuple or not all(
            isinstance(project, Project) for project in result
        ):
            _raise_invalid_result("Project query")
        projects = cast("tuple[Project, ...]", result)
        keys = tuple(project.key for project in projects)
        identities = tuple(project.id for project in projects)
        if (
            keys != tuple(sorted(keys))
            or len(set(keys)) != len(keys)
            or len(set(identities)) != len(identities)
            or any(project.instance_id != candidate.instance_id for project in projects)
        ):
            _raise_invalid_result("Project query")
        return projects

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Return one authorized Project selected by immutable key.

        Args:
            command: Validated Instance-, Subject-, and key-bound lookup.

        Returns:
            Exact authorized Project.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, GetProjectByKey):
            raise InvalidInputError
        result: object = self._repository.get_project_by_key(candidate)
        if not isinstance(result, Project):
            _raise_invalid_result("Project query")
        if (
            result.instance_id != candidate.instance_id
            or result.key != candidate.project_key
        ):
            _raise_invalid_result("Project query")
        return result

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Return one deterministic page of authorized Project Tasks.

        Args:
            command: Validated Task-list query.

        Returns:
            Task page ordered by Project-local number.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, ListTasks):
            raise InvalidInputError
        result: object = self._repository.list_tasks(candidate)
        if not isinstance(result, TaskPage):
            _raise_invalid_result("Task page")
        if any(task.project_id != candidate.project_id for task in result.tasks):
            _raise_invalid_result("Task page")
        return result

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Return one deterministic Task page across authorized Projects.

        Args:
            command: Validated profile-, Instance-, and Subject-bound page query.

        Returns:
            Task page ordered by Project key and Project-local number.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, ListInstanceTasks):
            raise InvalidInputError
        result: object = self._repository.list_tasks_for_instance(candidate)
        if not isinstance(result, TaskPage):
            _raise_invalid_result("Task page")
        return result

    def get_task(self, command: GetTask) -> Task:
        """Return one authorized Task selected by UID or Human key.

        Args:
            command: Validated Project-scoped Task lookup.

        Returns:
            Exact matching Task.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, GetTask):
            raise InvalidInputError
        result: object = self._repository.get_task(candidate)
        if not isinstance(result, Task):
            _raise_invalid_result("Task query")
        selector_matches = (
            result.uid == candidate.task
            if isinstance(candidate.task, TaskId)
            else result.key == candidate.task
        )
        if result.project_id != candidate.project_id or not selector_matches:
            _raise_invalid_result("Task query")
        return result

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Return complete Task details with authoritative derived readiness.

        Args:
            command: Validated Project-scoped detail query.

        Returns:
            Complete Task, prerequisites, current Result, and readiness.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, GetTaskDetails):
            raise InvalidInputError
        result: object = self._repository.get_task_details(candidate)
        if not isinstance(result, TaskDetails):
            _raise_invalid_result("Task details")
        selector_matches = (
            result.task.uid == candidate.task
            if isinstance(candidate.task, TaskId)
            else result.task.key == candidate.task
        )
        if result.task.project_id != candidate.project_id or not selector_matches:
            _raise_invalid_result("Task details")
        return result

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Return one view-bound page with aligned readiness projections.

        Args:
            command: Validated view, scope, and pagination query.

        Returns:
            Deterministic Task page using a version-3 view cursor.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, ListTasksByView):
            raise InvalidInputError
        result: object = self._repository.list_tasks_by_view(candidate)
        if not isinstance(result, TaskPage):
            _raise_invalid_result("Task view page")
        if (
            result.view is not candidate.view
            or len(result.readiness) != len(result.tasks)
            or (
                candidate.project_id is not None
                and any(
                    task.project_id != candidate.project_id for task in result.tasks
                )
            )
        ):
            _raise_invalid_result("Task view page")
        return result

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Return one polling-safe attributable TaskEvent snapshot.

        Args:
            command: Validated Task, Project, actor, cursor, and limit query.

        Returns:
            Events in strict Instance cursor order and next polling cursor.

        Raises:
            ApplicationError: If input or repository output violates its contract.

        """
        candidate: object = command
        if not isinstance(candidate, ReadTaskEvents):
            raise InvalidInputError
        result: object = self._repository.read_task_events_after(candidate)
        if not isinstance(result, TaskEventPage):
            _raise_invalid_result("TaskEvent page")
        if (
            len(result.events) > candidate.limit
            or (not result.events and result.next_cursor != candidate.after)
            or (
                result.events
                and (
                    result.events[0].cursor <= candidate.after
                    or result.next_cursor <= candidate.after
                )
            )
            or any(
                event.project_id != candidate.project_id
                or (
                    isinstance(candidate.task, TaskId)
                    and event.task_uid != candidate.task
                )
                for event in result.events
            )
        ):
            _raise_invalid_result("TaskEvent page")
        return result


def _require_callable(value: object, member_name: str) -> None:
    """Require one explicit repository query method.

    Args:
        value: Candidate repository dependency.
        member_name: Required callable attribute.

    Raises:
        TypeError: If the method is unavailable.

    """
    if not callable(getattr(value, member_name, None)):
        message = f"Query repository must provide {member_name}()."
        raise TypeError(message)


def _raise_invalid_result(label: str) -> Never:
    """Raise a safe internal error for a repository contract violation.

    Args:
        label: Safe result category used in the public diagnostic.

    Raises:
        ApplicationError: Always.

    """
    raise ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{label} persistence returned an invalid result.",
    )
