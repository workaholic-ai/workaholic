"""Profile-aware embedded Session over context-free application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Never, Protocol, cast

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapLocalProjectInput,
    BootstrapResult,
    ContextResult,
    CreateProjectInput,
    CreateTaskInput,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ProjectCreationResult,
    StatusResult,
    TaskPage,
)
from workaholic.domain import (
    Project,
    ProjectId,
    Task,
    TaskId,
    TaskState,
    WorkspaceBinding,
    build_task_key,
    validate_profile_name,
)
from workaholic.session.base import (
    LocalIdentity,
    WorkspaceContextSelection,
)
from workaholic.session.models import (
    ContextRequest,
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
)

if TYPE_CHECKING:
    from workaholic.session.base import (
        LocalRuntimeOpener,
        ProfileResolver,
        WorkspaceContextGateway,
    )


class _LocalIdentityService(Protocol):
    """Select trusted local identities from one opened embedded runtime."""

    def select(self) -> LocalIdentity:
        """Return the sole initialized Instance and bootstrap Human."""
        ...


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


class _ProjectService(Protocol):
    """Application capability required for named Project creation."""

    def create(self, command: CreateProjectInput) -> ProjectCreationResult:
        """Create one named Project.

        Args:
            command: Validated Project creation input.

        Returns:
            Committed or idempotently replayed Project and Owner grant.

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

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Return one authorized Project selected by immutable key.

        Args:
            command: Validated Instance-, Subject-, and key-bound query.

        Returns:
            Matching authorized Project.

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

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Return one stable Task page across authorized Projects.

        Args:
            command: Validated Instance-bound pagination query.

        Returns:
            Deterministic all-Projects Task page.

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


@dataclass(frozen=True, slots=True)
class LocalRuntime:
    """One profile-selected set of in-process application capabilities."""

    profile: str
    identity: _LocalIdentityService
    bootstrap: _BootstrapService
    projects: _ProjectService
    queries: _QueryService
    tasks: _TaskService

    def __post_init__(self) -> None:
        """Validate the runtime's explicit stable capability surface."""
        try:
            profile = validate_profile_name(self.profile)
        except ValueError as error:
            message = "Local runtime profile is invalid."
            raise TypeError(message) from error
        _require_callable(self.identity, "select", "identity service")
        _require_callable(self.bootstrap, "up", "bootstrap service")
        _require_callable(self.projects, "create", "Project service")
        for method_name in (
            "status",
            "list_projects",
            "get_project_by_key",
            "list_tasks",
            "list_tasks_for_instance",
            "get_task",
        ):
            _require_callable(self.queries, method_name, "query service")
        _require_callable(self.tasks, "create", "Task service")
        object.__setattr__(self, "profile", profile)


@dataclass(frozen=True, slots=True)
class _ResolvedRuntime:
    """One trusted profile runtime plus optional discovered context."""

    profile: str
    runtime: LocalRuntime
    discovered: WorkspaceContextSelection | None


@dataclass(frozen=True, slots=True)
class _SelectedProject:
    """One fully authorized Project and any matching context paths."""

    status: StatusResult
    workspace_root: Path | None
    context_source: Path | None


class LocalSession:
    """Invoke profile-selected application services through one safe pipeline."""

    def __init__(
        self,
        *,
        context: WorkspaceContextGateway,
        profiles: ProfileResolver,
        runtimes: LocalRuntimeOpener,
    ) -> None:
        """Initialize explicit profile, runtime, and Workspace dependencies.

        Args:
            context: Upward-discovery and durable Workspace context gateway.
            profiles: Trusted profile precedence resolver.
            runtimes: Opener for one trusted profile-selected local runtime.

        Raises:
            TypeError: If any dependency lacks a required operation.

        """
        for method_name in ("discover", "write_current", "bind"):
            _require_callable(context, method_name, "context gateway")
        _require_callable(profiles, "resolve", "profile resolver")
        _require_callable(runtimes, "open", "runtime opener")
        self._context = context
        self._profiles = profiles
        self._runtimes = runtimes

    def up(self, request: UpRequest) -> BootstrapResult:
        """Bootstrap only the selected profile before writing current context.

        Args:
            request: Validated bootstrap and optional profile request.

        Returns:
            Committed bootstrap graph with its selected-profile binding.

        """
        candidate: object = request
        if not isinstance(candidate, UpRequest):
            _raise_invalid_input("Bootstrap Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        project_name = (
            candidate.project_key
            if candidate.project_name is None
            else candidate.project_name
        )
        try:
            command = BootstrapLocalProjectInput(
                project_key=candidate.project_key,
                project_name=project_name,
                idempotency_key=candidate.idempotency_key,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Bootstrap Session request is invalid.",
            ) from error
        result: object = resolved.runtime.bootstrap.up(command)
        if (
            not isinstance(result, BootstrapResult)
            or result.project.key != command.project_key
            or result.project.name != command.project_name
        ):
            _raise_internal_result("Bootstrap")
        binding = WorkspaceBinding(
            context_version=1,
            profile=resolved.profile,
            instance_id=result.instance.id,
            project_id=result.project.id,
            project_key=result.project.key,
            workspace_root=".",
        )
        context_path: object = self._context.write_current(binding)
        _require_context_path(context_path)
        try:
            return BootstrapResult(
                instance=result.instance,
                project=result.project,
                subject=result.subject,
                grant=result.grant,
                workspace=binding,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Bootstrap returned an invalid result.",
            ) from error

    def status(self, request: StatusRequest) -> StatusResult:
        """Return authoritative status for the effective Project selection.

        Args:
            request: Validated optional profile and Project selectors.

        Returns:
            Authorized status for the effective selected Project.

        """
        candidate: object = request
        if not isinstance(candidate, StatusRequest):
            _raise_invalid_input("Status Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        _require_project_selector(resolved, candidate.project)
        identity = self._select_identity(resolved)
        selected = self._select_project(
            resolved,
            identity,
            explicit_project=candidate.project,
        )
        return selected.status

    def context(self, request: ContextRequest) -> ContextResult:
        """Return the effective profile, identity, Project, and safe paths.

        Args:
            request: Validated optional profile and Project selectors.

        Returns:
            Complete effective embedded context.

        """
        candidate: object = request
        if not isinstance(candidate, ContextRequest):
            _raise_invalid_input("Context Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        _require_project_selector(resolved, candidate.project)
        identity = self._select_identity(resolved)
        selected = self._select_project(
            resolved,
            identity,
            explicit_project=candidate.project,
        )
        return _context_result(
            selected.status,
            workspace_root=selected.workspace_root,
            context_source=selected.context_source,
        )

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """List Projects using only a resolved initialized profile.

        Args:
            request: Validated optional profile selector.

        Returns:
            Authorized Projects ordered by immutable key.

        """
        candidate: object = request
        if not isinstance(candidate, ProjectListRequest):
            _raise_invalid_input("Project-list Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        identity = self._select_identity(resolved)
        result: object = resolved.runtime.queries.list_projects(
            ListProjects(
                instance_id=identity.instance_id,
                subject_id=identity.subject_id,
            )
        )
        if type(result) is not tuple or not all(
            isinstance(project, Project) for project in result
        ):
            _raise_internal_result("Project query")
        projects = cast("tuple[Project, ...]", result)
        keys = tuple(project.key for project in projects)
        if keys != tuple(sorted(keys)) or any(
            project.instance_id != identity.instance_id for project in projects
        ):
            _raise_internal_result("Project query")
        return projects

    def create_project(
        self,
        request: ProjectCreateRequest,
    ) -> ProjectCreationResult:
        """Create a named Project using only a resolved initialized profile.

        Args:
            request: Validated Project input and optional profile selector.

        Returns:
            Committed Project and creator Owner grant.

        """
        candidate: object = request
        if not isinstance(candidate, ProjectCreateRequest):
            _raise_invalid_input("Project-create Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        identity = self._select_identity(resolved)
        try:
            command = CreateProjectInput(
                instance_id=identity.instance_id,
                subject_id=identity.subject_id,
                project_key=candidate.key,
                project_name=candidate.name,
                idempotency_key=candidate.idempotency_key,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Project-create Session request is invalid.",
            ) from error
        result: object = resolved.runtime.projects.create(command)
        if (
            not isinstance(result, ProjectCreationResult)
            or result.project.instance_id != identity.instance_id
            or result.project.key != command.project_key
            or result.project.name != command.project_name
            or result.grant.subject_id != identity.subject_id
        ):
            _raise_internal_result("Project creation")
        return result

    def bind_project(self, request: ProjectBindRequest) -> ContextResult:
        """Bind one authorized Project to an explicit or current Workspace.

        Args:
            request: Validated key, optional path/profile, and replacement intent.

        Returns:
            Effective authoritative selection at the durable target context.

        """
        candidate: object = request
        if not isinstance(candidate, ProjectBindRequest):
            _raise_invalid_input("Project-bind Session request is invalid.")
        resolved = self._resolve_runtime(candidate.profile)
        identity = self._select_identity(resolved)
        selected = self._select_project(
            resolved,
            identity,
            explicit_project=candidate.project,
        )
        project = selected.status.project
        binding = WorkspaceBinding(
            context_version=1,
            profile=resolved.profile,
            instance_id=identity.instance_id,
            project_id=project.id,
            project_key=project.key,
            workspace_root=".",
        )
        context_path: object = self._context.bind(
            candidate.path,
            binding,
            replace=candidate.replace,
        )
        validated_path = _require_context_path(context_path)
        return _context_result(
            selected.status,
            workspace_root=validated_path.parent,
            context_source=validated_path,
        )

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Create one Task in the explicit or discovered Project.

        Args:
            request: Validated Task input and optional Project selector.

        Returns:
            Atomically committed Task.

        """
        candidate: object = request
        if not isinstance(candidate, TaskCreateRequest):
            _raise_invalid_input("Task-create Session request is invalid.")
        resolved = self._resolve_runtime(None)
        _require_project_selector(resolved, candidate.project)
        identity = self._select_identity(resolved)
        selected = self._select_project(
            resolved,
            identity,
            explicit_project=candidate.project,
        )
        objective = (
            candidate.title if candidate.objective is None else candidate.objective
        )
        try:
            command = CreateTaskInput(
                project_id=selected.status.project.id,
                subject_id=identity.subject_id,
                title=candidate.title,
                objective=objective,
                priority=candidate.priority,
                available_at=candidate.available_at,
                approval=candidate.approval,
                acceptance=candidate.acceptance,
                context=candidate.context,
                idempotency_key=candidate.idempotency_key,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task-create Session request is invalid.",
            ) from error
        result: object = resolved.runtime.tasks.create(command)
        if (
            not isinstance(result, Task)
            or result.project_id != selected.status.project.id
            or result.key != build_task_key(selected.status.project.key, result.number)
            or result.created_by != identity.subject_id
            or result.title != command.title
            or result.objective != command.objective
            or result.priority != command.priority
            or result.available_at != command.available_at
            or result.approval is not command.approval
            or result.acceptance != command.acceptance
            or result.context != command.context
            or result.state is not TaskState.OPEN
            or result.version != 1
            or result.depends_on != ()
            or result.blocking_reason is not None
            or result.current_result_id is not None
            or result.created_at != result.updated_at
        ):
            _raise_internal_result("Task creation")
        return result

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """List one deterministic explicit, discovered, or all-Projects page.

        Args:
            request: Validated pagination and mutually exclusive selection.

        Returns:
            Stable Task page for the effective selection.

        """
        candidate: object = request
        if not isinstance(candidate, TaskListRequest):
            _raise_invalid_input("Task-list Session request is invalid.")
        resolved = self._resolve_runtime(None)
        if not candidate.all_projects:
            _require_project_selector(resolved, candidate.project)
        identity = self._select_identity(resolved)
        result: object
        expected_project_id: ProjectId | None = None
        if candidate.all_projects:
            try:
                command = ListInstanceTasks(
                    profile=resolved.profile,
                    instance_id=identity.instance_id,
                    subject_id=identity.subject_id,
                    cursor=candidate.cursor,
                    limit=candidate.limit,
                )
            except ValueError as error:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID_INPUT,
                    "Task-list Session request is invalid.",
                ) from error
            result = resolved.runtime.queries.list_tasks_for_instance(command)
        else:
            selected = self._select_project(
                resolved,
                identity,
                explicit_project=candidate.project,
            )
            expected_project_id = selected.status.project.id
            try:
                project_command = ListTasks(
                    profile=resolved.profile,
                    project_id=selected.status.project.id,
                    subject_id=identity.subject_id,
                    cursor=candidate.cursor,
                    limit=candidate.limit,
                )
            except ValueError as error:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID_INPUT,
                    "Task-list Session request is invalid.",
                ) from error
            result = resolved.runtime.queries.list_tasks(project_command)
        if not isinstance(result, TaskPage):
            _raise_internal_result("Task page")
        if expected_project_id is not None and any(
            task.project_id != expected_project_id for task in result.tasks
        ):
            _raise_internal_result("Task page")
        return result

    def get_task(self, request: TaskGetRequest) -> Task:
        """Read one Task from the explicit or discovered Project.

        Args:
            request: Validated Task and optional Project selectors.

        Returns:
            Matching immutable Task.

        """
        candidate: object = request
        if not isinstance(candidate, TaskGetRequest):
            _raise_invalid_input("Task-get Session request is invalid.")
        resolved = self._resolve_runtime(None)
        _require_project_selector(resolved, candidate.project)
        identity = self._select_identity(resolved)
        selected = self._select_project(
            resolved,
            identity,
            explicit_project=candidate.project,
        )
        try:
            command = GetTask(
                project_id=selected.status.project.id,
                subject_id=identity.subject_id,
                task=candidate.task,
            )
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task-get Session request is invalid.",
            ) from error
        result: object = resolved.runtime.queries.get_task(command)
        if not isinstance(result, Task):
            _raise_internal_result("Task query")
        selector_matches = (
            result.uid == command.task
            if isinstance(command.task, TaskId)
            else result.key == command.task
        )
        if result.project_id != selected.status.project.id or not selector_matches:
            _raise_internal_result("Task query")
        return result

    def _resolve_runtime(self, explicit_profile: str | None) -> _ResolvedRuntime:
        """Discover context, resolve trusted profile precedence, and open runtime.

        Args:
            explicit_profile: Validated caller profile selector when present.

        Returns:
            Trusted runtime and optional validated discovered context.

        """
        discovered_value: object = self._context.discover()
        if discovered_value is not None and not isinstance(
            discovered_value,
            WorkspaceContextSelection,
        ):
            _raise_context_invalid()
        discovered = discovered_value
        discovered_profile = None if discovered is None else discovered.binding.profile
        profile_value: object = self._profiles.resolve(
            explicit_profile=explicit_profile,
            discovered_profile=discovered_profile,
        )
        try:
            profile = validate_profile_name(profile_value)
        except ValueError as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Profile resolution returned an invalid result.",
            ) from error
        runtime_value: object = self._runtimes.open(profile)
        if (
            not isinstance(runtime_value, LocalRuntime)
            or runtime_value.profile != profile
        ):
            _raise_internal_result("Local runtime")
        return _ResolvedRuntime(
            profile=profile,
            runtime=runtime_value,
            discovered=discovered,
        )

    def _select_identity(self, resolved: _ResolvedRuntime) -> LocalIdentity:
        """Select and validate one runtime-owned local identity pair.

        Args:
            resolved: Trusted opened runtime.

        Returns:
            Validated Instance and Subject identities.

        """
        identity: object = resolved.runtime.identity.select()
        if not isinstance(identity, LocalIdentity):
            _raise_internal_result("Local identity selection")
        return identity

    def _select_project(
        self,
        resolved: _ResolvedRuntime,
        identity: LocalIdentity,
        *,
        explicit_project: str | None,
    ) -> _SelectedProject:
        """Apply explicit-then-context Project precedence and verify authority.

        Args:
            resolved: Trusted opened runtime and optional discovered context.
            identity: Runtime-owned Instance and Subject identities.
            explicit_project: Validated explicit Project key when supplied.

        Returns:
            Authorized status and paths only when context matches selection.

        """
        discovered = resolved.discovered
        applicable_context = (
            discovered
            if discovered is not None and discovered.binding.profile == resolved.profile
            else None
        )
        if applicable_context is not None and (
            applicable_context.binding.instance_id != identity.instance_id
        ):
            _raise_context_invalid()

        if explicit_project is not None:
            project: object = resolved.runtime.queries.get_project_by_key(
                GetProjectByKey(
                    instance_id=identity.instance_id,
                    subject_id=identity.subject_id,
                    project_key=explicit_project,
                )
            )
            if (
                not isinstance(project, Project)
                or project.instance_id != identity.instance_id
                or project.key != explicit_project
            ):
                _raise_internal_result("Project query")
            project_id = project.id
        elif applicable_context is not None:
            project_id = applicable_context.binding.project_id
        else:
            _raise_context_not_found()

        status: object = resolved.runtime.queries.status(
            GetLocalStatus(
                profile=resolved.profile,
                instance_id=identity.instance_id,
                project_id=project_id,
                subject_id=identity.subject_id,
            )
        )
        if (
            not isinstance(status, StatusResult)
            or status.profile != resolved.profile
            or status.instance.id != identity.instance_id
            or status.project.id != project_id
            or status.subject.id != identity.subject_id
        ):
            _raise_internal_result("Status query")
        if applicable_context is not None and (
            applicable_context.binding.project_id == status.project.id
        ):
            if applicable_context.binding.project_key != status.project.key:
                _raise_context_invalid()
            workspace_root = applicable_context.workspace_root
            context_source = applicable_context.context_source
        else:
            workspace_root = None
            context_source = None
        return _SelectedProject(
            status=status,
            workspace_root=workspace_root,
            context_source=context_source,
        )


def _context_result(
    status: StatusResult,
    *,
    workspace_root: Path | None,
    context_source: Path | None,
) -> ContextResult:
    """Build one safe effective context from verified status and paths.

    Args:
        status: Fully authorized status for the effective Project.
        workspace_root: Matching canonical Workspace root when available.
        context_source: Matching canonical context file when available.

    Returns:
        Validated complete effective context.

    Raises:
        ApplicationError: If verified dependencies combine inconsistently.

    """
    try:
        return ContextResult(
            profile=status.profile,
            instance=status.instance,
            project=status.project,
            subject=status.subject,
            grant=status.grant,
            workspace_root=workspace_root,
            context_source=context_source,
        )
    except ValueError as error:
        raise ApplicationError(
            ApplicationErrorCode.INTERNAL_ERROR,
            "Context selection returned an invalid result.",
        ) from error


def _require_project_selector(
    resolved: _ResolvedRuntime,
    explicit_project: str | None,
) -> None:
    """Fail before local-state access when no Project selector is applicable.

    Args:
        resolved: Trusted opened runtime and optional discovered context.
        explicit_project: Validated explicit Project key when supplied.

    Raises:
        ApplicationError: If neither explicit nor same-profile context selects
            a Project.

    """
    if explicit_project is not None:
        return
    discovered = resolved.discovered
    if discovered is None or discovered.binding.profile != resolved.profile:
        _raise_context_not_found()


def _require_context_path(value: object) -> Path:
    """Require one canonical absolute context-file path.

    Args:
        value: Candidate context gateway result.

    Returns:
        Validated absolute ``.workaholic.env`` path.

    Raises:
        ApplicationError: If the context gateway violates its result contract.

    """
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or value.name != ".workaholic.env"
    ):
        _raise_internal_result("Workspace context")
    return value


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


def _raise_context_not_found() -> Never:
    """Raise the stable missing effective Project-selection failure.

    Raises:
        ApplicationError: Always.

    """
    raise ApplicationError(
        ApplicationErrorCode.CONTEXT_NOT_FOUND,
        "No Workspace context or explicit Project selection was found.",
    )


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
