"""Unit tests for authoritative Project-to-Workspace Session binding."""

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
    PermissionDeniedError,
    ProfileNotFoundError,
    ProjectCreationResult,
    ProjectNotFoundError,
    StatusResult,
    TaskPage,
    WorkspaceBindingConflictError,
)
from workaholic.context import ContextStorageError
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
    LocalIdentity,
    LocalRuntime,
    LocalSession,
    ProjectBindRequest,
    WorkspaceContextSelection,
)

_NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


def _project(
    *,
    project_id: str = "prj_acme",
    key: str = "ACME",
    instance_id: str = "ins_local",
) -> Project:
    """Build one deterministic Project.

    Args:
        project_id: Opaque Project identity.
        key: Immutable Project key.
        instance_id: Owning Instance identity.

    Returns:
        Valid Project fixture.

    """
    return Project(
        id=ProjectId(project_id),
        instance_id=InstanceId(instance_id),
        key=key,
        name=f"{key} Project",
        created_at=_NOW,
    )


def _binding() -> WorkspaceBinding:
    """Build the verified current ACME Workspace binding."""
    return WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        project_key="ACME",
        workspace_root=".",
    )


def _subject() -> Subject:
    """Build the trusted local bootstrap Human."""
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _status(*, project: Project | None = None) -> StatusResult:
    """Build authoritative status matching one selected Project.

    Args:
        project: Selected Project, defaulting to ACME.

    Returns:
        Authorized deterministic status.

    """
    selected_project = _project() if project is None else project
    subject = _subject()
    return StatusResult(
        instance=Instance(id=InstanceId("ins_local"), created_at=_NOW),
        project=selected_project,
        subject=subject,
        grant=ProjectGrant(
            subject_id=subject.id,
            project_id=selected_project.id,
            role=ProjectRole.OWNER,
        ),
    )


class _Context:
    """Recording Workspace context gateway."""

    def __init__(self, log: list[str]) -> None:
        """Initialize deterministic context behavior."""
        self.log = log
        self.binding: object = _binding()
        self.bind_result: object = Path("/target/.workaholic.env")
        self.bind_errors: list[ApplicationError] = []
        self.bind_calls: list[tuple[Path | None, WorkspaceBinding, bool]] = []

    def read_current(self) -> WorkspaceBinding:
        """Return the configured current binding."""
        self.log.append("context.read")
        return cast("WorkspaceBinding", self.binding)

    def discover(self) -> WorkspaceContextSelection:
        """Return the configured nearest Workspace context."""
        self.log.append("context.read")
        return WorkspaceContextSelection(
            binding=cast("WorkspaceBinding", self.binding),
            context_source=Path("/current/.workaholic.env"),
            workspace_root=Path("/current"),
        )

    def write_current(self, _binding: WorkspaceBinding) -> Path:
        """Provide the unused bootstrap context operation."""
        return Path("/current/.workaholic.env")

    def bind(
        self,
        directory: Path | None,
        binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Record or fail one Project binding."""
        self.log.append("context.bind")
        self.bind_calls.append((directory, binding, replace))
        if self.bind_errors:
            raise self.bind_errors.pop(0)
        return cast("Path", self.bind_result)


class _Actors:
    """Recording local bootstrap-Human selector."""

    def __init__(self, log: list[str]) -> None:
        """Initialize the selected actor."""
        self.log = log

    def select(self) -> LocalIdentity:
        """Return the trusted local Instance and Subject."""
        self.log.append("actors.select")
        return LocalIdentity(
            instance_id=InstanceId("ins_local"),
            subject_id=SubjectId("sub_local"),
        )


class _Queries:
    """Recording query service with configurable Project lookup."""

    def __init__(self, log: list[str]) -> None:
        """Initialize valid status and BETA lookup output."""
        self.log = log
        self.status_result: object = _status()
        self.project_result: object = _project(
            project_id="prj_beta",
            key="BETA",
        )
        self.project_error: ApplicationError | None = None
        self.project_commands: list[GetProjectByKey] = []

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Return authoritative status for the requested Project."""
        self.log.append("queries.status")
        if command.project_id == ProjectId("prj_beta"):
            return _status(project=_project(project_id="prj_beta", key="BETA"))
        return cast("StatusResult", self.status_result)

    def list_projects(self, _command: ListProjects) -> tuple[Project, ...]:
        """Provide the unused Project-list capability."""
        return ()

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Record and return or fail the selected Project lookup."""
        self.log.append("queries.get_project_by_key")
        self.project_commands.append(command)
        if self.project_error is not None:
            raise self.project_error
        return cast("Project", self.project_result)

    def list_tasks(self, _command: ListTasks) -> TaskPage:
        """Provide the unused Task-list capability."""
        return TaskPage(tasks=(), next_cursor=None)

    def list_tasks_for_instance(self, _command: ListInstanceTasks) -> TaskPage:
        """Provide the unused all-Projects Task-list capability."""
        return TaskPage(tasks=(), next_cursor=None)

    def get_task(self, _command: GetTask) -> Task:
        """Fail if the unused Task lookup is invoked."""
        pytest.fail("Project binding must not query a Task")


class _Bootstrap:
    """Unused bootstrap service capability."""

    def up(self, _command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Fail if Project binding attempts bootstrap."""
        pytest.fail("Project binding must not invoke bootstrap")


class _Tasks:
    """Unused Task service capability."""

    def create(self, _command: CreateTaskInput) -> Task:
        """Fail if Project binding attempts Task creation."""
        pytest.fail("Project binding must not create a Task")


class _Projects:
    """Unused Project-creation application capability."""

    def create(self, _command: CreateProjectInput) -> ProjectCreationResult:
        """Fail if Project binding attempts Project creation."""
        pytest.fail("Project binding must not create a Project")


class _Profiles:
    """Resolve one fixed trusted local profile."""

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Select local or fail for an unknown explicit profile."""
        selected = explicit_profile or discovered_profile or "local"
        if selected != "local":
            raise ProfileNotFoundError
        return selected


@dataclass(frozen=True, slots=True)
class _Runtimes:
    """Open one fixed trusted local runtime."""

    runtime: LocalRuntime

    def open(self, profile: str) -> LocalRuntime:
        """Return the exact matching runtime."""
        if profile != self.runtime.profile:
            raise ProfileNotFoundError
        return self.runtime


def _session() -> tuple[LocalSession, _Context, _Queries, list[str]]:
    """Compose one Project-binding Session from strict fakes.

    Returns:
        Session, context fake, query fake, and operation log.

    """
    log: list[str] = []
    context = _Context(log)
    queries = _Queries(log)
    runtime = LocalRuntime(
        profile="local",
        identity=_Actors(log),
        bootstrap=_Bootstrap(),
        projects=_Projects(),
        queries=queries,
        tasks=_Tasks(),
    )
    session = LocalSession(
        context=context,
        profiles=_Profiles(),
        runtimes=_Runtimes(runtime),
    )
    return session, context, queries, log


def test_bind_project_resolves_authority_before_durable_context_write() -> None:
    """The Session builds exact target context only from authorized state."""
    session, context, queries, log = _session()
    target = Path("/target")

    result = session.bind_project(
        ProjectBindRequest(
            project="BETA",
            path=target,
            profile="local",
            replace=True,
        )
    )

    assert result.mode == "embedded"
    assert result.schema_version == 2
    assert result.profile == "local"
    assert result.instance.id == InstanceId("ins_local")
    assert result.project == _project(project_id="prj_beta", key="BETA")
    assert result.subject == _subject()
    assert result.grant == ProjectGrant(
        subject_id=SubjectId("sub_local"),
        project_id=ProjectId("prj_beta"),
        role=ProjectRole.OWNER,
    )
    assert result.workspace_root == target
    assert result.context_source == target / ".workaholic.env"
    assert queries.project_commands == [
        GetProjectByKey(
            instance_id=InstanceId("ins_local"),
            subject_id=SubjectId("sub_local"),
            project_key="BETA",
        )
    ]
    assert context.bind_calls == [
        (
            target,
            WorkspaceBinding(
                context_version=1,
                profile="local",
                instance_id=InstanceId("ins_local"),
                project_id=ProjectId("prj_beta"),
                project_key="BETA",
                workspace_root=".",
            ),
            True,
        )
    ]
    assert log == [
        "context.read",
        "actors.select",
        "queries.get_project_by_key",
        "queries.status",
        "context.bind",
    ]


def test_bind_project_defaults_to_current_profile_and_directory() -> None:
    """Omitted selectors preserve the verified profile and current target."""
    session, context, _queries, _log = _session()
    context.bind_result = Path("/current/.workaholic.env")

    result = session.bind_project(ProjectBindRequest(project="BETA"))

    assert result.profile == "local"
    assert result.workspace_root == Path("/current")
    assert context.bind_calls[0][0] is None
    assert context.bind_calls[0][2] is False


def test_bind_project_rejects_profile_mismatch_before_lookup_or_write() -> None:
    """A fixed embedded runtime cannot relabel its state as another profile."""
    session, context, queries, log = _session()

    with pytest.raises(ApplicationError) as captured:
        session.bind_project(ProjectBindRequest(project="BETA", profile="team"))

    assert captured.value.code is ApplicationErrorCode.PROFILE_NOT_FOUND
    assert queries.project_commands == []
    assert context.bind_calls == []
    assert log == ["context.read"]


@pytest.mark.parametrize(
    "error",
    [ProjectNotFoundError(), PermissionDeniedError()],
)
def test_project_lookup_failure_never_writes_context(
    error: ApplicationError,
) -> None:
    """Missing or unauthorized Projects leave the target untouched."""
    session, context, queries, _log = _session()
    queries.project_error = error

    with pytest.raises(type(error)) as captured:
        session.bind_project(ProjectBindRequest(project="BETA"))

    assert captured.value is error
    assert context.bind_calls == []


def test_cross_instance_project_result_is_a_safe_internal_error() -> None:
    """A repository cannot redirect binding into another Instance."""
    session, context, queries, _log = _session()
    queries.project_result = _project(
        project_id="prj_beta",
        key="BETA",
        instance_id="ins_other",
    )

    with pytest.raises(ApplicationError) as captured:
        session.bind_project(ProjectBindRequest(project="BETA"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert context.bind_calls == []


def test_valid_conflict_propagates_without_becoming_context_corruption() -> None:
    """A valid different binding retains its stable conflict category."""
    session, context, _queries, _log = _session()
    conflict = WorkspaceBindingConflictError()
    context.bind_errors.append(conflict)

    with pytest.raises(WorkspaceBindingConflictError) as captured:
        session.bind_project(ProjectBindRequest(project="BETA"))

    assert captured.value is conflict


def test_filesystem_failure_after_lookup_is_safely_retryable() -> None:
    """A durable Project is looked up again before a later binding retry."""
    session, context, queries, _log = _session()
    storage_error = ContextStorageError()
    context.bind_errors.append(storage_error)

    with pytest.raises(ContextStorageError) as captured:
        session.bind_project(ProjectBindRequest(project="BETA"))

    assert captured.value is storage_error
    result = session.bind_project(ProjectBindRequest(project="BETA"))
    assert result.project.key == "BETA"
    assert len(queries.project_commands) == 2
    assert len(context.bind_calls) == 2


@pytest.mark.parametrize(
    "invalid_result",
    [
        object(),
        Path("relative/.workaholic.env"),
        Path("/target/not-context"),
    ],
)
def test_invalid_context_gateway_result_is_redacted(
    invalid_result: object,
) -> None:
    """A context gateway contract breach never leaks a false binding result."""
    session, context, _queries, _log = _session()
    context.bind_result = invalid_result

    with pytest.raises(ApplicationError) as captured:
        session.bind_project(ProjectBindRequest(project="BETA"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_bind_project_runtime_validates_request_type_without_dependencies() -> None:
    """Unvalidated presentation input is rejected before context discovery."""
    session, context, queries, log = _session()

    with pytest.raises(ApplicationError) as captured:
        session.bind_project(cast("ProjectBindRequest", object()))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert context.bind_calls == []
    assert queries.project_commands == []
    assert log == []
