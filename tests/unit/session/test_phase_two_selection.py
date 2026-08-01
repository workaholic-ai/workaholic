"""Phase 2 tests for profile-aware LocalSession selection and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapLocalProjectInput,
    BootstrapResult,
    CreateProjectInput,
    CreateTaskInput,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProfileUnsupportedError,
    ProjectCreationResult,
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
    WorkspaceBinding,
)
from workaholic.session import (
    ContextRequest,
    LocalIdentity,
    LocalRuntime,
    LocalSession,
    ProjectCreateRequest,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskListRequest,
    UpRequest,
    WorkspaceContextSelection,
)

_NOW = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)


def _instance(*, instance_id: str = "ins_local") -> Instance:
    """Build one deterministic Instance fixture."""
    return Instance(id=InstanceId(instance_id), created_at=_NOW)


def _subject() -> Subject:
    """Build the trusted embedded Human fixture."""
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _project(
    *,
    key: str = "ACME",
    project_id: str = "prj_acme",
    instance_id: str = "ins_local",
    name: str | None = None,
) -> Project:
    """Build one deterministic Project fixture."""
    return Project(
        id=ProjectId(project_id),
        instance_id=InstanceId(instance_id),
        key=key,
        name=key if name is None else name,
        created_at=_NOW,
    )


def _grant(project: Project) -> ProjectGrant:
    """Build the trusted Human's Owner grant for a Project."""
    return ProjectGrant(
        subject_id=SubjectId("sub_local"),
        project_id=project.id,
        role=ProjectRole.OWNER,
    )


def _status(*, profile: str = "local", project: Project | None = None) -> StatusResult:
    """Build one internally consistent status for a selected profile."""
    selected = _project() if project is None else project
    return StatusResult(
        profile=profile,
        instance=_instance(instance_id=str(selected.instance_id)),
        project=selected,
        subject=_subject(),
        grant=_grant(selected),
    )


def _binding(
    *,
    profile: str = "local",
    project: Project | None = None,
    instance_id: str | None = None,
) -> WorkspaceBinding:
    """Build one validated discovered Workspace binding."""
    selected = _project() if project is None else project
    return WorkspaceBinding(
        context_version=1,
        profile=profile,
        instance_id=InstanceId(
            str(selected.instance_id) if instance_id is None else instance_id
        ),
        project_id=selected.id,
        project_key=selected.key,
        workspace_root=".",
    )


def _selection(
    *,
    profile: str = "local",
    project: Project | None = None,
    instance_id: str | None = None,
) -> WorkspaceContextSelection:
    """Build one canonical nearest-context discovery result."""
    return WorkspaceContextSelection(
        binding=_binding(
            profile=profile,
            project=project,
            instance_id=instance_id,
        ),
        context_source=Path("/repo/.workaholic.env"),
        workspace_root=Path("/repo/packages/api"),
    )


def _bootstrap_result(*, profile: str = "local") -> BootstrapResult:
    """Build one valid bootstrap result for a selected profile."""
    project = _project()
    return BootstrapResult(
        instance=_instance(),
        project=project,
        subject=_subject(),
        grant=_grant(project),
        workspace=_binding(profile=profile, project=project),
    )


class _Context:
    """Strict recording upward-discovery and context-write fake."""

    def __init__(self, selection: object = None) -> None:
        """Initialize one configurable discovery result."""
        self.selection = selection
        self.discover_calls = 0
        self.writes: list[WorkspaceBinding] = []
        self.binds: list[tuple[Path | None, WorkspaceBinding, bool]] = []

    def discover(self) -> WorkspaceContextSelection | None:
        """Return the configured nearest context result."""
        self.discover_calls += 1
        return cast("WorkspaceContextSelection | None", self.selection)

    def write_current(self, binding: WorkspaceBinding) -> Path:
        """Record an exact-current-directory binding write."""
        self.writes.append(binding)
        return Path("/repo/packages/api/.workaholic.env")

    def bind(
        self,
        directory: Path | None,
        binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Record an explicit Project binding write."""
        self.binds.append((directory, binding, replace))
        target = Path("/repo") if directory is None else directory
        return target / ".workaholic.env"


class _Profiles:
    """Strict recording trusted profile resolver fake."""

    def __init__(self, result: object = "local") -> None:
        """Initialize the configured resolver result or error."""
        self.result = result
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Record precedence inputs and return or raise the configured result."""
        self.calls.append((explicit_profile, discovered_profile))
        if isinstance(self.result, ApplicationError):
            raise self.result
        return cast("str", self.result)


class _Identity:
    """Strict recording runtime-owned identity selector fake."""

    def __init__(self, result: object | None = None) -> None:
        """Initialize a valid identity unless overridden."""
        self.result = (
            LocalIdentity(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
            )
            if result is None
            else result
        )
        self.calls = 0

    def select(self) -> LocalIdentity:
        """Record one trusted identity selection."""
        self.calls += 1
        return cast("LocalIdentity", self.result)


class _Bootstrap:
    """Strict recording bootstrap service fake."""

    def __init__(self, result: object) -> None:
        """Initialize a configured bootstrap result."""
        self.result = result
        self.commands: list[BootstrapLocalProjectInput] = []

    def up(self, command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Record and return one bootstrap result."""
        self.commands.append(command)
        return cast("BootstrapResult", self.result)


class _Projects:
    """Strict recording Project application fake."""

    def __init__(self, result: object) -> None:
        """Initialize a configured creation result."""
        self.result = result
        self.commands: list[CreateProjectInput] = []

    def create(self, command: CreateProjectInput) -> ProjectCreationResult:
        """Record and return one Project creation result."""
        self.commands.append(command)
        return cast("ProjectCreationResult", self.result)


class _Queries:
    """Strict recording query application fake."""

    def __init__(self, *, profile: str = "local") -> None:
        """Initialize valid ACME and BETA results for one profile."""
        beta = _project(key="BETA", project_id="prj_beta")
        self.status_results: dict[ProjectId, object] = {
            ProjectId("prj_acme"): _status(profile=profile),
            beta.id: _status(profile=profile, project=beta),
        }
        self.projects_result: object = (
            _project(),
            beta,
        )
        self.project_results: dict[str, object] = {
            "ACME": _project(),
            "BETA": beta,
        }
        self.task_page_result: object = TaskPage(tasks=(), next_cursor=None)
        self.status_commands: list[GetLocalStatus] = []
        self.list_project_commands: list[ListProjects] = []
        self.get_project_commands: list[GetProjectByKey] = []
        self.list_task_commands: list[ListTasks] = []
        self.list_instance_task_commands: list[ListInstanceTasks] = []

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Record and return status for the exact requested Project."""
        self.status_commands.append(command)
        return cast("StatusResult", self.status_results[command.project_id])

    def list_projects(self, command: ListProjects) -> tuple[Project, ...]:
        """Record and return the configured Project tuple."""
        self.list_project_commands.append(command)
        return cast("tuple[Project, ...]", self.projects_result)

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Record and return the configured exact-key Project."""
        self.get_project_commands.append(command)
        return cast("Project", self.project_results[command.project_key])

    def list_tasks(self, command: ListTasks) -> TaskPage:
        """Record and return the configured selected-Project page."""
        self.list_task_commands.append(command)
        return cast("TaskPage", self.task_page_result)

    def list_tasks_for_instance(self, command: ListInstanceTasks) -> TaskPage:
        """Record and return the configured all-Projects page."""
        self.list_instance_task_commands.append(command)
        return cast("TaskPage", self.task_page_result)

    def get_task(self, _command: GetTask) -> Task:
        """Fail if a selection test unexpectedly queries one Task."""
        pytest.fail("This selection test must not query one Task")


class _Tasks:
    """Strict recording Task application fake."""

    def __init__(self, result: object = None) -> None:
        """Initialize the configured Task result."""
        self.result = result
        self.commands: list[CreateTaskInput] = []

    def create(self, command: CreateTaskInput) -> Task:
        """Record and return one Task result."""
        self.commands.append(command)
        return cast("Task", self.result)


@dataclass(slots=True)
class _RuntimeBundle:
    """Owned services for one test runtime."""

    runtime: LocalRuntime
    identity: _Identity
    bootstrap: _Bootstrap
    projects: _Projects
    queries: _Queries
    tasks: _Tasks


def _runtime(*, profile: str = "local") -> _RuntimeBundle:
    """Build one complete recording runtime for a trusted profile."""
    identity = _Identity()
    bootstrap = _Bootstrap(_bootstrap_result(profile=profile))
    beta = _project(key="BETA", project_id="prj_beta")
    projects = _Projects(ProjectCreationResult(project=beta, grant=_grant(beta)))
    queries = _Queries(profile=profile)
    tasks = _Tasks()
    return _RuntimeBundle(
        runtime=LocalRuntime(
            profile=profile,
            identity=identity,
            bootstrap=bootstrap,
            projects=projects,
            queries=queries,
            tasks=tasks,
        ),
        identity=identity,
        bootstrap=bootstrap,
        projects=projects,
        queries=queries,
        tasks=tasks,
    )


class _Runtimes:
    """Strict recording trusted runtime opener fake."""

    def __init__(self, runtimes: dict[str, object]) -> None:
        """Initialize exact configured profile runtimes."""
        self.runtimes = runtimes
        self.calls: list[str] = []

    def open(self, profile: str) -> LocalRuntime:
        """Record and return the exact configured runtime value."""
        self.calls.append(profile)
        value = self.runtimes.get(profile)
        if isinstance(value, ApplicationError):
            raise value
        return cast("LocalRuntime", value)


def test_explicit_profile_and_project_override_discovered_context() -> None:
    """Explicit selectors choose their runtime without trusting another profile."""
    context = _Context(_selection())
    profiles = _Profiles("team")
    team = _runtime(profile="team")
    runtimes = _Runtimes({"team": team.runtime})
    session = LocalSession(context=context, profiles=profiles, runtimes=runtimes)

    result = session.status(StatusRequest(profile="team", project="BETA"))

    assert result.profile == "team"
    assert result.project.key == "BETA"
    assert profiles.calls == [("team", "local")]
    assert runtimes.calls == ["team"]
    assert team.queries.get_project_commands[0].project_key == "BETA"
    assert team.queries.status_commands[0].profile == "team"


def test_discovered_profile_precedes_configured_default() -> None:
    """A nearest binding supplies profile selection when no explicit value exists."""
    context = _Context(_selection(profile="team"))
    profiles = _Profiles("team")
    team = _runtime(profile="team")
    runtimes = _Runtimes({"team": team.runtime})
    session = LocalSession(context=context, profiles=profiles, runtimes=runtimes)

    result = session.status(StatusRequest())

    assert result.profile == "team"
    assert result.project.key == "ACME"
    assert profiles.calls == [(None, "team")]
    assert runtimes.calls == ["team"]


def test_project_listing_and_creation_require_no_workspace_context() -> None:
    """Profile-scoped Project operations work from an unbound directory."""
    context = _Context()
    profiles = _Profiles()
    local = _runtime()
    created_project = _project(
        key="BETA",
        project_id="prj_beta",
        name="Beta delivery",
    )
    local.projects.result = ProjectCreationResult(
        project=created_project,
        grant=_grant(created_project),
    )
    runtimes = _Runtimes({"local": local.runtime})
    session = LocalSession(context=context, profiles=profiles, runtimes=runtimes)

    listed = session.list_projects(ProjectListRequest())
    created = session.create_project(
        ProjectCreateRequest(
            key="BETA",
            name="Beta delivery",
            idempotency_key="project-1",
        )
    )

    assert tuple(project.key for project in listed) == ("ACME", "BETA")
    assert created.project.key == "BETA"
    assert local.queries.status_commands == []
    assert local.projects.commands == [
        CreateProjectInput(
            instance_id=InstanceId("ins_local"),
            subject_id=SubjectId("sub_local"),
            project_key="BETA",
            project_name="Beta delivery",
            idempotency_key="project-1",
        )
    ]


@pytest.mark.parametrize(
    "error",
    [ProfileNotFoundError(), ProfileInvalidError(), ProfileUnsupportedError()],
)
def test_profile_resolution_failures_stop_before_runtime_open(
    error: ApplicationError,
) -> None:
    """Trusted profile failures propagate without touching local state."""
    context = _Context()
    profiles = _Profiles(error)
    runtimes = _Runtimes({})
    session = LocalSession(context=context, profiles=profiles, runtimes=runtimes)

    with pytest.raises(type(error)) as captured:
        session.list_projects(ProjectListRequest(profile="missing"))

    assert captured.value is error
    assert runtimes.calls == []


def test_runtime_open_failure_stops_before_identity_selection() -> None:
    """A configured-but-unopenable profile cannot invoke application services."""
    error = ProfileUnsupportedError()
    context = _Context()
    profiles = _Profiles("team")
    runtimes = _Runtimes({"team": error})
    session = LocalSession(context=context, profiles=profiles, runtimes=runtimes)

    with pytest.raises(ProfileUnsupportedError) as captured:
        session.list_projects(ProjectListRequest(profile="team"))

    assert captured.value is error
    assert runtimes.calls == ["team"]


def test_discovered_context_reports_canonical_authoritative_selection() -> None:
    """Context output combines verified authority with canonical discovered paths."""
    context = _Context(_selection())
    local = _runtime()
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )

    result = session.context(ContextRequest())

    assert result.mode == "embedded"
    assert result.profile == "local"
    assert result.schema_version == 3
    assert result.instance.id == InstanceId("ins_local")
    assert result.project.key == "ACME"
    assert result.subject.id == SubjectId("sub_local")
    assert result.workspace_root == Path("/repo/packages/api")
    assert result.context_source == Path("/repo/.workaholic.env")


def test_context_instance_mismatch_fails_before_repository_queries() -> None:
    """Repository-controlled context cannot select another runtime Instance."""
    context = _Context(_selection(instance_id="ins_other"))
    local = _runtime()
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )

    with pytest.raises(ApplicationError) as captured:
        session.status(StatusRequest())

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
    assert local.queries.get_project_commands == []
    assert local.queries.status_commands == []


def test_explicit_same_profile_override_omits_nonmatching_context_paths() -> None:
    """Explicit Project override remains authorized but does not relabel paths."""
    context = _Context(_selection())
    local = _runtime()
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )

    result = session.context(ContextRequest(project="BETA"))

    assert result.project.key == "BETA"
    assert result.workspace_root is None
    assert result.context_source is None
    assert local.queries.get_project_commands[0].project_key == "BETA"
    assert local.queries.status_commands[0].project_id == ProjectId("prj_beta")


def test_different_profile_context_requires_explicit_project() -> None:
    """A context from another profile is inapplicable to explicit profile scope."""
    context = _Context(_selection())
    team = _runtime(profile="team")
    session = LocalSession(
        context=context,
        profiles=_Profiles("team"),
        runtimes=_Runtimes({"team": team.runtime}),
    )

    with pytest.raises(ApplicationError) as captured:
        session.status(StatusRequest(profile="team"))

    assert captured.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND
    assert team.queries.get_project_commands == []
    assert team.queries.status_commands == []


def test_all_projects_is_explicit_context_free_task_selection() -> None:
    """The all-Projects flag uses the profile-bound Instance page contract."""
    context = _Context()
    local = _runtime()
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )

    result = session.list_tasks(
        TaskListRequest(all_projects=True, cursor="cursor-1", limit=25)
    )

    assert result == TaskPage(tasks=(), next_cursor=None)
    assert local.queries.list_instance_task_commands == [
        ListInstanceTasks(
            profile="local",
            instance_id=InstanceId("ins_local"),
            subject_id=SubjectId("sub_local"),
            cursor="cursor-1",
            limit=25,
        )
    ]
    assert local.queries.get_project_commands == []
    assert local.queries.status_commands == []


def test_task_operation_without_selection_never_reaches_task_repository() -> None:
    """A missing explicit and discovered Project stops before mutation."""
    context = _Context()
    local = _runtime()
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )

    with pytest.raises(ApplicationError) as captured:
        session.create_task(TaskCreateRequest(title="Unbound task"))

    assert captured.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND
    assert local.queries.status_commands == []
    assert local.tasks.commands == []


def test_up_initializes_selected_profile_and_writes_exact_current_binding() -> None:
    """Bootstrap uses the selected profile and publishes only committed identity."""
    context = _Context()
    team = _runtime(profile="team")
    bootstrapped_project = _project(name="Agent platform")
    team.bootstrap.result = BootstrapResult(
        instance=_instance(),
        project=bootstrapped_project,
        subject=_subject(),
        grant=_grant(bootstrapped_project),
        workspace=_binding(profile="team", project=bootstrapped_project),
    )
    session = LocalSession(
        context=context,
        profiles=_Profiles("team"),
        runtimes=_Runtimes({"team": team.runtime}),
    )

    result = session.up(
        UpRequest(
            project_key="ACME",
            project_name="Agent platform",
            profile="team",
            idempotency_key="up-team-1",
        )
    )

    assert team.bootstrap.commands == [
        BootstrapLocalProjectInput(
            project_key="ACME",
            project_name="Agent platform",
            idempotency_key="up-team-1",
        )
    ]
    assert result.workspace.profile == "team"
    assert context.writes == [result.workspace]


@pytest.mark.parametrize(
    ("profiles_result", "runtime_result"),
    [
        ("bad profile", _runtime().runtime),
        ("local", object()),
        ("local", _runtime().runtime),
    ],
)
def test_invalid_selection_adapter_results_are_redacted(
    profiles_result: object,
    runtime_result: object,
) -> None:
    """Malformed selection-adapter results become bounded internal failures."""
    context = _Context()
    profiles = _Profiles(profiles_result)
    local = _runtime()
    if (
        profiles_result == "local"
        and isinstance(runtime_result, LocalRuntime)
        and runtime_result.profile == "local"
    ):
        local.identity.result = object()
        selected_runtime: object = local.runtime
    else:
        selected_runtime = runtime_result
    session = LocalSession(
        context=context,
        profiles=profiles,
        runtimes=_Runtimes(
            {"local": selected_runtime, "bad profile": selected_runtime}
        ),
    )

    with pytest.raises(ApplicationError) as captured:
        session.list_projects(ProjectListRequest())

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_invalid_discovery_and_authority_results_fail_closed() -> None:
    """Invalid context and query results never reach a requested operation."""
    local = _runtime()
    invalid_context_session = LocalSession(
        context=_Context(object()),
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )
    with pytest.raises(ApplicationError) as context_error:
        invalid_context_session.status(StatusRequest())
    assert context_error.value.code is ApplicationErrorCode.CONTEXT_INVALID

    local.queries.status_results[ProjectId("prj_acme")] = _status(profile="team")
    invalid_status_session = LocalSession(
        context=_Context(_selection()),
        profiles=_Profiles(),
        runtimes=_Runtimes({"local": local.runtime}),
    )
    with pytest.raises(ApplicationError) as status_error:
        invalid_status_session.status(StatusRequest())
    assert status_error.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_session_owned_selection_models_validate_runtime_values() -> None:
    """Typed Session models still reject invalid direct runtime construction."""
    with pytest.raises(TypeError, match="typed Instance"):
        LocalIdentity(
            instance_id=cast("InstanceId", "ins_local"),
            subject_id=SubjectId("sub_local"),
        )
    with pytest.raises(TypeError, match="WorkspaceBinding"):
        WorkspaceContextSelection(
            binding=cast("WorkspaceBinding", object()),
            context_source=Path("/repo/.workaholic.env"),
            workspace_root=Path("/repo"),
        )
    with pytest.raises(ValueError, match="canonical and absolute"):
        WorkspaceContextSelection(
            binding=_binding(),
            context_source=Path("relative/.workaholic.env"),
            workspace_root=Path("/repo"),
        )
    with pytest.raises(ValueError, match="remain under"):
        WorkspaceContextSelection(
            binding=_binding(),
            context_source=Path("/repo/.workaholic.env"),
            workspace_root=Path("/other"),
        )


def test_local_runtime_validates_profile_and_capabilities() -> None:
    """Runtime composition rejects unsafe profiles and missing operations."""
    valid = _runtime()
    with pytest.raises(TypeError, match="profile is invalid"):
        LocalRuntime(
            profile="bad profile",
            identity=valid.identity,
            bootstrap=valid.bootstrap,
            projects=valid.projects,
            queries=valid.queries,
            tasks=valid.tasks,
        )
    with pytest.raises(TypeError, match=r"select\(\)"):
        LocalRuntime(
            profile="local",
            identity=cast("_Identity", object()),
            bootstrap=valid.bootstrap,
            projects=valid.projects,
            queries=valid.queries,
            tasks=valid.tasks,
        )
