"""Unit tests for hostile exact-directory Workspace context files."""

from __future__ import annotations

import os
import stat
import subprocess
import urllib.request
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import ApplicationErrorCode
from workaholic.context import (
    CONTEXT_FILENAME,
    ContextInvalidError,
    ContextNotFoundError,
    ContextStorageError,
    exclude_context_from_git,
    read_current_workspace_context,
    write_current_workspace_context,
    write_workspace_context,
)
from workaholic.context import local as local_context
from workaholic.domain import InstanceId, ProjectId, WorkspaceBinding

if TYPE_CHECKING:
    from pathlib import Path

_CANONICAL_CONTEXT = (
    "WORKAHOLIC_CONTEXT_VERSION=1\n"
    "WORKAHOLIC_PROFILE=local\n"
    "WORKAHOLIC_INSTANCE_ID=ins_local\n"
    "WORKAHOLIC_PROJECT_ID=prj_acme\n"
    "WORKAHOLIC_PROJECT_KEY=ACME\n"
    "WORKAHOLIC_WORKSPACE_ROOT=.\n"
)


def _binding(
    *,
    project_key: str = "ACME",
    profile: str = "local",
    workspace_root: str = ".",
) -> WorkspaceBinding:
    """Build one valid local Workspace binding.

    Args:
        project_key: Project key override for conflict tests.
        profile: Trusted profile name to serialize.
        workspace_root: Relative Workspace root from the context directory.

    Returns:
        A valid exact-directory binding.

    """
    return WorkspaceBinding(
        context_version=1,
        profile=profile,
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        project_key=project_key,
        workspace_root=workspace_root,
    )


def _write_text(directory: Path, content: str) -> Path:
    """Write raw hostile-test content into an exact context file.

    Args:
        directory: Existing test Workspace.
        content: Raw text under test.

    Returns:
        The written context path.

    """
    path = directory / CONTEXT_FILENAME
    path.write_text(content, encoding="utf-8", newline="")
    return path


def test_read_valid_context_returns_dependency_free_binding(tmp_path: Path) -> None:
    """All six canonical keys deserialize into the domain value object."""
    _write_text(tmp_path, f"\n{_CANONICAL_CONTEXT}")

    assert read_current_workspace_context(tmp_path) == _binding()


def test_read_operating_system_failure_is_mapped_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Filesystem driver details do not cross the context boundary."""

    def fail_read(_path: Path, *, maximum: int) -> bytes:
        """Simulate an unavailable context file after path validation."""
        assert maximum > 0
        message = "private filesystem failure"
        raise OSError(message)

    monkeypatch.setattr(local_context, "_read_regular_file_snapshot", fail_read)

    with pytest.raises(ContextStorageError) as captured:
        read_current_workspace_context(tmp_path)

    assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
    assert "private filesystem failure" not in captured.value.safe_message


def test_read_does_not_search_parent_directories(tmp_path: Path) -> None:
    """A parent binding never applies to a nested working directory."""
    child = tmp_path / "child"
    child.mkdir()
    _write_text(tmp_path, _CANONICAL_CONTEXT)

    with pytest.raises(ContextNotFoundError) as captured:
        read_current_workspace_context(child)

    assert captured.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND


def test_write_is_canonical_private_and_round_trips(tmp_path: Path) -> None:
    """A new context is UTF-8, LF-only, private, and readable."""
    context_path = write_current_workspace_context(tmp_path, _binding())

    assert context_path == tmp_path / CONTEXT_FILENAME
    assert context_path.read_bytes() == _CANONICAL_CONTEXT.encode()
    assert stat.S_IMODE(context_path.stat().st_mode) == 0o600
    assert read_current_workspace_context(tmp_path) == _binding()


def test_phase_two_profile_and_relative_root_round_trip(tmp_path: Path) -> None:
    """The strict format carries validated profile names and relative roots."""
    workspace_root = tmp_path / "apps" / "api"
    workspace_root.mkdir(parents=True)
    binding = _binding(profile="team_1", workspace_root="apps/api")

    context_path = write_workspace_context(tmp_path, binding)

    assert read_current_workspace_context(tmp_path) == binding
    assert context_path.read_text(encoding="utf-8").endswith(
        "WORKAHOLIC_WORKSPACE_ROOT=apps/api\n"
    )


def test_equivalent_existing_context_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful retry returns an equivalent context without another write."""
    context_path = _write_text(tmp_path, _CANONICAL_CONTEXT)

    def fail_write(*_arguments: object, **_keywords: object) -> None:
        """Fail if idempotent context creation attempts filesystem mutation."""
        pytest.fail("equivalent context must not be rewritten")

    monkeypatch.setattr(local_context, "_atomic_write_bytes", fail_write)

    assert write_current_workspace_context(tmp_path, _binding()) == context_path


def test_existing_conflicting_context_is_never_overwritten(tmp_path: Path) -> None:
    """Binding an already-bound directory to a different Project fails safely."""
    context_path = _write_text(
        tmp_path,
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME",
            "WORKAHOLIC_PROJECT_KEY=OTHER",
        ),
    )
    original = context_path.read_bytes()

    with pytest.raises(ContextInvalidError):
        write_current_workspace_context(tmp_path, _binding())

    assert context_path.read_bytes() == original


def test_existing_malformed_context_is_never_overwritten(tmp_path: Path) -> None:
    """Malformed repository data remains untouched for explicit recovery."""
    context_path = _write_text(tmp_path, "malformed")

    with pytest.raises(ContextInvalidError):
        write_current_workspace_context(tmp_path, _binding())

    assert context_path.read_text(encoding="utf-8") == "malformed"


def test_racing_conflicting_context_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A competing initializer cannot be clobbered after the preflight check."""
    competing = _CANONICAL_CONTEXT.replace(
        "WORKAHOLIC_PROJECT_KEY=ACME",
        "WORKAHOLIC_PROJECT_KEY=OTHER",
    )

    def simulate_lost_race(
        path: Path,
        _content: bytes,
        *,
        mode: int,
        replace_existing: bool = True,
    ) -> bool:
        """Install competing context at the no-clobber publication boundary."""
        assert mode == 0o600
        assert not replace_existing
        path.write_text(competing, encoding="utf-8", newline="")
        return False

    monkeypatch.setattr(
        local_context,
        "_atomic_write_bytes",
        simulate_lost_race,
    )

    with pytest.raises(ContextInvalidError):
        write_current_workspace_context(tmp_path, _binding())

    assert (tmp_path / CONTEXT_FILENAME).read_text(encoding="utf-8") == competing


def test_atomic_no_clobber_publish_preserves_existing_file(tmp_path: Path) -> None:
    """The low-level publication primitive reports a lost race without mutation."""
    destination = tmp_path / "metadata"
    destination.write_bytes(b"winner")

    created = local_context._atomic_write_bytes(
        destination,
        b"loser",
        mode=0o600,
        replace_existing=False,
    )

    assert not created
    assert destination.read_bytes() == b"winner"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize(
    "content",
    [
        _CANONICAL_CONTEXT + "WORKAHOLIC_TOKEN=secret\n",
        _CANONICAL_CONTEXT + "DATABASE_URL=https://attacker.invalid\n",
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME\n",
            "WORKAHOLIC_PROJECT_KEY=ACME\nWORKAHOLIC_PROJECT_KEY=OTHER\n",
        ),
        _CANONICAL_CONTEXT.replace("WORKAHOLIC_PROFILE=local\n", ""),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROFILE=local",
            "WORKAHOLIC_PROFILE",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_CONTEXT_VERSION=1",
            "WORKAHOLIC_CONTEXT_VERSION=2",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_WORKSPACE_ROOT=.",
            "WORKAHOLIC_WORKSPACE_ROOT=..",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_INSTANCE_ID=ins_local",
            "WORKAHOLIC_INSTANCE_ID=prj_wrong",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_ID=prj_acme",
            "WORKAHOLIC_PROJECT_ID=ins_wrong",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME",
            "WORKAHOLIC_PROJECT_KEY=acme",
        ),
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME",
            "WORKAHOLIC_PROJECT_KEY=ACME\x01",
        ),
    ],
)
def test_strict_parser_rejects_noncanonical_or_untrusted_data(
    content: str,
    tmp_path: Path,
) -> None:
    """Unknown, incomplete, inconsistent, or unsupported context is rejected."""
    _write_text(tmp_path, content)

    with pytest.raises(ContextInvalidError) as captured:
        read_current_workspace_context(tmp_path)

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID


@pytest.mark.parametrize("marker", ["$(touch ignored)", "${HOME}", "`id`"])
def test_command_substitution_text_is_data_and_is_rejected(
    marker: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rejecting shell syntax never delegates to a command processor."""

    def fail_process(*_arguments: object, **_keywords: object) -> object:
        """Fail if hostile context reaches an operating-system command API."""
        pytest.fail("context parsing must not execute commands")

    monkeypatch.setattr(os, "system", fail_process)
    monkeypatch.setattr(subprocess, "run", fail_process)
    content = _CANONICAL_CONTEXT.replace(
        "WORKAHOLIC_PROJECT_KEY=ACME",
        f"WORKAHOLIC_PROJECT_KEY={marker}",
    )
    _write_text(tmp_path, content)

    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)


def test_endpoint_text_is_rejected_without_network_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An endpoint-like unknown key cannot trigger URL handling."""

    def fail_urlopen(*_arguments: object, **_keywords: object) -> object:
        """Fail if context parsing attempts a network request."""
        pytest.fail("context parsing must not contact network endpoints")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    _write_text(
        tmp_path,
        _CANONICAL_CONTEXT + "WORKAHOLIC_ENDPOINT=https://attacker.invalid\n",
    )

    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)


def test_invalid_utf8_and_oversized_context_are_rejected(tmp_path: Path) -> None:
    """The bounded reader rejects undecodable and unreasonably large files."""
    context_path = tmp_path / CONTEXT_FILENAME
    context_path.write_bytes(b"\xff")
    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)

    context_path.write_bytes(b"A" * (16 * 1_024 + 1))
    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)


def test_symlink_and_non_file_context_are_rejected(tmp_path: Path) -> None:
    """Repository content cannot redirect reads through a context symlink."""
    target = tmp_path / "target"
    target.write_text(_CANONICAL_CONTEXT, encoding="utf-8")
    context_path = tmp_path / CONTEXT_FILENAME
    context_path.symlink_to(target)

    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)

    context_path.unlink()
    context_path.mkdir()
    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)


def test_file_identity_change_during_open_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A path-swap indication aborts the bounded read."""
    _write_text(tmp_path, _CANONICAL_CONTEXT)
    monkeypatch.setattr(
        "workaholic.context.local.os.path.samestat",
        lambda *_: False,
    )

    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path)


def test_workspace_directory_runtime_validation(tmp_path: Path) -> None:
    """Public functions require an existing pathlib directory at runtime."""
    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(cast("Path", str(tmp_path)))
    with pytest.raises(ContextInvalidError):
        read_current_workspace_context(tmp_path / "missing")


def test_context_can_be_addressed_through_workspace_directory_symlink(
    tmp_path: Path,
) -> None:
    """An explicitly supplied directory symlink remains an exact directory."""
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    _write_text(real_directory, _CANONICAL_CONTEXT)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    assert read_current_workspace_context(linked_directory) == _binding()


def test_write_failure_is_mapped_and_leaves_no_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unavailable target produces the stable storage failure contract."""

    def fail_create(*_arguments: object, **_keywords: object) -> tuple[int, str]:
        """Simulate an unwritable target without depending on process privileges."""
        message = "private permission detail"
        raise PermissionError(message)

    monkeypatch.setattr(
        "workaholic.context.local.tempfile.mkstemp",
        fail_create,
    )

    with pytest.raises(ContextStorageError) as captured:
        write_current_workspace_context(tmp_path, _binding())

    assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
    assert "private permission detail" not in captured.value.safe_message
    assert not (tmp_path / CONTEXT_FILENAME).exists()


def test_failed_write_removes_its_private_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mid-write failure leaves neither final nor temporary metadata."""

    def fail_permissions(_descriptor: int, _mode: int) -> None:
        """Simulate failure after a temporary file has been allocated."""
        message = "private permission detail"
        raise PermissionError(message)

    monkeypatch.setattr(
        "workaholic.context.local.os.fchmod",
        fail_permissions,
    )

    with pytest.raises(ContextStorageError):
        write_current_workspace_context(tmp_path, _binding())

    assert list(tmp_path.iterdir()) == []


def test_writer_runtime_validates_the_binding_type(tmp_path: Path) -> None:
    """Serialization rejects callers that violate its annotated interface."""
    with pytest.raises(ContextInvalidError):
        write_current_workspace_context(tmp_path, cast("WorkspaceBinding", object()))


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("context_version", 2),
        ("profile", "INVALID"),
        ("workspace_root", ".."),
    ],
)
def test_writer_defends_against_compromised_binding_instances(
    field_name: str,
    invalid_value: object,
    tmp_path: Path,
) -> None:
    """Boundary validation rejects a low-level mutation of a frozen value."""
    binding = _binding()
    object.__setattr__(binding, field_name, invalid_value)

    with pytest.raises(ContextInvalidError):
        write_current_workspace_context(tmp_path, binding)


def test_replace_updates_only_a_valid_conflicting_context(tmp_path: Path) -> None:
    """Explicit replacement atomically publishes a private validated binding."""
    _write_text(
        tmp_path,
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME",
            "WORKAHOLIC_PROJECT_KEY=OTHER",
        ),
    )

    context_path = write_workspace_context(tmp_path, _binding(), replace=True)

    assert read_current_workspace_context(tmp_path) == _binding()
    assert stat.S_IMODE(context_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "existing_shape",
    ["malformed", "symlink", "directory", "invalid-root"],
)
def test_replace_never_overwrites_unsafe_context(
    existing_shape: str,
    tmp_path: Path,
) -> None:
    """Replacement fails closed unless the existing file parsed safely."""
    context_path = tmp_path / CONTEXT_FILENAME
    if existing_shape == "malformed":
        context_path.write_text("malformed", encoding="utf-8")
    elif existing_shape == "symlink":
        target = tmp_path / "target"
        target.write_text(_CANONICAL_CONTEXT, encoding="utf-8")
        context_path.symlink_to(target)
    elif existing_shape == "invalid-root":
        context_path.write_text(
            _CANONICAL_CONTEXT.replace(
                "WORKAHOLIC_WORKSPACE_ROOT=.",
                "WORKAHOLIC_WORKSPACE_ROOT=missing",
            ),
            encoding="utf-8",
        )
    else:
        context_path.mkdir()

    with pytest.raises(ContextInvalidError):
        write_workspace_context(tmp_path, _binding(), replace=True)

    if existing_shape in {"malformed", "invalid-root"}:
        assert context_path.is_file()
    elif existing_shape == "directory":
        assert context_path.is_dir()
    else:
        assert context_path.is_symlink()


def test_concurrent_replacement_aborts_validated_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A destination identity change after parsing is never overwritten."""
    context_path = _write_text(
        tmp_path,
        _CANONICAL_CONTEXT.replace(
            "WORKAHOLIC_PROJECT_KEY=ACME",
            "WORKAHOLIC_PROJECT_KEY=OTHER",
        ),
    )
    competing = _CANONICAL_CONTEXT.replace(
        "WORKAHOLIC_PROJECT_KEY=ACME",
        "WORKAHOLIC_PROJECT_KEY=THIRD",
    )
    original_check = local_context._require_unchanged_regular_file

    def replace_before_check(path: Path, expected: os.stat_result) -> None:
        """Install a competing inode immediately before the identity check."""
        replacement = tmp_path / "competing"
        replacement.write_text(competing, encoding="utf-8", newline="")
        replacement.replace(path)
        original_check(path, expected)

    monkeypatch.setattr(
        local_context,
        "_require_unchanged_regular_file",
        replace_before_check,
    )

    with pytest.raises(ContextInvalidError):
        write_workspace_context(tmp_path, _binding(), replace=True)

    assert context_path.read_text(encoding="utf-8") == competing
    assert not any(
        path.name.startswith(f".{CONTEXT_FILENAME}.") for path in tmp_path.iterdir()
    )


def test_writer_rejects_non_boolean_replace_and_escaping_root(
    tmp_path: Path,
) -> None:
    """Runtime flags and physical root containment fail closed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContextInvalidError):
        write_workspace_context(
            workspace,
            _binding(workspace_root="linked"),
            replace=False,
        )
    with pytest.raises(ContextInvalidError):
        write_workspace_context(
            workspace,
            _binding(),
            replace=cast("bool", 1),
        )

    assert not (workspace / CONTEXT_FILENAME).exists()


def test_git_exclude_is_local_preserved_and_idempotent(tmp_path: Path) -> None:
    """Only conventional local Git exclude gains one context pattern."""
    info_directory = tmp_path / ".git" / "info"
    info_directory.mkdir(parents=True)
    exclude = info_directory / "exclude"
    exclude.write_text("existing-pattern", encoding="utf-8")
    exclude.chmod(0o640)
    shared_ignore = tmp_path / ".gitignore"
    shared_ignore.write_text("shared\n", encoding="utf-8")

    exclude_context_from_git(tmp_path)
    exclude_context_from_git(tmp_path)

    assert exclude.read_text(encoding="utf-8") == (
        "existing-pattern\n.workaholic.env\n"
    )
    assert stat.S_IMODE(exclude.stat().st_mode) == 0o640
    assert shared_ignore.read_text(encoding="utf-8") == "shared\n"


@pytest.mark.parametrize("git_shape", ["missing", "file", "symlink"])
def test_nonconventional_git_metadata_is_a_noop(
    git_shape: str,
    tmp_path: Path,
) -> None:
    """Non-Git directories and redirecting Git metadata remain untouched."""
    git_path = tmp_path / ".git"
    if git_shape == "file":
        git_path.write_text("gitdir: elsewhere", encoding="utf-8")
    elif git_shape == "symlink":
        target = tmp_path / "elsewhere"
        target.mkdir()
        git_path.symlink_to(target, target_is_directory=True)

    exclude_context_from_git(tmp_path)

    assert not (tmp_path / ".git" / "info" / "exclude").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_new_git_exclude_is_private(tmp_path: Path) -> None:
    """A newly created local exclude uses owner-only permissions."""
    (tmp_path / ".git").mkdir()

    exclude_context_from_git(tmp_path)

    exclude = tmp_path / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8") == ".workaholic.env\n"
    assert stat.S_IMODE(exclude.stat().st_mode) == 0o600


def test_symlinked_git_info_directory_is_rejected(tmp_path: Path) -> None:
    """Conventional Git metadata cannot redirect its info directory."""
    (tmp_path / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".git" / "info").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ContextStorageError):
        exclude_context_from_git(tmp_path)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"A" * (1_024 * 1_024 + 1),
    ],
)
def test_invalid_or_oversized_git_exclude_is_rejected(
    content: bytes,
    tmp_path: Path,
) -> None:
    """Existing local exclude data must be bounded valid UTF-8."""
    info_directory = tmp_path / ".git" / "info"
    info_directory.mkdir(parents=True)
    (info_directory / "exclude").write_bytes(content)

    with pytest.raises(ContextStorageError):
        exclude_context_from_git(tmp_path)


def test_unsafe_git_exclude_is_rejected(tmp_path: Path) -> None:
    """A symlinked exclude cannot redirect a repository-local write."""
    info_directory = tmp_path / ".git" / "info"
    info_directory.mkdir(parents=True)
    target = tmp_path / "outside"
    target.write_text("preserved\n", encoding="utf-8")
    (info_directory / "exclude").symlink_to(target)

    with pytest.raises(ContextStorageError):
        exclude_context_from_git(tmp_path)

    assert target.read_text(encoding="utf-8") == "preserved\n"
