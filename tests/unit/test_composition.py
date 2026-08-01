"""Unit tests for the explicit embedded local composition root."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from workaholic import composition
from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    NotInitializedError,
    PermissionDeniedError,
)
from workaholic.cli.main import create_app
from workaholic.context import (
    ContextInvalidError,
    LocalConfigPaths,
    ProfileInvalidError,
    ProfileNotFoundError,
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
)
from workaholic.persistence.sqlite import (
    SchemaUnsupportedError,
    SQLiteLocalActorSelector,
    SQLiteRepository,
    StorageUnavailableError,
    initialize_empty_store,
    open_write_transaction,
)
from workaholic.session import (
    ContextRequest,
    LocalSession,
    StatusRequest,
    TaskCreateRequest,
    TaskListRequest,
    UpRequest,
)

_NOW = datetime(2026, 7, 30, 19, 0, tzinfo=UTC)


def _environment(data_directory: Path) -> dict[str, str]:
    """Build one trusted isolated process environment.

    Args:
        data_directory: Absolute test-owned local data directory.

    Returns:
        Minimal environment mapping for composition.

    """
    return {
        "WORKAHOLIC_CONFIG_DIR": str(data_directory.parent / "config"),
        "WORKAHOLIC_DATA_DIR": str(data_directory),
    }


def _write_profile_registry(
    *,
    config_directory: Path,
    local_directory: Path,
    team_directory: Path,
    default_profile: str = "local",
) -> None:
    """Write one valid isolated two-profile configuration fixture.

    Args:
        config_directory: Test-owned trusted configuration directory.
        local_directory: Embedded data directory for ``local``.
        team_directory: Embedded data directory for ``team``.
        default_profile: Configured default profile name.

    """
    config_directory.mkdir()
    (config_directory / "profiles.toml").write_text(
        "version = 1\n"
        f'default_profile = "{default_profile}"\n'
        "[profiles.local]\n"
        'mode = "embedded"\n'
        f'data_directory = "{local_directory}"\n'
        "[profiles.team]\n"
        'mode = "embedded"\n'
        f'data_directory = "{team_directory}"\n',
        encoding="utf-8",
    )


class _FixedClock:
    """Provide one deterministic application timestamp."""

    def now(self) -> datetime:
        """Return the fixed timezone-aware UTC timestamp."""
        return _NOW


class _FixedIdentifiers:
    """Provide deterministic typed identifiers for composition tests."""

    def new_instance_id(self) -> InstanceId:
        """Return the fixed Instance identity."""
        return InstanceId("ins_fixed")

    def new_project_id(self) -> ProjectId:
        """Return the fixed Project identity."""
        return ProjectId("prj_fixed")

    def new_subject_id(self) -> SubjectId:
        """Return the fixed Subject identity."""
        return SubjectId("sub_fixed")

    def new_task_id(self) -> TaskId:
        """Return the fixed Task identity."""
        return TaskId("tsk_fixed")

    def new_event_id(self) -> TaskEventId:
        """Return the fixed TaskEvent identity."""
        return TaskEventId("evt_fixed")

    def new_request_id(self) -> RequestId:
        """Return the fixed request identity."""
        return RequestId("req_fixed")


def _require_uuid7(identifier: object, *, prefix: str) -> None:
    """Assert one domain identifier contains a canonical UUID7 suffix.

    Args:
        identifier: Candidate prefixed identifier value object.
        prefix: Required type prefix.

    """
    serialized = str(identifier)
    assert serialized.startswith(prefix)
    parsed = uuid.UUID(serialized.removeprefix(prefix))
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122


def test_session_composition_performs_no_eager_filesystem_writes(
    tmp_path: Path,
) -> None:
    """Constructing a Session creates neither context nor local storage."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"

    session = composition.create_local_session(
        cwd=workspace,
        environment=_environment(data_directory),
    )

    assert isinstance(session, LocalSession)
    assert not data_directory.exists()
    assert not (workspace / ".workaholic.env").exists()


def test_composed_session_persists_and_reopens_real_local_state(
    tmp_path: Path,
) -> None:
    """All real components cooperate across short-lived Session instances."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    environment = _environment(data_directory)
    first_session = composition.create_local_session(
        cwd=workspace,
        environment=environment,
    )

    bootstrap = first_session.up(
        UpRequest(project_key="ACME", idempotency_key="bootstrap-1")
    )
    created = first_session.create_task(
        TaskCreateRequest(
            title="First persistent task",
            idempotency_key="task-1",
        )
    )
    reopened = composition.create_local_session(
        cwd=workspace,
        environment=environment,
    )

    status = reopened.status(StatusRequest())
    page = reopened.list_tasks(TaskListRequest())

    assert status.project == bootstrap.project
    assert page.tasks == (created,)
    assert (workspace / ".workaholic.env").is_file()
    assert (data_directory / "local.db").is_file()
    for identifier, prefix in (
        (bootstrap.instance.id, "ins_"),
        (bootstrap.project.id, "prj_"),
        (bootstrap.subject.id, "sub_"),
        (created.uid, "tsk_"),
    ):
        _require_uuid7(identifier, prefix=prefix)


def test_injected_factories_are_lazy_and_deterministic(tmp_path: Path) -> None:
    """Composition defers runtime factories and honors deterministic adapters."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    config_directory = tmp_path / "config"
    observed: list[tuple[str, object]] = []

    def resolve_config(
        environment: object,
    ) -> LocalConfigPaths:
        """Record trusted configuration resolution without filesystem writes."""
        observed.append(("config", environment))
        return LocalConfigPaths(
            config_directory=config_directory,
            profiles_file=config_directory / "profiles.toml",
        )

    def repository_factory(database_path: Path) -> SQLiteRepository:
        """Record and construct one semantic SQLite repository."""
        observed.append(("repository", database_path))
        return SQLiteRepository(database_path)

    def identity_factory(database_path: Path) -> SQLiteLocalActorSelector:
        """Record and construct one local identity selector."""
        observed.append(("identity", database_path))
        return SQLiteLocalActorSelector(database_path)

    def clock_factory() -> _FixedClock:
        """Record and return one fixed clock."""
        observed.append(("clock", None))
        return _FixedClock()

    def identifier_factory() -> _FixedIdentifiers:
        """Record and return one fixed identifier source."""
        observed.append(("identifiers", None))
        return _FixedIdentifiers()

    environment = _environment(data_directory)
    factories = composition.LocalCompositionFactories(
        repository=repository_factory,
        identity=identity_factory,
        clock=clock_factory,
        identifiers=identifier_factory,
    )

    session = composition.create_local_session(
        cwd=workspace,
        environment=environment,
        config_path_resolver=resolve_config,
        factories=factories,
    )

    assert observed == [("config", environment)]
    assert not data_directory.exists()

    result = session.up(UpRequest(project_key="ACME"))

    assert result.instance.id == InstanceId("ins_fixed")
    assert result.project.id == ProjectId("prj_fixed")
    assert result.subject.id == SubjectId("sub_fixed")
    assert observed[1:] == [
        ("repository", data_directory / "local.db"),
        ("identity", data_directory / "local.db"),
        ("clock", None),
        ("identifiers", None),
    ]


def test_two_embedded_profiles_are_isolated_across_process_restarts(
    tmp_path: Path,
) -> None:
    """Configured profiles persist independent Instances and Task sequences."""
    config_directory = tmp_path / "config"
    local_directory = tmp_path / "local-data"
    team_directory = tmp_path / "team-data"
    local_workspace = tmp_path / "local-workspace"
    team_workspace = tmp_path / "team-workspace"
    local_workspace.mkdir()
    team_workspace.mkdir()
    _write_profile_registry(
        config_directory=config_directory,
        local_directory=local_directory,
        team_directory=team_directory,
        default_profile="team",
    )
    environment = {"WORKAHOLIC_CONFIG_DIR": str(config_directory)}

    local_session = composition.create_local_session(
        cwd=local_workspace,
        environment=environment,
    )
    team_session = composition.create_local_session(
        cwd=team_workspace,
        environment=environment,
    )
    local_bootstrap = local_session.up(UpRequest(project_key="ACME", profile="local"))
    team_bootstrap = team_session.up(UpRequest(project_key="ACME"))
    local_task = local_session.create_task(TaskCreateRequest(title="Local task"))
    team_task = team_session.create_task(TaskCreateRequest(title="Team task"))

    reopened_local = composition.create_local_session(
        cwd=local_workspace,
        environment=environment,
    )
    reopened_team = composition.create_local_session(
        cwd=team_workspace,
        environment=environment,
    )

    assert local_bootstrap.instance.id != team_bootstrap.instance.id
    assert local_bootstrap.project.id != team_bootstrap.project.id
    assert local_task.key == team_task.key == "ACME-1"
    assert reopened_local.status(StatusRequest()).instance == local_bootstrap.instance
    assert reopened_team.status(StatusRequest()).instance == team_bootstrap.instance
    assert (local_directory / "local.db").is_file()
    assert (team_directory / "local.db").is_file()


def test_environment_profile_precedes_context_and_explicit_profile_wins(
    tmp_path: Path,
) -> None:
    """Concrete profile resolution follows explicit, environment, then context."""
    config_directory = tmp_path / "config"
    local_directory = tmp_path / "local-data"
    team_directory = tmp_path / "team-data"
    team_workspace = tmp_path / "team-workspace"
    team_workspace.mkdir()
    _write_profile_registry(
        config_directory=config_directory,
        local_directory=local_directory,
        team_directory=team_directory,
    )
    base_environment = {"WORKAHOLIC_CONFIG_DIR": str(config_directory)}
    composition.create_local_session(
        cwd=team_workspace,
        environment=base_environment,
    ).up(UpRequest(project_key="ACME", profile="team"))
    selected_environment = {
        **base_environment,
        "WORKAHOLIC_PROFILE": "local",
    }
    session = composition.create_local_session(
        cwd=team_workspace,
        environment=selected_environment,
    )

    with pytest.raises(ApplicationError) as missing_context:
        session.status(StatusRequest())
    assert missing_context.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND

    assert session.status(StatusRequest(profile="team")).profile == "team"


def test_environment_profile_must_be_valid_and_configured(tmp_path: Path) -> None:
    """Trusted environment selection rejects malformed and unknown profiles."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = _environment(tmp_path / "data")

    with pytest.raises(ProfileInvalidError):
        composition.create_local_session(
            cwd=workspace,
            environment={**environment, "WORKAHOLIC_PROFILE": "Team"},
        )

    unknown = composition.create_local_session(
        cwd=workspace,
        environment={**environment, "WORKAHOLIC_PROFILE": "missing"},
    )
    with pytest.raises(ProfileNotFoundError):
        unknown.up(UpRequest(project_key="ACME"))


def test_canonical_current_directory_is_reported_through_symlink(
    tmp_path: Path,
) -> None:
    """Composition resolves the process directory before context operations."""
    physical_workspace = tmp_path / "physical"
    linked_workspace = tmp_path / "linked"
    physical_workspace.mkdir()
    linked_workspace.symlink_to(physical_workspace, target_is_directory=True)
    environment = _environment(tmp_path / "data")
    session = composition.create_local_session(
        cwd=linked_workspace,
        environment=environment,
    )

    session.up(UpRequest(project_key="ACME"))
    result = session.context(ContextRequest())

    assert result.workspace_root == physical_workspace
    assert result.context_source == physical_workspace / ".workaholic.env"


def test_missing_context_does_not_open_initialized_profile_storage(
    tmp_path: Path,
) -> None:
    """An unbound Workspace fails before reading an existing profile database."""
    bound_workspace = tmp_path / "bound"
    unbound_workspace = tmp_path / "unbound"
    bound_workspace.mkdir()
    unbound_workspace.mkdir()
    data_directory = tmp_path / "data"
    environment = _environment(data_directory)
    composition.create_local_session(
        cwd=bound_workspace,
        environment=environment,
    ).up(UpRequest(project_key="ACME"))
    database_path = data_directory / "local.db"
    database_before = database_path.read_bytes()
    unbound = composition.create_local_session(
        cwd=unbound_workspace,
        environment=environment,
    )

    with pytest.raises(ApplicationError) as missing_context:
        unbound.status(StatusRequest())
    assert missing_context.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND

    assert database_path.read_bytes() == database_before
    assert tuple(unbound_workspace.iterdir()) == ()


def test_profile_selected_runtime_rejects_unsupported_schema(
    tmp_path: Path,
) -> None:
    """A selected profile never upgrades or rewrites an older store schema."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    environment = _environment(data_directory)
    composition.create_local_session(
        cwd=workspace,
        environment=environment,
    ).up(UpRequest(project_key="ACME"))
    database_path = data_directory / "local.db"
    with open_write_transaction(database_path) as connection:
        connection.execute("UPDATE store_metadata SET schema_version = 1")
    database_before = database_path.read_bytes()
    reopened = composition.create_local_session(
        cwd=workspace,
        environment=environment,
    )

    with pytest.raises(SchemaUnsupportedError):
        reopened.status(StatusRequest())

    assert database_path.read_bytes() == database_before


def test_unexpected_configuration_failures_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver and TOML loader exceptions expose only stable profile errors."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sensitive = str(tmp_path / "secret profiles.toml")

    def fail_resolver(_environment: object) -> LocalConfigPaths:
        """Raise one unexpected path-bearing resolver failure."""
        message = f"cannot read {sensitive}"
        raise RuntimeError(message)

    with pytest.raises(ProfileInvalidError) as resolver_error:
        composition.create_local_session(
            cwd=workspace,
            environment={},
            config_path_resolver=fail_resolver,
        )
    assert sensitive not in str(resolver_error.value)

    paths = LocalConfigPaths(
        config_directory=tmp_path / "config",
        profiles_file=tmp_path / "config" / "profiles.toml",
    )

    def return_paths(_environment: object) -> LocalConfigPaths:
        """Return deterministic trusted configuration paths."""
        return paths

    def fail_loader(_paths: object, _environment: object) -> object:
        """Raise one unexpected TOML-content-bearing loader failure."""
        message = f'token = "private" from {sensitive}'
        raise RuntimeError(message)

    monkeypatch.setattr(composition, "load_profile_registry", fail_loader)
    with pytest.raises(ProfileInvalidError) as loader_error:
        composition.create_local_session(
            cwd=workspace,
            environment={},
            config_path_resolver=return_paths,
        )
    assert sensitive not in str(loader_error.value)
    assert "private" not in str(loader_error.value)


def test_unavailable_configuration_directory_is_profile_invalid(
    tmp_path: Path,
) -> None:
    """A non-directory configuration root fails with no path disclosure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("occupied", encoding="utf-8")
    paths = LocalConfigPaths(
        config_directory=unavailable,
        profiles_file=unavailable / "profiles.toml",
    )

    def resolve_unavailable(_environment: object) -> LocalConfigPaths:
        """Return a trusted path whose parent is unavailable."""
        return paths

    with pytest.raises(ProfileInvalidError) as captured:
        composition.create_local_session(
            cwd=workspace,
            environment={},
            config_path_resolver=resolve_unavailable,
        )

    assert str(unavailable) not in str(captured.value)


def test_unexpected_runtime_factory_failure_is_redacted(tmp_path: Path) -> None:
    """Profile database paths never escape an unexpected factory failure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "sensitive-data"

    def fail_repository(database_path: Path) -> SQLiteRepository:
        """Raise one unexpected database-path-bearing factory failure."""
        message = f"could not open {database_path}"
        raise RuntimeError(message)

    factories = composition.LocalCompositionFactories(
        repository=fail_repository,
        identity=SQLiteLocalActorSelector,
        clock=_FixedClock,
        identifiers=_FixedIdentifiers,
    )
    session = composition.create_local_session(
        cwd=workspace,
        environment=_environment(data_directory),
        factories=factories,
    )

    with pytest.raises(ApplicationError) as captured:
        session.up(UpRequest(project_key="ACME"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert str(data_directory) not in str(captured.value)


def test_help_and_version_never_acquire_the_process_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eager informational commands do not read profiles or open SQLite."""
    calls = 0

    def fail_session_creation(**_kwargs: object) -> LocalSession:
        """Fail if an informational command attempts local composition."""
        nonlocal calls
        calls += 1
        raise AssertionError

    monkeypatch.setattr(composition, "create_local_session", fail_session_creation)
    application = create_app(composition._create_process_session)
    runner = CliRunner()

    help_result = runner.invoke(application, [])
    version_result = runner.invoke(application, ["--version"])

    assert help_result.exit_code == 0
    assert version_result.exit_code == 0
    assert calls == 0


def test_composition_injection_boundary_rejects_invalid_dependencies(
    tmp_path: Path,
) -> None:
    """Direct callers cannot bypass explicit composition dependency contracts."""
    with pytest.raises(TypeError, match="repository factory must be callable"):
        composition.LocalCompositionFactories(
            repository=cast("type[SQLiteRepository]", object()),
            identity=SQLiteLocalActorSelector,
            clock=_FixedClock,
            identifiers=_FixedIdentifiers,
        )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = _environment(tmp_path / "data")
    with pytest.raises(TypeError, match="path resolver must be callable"):
        composition.create_local_session(
            cwd=workspace,
            environment=environment,
            config_path_resolver=cast("composition.ConfigPathResolver", object()),
        )
    with pytest.raises(TypeError, match="factories are invalid"):
        composition.create_local_session(
            cwd=workspace,
            environment=environment,
            factories=cast("composition.LocalCompositionFactories", object()),
        )


def test_identifier_factory_uses_unique_typed_uuid7_values() -> None:
    """Every application identifier kind is typed, prefixed, and unique."""
    factory = composition._Uuid7IdentifierFactory()
    identifiers = (
        factory.new_instance_id(),
        factory.new_project_id(),
        factory.new_subject_id(),
        factory.new_task_id(),
        factory.new_result_id(),
        factory.new_event_id(),
        factory.new_request_id(),
    )
    expectations = (
        (InstanceId, "ins_"),
        (ProjectId, "prj_"),
        (SubjectId, "sub_"),
        (TaskId, "tsk_"),
        (ResultId, "res_"),
        (TaskEventId, "evt_"),
        (RequestId, "req_"),
    )

    assert len({str(identifier) for identifier in identifiers}) == len(identifiers)
    for identifier, (expected_type, prefix) in zip(
        identifiers,
        expectations,
        strict=True,
    ):
        assert isinstance(identifier, expected_type)
        _require_uuid7(identifier, prefix=prefix)


def test_uuid7_helper_rejects_unknown_prefix() -> None:
    """Identifier generation cannot silently create an untyped ID family."""
    with pytest.raises(ValueError, match="prefix is unsupported"):
        composition._new_uuid7_text("unknown_")


def test_uuid7_helper_rejects_invalid_generator_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standard-library contract violation fails before producing an ID."""
    monkeypatch.setattr("workaholic.composition.uuid.uuid7", uuid.uuid4)

    with pytest.raises(RuntimeError, match=r"uuid7\(\) returned an invalid UUID"):
        composition._new_uuid7_text("tsk_")


def test_system_clock_returns_aware_utc_time() -> None:
    """The operation clock satisfies the authoritative timestamp contract."""
    timestamp = composition._UtcSystemClock().now()
    offset = timestamp.utcoffset()

    assert timestamp.tzinfo is UTC
    assert offset is not None
    assert offset.total_seconds() == 0


@pytest.mark.parametrize(
    "cwd",
    [
        Path("relative"),
        Path("/definitely/missing/workaholic-composition-directory"),
    ],
)
def test_session_composition_rejects_invalid_workspace(
    cwd: Path,
    tmp_path: Path,
) -> None:
    """Composition rejects relative or missing Workspace directories."""
    with pytest.raises(ContextInvalidError):
        composition.create_local_session(
            cwd=cwd,
            environment=_environment(tmp_path / "data"),
        )


def test_sqlite_actor_selector_rejects_non_absolute_path() -> None:
    """The concrete identity adapter validates its storage boundary."""
    with pytest.raises(TypeError, match="absolute Path"):
        SQLiteLocalActorSelector(Path("local.db"))


def test_sqlite_actor_selector_rejects_invalid_binding_before_storage(
    tmp_path: Path,
) -> None:
    """Unvalidated context cannot cause an actor-selection database read."""
    selector = SQLiteLocalActorSelector(tmp_path / "local.db")

    with pytest.raises(PermissionDeniedError):
        selector.select(cast("WorkspaceBinding", object()))

    assert not (tmp_path / "local.db").exists()


def test_sqlite_actor_selector_fails_closed_without_unique_human(
    tmp_path: Path,
) -> None:
    """An initialized store without one active bootstrap Human is unauthorized."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    with open_write_transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO instances (id, created_at)
            VALUES ('ins_missing', '2026-07-30T16:30:00.000000Z')
            """
        )
    selector = SQLiteLocalActorSelector(database_path)
    binding = WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=InstanceId("ins_missing"),
        project_id=ProjectId("prj_missing"),
        project_key="ACME",
        workspace_root=".",
    )

    with pytest.raises(PermissionDeniedError):
        selector.select(binding)


def test_sqlite_identity_selection_requires_one_initialized_instance(
    tmp_path: Path,
) -> None:
    """Empty and malformed multi-Instance stores fail with stable errors."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    selector = SQLiteLocalActorSelector(database_path)

    with pytest.raises(NotInitializedError):
        selector.select_local()

    with open_write_transaction(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO instances (id, created_at)
            VALUES (?, '2026-07-30T16:30:00.000000Z')
            """,
            (("ins_first",), ("ins_second",)),
        )

    with pytest.raises(StorageUnavailableError):
        selector.select_local()


def test_workspace_validation_maps_operating_system_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable current directory becomes one safe context failure."""

    def fail_is_dir(_path: Path) -> bool:
        """Raise a simulated filesystem inspection failure."""
        raise OSError

    monkeypatch.setattr(Path, "is_dir", fail_is_dir)

    with pytest.raises(ContextInvalidError, match="directory is unavailable"):
        composition._require_workspace_directory(tmp_path)


def test_process_session_uses_current_directory_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process provider forwards only current trusted boundaries."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("WORKAHOLIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("WORKAHOLIC_DATA_DIR", str(data_directory))

    session = composition._create_process_session()

    assert isinstance(session, LocalSession)
    assert not data_directory.exists()


def test_public_main_builds_one_application_with_process_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console root supplies its stateless process Session provider."""
    observed_providers: list[object] = []
    observed_programs: list[str] = []

    def fake_create_app(provider: object) -> object:
        """Record the provider and return one callable application."""
        observed_providers.append(provider)

        def fake_application(*, prog_name: str) -> None:
            """Record the stable executable name."""
            observed_programs.append(prog_name)

        return fake_application

    monkeypatch.setattr(composition, "create_app", fake_create_app)

    composition.main()

    assert observed_providers == [composition._create_process_session]
    assert observed_programs == ["workaholic"]
