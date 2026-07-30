"""Unit tests for the explicit embedded local composition root."""

from __future__ import annotations

import uuid
from datetime import UTC
from pathlib import Path
from typing import cast

import pytest

from workaholic import composition
from workaholic.application import NotInitializedError, PermissionDeniedError
from workaholic.context import ContextInvalidError
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
    WorkspaceBinding,
)
from workaholic.persistence.sqlite import (
    SQLiteLocalActorSelector,
    StorageUnavailableError,
    initialize_empty_store,
    open_write_transaction,
)
from workaholic.session import (
    LocalSession,
    StatusRequest,
    TaskCreateRequest,
    TaskListRequest,
    UpRequest,
)


def _environment(data_directory: Path) -> dict[str, str]:
    """Build one trusted isolated process environment.

    Args:
        data_directory: Absolute test-owned local data directory.

    Returns:
        Minimal environment mapping for composition.

    """
    return {"WORKAHOLIC_DATA_DIR": str(data_directory)}


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


def test_identifier_factory_uses_unique_typed_uuid7_values() -> None:
    """Every application identifier kind is typed, prefixed, and unique."""
    factory = composition._Uuid7IdentifierFactory()
    identifiers = (
        factory.new_instance_id(),
        factory.new_project_id(),
        factory.new_subject_id(),
        factory.new_task_id(),
        factory.new_event_id(),
        factory.new_request_id(),
    )
    expectations = (
        (InstanceId, "ins_"),
        (ProjectId, "prj_"),
        (SubjectId, "sub_"),
        (TaskId, "tsk_"),
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
