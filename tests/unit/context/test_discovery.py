"""Unit tests for canonical physical upward Workspace discovery."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import ApplicationErrorCode
from workaholic.context import (
    CONTEXT_FILENAME,
    ContextInvalidError,
    ContextNotFoundError,
    ContextStorageError,
    DiscoveredWorkspace,
    discover_workspace_context,
)
from workaholic.context import local as local_context
from workaholic.domain import InstanceId, ProjectId, WorkspaceBinding

if TYPE_CHECKING:
    from workaholic.context._files import RegularFileSnapshot


def _binding(
    *,
    project_key: str = "ACME",
    workspace_root: str = ".",
) -> WorkspaceBinding:
    """Build one valid discovery binding.

    Args:
        project_key: Project key used to distinguish nearest contexts.
        workspace_root: Relative root resolved from the context directory.

    Returns:
        Valid dependency-free Workspace binding.

    """
    return WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId(f"prj_{project_key.lower()}"),
        project_key=project_key,
        workspace_root=workspace_root,
    )


def _serialize(binding: WorkspaceBinding) -> str:
    """Serialize one fixture binding in the strict documented key order.

    Args:
        binding: Valid binding to serialize.

    Returns:
        Strict context text.

    """
    return (
        "WORKAHOLIC_CONTEXT_VERSION=1\n"
        f"WORKAHOLIC_PROFILE={binding.profile}\n"
        f"WORKAHOLIC_INSTANCE_ID={binding.instance_id}\n"
        f"WORKAHOLIC_PROJECT_ID={binding.project_id}\n"
        f"WORKAHOLIC_PROJECT_KEY={binding.project_key}\n"
        f"WORKAHOLIC_WORKSPACE_ROOT={binding.workspace_root}\n"
    )


def _write_context(directory: Path, binding: WorkspaceBinding) -> Path:
    """Write one test-owned context file.

    Args:
        directory: Existing context directory.
        binding: Binding fixture to serialize.

    Returns:
        Written context path.

    """
    context_path = directory / CONTEXT_FILENAME
    context_path.write_text(_serialize(binding), encoding="utf-8", newline="")
    return context_path


def test_nearest_context_wins_from_a_deep_directory(tmp_path: Path) -> None:
    """The first physical ancestor with context is authoritative."""
    parent_binding = _binding(project_key="PARENT")
    nearest_binding = _binding(project_key="NEAR")
    _write_context(tmp_path, parent_binding)
    nearest = tmp_path / "nested"
    start = nearest / "deep" / "worker"
    start.mkdir(parents=True)
    nearest_context = _write_context(nearest, nearest_binding)

    discovered = discover_workspace_context(start)

    assert discovered == DiscoveredWorkspace(
        binding=nearest_binding,
        context_file=nearest_context,
        workspace_root=nearest,
    )


def test_parent_context_resolves_a_contained_relative_root(tmp_path: Path) -> None:
    """A relative root is resolved physically from the context directory."""
    binding = _binding(workspace_root="apps/api")
    workspace_root = tmp_path / "apps" / "api"
    start = workspace_root / "src" / "workers"
    start.mkdir(parents=True)
    context_path = _write_context(tmp_path, binding)

    discovered = discover_workspace_context(start)

    assert discovered.binding == binding
    assert discovered.context_file == context_path
    assert discovered.workspace_root == workspace_root


def test_discovery_terminates_after_inspecting_filesystem_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An absent context produces one bounded walk through physical root."""
    inspected: list[Path] = []

    def report_missing(path: Path) -> tuple[WorkspaceBinding, os.stat_result]:
        """Record each candidate while reporting an absent exact file."""
        inspected.append(path)
        raise ContextNotFoundError

    monkeypatch.setattr(local_context, "_read_context_file", report_missing)

    with pytest.raises(ContextNotFoundError):
        discover_workspace_context(tmp_path)

    assert inspected[0] == tmp_path.resolve() / CONTEXT_FILENAME
    assert inspected[-1] == Path("/").resolve() / CONTEXT_FILENAME
    assert len(inspected) == len(set(inspected))


def test_malformed_nearer_context_never_falls_back_to_parent(
    tmp_path: Path,
) -> None:
    """Hostile nearer repository data terminates otherwise valid discovery."""
    _write_context(tmp_path, _binding(project_key="PARENT"))
    nearer = tmp_path / "nearer"
    start = nearer / "child"
    start.mkdir(parents=True)
    (nearer / CONTEXT_FILENAME).write_text("malformed", encoding="utf-8")

    with pytest.raises(ContextInvalidError) as captured:
        discover_workspace_context(start)

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID


def test_nested_git_metadata_does_not_stop_or_execute_during_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery ignores repository boundaries and never invokes Git."""
    binding = _binding()
    _write_context(tmp_path, binding)
    nested_repository = tmp_path / "packages" / "worker"
    (nested_repository / ".git").mkdir(parents=True)
    start = nested_repository / "src"
    start.mkdir()

    def fail_process(*_arguments: object, **_keywords: object) -> object:
        """Fail if discovery delegates to a command processor."""
        pytest.fail("physical context discovery must not execute processes")

    monkeypatch.setattr(os, "system", fail_process)
    monkeypatch.setattr(subprocess, "run", fail_process)

    assert discover_workspace_context(start).binding == binding


def test_symlinked_start_uses_canonical_physical_ancestors(tmp_path: Path) -> None:
    """A directory alias cannot change which physical ancestor is selected."""
    if os.name == "nt":
        pytest.skip("Symlink creation is not generally available on Windows.")
    physical = tmp_path / "physical"
    start = physical / "workspace" / "deep"
    start.mkdir(parents=True)
    binding = _binding(workspace_root="workspace")
    context_path = _write_context(physical, binding)
    alias = tmp_path / "alias"
    alias.symlink_to(start, target_is_directory=True)

    discovered = discover_workspace_context(alias)

    assert discovered.context_file == context_path
    assert discovered.workspace_root == physical / "workspace"


def test_symlinked_nearer_context_is_invalid_and_authoritative(
    tmp_path: Path,
) -> None:
    """A context symlink fails instead of redirecting or falling back."""
    if os.name == "nt":
        pytest.skip("Symlink creation is not generally available on Windows.")
    _write_context(tmp_path, _binding(project_key="PARENT"))
    nearer = tmp_path / "nearer"
    start = nearer / "child"
    start.mkdir(parents=True)
    target = tmp_path / "target-context"
    target.write_text(_serialize(_binding()), encoding="utf-8")
    (nearer / CONTEXT_FILENAME).symlink_to(target)

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(start)


def test_workspace_root_symlink_cannot_escape_context_directory(
    tmp_path: Path,
) -> None:
    """Lexically contained roots must also remain physically contained."""
    if os.name == "nt":
        pytest.skip("Symlink creation is not generally available on Windows.")
    context_directory = tmp_path / "workspace"
    context_directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (context_directory / "linked").symlink_to(outside, target_is_directory=True)
    _write_context(
        context_directory,
        _binding(workspace_root="linked"),
    )

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(context_directory)


@pytest.mark.parametrize("root_shape", ["missing", "file"])
def test_workspace_root_must_be_an_existing_directory(
    root_shape: str,
    tmp_path: Path,
) -> None:
    """Missing roots and regular files are invalid context targets."""
    context_directory = tmp_path / "workspace"
    context_directory.mkdir()
    root = context_directory / "target"
    if root_shape == "file":
        root.write_text("not a directory", encoding="utf-8")
    _write_context(context_directory, _binding(workspace_root="target"))

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(context_directory)


def test_unreadable_nearer_context_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An operating-system error at the nearer source is authoritative."""
    _write_context(tmp_path, _binding(project_key="PARENT"))
    nearer = tmp_path / "nearer"
    start = nearer / "child"
    start.mkdir(parents=True)
    nearer_context = _write_context(nearer, _binding())
    original_read = local_context._read_regular_file_snapshot

    def fail_nearer_read(
        path: Path,
        *,
        maximum: int,
    ) -> RegularFileSnapshot:
        """Fail only the nearer context read with a private driver error."""
        if path == nearer_context:
            message = "private permission detail"
            raise PermissionError(message)
        return original_read(path, maximum=maximum)

    monkeypatch.setattr(
        local_context,
        "_read_regular_file_snapshot",
        fail_nearer_read,
    )

    with pytest.raises(ContextStorageError) as captured:
        discover_workspace_context(start)

    assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
    assert "private permission detail" not in captured.value.safe_message


def test_context_removed_during_open_is_invalid_not_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A nearer file that races after inspection cannot reveal parent context."""
    _write_context(tmp_path, _binding(project_key="PARENT"))
    nearer = tmp_path / "nearer"
    start = nearer / "child"
    start.mkdir(parents=True)
    nearer_context = _write_context(nearer, _binding())
    original_open = os.open

    def remove_before_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        """Remove only the nearer file after lstat and before descriptor open."""
        if Path(path) == nearer_context:
            nearer_context.unlink()
            raise FileNotFoundError
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("workaholic.auth._files.os.open", remove_before_open)

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(start)


def test_context_replaced_during_root_resolution_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery rechecks the parsed snapshot before returning its result."""
    context_path = _write_context(tmp_path, _binding())
    original_resolve = local_context._resolve_workspace_root

    def replace_during_resolution(
        context_directory: Path,
        relative_root: str,
    ) -> Path:
        """Replace context with a distinct valid inode after it was parsed."""
        resolved = original_resolve(context_directory, relative_root)
        replacement = tmp_path / "replacement"
        replacement.write_text(
            _serialize(_binding(project_key="OTHER")),
            encoding="utf-8",
        )
        replacement.replace(context_path)
        return resolved

    monkeypatch.setattr(
        local_context,
        "_resolve_workspace_root",
        replace_during_resolution,
    )

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(tmp_path)

    assert context_path.read_text(encoding="utf-8") == _serialize(
        _binding(project_key="OTHER")
    )


@pytest.mark.parametrize(
    "start_kind",
    [
        "missing",
        "file",
        "string",
    ],
)
def test_discovery_runtime_validates_start_directory(
    start_kind: str,
    tmp_path: Path,
) -> None:
    """Discovery accepts only an existing directory Path."""
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    starts: dict[str, object] = {
        "missing": tmp_path / "missing",
        "file": file_path,
        "string": str(tmp_path),
    }

    with pytest.raises(ContextInvalidError):
        discover_workspace_context(cast("Path", starts[start_kind]))


@pytest.mark.parametrize(
    ("binding", "context_file", "workspace_root"),
    [
        (
            cast("WorkspaceBinding", object()),
            Path("/workspace/.workaholic.env"),
            Path("/workspace"),
        ),
        (_binding(), Path("relative/.workaholic.env"), Path("/workspace")),
        (_binding(), Path("/workspace/context"), Path("/workspace")),
        (_binding(), Path("/workspace/.workaholic.env"), Path("relative")),
        (
            _binding(),
            Path("/workspace/.workaholic.env"),
            Path("/outside"),
        ),
    ],
)
def test_discovered_workspace_validates_complete_result(
    binding: WorkspaceBinding,
    context_file: Path,
    workspace_root: Path,
) -> None:
    """Direct result construction cannot bypass discovery invariants."""
    with pytest.raises(ContextInvalidError):
        DiscoveredWorkspace(
            binding=binding,
            context_file=context_file,
            workspace_root=workspace_root,
        )
