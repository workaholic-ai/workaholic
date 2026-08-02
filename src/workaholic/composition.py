"""Explicit embedded local composition root for the Workaholic CLI."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapApplication,
    BootstrapRepository,
    Clock,
    IdentifierFactory,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProjectApplication,
    ProjectRepository,
    QueryApplication,
    QueryRepository,
    ResultIdentifierFactory,
    TaskApplication,
    TaskDependencyApplication,
    TaskLifecycleApplication,
    TaskRepository,
    TaskResultApplication,
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
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
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

_PROGRAM_NAME = "workaholic"
_UUID7_VERSION = 7
_IDENTIFIER_PREFIXES: Final = frozenset(
    ("ins_", "prj_", "sub_", "tsk_", "res_", "evt_", "req_")
)

type ConfigPathResolver = Callable[[Mapping[str, str]], LocalConfigPaths]


class _ComposedRepository(
    BootstrapRepository,
    ProjectRepository,
    TaskRepository,
    QueryRepository,
    Protocol,
):
    """Expose only operations consumed by the current local composition."""


class _ComposedIdentifierFactory(
    IdentifierFactory,
    ResultIdentifierFactory,
    Protocol,
):
    """Generate every identity owned by the embedded application services."""


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
            return LocalRuntime(
                profile=selected.name,
                identity=_EmbeddedRuntimeIdentity(identity),
                bootstrap=BootstrapApplication(repository, clock, identifiers),
                projects=ProjectApplication(repository, clock, identifiers),
                queries=QueryApplication(repository),
                tasks=TaskApplication(repository, clock, identifiers),
                lifecycle=TaskLifecycleApplication(repository, clock, identifiers),
                dependencies=TaskDependencyApplication(
                    repository,
                    clock,
                    identifiers,
                ),
                results=TaskResultApplication(repository, clock, identifiers),
            )
        except ApplicationError:
            raise
        except Exception as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "The selected embedded runtime could not be composed.",
            ) from error


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
    registry = _load_registry(
        environment=environment_value,
        config_path_resolver=config_path_resolver,
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


def _load_registry(
    *,
    environment: Mapping[str, str],
    config_path_resolver: ConfigPathResolver,
) -> ProfileRegistry:
    """Resolve and load one trusted registry with redacted unexpected failures.

    Args:
        environment: Trusted process environment mapping.
        config_path_resolver: Validated injectable configuration resolver.

    Returns:
        Immutable trusted embedded profile registry.

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

    try:
        registry_value: object = load_profile_registry(paths_value, environment)
    except ApplicationError:
        raise
    except Exception as error:
        raise ProfileInvalidError from error
    if not isinstance(registry_value, ProfileRegistry):
        raise ProfileInvalidError
    return registry_value


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
