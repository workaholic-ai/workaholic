"""Unit tests for the transport-neutral Phase 1 LocalSession."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

import workaholic.session as session_package
from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapLocalProjectInput,
    BootstrapResult,
    CreateTaskInput,
    GetLocalStatus,
    GetTask,
    IdempotencyConflictError,
    ListProjects,
    ListTasks,
    PermissionDeniedError,
    StatusResult,
    TaskPage,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskId,
    TaskState,
    WorkspaceBinding,
)
from workaholic.session import (
    LocalActorSelector,
    LocalSession,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
    WorkspaceContextGateway,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workaholic.application import (
        BootstrapApplication,
        QueryApplication,
        TaskApplication,
    )

_NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def _binding(*, project_key: str = "ACME") -> WorkspaceBinding:
    """Build one exact-directory local Workspace binding.

    Args:
        project_key: Context Project key override.

    Returns:
        Validated Workspace binding.

    """
    return WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        project_key=project_key,
        workspace_root=".",
    )


def _instance() -> Instance:
    """Build the authoritative local Instance.

    Returns:
        Deterministic Instance fixture.

    """
    return Instance(id=InstanceId("ins_local"), created_at=_NOW)


def _project() -> Project:
    """Build the authoritative ACME Project.

    Returns:
        Deterministic Project fixture.

    """
    return Project(
        id=ProjectId("prj_acme"),
        instance_id=InstanceId("ins_local"),
        key="ACME",
        name="Acme",
        created_at=_NOW,
    )


def _subject() -> Subject:
    """Build the authoritative local Human Owner.

    Returns:
        Deterministic Subject fixture.

    """
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _grant() -> ProjectGrant:
    """Build the authoritative local Owner grant.

    Returns:
        Deterministic ProjectGrant fixture.

    """
    return ProjectGrant(
        subject_id=SubjectId("sub_local"),
        project_id=ProjectId("prj_acme"),
        role=ProjectRole.OWNER,
    )


def _bootstrap_result() -> BootstrapResult:
    """Build one internally consistent bootstrap result.

    Returns:
        Deterministic bootstrap result.

    """
    return BootstrapResult(
        instance=_instance(),
        project=_project(),
        subject=_subject(),
        grant=_grant(),
        workspace=_binding(),
    )


def _status_result() -> StatusResult:
    """Build one internally consistent local status.

    Returns:
        Deterministic status result.

    """
    return StatusResult(
        instance=_instance(),
        project=_project(),
        subject=_subject(),
        grant=_grant(),
    )


def _task() -> Task:
    """Build one valid attributable initial Task.

    Returns:
        Deterministic Task fixture.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="First task",
        objective="First task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _other_task() -> Task:
    """Build one valid Task from another Project.

    Returns:
        Deterministic cross-Project Task fixture.

    """
    return Task(
        uid=TaskId("tsk_other"),
        project_id=ProjectId("prj_beta"),
        number=1,
        key="BETA-1",
        title="Other task",
        objective="Other task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Context:
    """Recording exact-directory context gateway fake."""

    def __init__(
        self,
        log: list[str],
        *,
        binding: WorkspaceBinding | None = None,
    ) -> None:
        """Initialize context state and failure controls.

        Args:
            log: Shared operation-order recording.
            binding: Binding returned by reads.

        """
        self.log = log
        self.binding = binding if binding is not None else _binding()
        self.read_error: ApplicationError | None = None
        self.write_errors: list[ApplicationError] = []
        self.write_result: object = Path("/workspace/.workaholic.env")
        self.written: list[WorkspaceBinding] = []

    def read_current(self) -> WorkspaceBinding:
        """Record and return or fail the configured context read."""
        self.log.append("context.read")
        if self.read_error is not None:
            raise self.read_error
        return self.binding

    def write_current(self, binding: WorkspaceBinding) -> Path:
        """Record and return or fail one configured context write."""
        self.log.append("context.write")
        self.written.append(binding)
        if self.write_errors:
            raise self.write_errors.pop(0)
        return cast("Path", self.write_result)


class _Actors:
    """Recording trusted local actor selector fake."""

    def __init__(self, log: list[str]) -> None:
        """Initialize the selected Subject and recordings."""
        self.log = log
        self.result: object = SubjectId("sub_local")
        self.bindings: list[WorkspaceBinding] = []

    def select(self, binding: WorkspaceBinding) -> SubjectId:
        """Record selection and return the configured identity."""
        self.log.append("actors.select")
        self.bindings.append(binding)
        return cast("SubjectId", self.result)


class _Bootstrap:
    """Recording bootstrap application fake."""

    def __init__(self, log: list[str]) -> None:
        """Initialize a valid result and no failure."""
        self.log = log
        self.result: object = _bootstrap_result()
        self.error: ApplicationError | None = None
        self.commands: list[BootstrapLocalProjectInput] = []

    def up(self, command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Record and return or fail one bootstrap invocation."""
        self.log.append("bootstrap.up")
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return cast("BootstrapResult", self.result)


class _Queries:
    """Recording query application fake."""

    def __init__(self, log: list[str]) -> None:
        """Initialize valid results, recordings, and no failures."""
        self.log = log
        self.status_result: object = _status_result()
        self.projects_result: object = (_project(),)
        self.tasks_result: object = TaskPage(tasks=(_task(),), next_cursor=None)
        self.task_result: object = _task()
        self.status_error: ApplicationError | None = None
        self.projects_error: ApplicationError | None = None
        self.tasks_error: ApplicationError | None = None
        self.task_error: ApplicationError | None = None
        self.status_commands: list[GetLocalStatus] = []
        self.project_commands: list[ListProjects] = []
        self.task_list_commands: list[ListTasks] = []
        self.task_get_commands: list[GetTask] = []

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Record and return or fail one status query."""
        self.log.append("queries.status")
        self.status_commands.append(command)
        if self.status_error is not None:
            raise self.status_error
        return cast("StatusResult", self.status_result)

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """Record and return or fail one Project query."""
        self.log.append("queries.list_projects")
        self.project_commands.append(command)
        if self.projects_error is not None:
            raise self.projects_error
        return cast("tuple[Project, ...]", self.projects_result)

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Record and return or fail one Task page query."""
        self.log.append("queries.list_tasks")
        self.task_list_commands.append(command)
        if self.tasks_error is not None:
            raise self.tasks_error
        return cast("TaskPage", self.tasks_result)

    def get_task(self, command: GetTask) -> Task:
        """Record and return or fail one Task lookup."""
        self.log.append("queries.get_task")
        self.task_get_commands.append(command)
        if self.task_error is not None:
            raise self.task_error
        return cast("Task", self.task_result)


class _Tasks:
    """Recording Task application fake."""

    def __init__(self, log: list[str]) -> None:
        """Initialize a valid Task result and no failure."""
        self.log = log
        self.result: object = _task()
        self.error: ApplicationError | None = None
        self.commands: list[CreateTaskInput] = []

    def create(self, command: CreateTaskInput) -> Task:
        """Record and return or fail one Task creation."""
        self.log.append("tasks.create")
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return cast("Task", self.result)


def _dependencies() -> tuple[
    list[str],
    _Context,
    _Actors,
    _Bootstrap,
    _Queries,
    _Tasks,
]:
    """Build one complete set of explicit recording dependencies.

    Returns:
        Shared log followed by each concrete fake.

    """
    log: list[str] = []
    return (
        log,
        _Context(log),
        _Actors(log),
        _Bootstrap(log),
        _Queries(log),
        _Tasks(log),
    )


def _session(
    context: _Context,
    actors: _Actors,
    bootstrap: _Bootstrap,
    queries: _Queries,
    tasks: _Tasks,
) -> LocalSession:
    """Compose one LocalSession from structurally typed fakes.

    Args:
        context: Context fake.
        actors: Actor selector fake.
        bootstrap: Bootstrap application fake.
        queries: Query application fake.
        tasks: Task application fake.

    Returns:
        Configured LocalSession.

    """
    return LocalSession(
        context=context,
        actors=actors,
        bootstrap=bootstrap,
        queries=queries,
        tasks=tasks,
    )


def test_up_commits_before_writing_exact_context() -> None:
    """Bootstrap output is durable before its exact binding is written."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)
    request = UpRequest(project_key="ACME", idempotency_key="up-1")

    result = session.up(request)

    assert result == _bootstrap_result()
    assert bootstrap.commands == [
        BootstrapLocalProjectInput(
            project_key="ACME",
            idempotency_key="up-1",
        )
    ]
    assert context.written == [result.workspace]
    assert log == ["bootstrap.up", "context.write"]
    assert actors.bindings == []


def test_bootstrap_failure_never_attempts_context_write() -> None:
    """A failed durable bootstrap cannot leave a misleading binding."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    failure = ApplicationError(
        ApplicationErrorCode.STORAGE_UNAVAILABLE,
        "Local storage is unavailable.",
    )
    bootstrap.error = failure
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(ApplicationError) as captured:
        session.up(UpRequest(project_key="ACME"))

    assert captured.value is failure
    assert log == ["bootstrap.up"]
    assert context.written == []


def test_context_failure_can_be_retried_after_durable_bootstrap() -> None:
    """Retry invokes idempotent bootstrap again and then completes the binding."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    context.write_errors.append(
        ApplicationError(
            ApplicationErrorCode.STORAGE_UNAVAILABLE,
            "Workspace context could not be written.",
        )
    )
    session = _session(context, actors, bootstrap, queries, tasks)
    request = UpRequest(project_key="ACME", idempotency_key="up-1")

    with pytest.raises(ApplicationError):
        session.up(request)
    result = session.up(request)

    assert result == _bootstrap_result()
    assert bootstrap.commands == [
        BootstrapLocalProjectInput(
            project_key="ACME",
            idempotency_key="up-1",
        ),
        BootstrapLocalProjectInput(
            project_key="ACME",
            idempotency_key="up-1",
        ),
    ]
    assert context.written == [result.workspace, result.workspace]
    assert log == [
        "bootstrap.up",
        "context.write",
        "bootstrap.up",
        "context.write",
    ]


def test_successful_operations_supply_verified_subject_and_context() -> None:
    """Every normal operation verifies status and supplies one selected Human."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)

    status = session.status(StatusRequest())
    projects = session.list_projects(ProjectListRequest())
    created = session.create_task(
        TaskCreateRequest(
            title="  First task  ",
            idempotency_key="task-1",
        )
    )
    page = session.list_tasks(TaskListRequest(limit=25))
    found = session.get_task(TaskGetRequest(task="ACME-1"))

    assert status == _status_result()
    assert projects == (_project(),)
    assert created == _task()
    assert page == TaskPage(tasks=(_task(),), next_cursor=None)
    assert found == _task()
    expected_status = GetLocalStatus(
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )
    assert queries.status_commands == [expected_status] * 5
    assert queries.project_commands == [
        ListProjects(
            instance_id=InstanceId("ins_local"),
            subject_id=SubjectId("sub_local"),
        )
    ]
    assert tasks.commands == [
        CreateTaskInput(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            title="First task",
            objective="First task",
            priority=50,
            idempotency_key="task-1",
        )
    ]
    assert queries.task_list_commands == [
        ListTasks(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            limit=25,
        )
    ]
    assert queries.task_get_commands == [
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task="ACME-1",
        )
    ]
    assert actors.bindings == [_binding()] * 5
    assert context.written == []
    assert log == [
        "context.read",
        "actors.select",
        "queries.status",
        "context.read",
        "actors.select",
        "queries.status",
        "queries.list_projects",
        "context.read",
        "actors.select",
        "queries.status",
        "tasks.create",
        "context.read",
        "actors.select",
        "queries.status",
        "queries.list_tasks",
        "context.read",
        "actors.select",
        "queries.status",
        "queries.get_task",
    ]


def test_context_failure_precedes_actor_and_application_invocation() -> None:
    """Every non-bootstrap operation reads exact context before other work."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    failure = ApplicationError(
        ApplicationErrorCode.CONTEXT_NOT_FOUND,
        "No exact-directory context exists.",
    )
    context.read_error = failure
    session = _session(context, actors, bootstrap, queries, tasks)
    invocations: tuple[Callable[[], object], ...] = (
        lambda: session.status(StatusRequest()),
        lambda: session.list_projects(ProjectListRequest()),
        lambda: session.create_task(TaskCreateRequest(title="First task")),
        lambda: session.list_tasks(TaskListRequest()),
        lambda: session.get_task(TaskGetRequest(task="ACME-1")),
    )

    for invoke in invocations:
        log.clear()
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value is failure
        assert log == ["context.read"]
    assert actors.bindings == []
    assert queries.status_commands == []
    assert tasks.commands == []


def test_task_uid_selector_is_preserved_through_session() -> None:
    """A canonical TaskId remains typed when the Session adds actor context."""
    _log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)

    result = session.get_task(TaskGetRequest(task=TaskId("tsk_first")))

    assert result == _task()
    assert queries.task_get_commands == [
        GetTask(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            task=TaskId("tsk_first"),
        )
    ]


def test_context_key_mismatch_is_rejected_before_requested_operation() -> None:
    """Untrusted key text must match authoritative Project identity and key."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    context.binding = _binding(project_key="BETA")
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(ApplicationError) as captured:
        session.list_tasks(TaskListRequest())

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
    assert queries.task_list_commands == []
    assert log == ["context.read", "actors.select", "queries.status"]


def test_authorization_failure_propagates_before_mutation() -> None:
    """Disabled or unauthorized actor status stops Task creation unchanged."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    failure = PermissionDeniedError()
    queries.status_error = failure
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(PermissionDeniedError) as captured:
        session.create_task(TaskCreateRequest(title="First task"))

    assert captured.value is failure
    assert tasks.commands == []
    assert log == ["context.read", "actors.select", "queries.status"]


def test_requested_application_error_propagates_unchanged() -> None:
    """Typed failures from the requested operation keep their public semantics."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    failure = IdempotencyConflictError()
    tasks.error = failure
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(IdempotencyConflictError) as captured:
        session.create_task(
            TaskCreateRequest(
                title="First task",
                idempotency_key="task-1",
            )
        )

    assert captured.value is failure
    assert log == [
        "context.read",
        "actors.select",
        "queries.status",
        "tasks.create",
    ]


def test_malformed_user_values_map_to_invalid_input_after_context() -> None:
    """Session-built application commands never leak Pydantic diagnostics."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)
    invalid_operations: tuple[Callable[[], object], ...] = (
        lambda: session.up(UpRequest.model_construct(project_key="bad")),
        lambda: session.create_task(TaskCreateRequest(title="   ")),
        lambda: session.list_tasks(TaskListRequest(cursor=" ")),
        lambda: session.get_task(TaskGetRequest(task="not-a-task")),
    )

    for invoke in invalid_operations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert bootstrap.commands == []
    assert tasks.commands == []
    assert queries.task_list_commands == []
    assert queries.task_get_commands == []
    assert log == [
        "context.read",
        "actors.select",
        "queries.status",
        "context.read",
        "actors.select",
        "queries.status",
        "context.read",
        "actors.select",
        "queries.status",
    ]


def test_runtime_request_type_validation_precedes_all_dependencies() -> None:
    """Direct callers cannot bypass any Session request model."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)
    invocations: tuple[Callable[[], object], ...] = (
        lambda: session.up(cast("UpRequest", object())),
        lambda: session.status(cast("StatusRequest", object())),
        lambda: session.list_projects(cast("ProjectListRequest", object())),
        lambda: session.create_task(cast("TaskCreateRequest", object())),
        lambda: session.list_tasks(cast("TaskListRequest", object())),
        lambda: session.get_task(cast("TaskGetRequest", object())),
    )

    for invoke in invocations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert log == []


@pytest.mark.parametrize(
    "dependency_index",
    range(5),
)
def test_constructor_runtime_validates_every_dependency(
    dependency_index: int,
) -> None:
    """Missing dependency operations fail at composition time."""
    _log, context, actors, bootstrap, queries, tasks = _dependencies()
    dependencies: list[object] = [context, actors, bootstrap, queries, tasks]
    dependencies[dependency_index] = object()

    with pytest.raises(TypeError, match="LocalSession"):
        LocalSession(
            context=cast("WorkspaceContextGateway", dependencies[0]),
            actors=cast("LocalActorSelector", dependencies[1]),
            bootstrap=cast("BootstrapApplication", dependencies[2]),
            queries=cast("QueryApplication", dependencies[3]),
            tasks=cast("TaskApplication", dependencies[4]),
        )


def test_invalid_dependency_outputs_are_safe_internal_errors() -> None:
    """Malformed service values never cross the Session result boundary."""
    _log, context, actors, bootstrap, queries, tasks = _dependencies()
    bootstrap.result = object()
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(ApplicationError) as bootstrap_error:
        session.up(UpRequest(project_key="ACME"))
    assert bootstrap_error.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert context.written == []

    bootstrap.result = _bootstrap_result()
    actors.result = object()
    with pytest.raises(ApplicationError) as actor_error:
        session.status(StatusRequest())
    assert actor_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    actors.result = SubjectId("sub_local")
    queries.status_result = object()
    with pytest.raises(ApplicationError) as status_error:
        session.status(StatusRequest())
    assert status_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    queries.status_result = _status_result()
    queries.projects_result = object()
    with pytest.raises(ApplicationError) as projects_error:
        session.list_projects(ProjectListRequest())
    assert projects_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    queries.projects_result = (_project(),)
    tasks.result = object()
    with pytest.raises(ApplicationError) as task_create_error:
        session.create_task(TaskCreateRequest(title="First task"))
    assert task_create_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    tasks.result = _task()
    queries.tasks_result = object()
    with pytest.raises(ApplicationError) as task_page_error:
        session.list_tasks(TaskListRequest())
    assert task_page_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    queries.tasks_result = TaskPage(tasks=(_task(),), next_cursor=None)
    queries.task_result = object()
    with pytest.raises(ApplicationError) as task_get_error:
        session.get_task(TaskGetRequest(task="ACME-1"))
    assert task_get_error.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_type_correct_cross_selection_outputs_are_internal_errors() -> None:
    """A service cannot return valid models from another query selection."""
    _log, context, actors, bootstrap, queries, tasks = _dependencies()
    session = _session(context, actors, bootstrap, queries, tasks)
    other_project = Project(
        id=ProjectId("prj_beta"),
        instance_id=InstanceId("ins_other"),
        key="BETA",
        name="Beta",
        created_at=_NOW,
    )
    queries.projects_result = (other_project,)
    with pytest.raises(ApplicationError) as projects_error:
        session.list_projects(ProjectListRequest())
    assert projects_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    tasks.result = Task(
        uid=TaskId("tsk_wrong"),
        project_id=ProjectId("prj_acme"),
        number=2,
        key="ACME-2",
        title="Wrong task",
        objective="Wrong task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )
    with pytest.raises(ApplicationError) as create_error:
        session.create_task(TaskCreateRequest(title="First task"))
    assert create_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    queries.tasks_result = TaskPage(tasks=(_other_task(),), next_cursor=None)
    with pytest.raises(ApplicationError) as page_error:
        session.list_tasks(TaskListRequest())
    assert page_error.value.code is ApplicationErrorCode.INTERNAL_ERROR

    queries.task_result = _task()
    with pytest.raises(ApplicationError) as task_error:
        session.get_task(TaskGetRequest(task="ACME-404"))
    assert task_error.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_invalid_context_gateway_output_is_context_invalid() -> None:
    """A gateway cannot inject a non-binding object into actor selection."""

    class _InvalidContext:
        """Context gateway that violates its read contract."""

        def read_current(self) -> object:
            """Return a deliberately invalid context value."""
            return object()

        def write_current(self, _binding: WorkspaceBinding) -> Path:
            """Return a valid unused context path."""
            return Path("/workspace/.workaholic.env")

    log, _context, actors, bootstrap, queries, tasks = _dependencies()
    session = LocalSession(
        context=cast("WorkspaceContextGateway", _InvalidContext()),
        actors=actors,
        bootstrap=bootstrap,
        queries=queries,
        tasks=tasks,
    )

    with pytest.raises(ApplicationError) as captured:
        session.status(StatusRequest())

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
    assert log == []
    assert actors.bindings == []


def test_invalid_context_write_result_is_reported_after_durable_bootstrap() -> None:
    """A context gateway contract breach is redacted without hiding call order."""
    log, context, actors, bootstrap, queries, tasks = _dependencies()
    context.write_result = object()
    session = _session(context, actors, bootstrap, queries, tasks)

    with pytest.raises(ApplicationError) as captured:
        session.up(UpRequest(project_key="ACME"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert log == ["bootstrap.up", "context.write"]


def test_request_models_are_strict_frozen_and_bounded() -> None:
    """Session requests reject coercion, extras, and unsupported page bounds."""
    assert TaskCreateRequest(title="First task").priority == 50
    assert TaskCreateRequest(title="First task").objective is None
    assert TaskCreateRequest(title=f" {'x' * 200} ").title.endswith(" ")
    assert TaskListRequest().limit == 100
    with pytest.raises(ValidationError):
        StatusRequest.model_validate({"extra": True})
    with pytest.raises(ValidationError):
        TaskListRequest(limit=True)
    with pytest.raises(ValidationError):
        TaskListRequest(limit=501)
    with pytest.raises(ValidationError):
        TaskCreateRequest(title="First task", priority=-1)
    request = UpRequest(project_key="ACME")
    with pytest.raises(ValidationError):
        request.project_key = "BETA"


def test_session_package_has_no_forbidden_boundary_imports() -> None:
    """Session source imports no presentation, concrete adapter, or transport."""
    package_file = session_package.__file__
    assert package_file is not None
    session_root = Path(package_file).parent
    forbidden_prefixes = (
        "typer",
        "workaholic.cli",
        "workaholic.client",
        "workaholic.context",
        "workaholic.persistence",
        "workaholic.protocol",
        "workaholic.server",
    )

    for path in session_root.glob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in forbidden_prefixes
        ), f"{path.name} imports a forbidden boundary: {sorted(imported)}"
