"""Integration tests for durable Project Workspace binding files."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from workaholic import composition
from workaholic.application import (
    ApplicationErrorCode,
    PermissionDeniedError,
    ProjectCreationMutation,
    ProjectNotFoundError,
    WorkspaceBindingConflictError,
)
from workaholic.context import (
    CONTEXT_FILENAME,
    ContextInvalidError,
    ContextStorageError,
    bind_workspace_context,
    read_current_workspace_context,
)
from workaholic.context import local as local_context
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    WorkspaceBinding,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    open_write_transaction,
)
from workaholic.session import ProjectBindRequest, UpRequest

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration
_NOW = datetime(2026, 7, 30, 16, 30, tzinfo=UTC)


def _binding(
    *,
    profile: str = "local",
    instance_id: str = "ins_local",
    project_id: str = "prj_acme",
    project_key: str = "ACME",
) -> WorkspaceBinding:
    """Build one authoritative target-directory binding.

    Args:
        profile: Trusted embedded profile name.
        instance_id: Selected Instance identity.
        project_id: Selected Project identity.
        project_key: Selected immutable Project key.

    Returns:
        Valid Workspace binding rooted at its context directory.

    """
    return WorkspaceBinding(
        context_version=1,
        profile=profile,
        instance_id=InstanceId(instance_id),
        project_id=ProjectId(project_id),
        project_key=project_key,
        workspace_root=".",
    )


def _environment(data_directory: Path) -> dict[str, str]:
    """Build one isolated trusted local data environment.

    Args:
        data_directory: Absolute test-owned embedded data directory.

    Returns:
        Minimal process environment mapping.

    """
    return {
        "WORKAHOLIC_CONFIG_DIR": str(data_directory.parent / "config"),
        "WORKAHOLIC_DATA_DIR": str(data_directory),
    }


def test_composed_session_binds_existing_project_without_database_mutation(
    tmp_path: Path,
) -> None:
    """Real Session, query, SQLite, and context adapters cooperate safely."""
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    target.mkdir()
    (target / ".git").mkdir()
    data_directory = tmp_path / "data"
    session = composition.create_local_session(
        cwd=current,
        environment=_environment(data_directory),
    )
    bootstrap = session.up(UpRequest(project_key="ACME"))
    repository = SQLiteRepository(data_directory / "local.db")
    created = repository.create_project(
        ProjectCreationMutation(
            project_id=ProjectId("prj_docs"),
            request_id=RequestId("req_docs"),
            instance_id=bootstrap.instance.id,
            actor_subject_id=bootstrap.subject.id,
            occurred_at=_NOW,
            project_key="DOCS",
            project_name="Documentation",
        )
    )
    database_before = repository.database_path.read_bytes()

    result = session.bind_project(ProjectBindRequest(project="DOCS", path=target))

    assert result.project == created.project
    assert result.grant == created.grant
    assert result.workspace_root == target
    assert result.context_source == target / CONTEXT_FILENAME
    assert read_current_workspace_context(current).project_key == "ACME"
    assert read_current_workspace_context(target).project_key == "DOCS"
    assert (target / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    ) == ".workaholic.env\n"
    assert repository.database_path.read_bytes() == database_before


def test_composed_session_rejects_missing_project_keys(
    tmp_path: Path,
) -> None:
    """Missing Projects cannot produce a durable Workspace binding."""
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    target.mkdir()
    data_directory = tmp_path / "data"
    session = composition.create_local_session(
        cwd=current,
        environment=_environment(data_directory),
    )
    session.up(UpRequest(project_key="ACME"))
    for project_key in ("DOCS", "OTHER"):
        with pytest.raises(ProjectNotFoundError) as captured:
            session.bind_project(ProjectBindRequest(project=project_key, path=target))
        assert captured.value.code is ApplicationErrorCode.PROJECT_NOT_FOUND
        assert not (target / CONTEXT_FILENAME).exists()


def test_composed_session_revalidates_active_subject_before_binding(
    tmp_path: Path,
) -> None:
    """A disabled bootstrap Human cannot publish a new Workspace binding."""
    current = tmp_path / "current"
    target = tmp_path / "target"
    current.mkdir()
    target.mkdir()
    data_directory = tmp_path / "data"
    session = composition.create_local_session(
        cwd=current,
        environment=_environment(data_directory),
    )
    session.up(UpRequest(project_key="ACME"))
    with open_write_transaction(data_directory / "local.db") as connection:
        connection.execute("UPDATE subjects SET enabled = 0")

    with pytest.raises(PermissionDeniedError) as captured:
        session.bind_project(ProjectBindRequest(project="ACME", path=target))

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED
    assert not (target / CONTEXT_FILENAME).exists()


def test_binding_is_private_canonical_and_locally_git_excluded(
    tmp_path: Path,
) -> None:
    """Context becomes durable before one conventional exclude is preserved."""
    git_info = tmp_path / ".git" / "info"
    git_info.mkdir(parents=True)
    exclude = git_info / "exclude"
    exclude.write_text("existing-pattern", encoding="utf-8")
    exclude.chmod(0o640)

    context_path = bind_workspace_context(tmp_path, _binding())

    assert context_path == tmp_path / CONTEXT_FILENAME
    assert context_path.is_file()
    assert read_current_workspace_context(tmp_path) == _binding()
    assert stat.S_IMODE(context_path.stat().st_mode) == 0o600
    assert exclude.read_text(encoding="utf-8") == (
        "existing-pattern\n.workaholic.env\n"
    )
    assert stat.S_IMODE(exclude.stat().st_mode) == 0o640
    assert not (tmp_path / ".gitignore").exists()


def test_same_project_can_bind_two_independent_workspace_paths(
    tmp_path: Path,
) -> None:
    """Workspace bindings are filesystem state rather than database records."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    binding = _binding(project_id="prj_docs", project_key="DOCS")

    first_path = bind_workspace_context(first, binding)
    second_path = bind_workspace_context(second, binding)

    assert first_path != second_path
    assert read_current_workspace_context(first) == binding
    assert read_current_workspace_context(second) == binding


def test_equivalent_binding_retry_writes_neither_context_nor_exclude(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An equivalent retry is a complete no-op after Git exclusion exists."""
    (tmp_path / ".git").mkdir()
    context_path = bind_workspace_context(tmp_path, _binding())
    original_context = context_path.stat()
    exclude = tmp_path / ".git" / "info" / "exclude"
    original_exclude = exclude.stat()

    def fail_write(*_args: object, **_kwargs: object) -> bool:
        """Fail if an equivalent binding attempts any filesystem write."""
        pytest.fail("equivalent binding retry must not rewrite local files")

    monkeypatch.setattr(local_context, "_atomic_write_bytes", fail_write)

    assert bind_workspace_context(tmp_path, _binding()) == context_path
    assert context_path.stat().st_ino == original_context.st_ino
    assert exclude.stat().st_ino == original_exclude.st_ino


@pytest.mark.parametrize(
    "conflicting",
    [
        _binding(profile="team"),
        _binding(instance_id="ins_other"),
        _binding(project_id="prj_docs", project_key="DOCS"),
    ],
)
def test_valid_profile_instance_or_project_change_requires_replace(
    conflicting: WorkspaceBinding,
    tmp_path: Path,
) -> None:
    """A valid target never silently changes any authoritative identity."""
    context_path = bind_workspace_context(tmp_path, _binding())
    original = context_path.read_bytes()

    with pytest.raises(WorkspaceBindingConflictError):
        bind_workspace_context(tmp_path, conflicting)

    assert context_path.read_bytes() == original
    assert (
        bind_workspace_context(
            tmp_path,
            conflicting,
            replace=True,
        )
        == context_path
    )
    assert read_current_workspace_context(tmp_path) == conflicting


@pytest.mark.parametrize("unsafe_shape", ["malformed", "symlink"])
def test_replace_refuses_malformed_or_redirecting_context(
    unsafe_shape: str,
    tmp_path: Path,
) -> None:
    """Explicit replacement never bypasses hostile-input validation."""
    context_path = tmp_path / CONTEXT_FILENAME
    if unsafe_shape == "malformed":
        context_path.write_text("not-context", encoding="utf-8")
    else:
        outside = tmp_path / "outside"
        outside.write_text("preserved", encoding="utf-8")
        context_path.symlink_to(outside)

    with pytest.raises(ContextInvalidError):
        bind_workspace_context(
            tmp_path,
            _binding(),
            replace=True,
        )

    if unsafe_shape == "malformed":
        assert context_path.read_text(encoding="utf-8") == "not-context"
    else:
        assert context_path.is_symlink()
        assert context_path.read_text(encoding="utf-8") == "preserved"


def test_non_git_workspace_binding_does_not_create_git_metadata(
    tmp_path: Path,
) -> None:
    """A normal directory receives context without being turned into a Git repo."""
    context_path = bind_workspace_context(tmp_path, _binding())

    assert context_path.is_file()
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_git_exclusion_runs_only_after_context_is_durable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The Git metadata boundary can always observe authoritative context."""
    observed: list[WorkspaceBinding] = []

    def observe_context(directory: Path) -> None:
        """Record the already durable binding at exclude time."""
        observed.append(read_current_workspace_context(directory))

    monkeypatch.setattr(local_context, "exclude_context_from_git", observe_context)

    bind_workspace_context(tmp_path, _binding())

    assert observed == [_binding()]


def test_exclude_failure_leaves_retryable_durable_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failure after context publication preserves the authoritative result."""
    calls = 0
    original_exclude = local_context.exclude_context_from_git

    def fail_once(directory: Path) -> None:
        """Fail after proving the context is already readable."""
        nonlocal calls
        calls += 1
        assert read_current_workspace_context(directory) == _binding()
        if calls == 1:
            raise ContextStorageError
        original_exclude(directory)

    monkeypatch.setattr(local_context, "exclude_context_from_git", fail_once)

    with pytest.raises(ContextStorageError):
        bind_workspace_context(tmp_path, _binding())

    context_path = tmp_path / CONTEXT_FILENAME
    assert context_path.is_file()
    assert read_current_workspace_context(tmp_path) == _binding()
    assert bind_workspace_context(tmp_path, _binding()) == context_path
    assert calls == 2


def test_symlinked_target_directory_resolves_to_physical_context(
    tmp_path: Path,
) -> None:
    """Binding returns the canonical physical path used for authority."""
    physical = tmp_path / "physical"
    physical.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(physical, target_is_directory=True)

    context_path = bind_workspace_context(linked, _binding())

    assert context_path == physical / CONTEXT_FILENAME
    assert context_path.is_file()
