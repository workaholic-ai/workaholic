"""Explicit embedded local composition root for the Workaholic CLI."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Final, Protocol, cast

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AuditApplication,
    AuthenticationApplication,
    BootstrapApplication,
    BootstrapRepository,
    ClaimExecutionRepository,
    Clock,
    ExecutionIdentifierFactory,
    GrantApplication,
    IdentifierFactory,
    IdentityIdentifierFactory,
    IdentityRepository,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProjectApplication,
    ProjectRepository,
    QueryApplication,
    QueryRepository,
    SubjectApplication,
    TaskApplication,
    TaskClaimApplication,
    TaskDependencyApplication,
    TaskExecutionApplication,
    TaskLifecycleApplication,
    TaskRepository,
    TaskResultApplication,
    TokenApplication,
)
from workaholic.auth import (
    CredentialStore,
    FileCredentialStore,
    HumanCredential,
    KeyringCredentialStore,
    resolve_credential_backend,
    select_credential_store,
)
from workaholic.cli.main import create_app
from workaholic.context import (
    ContextInvalidError,
    ContextNotFoundError,
    LocalConfigPaths,
    ProfileRegistry,
    bind_workspace_context,
    discover_workspace_context,
    exclude_context_from_git,
    load_profile_registry,
    resolve_local_config_paths,
    write_current_workspace_context,
)
from workaholic.domain import (
    AttemptId,
    AuditEventId,
    AuthenticatedActor,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
    TokenId,
    WorkspaceBinding,
    validate_profile_name,
)
from workaholic.persistence.sqlite import (
    SQLiteLocalActorSelector,
    SQLiteRepository,
)
from workaholic.session import (
    LocalIdentity,
    LocalRuntime,
    LocalSession,
    WorkaholicSession,
    WorkspaceContextSelection,
)
from workaholic.session._phase_five import PhaseFiveRuntime

if TYPE_CHECKING:
    from pydantic import BaseModel

    from workaholic.application import (
        AddTaskDependencyMutation,
        ApproveResultMutation,
        BootstrapMutation,
        ClaimNextTaskMutation,
        ClaimTaskMutation,
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
        ReleaseClaimMutation,
        RemoveTaskDependencyMutation,
        RenewClaimMutation,
        ReportTaskProgressMutation,
        SubmitAgentResultMutation,
        SubmitHumanResultMutation,
        TaskBlockMutation,
        TaskCancelMutation,
        TaskCreationMutation,
        TaskUnblockMutation,
        TaskUpdateMutation,
    )

_PROGRAM_NAME = "workaholic"
_UUID7_VERSION = 7
_IDENTIFIER_PREFIXES: Final = frozenset(
    (
        "ins_",
        "prj_",
        "sub_",
        "tsk_",
        "res_",
        "evt_",
        "req_",
        "atm_",
        "tok_",
        "aev_",
    )
)

type ConfigPathResolver = Callable[[Mapping[str, str]], LocalConfigPaths]


class _ComposedRepository(
    BootstrapRepository,
    ProjectRepository,
    TaskRepository,
    QueryRepository,
    ClaimExecutionRepository,
    IdentityRepository,
    Protocol,
):
    """Expose only operations consumed by the current local composition."""


class _ComposedIdentifierFactory(
    IdentifierFactory, ExecutionIdentifierFactory, Protocol
):
    """Generate every identity owned by the embedded application services."""


class _PhaseFiveComposedIdentifierFactory(
    _ComposedIdentifierFactory,
    IdentityIdentifierFactory,
    Protocol,
):
    """Generate cumulative identities for authenticated local composition."""


class _ActorBoundRepository:
    """Inject one authenticated actor into every cumulative Task operation."""

    def __init__(
        self,
        repository: _ComposedRepository,
        actor: AuthenticatedActor,
    ) -> None:
        """Bind one raw repository to one command-scoped actor.

        Args:
            repository: Complete raw persistence adapter.
            actor: Authenticated actor to inject into every operation.

        """
        actor_value: object = actor
        if not isinstance(actor_value, AuthenticatedActor):
            message = "Actor-bound repository requires an AuthenticatedActor."
            raise TypeError(message)
        self._repository = repository
        self._actor = actor

    def bootstrap_local_project(self, value: BaseModel) -> object:
        """Delegate bootstrap only after the Session authenticated this runtime."""
        return self._repository.bootstrap_local_project(
            cast("BootstrapMutation", value)
        )

    def create_task(self, value: BaseModel) -> object:
        """Delegate authenticated Task creation."""
        return self._repository.create_task(
            cast("TaskCreationMutation", self._bind(value))
        )

    def update_task_if_version(self, value: BaseModel) -> object:
        """Delegate an authenticated Task definition update."""
        return self._repository.update_task_if_version(
            cast("TaskUpdateMutation", self._bind(value))
        )

    def block_task(self, value: BaseModel) -> object:
        """Delegate an authenticated Task block transition."""
        return self._repository.block_task(cast("TaskBlockMutation", self._bind(value)))

    def unblock_task(self, value: BaseModel) -> object:
        """Delegate an authenticated Task unblock transition."""
        return self._repository.unblock_task(
            cast("TaskUnblockMutation", self._bind(value))
        )

    def cancel_task(self, value: BaseModel) -> object:
        """Delegate an authenticated Task cancellation."""
        return self._repository.cancel_task(
            cast("TaskCancelMutation", self._bind(value))
        )

    def claim_task(self, value: BaseModel) -> object:
        """Delegate an authenticated targeted Claim."""
        return self._repository.claim_task(cast("ClaimTaskMutation", self._bind(value)))

    def claim_next_task(self, value: BaseModel) -> object:
        """Delegate an authenticated Agent pull."""
        return self._repository.claim_next_task(
            cast("ClaimNextTaskMutation", self._bind(value))
        )

    def renew_claim(self, value: BaseModel) -> object:
        """Delegate an authenticated Claim renewal."""
        return self._repository.renew_claim(
            cast("RenewClaimMutation", self._bind(value))
        )

    def release_claim(self, value: BaseModel) -> object:
        """Delegate an authenticated Claim release."""
        return self._repository.release_claim(
            cast("ReleaseClaimMutation", self._bind(value))
        )

    def report_task_progress(self, value: BaseModel) -> object:
        """Delegate an authenticated progress report."""
        return self._repository.report_task_progress(
            cast("ReportTaskProgressMutation", self._bind(value))
        )

    def add_task_dependency(self, value: BaseModel) -> object:
        """Delegate an authenticated dependency addition."""
        return self._repository.add_task_dependency(
            cast("AddTaskDependencyMutation", self._bind(value))
        )

    def remove_task_dependency(self, value: BaseModel) -> object:
        """Delegate an authenticated dependency removal."""
        return self._repository.remove_task_dependency(
            cast("RemoveTaskDependencyMutation", self._bind(value))
        )

    def submit_human_result(self, value: BaseModel) -> object:
        """Delegate an authenticated Human submission."""
        return self._repository.submit_human_result(
            cast("SubmitHumanResultMutation", self._bind(value))
        )

    def submit_agent_result(self, value: BaseModel) -> object:
        """Delegate an authenticated Agent submission."""
        return self._repository.submit_agent_result(
            cast("SubmitAgentResultMutation", self._bind(value))
        )

    def approve_result(self, value: BaseModel) -> object:
        """Delegate an authenticated Result approval."""
        return self._repository.approve_result(
            cast("ApproveResultMutation", self._bind(value))
        )

    def reject_result(self, value: BaseModel) -> object:
        """Delegate an authenticated Result rejection."""
        return self._repository.reject_result(
            cast("RejectResultMutation", self._bind(value))
        )

    def create_project(self, value: BaseModel) -> object:
        """Delegate authenticated Project creation."""
        return self._repository.create_project(
            cast("ProjectCreationMutation", self._bind(value))
        )

    def get_local_status(self, value: BaseModel) -> object:
        """Delegate an authenticated status query."""
        return self._repository.get_local_status(
            cast("GetLocalStatus", self._bind(value))
        )

    def list_projects(self, value: BaseModel) -> object:
        """Delegate an authenticated Project listing."""
        return self._repository.list_projects(cast("ListProjects", self._bind(value)))

    def get_project_by_key(self, value: BaseModel) -> object:
        """Delegate an authenticated Project lookup."""
        return self._repository.get_project_by_key(
            cast("GetProjectByKey", self._bind(value))
        )

    def list_tasks(self, value: BaseModel) -> object:
        """Delegate an authenticated Project Task page."""
        return self._repository.list_tasks(cast("ListTasks", self._bind(value)))

    def list_tasks_for_instance(self, value: BaseModel) -> object:
        """Delegate an authenticated Instance Task page."""
        return self._repository.list_tasks_for_instance(
            cast("ListInstanceTasks", self._bind(value))
        )

    def get_task(self, value: BaseModel) -> object:
        """Delegate an authenticated Task lookup."""
        return self._repository.get_task(cast("GetTask", self._bind(value)))

    def get_task_details(self, value: BaseModel) -> object:
        """Delegate an authenticated Task-details query."""
        return self._repository.get_task_details(
            cast("GetTaskDetails", self._bind(value))
        )

    def list_tasks_by_view(self, value: BaseModel) -> object:
        """Delegate an authenticated Task-view page."""
        return self._repository.list_tasks_by_view(
            cast("ListTasksByView", self._bind(value))
        )

    def read_task_events_after(self, value: BaseModel) -> object:
        """Delegate an authenticated TaskEvent page."""
        return self._repository.read_task_events_after(
            cast("ReadTaskEvents", self._bind(value))
        )

    def _bind(self, value: BaseModel) -> BaseModel:
        """Copy one validated command with authoritative actor identities."""
        fields = type(value).model_fields
        if "actor" not in fields:
            message = "Actor-bound repository command has no actor field."
            raise TypeError(message)
        updates: dict[str, object] = {"actor": self._actor}
        if "actor_subject_id" in fields:
            updates["actor_subject_id"] = self._actor.subject_id
        if "subject_id" in fields:
            updates["subject_id"] = self._actor.subject_id
        if "instance_id" in fields:
            updates["instance_id"] = self._actor.instance_id
        return value.model_copy(update=updates)


class _LazyCredentialStore:
    """Select one Human credential backend on its first profile operation."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str],
        paths: LocalConfigPaths,
        forbidden_roots: tuple[Path, ...],
    ) -> None:
        """Retain trusted inputs without touching a keyring or filesystem."""
        self._environment = environment
        self._paths = paths
        self._forbidden_roots = forbidden_roots
        self._selected: CredentialStore | None = None
        self._lock = Lock()

    def load(self, profile: str) -> HumanCredential | None:
        """Load one profile credential from the selected backend."""
        return self._store().load(profile)

    def replace(self, credential: HumanCredential) -> None:
        """Replace one profile credential in the selected backend."""
        self._store().replace(credential)

    def delete(self, profile: str) -> None:
        """Delete one profile credential from the selected backend."""
        self._store().delete(profile)

    def _store(self) -> CredentialStore:
        """Select and cache one backend without operational downgrade."""
        with self._lock:
            if self._selected is None:
                backend = resolve_credential_backend(self._environment)
                self._selected = select_credential_store(
                    backend,
                    keyring_store=KeyringCredentialStore.system(),
                    file_store=FileCredentialStore(
                        self._paths.credentials_directory,
                        self._paths.credentials_file,
                        forbidden_roots=self._forbidden_roots,
                    ),
                )
            return self._selected


@dataclass(frozen=True, slots=True)
class _WorkspaceContextAdapter:
    """Adapt one canonical current directory to the Session context port."""

    directory: Path

    def discover(self) -> WorkspaceContextSelection | None:
        """Discover and adapt the nearest physical Workspace context."""
        try:
            discovered = discover_workspace_context(self.directory)
        except ContextNotFoundError:
            return None
        return WorkspaceContextSelection(
            binding=discovered.binding,
            context_source=discovered.context_file,
            workspace_root=discovered.workspace_root,
        )

    def write_current(self, binding: WorkspaceBinding) -> Path:
        """Durably write context and exclude it from conventional local Git.

        Args:
            binding: Committed bootstrap Workspace binding.

        Returns:
            Exact context-file path.

        """
        context_path = write_current_workspace_context(self.directory, binding)
        exclude_context_from_git(self.directory)
        return context_path

    def bind(
        self,
        directory: Path | None,
        binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Bind an explicit target or the composed current directory.

        Args:
            directory: Explicit target, or ``None`` for the current directory.
            binding: Authoritative Project binding.
            replace: Whether valid conflicting context may be replaced.

        Returns:
            Canonical durable context-file path.

        """
        target = self.directory if directory is None else directory
        return bind_workspace_context(target, binding, replace=replace)


@dataclass(frozen=True, slots=True)
class _RegistryProfileResolver:
    """Apply trusted profile precedence against one immutable registry."""

    registry: ProfileRegistry
    environment_profile: str | None

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Resolve one configured profile through the documented precedence.

        Args:
            explicit_profile: Explicit caller selection when present.
            discovered_profile: Nearest context profile when present.

        Returns:
            Exact configured embedded profile name.

        Raises:
            ProfileInvalidError: If a selector violates profile-name syntax.
            ProfileNotFoundError: If selection names no configured profile.

        """
        candidate: object = (
            explicit_profile
            if explicit_profile is not None
            else self.environment_profile
        )
        if candidate is None:
            candidate = (
                discovered_profile
                if discovered_profile is not None
                else self.registry.default_profile
            )
        try:
            selected = validate_profile_name(candidate)
        except ValueError as error:
            raise ProfileInvalidError from error
        if selected not in self.registry.profiles:
            raise ProfileNotFoundError
        return selected


class EmbeddedIdentitySelector(Protocol):
    """Select trusted identities from one embedded profile database."""

    def select_local(self) -> tuple[InstanceId, SubjectId]:
        """Return the initialized Instance and bootstrap Human."""
        ...

    def select_instance(self) -> InstanceId:
        """Return only the singleton Instance identity before authentication."""
        ...

    def select_bootstrap_subject(
        self,
        *,
        instance_id: InstanceId,
        handle: str,
    ) -> SubjectId:
        """Return the confirmed bootstrap Human for recovery only."""
        ...

    def has_tokens(self) -> bool:
        """Return whether the selected Instance has any Token rows."""
        ...


@dataclass(frozen=True, slots=True)
class _EmbeddedRuntimeIdentity:
    """Adapt SQLite identity selection to the Session-owned result model."""

    selector: EmbeddedIdentitySelector

    def select(self) -> LocalIdentity:
        """Return the exact initialized Instance and bootstrap Human."""
        instance_id, subject_id = self.selector.select_local()
        return LocalIdentity(
            instance_id=instance_id,
            subject_id=subject_id,
        )


class _UtcSystemClock:
    """Supply authoritative timezone-aware UTC operation timestamps."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        return datetime.now(UTC)


class _Uuid7IdentifierFactory:
    """Generate globally unique prefixed Python 3.14 UUID7 identifiers."""

    def new_instance_id(self) -> InstanceId:
        """Create a candidate Instance identifier."""
        return InstanceId(_new_uuid7_text("ins_"))

    def new_project_id(self) -> ProjectId:
        """Create a candidate Project identifier."""
        return ProjectId(_new_uuid7_text("prj_"))

    def new_subject_id(self) -> SubjectId:
        """Create a candidate Subject identifier."""
        return SubjectId(_new_uuid7_text("sub_"))

    def new_task_id(self) -> TaskId:
        """Create a candidate Task identifier."""
        return TaskId(_new_uuid7_text("tsk_"))

    def new_result_id(self) -> ResultId:
        """Create a candidate Result identifier."""
        return ResultId(_new_uuid7_text("res_"))

    def new_event_id(self) -> TaskEventId:
        """Create a candidate TaskEvent identifier."""
        return TaskEventId(_new_uuid7_text("evt_"))

    def new_request_id(self) -> RequestId:
        """Create a candidate request identifier."""
        return RequestId(_new_uuid7_text("req_"))

    def new_attempt_id(self) -> AttemptId:
        """Create a candidate Agent Attempt identifier."""
        return AttemptId(_new_uuid7_text("atm_"))

    def new_token_id(self) -> TokenId:
        """Create a candidate bearer Token identifier."""
        return TokenId(_new_uuid7_text("tok_"))

    def new_audit_event_id(self) -> AuditEventId:
        """Create a candidate administrative AuditEvent identifier."""
        return AuditEventId(_new_uuid7_text("aev_"))


@dataclass(frozen=True, slots=True)
class LocalCompositionFactories:
    """Injectable constructors for one profile-selected embedded runtime."""

    repository: Callable[[Path], _ComposedRepository]
    identity: Callable[[Path], EmbeddedIdentitySelector]
    clock: Callable[[], Clock]
    identifiers: Callable[[], _ComposedIdentifierFactory]

    def __post_init__(self) -> None:
        """Validate the explicit factory boundary at composition time."""
        for name in ("repository", "identity", "clock", "identifiers"):
            if not callable(getattr(self, name)):
                message = f"Local composition {name} factory must be callable."
                raise TypeError(message)


@dataclass(frozen=True, slots=True)
class _ProfileRuntimeOpener:
    """Lazily compose one isolated embedded runtime from a trusted profile."""

    registry: ProfileRegistry
    factories: LocalCompositionFactories
    credentials: CredentialStore
    environment: Mapping[str, str]
    forbidden_roots: tuple[Path, ...]

    def open(self, profile: str) -> LocalRuntime:
        """Compose application services for one exact configured profile.

        Args:
            profile: Trusted name returned by the registry profile resolver.

        Returns:
            Isolated embedded application runtime.

        Raises:
            ProfileNotFoundError: If the profile is not configured.
            ApplicationError: If a factory violates the composition contract.

        """
        selected = self.registry.profiles.get(profile)
        if selected is None:
            raise ProfileNotFoundError
        try:
            repository = self.factories.repository(selected.database_path)
            identity = self.factories.identity(selected.database_path)
            clock = self.factories.clock()
            identifiers = self.factories.identifiers()
            if not callable(getattr(identifiers, "new_token_id", None)):
                return _compose_application_runtime(
                    profile=selected.name,
                    repository=repository,
                    identity=identity,
                    clock=clock,
                    identifiers=identifiers,
                )
            phase_five_identifiers = cast(
                "_PhaseFiveComposedIdentifierFactory",
                identifiers,
            )
            phase_five = PhaseFiveRuntime(
                profile=selected.name,
                instance=identity,
                authentication=AuthenticationApplication(
                    repository,
                    repository,
                    clock,
                ),
                subjects=SubjectApplication(repository, clock, phase_five_identifiers),
                tokens=TokenApplication(repository, clock, phase_five_identifiers),
                grants=GrantApplication(repository, clock, phase_five_identifiers),
                audit=AuditApplication(repository),
                credentials=self.credentials,
                environment=self.environment,
                clock=clock,
                identifiers=phase_five_identifiers,
                credential_lock_path=(
                    selected.data_directory / ".identity-credentials.lock"
                ),
                forbidden_roots=self.forbidden_roots,
            )

            def actor_runtime(actor: AuthenticatedActor) -> LocalRuntime:
                """Compose one command-scoped actor-bound service set."""
                bound = cast(
                    "_ComposedRepository",
                    _ActorBoundRepository(repository, actor),
                )
                return _compose_application_runtime(
                    profile=selected.name,
                    repository=bound,
                    identity=identity,
                    clock=clock,
                    identifiers=identifiers,
                    phase_five=phase_five,
                    actor=actor,
                    actor_runtime_factory=actor_runtime,
                )

            return _compose_application_runtime(
                profile=selected.name,
                repository=repository,
                identity=identity,
                clock=clock,
                identifiers=identifiers,
                phase_five=phase_five,
                actor_runtime_factory=actor_runtime,
            )
        except ApplicationError:
            raise
        except Exception as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "The selected embedded runtime could not be composed.",
            ) from error


def _compose_application_runtime(  # noqa: PLR0913 - explicit composition root.
    *,
    profile: str,
    repository: _ComposedRepository,
    identity: EmbeddedIdentitySelector,
    clock: Clock,
    identifiers: _ComposedIdentifierFactory,
    phase_five: PhaseFiveRuntime | None = None,
    actor: AuthenticatedActor | None = None,
    actor_runtime_factory: Callable[[AuthenticatedActor], LocalRuntime] | None = None,
) -> LocalRuntime:
    """Compose cumulative application services over one repository view."""
    return LocalRuntime(
        profile=profile,
        identity=_EmbeddedRuntimeIdentity(identity),
        bootstrap=BootstrapApplication(repository, clock, identifiers),
        projects=ProjectApplication(repository, clock, identifiers),
        queries=QueryApplication(repository),
        tasks=TaskApplication(repository, clock, identifiers),
        lifecycle=TaskLifecycleApplication(repository, clock, identifiers),
        dependencies=TaskDependencyApplication(repository, clock, identifiers),
        results=TaskResultApplication(repository, clock, identifiers),
        claims=TaskClaimApplication(repository, clock, identifiers),
        execution=TaskExecutionApplication(repository, clock, identifiers),
        phase_five=phase_five,
        actor=actor,
        actor_runtime_factory=actor_runtime_factory,
    )


def create_local_session(
    *,
    cwd: Path,
    environment: Mapping[str, str],
    config_path_resolver: ConfigPathResolver = resolve_local_config_paths,
    factories: LocalCompositionFactories | None = None,
) -> WorkaholicSession:
    """Compose one short-lived profile-aware embedded Session.

    Session construction reads bounded trusted profile configuration but
    performs no database or Workspace writes. A profile runtime is constructed
    lazily only when an operation selects it.

    Args:
        cwd: Existing absolute current Workspace directory.
        environment: Trusted process environment used for profile selection.
        config_path_resolver: Injectable trusted configuration-path resolver.
        factories: Injectable embedded runtime constructors, or production
            SQLite, UTC-clock, and UUID7 constructors when omitted.

    Returns:
        Fully composed profile-aware embedded Session.

    Raises:
        ContextInvalidError: If ``cwd`` or trusted environment is invalid.
        ProfileInvalidError: If profile configuration is invalid.

    """
    directory = _require_workspace_directory(cwd)
    environment_value: object = environment
    if not isinstance(environment_value, Mapping):
        raise ProfileInvalidError
    resolver_value: object = config_path_resolver
    if not callable(resolver_value):
        message = "Local configuration path resolver must be callable."
        raise TypeError(message)
    factories_value: object = factories
    configured_factories = (
        _production_factories() if factories_value is None else factories_value
    )
    if not isinstance(configured_factories, LocalCompositionFactories):
        message = "Local composition factories are invalid."
        raise TypeError(message)
    environment_profile = _resolve_environment_profile(environment_value)
    paths = _resolve_config_paths(
        environment=environment_value,
        config_path_resolver=config_path_resolver,
    )
    registry = _load_registry(paths=paths, environment=environment_value)
    forbidden_roots = _credential_forbidden_roots(directory)
    credentials = _LazyCredentialStore(
        environment=environment_value,
        paths=paths,
        forbidden_roots=forbidden_roots,
    )
    return LocalSession(
        context=_WorkspaceContextAdapter(directory),
        profiles=_RegistryProfileResolver(
            registry=registry,
            environment_profile=environment_profile,
        ),
        runtimes=_ProfileRuntimeOpener(
            registry=registry,
            factories=configured_factories,
            credentials=credentials,
            environment=environment_value,
            forbidden_roots=forbidden_roots,
        ),
    )


def _production_factories() -> LocalCompositionFactories:
    """Build the side-effect-free production constructor set.

    Returns:
        SQLite repository and identity constructors with UTC and UUID7
        application dependency constructors.

    """
    return LocalCompositionFactories(
        repository=SQLiteRepository,
        identity=SQLiteLocalActorSelector,
        clock=_UtcSystemClock,
        identifiers=_Uuid7IdentifierFactory,
    )


def _resolve_environment_profile(
    environment: Mapping[str, str],
) -> str | None:
    """Validate the optional trusted environment profile selector.

    Args:
        environment: Trusted process environment mapping.

    Returns:
        Validated profile name, or ``None`` when unset or empty.

    Raises:
        ProfileInvalidError: If the value violates profile-name syntax.

    """
    candidate: object = environment.get("WORKAHOLIC_PROFILE")
    if candidate is None or candidate == "":
        return None
    try:
        return validate_profile_name(candidate)
    except ValueError as error:
        raise ProfileInvalidError from error


def _resolve_config_paths(
    *,
    environment: Mapping[str, str],
    config_path_resolver: ConfigPathResolver,
) -> LocalConfigPaths:
    """Resolve trusted local configuration paths with redacted failures.

    Args:
        environment: Trusted process environment mapping.
        config_path_resolver: Validated injectable configuration resolver.

    Returns:
        Validated local configuration paths.

    Raises:
        ApplicationError: For typed configuration failures.
        ProfileInvalidError: If a dependency violates its result contract or
            raises an unexpected exception.

    """
    try:
        paths_value: object = config_path_resolver(environment)
    except ApplicationError:
        raise
    except Exception as error:
        raise ProfileInvalidError from error
    if not isinstance(paths_value, LocalConfigPaths):
        raise ProfileInvalidError
    return paths_value


def _load_registry(
    *,
    paths: LocalConfigPaths,
    environment: Mapping[str, str],
) -> ProfileRegistry:
    """Load one trusted embedded profile registry.

    Args:
        paths: Validated trusted configuration paths.
        environment: Trusted process environment mapping.

    Returns:
        Immutable trusted embedded profile registry.

    Raises:
        ApplicationError: For typed configuration failures.
        ProfileInvalidError: If the loader violates its result contract.

    """
    try:
        registry_value: object = load_profile_registry(paths, environment)
    except ApplicationError:
        raise
    except Exception as error:
        raise ProfileInvalidError from error
    if not isinstance(registry_value, ProfileRegistry):
        raise ProfileInvalidError
    return registry_value


def _credential_forbidden_roots(directory: Path) -> tuple[Path, ...]:
    """Return the Workspace and nearest Git root forbidden to credentials."""
    roots = [directory]
    for candidate in (directory, *directory.parents):
        marker = candidate / ".git"
        try:
            if marker.exists() or marker.is_symlink():
                if candidate != directory:
                    roots.append(candidate)
                break
        except OSError as error:
            raise ProfileInvalidError from error
    return tuple(roots)


def main() -> None:
    """Compose and run the Workaholic command-line application."""
    application = create_app(_create_process_session)
    application(prog_name=_PROGRAM_NAME)


def _create_process_session() -> WorkaholicSession:
    """Compose one Session from the current trusted process boundary."""
    return create_local_session(
        cwd=Path.cwd(),
        environment=os.environ,
    )


def _require_workspace_directory(value: object) -> Path:
    """Require one existing absolute Workspace directory.

    Args:
        value: Candidate process current directory.

    Returns:
        Canonical physical directory without upward discovery.

    Raises:
        ContextInvalidError: If the value is not an existing absolute Path.

    """
    if not isinstance(value, Path) or not value.is_absolute():
        message = "The process current directory must be an absolute Path."
        raise ContextInvalidError(message)
    try:
        directory = value.resolve(strict=True)
        is_directory = directory.is_dir()
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        message = "The process current directory must already exist."
        raise ContextInvalidError(message) from error
    except OSError as error:
        message = "The process current directory is unavailable."
        raise ContextInvalidError(message) from error
    if not is_directory:
        message = "The process current directory must already exist."
        raise ContextInvalidError(message)
    return directory


def _new_uuid7_text(prefix: str) -> str:
    """Generate one prefixed canonical UUID7 string.

    Args:
        prefix: Validated identifier-type prefix.

    Returns:
        Prefix followed by a canonical RFC 9562 UUID7 string.

    Raises:
        ValueError: If ``prefix`` is not one of the domain identifier prefixes.
        RuntimeError: If the standard-library generator violates its contract.

    """
    candidate_prefix: object = prefix
    if (
        not isinstance(candidate_prefix, str)
        or candidate_prefix not in _IDENTIFIER_PREFIXES
    ):
        message = "UUID7 identifier prefix is unsupported."
        raise ValueError(message)
    generated = uuid.uuid7()
    if generated.version != _UUID7_VERSION or generated.variant != uuid.RFC_4122:
        message = "Python uuid7() returned an invalid UUID."
        raise RuntimeError(message)
    return f"{prefix}{generated}"
