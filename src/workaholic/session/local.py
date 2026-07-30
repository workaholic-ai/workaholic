"""Embedded LocalSession over context-free application services."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Never, Protocol, cast

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapLocalProjectInput,
    BootstrapResult,
    CreateTaskInput,
    GetLocalStatus,
    GetTask,
    ListProjects,
    ListTasks,
    StatusResult,
    TaskPage,
)
from workaholic.domain import (
    Project,
    SubjectId,
    Task,
    TaskId,
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

if TYPE_CHECKING:
    from workaholic.session.base import (
        LocalActorSelector,
        WorkspaceContextGateway,
    )


class _BootstrapService(Protocol):
    """Application capability required for local bootstrap."""

    def up(self, command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Commit or locate one local Project graph.

        Args:
            command: Validated application bootstrap input.

        Returns:
            Committed or idempotently replayed bootstrap result.

        """
        ...


class _QueryService(Protocol):
    """Application capabilities required for authorized local reads."""

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Return exact authorized local status.

        Args:
            command: Validated exact identity query.

        Returns:
            Matching authorized local status.

        """
        ...

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """Return authorized Projects in stable order.

        Args:
            command: Validated Instance and Subject query.

        Returns:
            Projects ordered by immutable key.

        """
        ...

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Return one stable selected-Project Task page.

        Args:
            command: Validated Project-bound pagination query.

        Returns:
            Deterministic Task page.

        """
        ...

    def get_task(self, command: GetTask) -> Task:
        """Return one selected-Project Task.

        Args:
            command: Validated Project-scoped selector query.

        Returns:
            Matching immutable Task.

        """
        ...


class _TaskService(Protocol):
    """Application capability required for attributable Task creation."""

    def create(self, command: CreateTaskInput) -> Task:
        """Create one initial Task.

        Args:
            command: Validated attributable Task creation input.

        Returns:
            Atomically committed Task.

        """
        ...


class LocalSession:
    """Invoke application services in-process with verified local context."""

    def __init__(
        self,
        *,
        context: WorkspaceContextGateway,
        actors: LocalActorSelector,
        bootstrap: _BootstrapService,
        queries: _QueryService,
        tasks: _TaskService,
    ) -> None:
        """Initialize all explicit embedded Session dependencies.

        Args:
            context: Exact-directory Workspace context gateway.
            actors: Trusted local bootstrap-Human selector.
            bootstrap: Bootstrap application service.
            queries: Authorized query application service.
            tasks: Task mutation application service.

        Raises:
            TypeError: If any dependency lacks a required operation.

        """
        _require_callable(context, "read_current", "context gateway")
        _require_callable(context, "write_current", "context gateway")
        _require_callable(actors, "select", "local actor selector")
        _require_callable(bootstrap, "up", "bootstrap service")
        for method_name in (
            "status",
            "list_projects",
            "list_tasks",
            "get_task",
        ):
            _require_callable(queries, method_name, "query service")
        _require_callable(tasks, "create", "Task service")
        self._context = context
        self._actors = actors
        self._bootstrap = bootstrap
        self._queries = queries
        self._tasks = tasks

    def up(self, request: UpRequest) -> BootstrapResult:
        """Durably bootstrap before writing exact-directory context.

        Args:
            request: Validated context-free bootstrap request.

        Returns:
            Committed bootstrap graph after context is durably bound.

        Raises:
            ApplicationError: If input, application, or context operations fail.

        """
        candidate: object = request
        if not isinstance(candidate, UpRequest):
            _raise_invalid_input("Bootstrap Session request is invalid.")
        try:
            command = BootstrapLocalProjectInput(
                project_key=candidate.project_key,
                idempotency_key=candidate.idempotency_key,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Bootstrap Session request is invalid.",
            ) from error
        result: object = self._bootstrap.up(command)
        if (
            not isinstance(result, BootstrapResult)
            or result.project.key != candidate.project_key
            or result.workspace.project_key != candidate.project_key
        ):
            _raise_internal_result("Bootstrap")
        context_path: object = self._context.write_current(result.workspace)
        if not isinstance(context_path, Path):
            _raise_internal_result("Workspace context")
        return result

    def status(self, request: StatusRequest) -> StatusResult:
        """Return authoritative status for exact current context.

        Args:
            request: Validated empty status request.

        Returns:
            Authorized status matching all context identities.

        """
        candidate: object = request
        if not isinstance(candidate, StatusRequest):
            _raise_invalid_input("Status Session request is invalid.")
        _binding, _subject_id, status = self._verified_selection()
        return status

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """List Projects authorized for the verified current local Human.

        Args:
            request: Validated empty Project-list request.

        Returns:
            Projects ordered by immutable key.

        """
        candidate: object = request
        if not isinstance(candidate, ProjectListRequest):
            _raise_invalid_input("Project-list Session request is invalid.")
        binding, subject_id, _status = self._verified_selection()
        result: object = self._queries.list_projects(
            ListProjects(
                instance_id=binding.instance_id,
                subject_id=subject_id,
            )
        )
        if type(result) is not tuple or not all(
            isinstance(project, Project) for project in result
        ):
            _raise_internal_result("Project query")
        projects = cast("tuple[Project, ...]", result)
        keys = tuple(project.key for project in projects)
        if keys != tuple(sorted(keys)) or any(
            project.instance_id != binding.instance_id for project in projects
        ):
            _raise_internal_result("Project query")
        return projects

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Create one Task attributed to the verified current local Human.

        Args:
            request: Validated context-free Task creation request.

        Returns:
            Atomically committed Task.

        """
        candidate: object = request
        if not isinstance(candidate, TaskCreateRequest):
            _raise_invalid_input("Task-create Session request is invalid.")
        binding, subject_id, _status = self._verified_selection()
        objective = (
            candidate.title if candidate.objective is None else candidate.objective
        )
        try:
            command = CreateTaskInput(
                project_id=binding.project_id,
                subject_id=subject_id,
                title=candidate.title,
                objective=objective,
                priority=candidate.priority,
                idempotency_key=candidate.idempotency_key,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task-create Session request is invalid.",
            ) from error
        result: object = self._tasks.create(command)
        if (
            not isinstance(result, Task)
            or result.project_id != binding.project_id
            or result.created_by != subject_id
            or result.title != command.title
            or result.objective != command.objective
            or result.priority != command.priority
        ):
            _raise_internal_result("Task creation")
        return result

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """List one deterministic page from the verified selected Project.

        Args:
            request: Validated context-free pagination request.

        Returns:
            Stable Project-bound Task page.

        """
        candidate: object = request
        if not isinstance(candidate, TaskListRequest):
            _raise_invalid_input("Task-list Session request is invalid.")
        binding, subject_id, _status = self._verified_selection()
        try:
            command = ListTasks(
                project_id=binding.project_id,
                subject_id=subject_id,
                cursor=candidate.cursor,
                limit=candidate.limit,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task-list Session request is invalid.",
            ) from error
        result: object = self._queries.list_tasks(command)
        if not isinstance(result, TaskPage) or any(
            task.project_id != binding.project_id for task in result.tasks
        ):
            _raise_internal_result("Task page")
        return result

    def get_task(self, request: TaskGetRequest) -> Task:
        """Read one Task from the verified selected Project.

        Args:
            request: Validated context-free Task selector.

        Returns:
            Matching immutable Task.

        """
        candidate: object = request
        if not isinstance(candidate, TaskGetRequest):
            _raise_invalid_input("Task-get Session request is invalid.")
        binding, subject_id, _status = self._verified_selection()
        try:
            command = GetTask(
                project_id=binding.project_id,
                subject_id=subject_id,
                task=candidate.task,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task-get Session request is invalid.",
            ) from error
        result: object = self._queries.get_task(command)
        if not isinstance(result, Task):
            _raise_internal_result("Task query")
        selector_matches = (
            result.uid == command.task
            if isinstance(command.task, TaskId)
            else result.key == command.task
        )
        if result.project_id != binding.project_id or not selector_matches:
            _raise_internal_result("Task query")
        return result

    def _verified_selection(
        self,
    ) -> tuple[WorkspaceBinding, SubjectId, StatusResult]:
        """Read context, select its local Human, and verify authoritative state.

        Returns:
            Context binding, selected Subject identity, and matching status.

        Raises:
            ApplicationError: If context, selection, or authorization is invalid.

        """
        binding: object = self._context.read_current()
        if not isinstance(binding, WorkspaceBinding):
            _raise_context_invalid()
        subject_id: object = self._actors.select(binding)
        if not isinstance(subject_id, SubjectId):
            _raise_internal_result("Local actor selection")
        status = self._queries.status(
            GetLocalStatus(
                instance_id=binding.instance_id,
                project_id=binding.project_id,
                subject_id=subject_id,
            )
        )
        if not isinstance(status, StatusResult):
            _raise_internal_result("Status query")
        if (
            status.instance.id != binding.instance_id
            or status.project.id != binding.project_id
            or status.project.key != binding.project_key
            or status.subject.id != subject_id
        ):
            _raise_context_invalid()
        return binding, subject_id, status


def _require_callable(value: object, member_name: str, label: str) -> None:
    """Require one explicit dependency operation.

    Args:
        value: Candidate dependency.
        member_name: Required callable attribute.
        label: Safe dependency name.

    Raises:
        TypeError: If the operation is unavailable.

    """
    if not callable(getattr(value, member_name, None)):
        message = f"LocalSession {label} must provide {member_name}()."
        raise TypeError(message)


def _raise_invalid_input(message: str) -> Never:
    """Raise one safe Session input failure.

    Args:
        message: Bounded public diagnostic.

    Raises:
        ApplicationError: Always.

    """
    raise ApplicationError(ApplicationErrorCode.INVALID_INPUT, message)


def _raise_context_invalid() -> Never:
    """Raise one safe untrusted-context identity mismatch.

    Raises:
        ApplicationError: Always.

    """
    raise ApplicationError(
        ApplicationErrorCode.CONTEXT_INVALID,
        "Workspace context does not match authoritative local state.",
    )


def _raise_internal_result(label: str) -> Never:
    """Raise one safe application dependency contract failure.

    Args:
        label: Safe result category.

    Raises:
        ApplicationError: Always.

    """
    raise ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{label} returned an invalid result.",
    )
