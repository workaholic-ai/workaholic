"""Strict exact-directory Workspace context parsing and durable writing."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from workaholic.context.errors import (
    ContextInvalidError,
    ContextNotFoundError,
    ContextStorageError,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    WorkspaceBinding,
)

CONTEXT_FILENAME = ".workaholic.env"
_CONTEXT_MAX_BYTES = 16 * 1_024
_GIT_EXCLUDE_MAX_BYTES = 1_024 * 1_024
_GIT_EXCLUDE_PATTERN = ".workaholic.env"
_EXPECTED_KEYS = (
    "WORKAHOLIC_CONTEXT_VERSION",
    "WORKAHOLIC_PROFILE",
    "WORKAHOLIC_INSTANCE_ID",
    "WORKAHOLIC_PROJECT_ID",
    "WORKAHOLIC_PROJECT_KEY",
    "WORKAHOLIC_WORKSPACE_ROOT",
)
_SHELL_EXPANSION_MARKERS = ("$(", "${", "`")


def read_current_workspace_context(directory: Path) -> WorkspaceBinding:
    """Read only the exact current directory's strict context file.

    Args:
        directory: Directory treated as the current Workspace.

    Returns:
        The validated dependency-free Workspace binding.

    Raises:
        ContextNotFoundError: If the exact directory has no context file.
        ContextInvalidError: If the file is unsafe, malformed, or unsupported.
        ContextStorageError: If the file cannot be read.

    """
    context_path = _context_path(directory)
    try:
        content = _read_regular_file(context_path, maximum=_CONTEXT_MAX_BYTES)
    except FileNotFoundError as error:
        raise ContextNotFoundError from error
    except ContextInvalidError:
        raise
    except OSError as error:
        message = "The Workspace context could not be read."
        raise ContextStorageError(message) from error

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        message = "The Workspace context must be UTF-8."
        raise ContextInvalidError(message) from error
    return _parse_context(text)


def write_current_workspace_context(
    directory: Path,
    context: WorkspaceBinding,
) -> Path:
    """Atomically write one strict context file in the exact directory.

    Existing equivalent context is returned without a rewrite. Existing
    conflicting or malformed context is never overwritten.

    Args:
        directory: Existing Workspace directory.
        context: Validated logical binding to serialize.

    Returns:
        The exact context-file path.

    Raises:
        ContextInvalidError: If the target or binding violates Phase 1 rules.
        ContextStorageError: If the write cannot be completed durably.

    """
    context_path = _context_path(directory)
    _require_phase_one_binding(context)
    if context_path.exists() or context_path.is_symlink():
        existing = read_current_workspace_context(directory)
        if existing != context:
            message = "The current directory is already bound to different context."
            raise ContextInvalidError(message)
        return context_path

    serialized = _serialize_context(context).encode("utf-8")
    created = _atomic_write_bytes(
        context_path,
        serialized,
        mode=0o600,
        replace_existing=False,
    )
    if not created:
        existing = read_current_workspace_context(directory)
        if existing != context:
            message = "The current directory is already bound to different context."
            raise ContextInvalidError(message)
    return context_path


def exclude_context_from_git(directory: Path) -> None:
    """Add the context filename only to a conventional local Git exclude.

    Git worktree indirection files and symlinked Git metadata are deliberately
    ignored so repository content cannot redirect a local metadata write.

    Args:
        directory: Workspace root expected to contain a conventional ``.git``.

    Raises:
        ContextInvalidError: If ``directory`` is not a valid directory Path.
        ContextStorageError: If a conventional exclude exists but cannot be updated.

    """
    workspace = _require_directory(directory)
    git_directory = workspace / ".git"
    try:
        git_metadata = git_directory.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        message = "Local Git metadata could not be inspected."
        raise ContextStorageError(message) from error
    if stat.S_ISLNK(git_metadata.st_mode) or not stat.S_ISDIR(git_metadata.st_mode):
        return

    info_directory = git_directory / "info"
    try:
        if info_directory.is_symlink():
            message = "The local Git info directory is unsafe."
            raise ContextStorageError(message)
        info_directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        message = "The local Git info directory is unavailable."
        raise ContextStorageError(message) from error

    exclude_path = info_directory / "exclude"
    existing = b""
    mode = 0o600
    try:
        if exclude_path.exists() or exclude_path.is_symlink():
            metadata = exclude_path.lstat()
            _require_safe_git_exclude(metadata)
            try:
                existing = _read_regular_file(
                    exclude_path,
                    maximum=_GIT_EXCLUDE_MAX_BYTES,
                )
            except ContextInvalidError as error:
                message = "The local Git exclude file is unsafe."
                raise ContextStorageError(message) from error
            mode = stat.S_IMODE(metadata.st_mode)
    except ContextStorageError:
        raise
    except OSError as error:
        message = "The local Git exclude file is unavailable."
        raise ContextStorageError(message) from error

    try:
        decoded = existing.decode("utf-8")
    except UnicodeDecodeError as error:
        message = "The local Git exclude file is not UTF-8."
        raise ContextStorageError(message) from error
    if _GIT_EXCLUDE_PATTERN in {
        line.strip() for line in decoded.splitlines() if line.strip()
    }:
        return

    separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
    updated = existing + separator + f"{_GIT_EXCLUDE_PATTERN}\n".encode()
    _atomic_write_bytes(exclude_path, updated, mode=mode)


def _context_path(directory: Path) -> Path:
    """Return the exact context path after validating the directory.

    Args:
        directory: Candidate Workspace directory.

    Returns:
        ``directory / ".workaholic.env"`` without parent traversal.

    """
    return _require_directory(directory) / CONTEXT_FILENAME


def _require_directory(value: object) -> Path:
    """Require an existing directory Path without canonicalizing symlinks.

    Args:
        value: Candidate directory.

    Returns:
        The accepted directory Path.

    Raises:
        ContextInvalidError: If the value is not an existing directory Path.

    """
    if not isinstance(value, Path):
        message = "The Workspace directory must be a pathlib Path."
        raise ContextInvalidError(message)
    try:
        is_directory = value.is_dir()
    except OSError as error:
        message = "The Workspace directory is unavailable."
        raise ContextStorageError(message) from error
    if not is_directory:
        message = "The Workspace directory must already exist."
        raise ContextInvalidError(message)
    return value


def _require_safe_git_exclude(metadata: os.stat_result) -> None:
    """Require conventional Git exclude metadata to describe a regular file.

    Args:
        metadata: Result of inspecting the exclude path without following links.

    Raises:
        ContextStorageError: If the path is a symlink or non-regular file.

    """
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        message = "The local Git exclude file is unsafe."
        raise ContextStorageError(message)


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    """Read a bounded regular file without following a symlink.

    Args:
        path: Exact file path.
        maximum: Maximum accepted byte count.

    Returns:
        File contents up to the accepted bound.

    Raises:
        FileNotFoundError: If the path does not exist.
        ContextInvalidError: If the path is a symlink, non-file, or oversized.
        OSError: If the operating system cannot read or inspect the path.

    """
    initial = path.lstat()
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        message = "The Workspace context must be a regular file."
        raise ContextInvalidError(message)

    flags = os.O_RDONLY
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | no_follow)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(initial, opened):
            message = "The Workspace context changed while opening."
            raise ContextInvalidError(message)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum:
        message = "The Workspace context is too large."
        raise ContextInvalidError(message)
    return content


def _parse_context(text: str) -> WorkspaceBinding:
    """Parse strict data-only context text.

    Args:
        text: Decoded UTF-8 context.

    Returns:
        A validated WorkspaceBinding.

    Raises:
        ContextInvalidError: If syntax, keys, or values violate the allowlist.

    """
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        if "=" not in line:
            message = f"Workspace context line {line_number} must use KEY=VALUE syntax."
            raise ContextInvalidError(message)
        key, value = line.split("=", maxsplit=1)
        if key not in _EXPECTED_KEYS:
            message = f"Workspace context line {line_number} contains an unknown key."
            raise ContextInvalidError(message)
        if key in values:
            message = f"Workspace context line {line_number} duplicates a key."
            raise ContextInvalidError(message)
        if (
            not value
            or "\x00" in value
            or any(marker in value for marker in _SHELL_EXPANSION_MARKERS)
        ):
            message = f"Workspace context line {line_number} contains an unsafe value."
            raise ContextInvalidError(message)
        if any(not character.isprintable() for character in value):
            message = (
                f"Workspace context line {line_number} contains a control character."
            )
            raise ContextInvalidError(message)
        values[key] = value

    missing = set(_EXPECTED_KEYS).difference(values)
    if missing:
        message = "Workspace context is missing required keys."
        raise ContextInvalidError(message)
    if values["WORKAHOLIC_CONTEXT_VERSION"] != "1":
        message = "Workspace context version is unsupported."
        raise ContextInvalidError(message)
    if values["WORKAHOLIC_PROFILE"] != "local":
        message = "Workspace profile must be local in Phase 1."
        raise ContextInvalidError(message)
    if values["WORKAHOLIC_WORKSPACE_ROOT"] != ".":
        message = "Workspace root must be the exact current directory in Phase 1."
        raise ContextInvalidError(message)
    try:
        return WorkspaceBinding(
            context_version=1,
            profile="local",
            instance_id=InstanceId(values["WORKAHOLIC_INSTANCE_ID"]),
            project_id=ProjectId(values["WORKAHOLIC_PROJECT_ID"]),
            project_key=values["WORKAHOLIC_PROJECT_KEY"],
            workspace_root=".",
        )
    except ValueError as error:
        message = "Workspace context identifiers are invalid."
        raise ContextInvalidError(message) from error


def _require_phase_one_binding(value: object) -> WorkspaceBinding:
    """Validate a binding before serialization.

    Args:
        value: Candidate binding.

    Returns:
        The accepted Phase 1 binding.

    Raises:
        ContextInvalidError: If the value is not exact-directory local context.

    """
    if not isinstance(value, WorkspaceBinding):
        message = "Workspace context must be a WorkspaceBinding."
        raise ContextInvalidError(message)
    if value.context_version != 1 or value.profile != "local":
        message = "Workspace context must use the Phase 1 local format."
        raise ContextInvalidError(message)
    if value.workspace_root != ".":
        message = "Workspace root must be the exact current directory in Phase 1."
        raise ContextInvalidError(message)
    return value


def _serialize_context(context: WorkspaceBinding) -> str:
    """Serialize a validated binding in canonical key order.

    Args:
        context: Validated exact-directory binding.

    Returns:
        Canonical UTF-8-compatible text with LF endings.

    """
    return (
        "WORKAHOLIC_CONTEXT_VERSION=1\n"
        "WORKAHOLIC_PROFILE=local\n"
        f"WORKAHOLIC_INSTANCE_ID={context.instance_id}\n"
        f"WORKAHOLIC_PROJECT_ID={context.project_id}\n"
        f"WORKAHOLIC_PROJECT_KEY={context.project_key}\n"
        "WORKAHOLIC_WORKSPACE_ROOT=.\n"
    )


def _atomic_write_bytes(
    path: Path,
    content: bytes,
    *,
    mode: int,
    replace_existing: bool = True,
) -> bool:
    """Atomically publish one local file after flushing its contents.

    Args:
        path: Exact destination file.
        content: Complete replacement bytes.
        mode: Permission bits for the replacement.
        replace_existing: Whether an existing destination may be replaced.

    Returns:
        ``True`` after publication, or ``False`` when no-clobber publication
        discovers an existing destination.

    Raises:
        ContextStorageError: If any durable-write step fails.

    """
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
        if replace_existing:
            temporary_path.replace(path)
        else:
            try:
                path.hardlink_to(temporary_path)
            except FileExistsError:
                return False
            temporary_path.unlink()
        temporary_path = None
        _fsync_directory(path.parent)
    except OSError as error:
        message = "A local metadata file could not be written."
        raise ContextStorageError(message) from error
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
    return True


def _fsync_directory(directory: Path) -> None:
    """Flush a containing directory where the platform supports it.

    Args:
        directory: Directory whose replacement entry must become durable.

    Raises:
        OSError: If a supported POSIX directory flush fails.

    """
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
