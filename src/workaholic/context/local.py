"""Strict Workspace context parsing, discovery, and durable writing."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from workaholic.application import WorkspaceBindingConflictError
from workaholic.context._files import (
    RegularFileSnapshot,
    UnsafeDataFileError,
    read_bounded_regular_file,
    read_bounded_regular_file_snapshot,
)
from workaholic.context.errors import (
    ContextInvalidError,
    ContextNotFoundError,
    ContextStorageError,
)
from workaholic.context.models import DiscoveredWorkspace
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


class _WorkspaceContextConflictError(ContextInvalidError):
    """Signal one valid conflicting binding inside the context boundary."""

    def __init__(self) -> None:
        """Initialize the internal Phase 1-compatible context failure."""
        super().__init__("The current directory is already bound to different context.")


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
    binding, _metadata = _read_context_file(context_path)
    return binding


def discover_workspace_context(start: Path) -> DiscoveredWorkspace:
    """Discover the nearest authoritative context through physical ancestors.

    Args:
        start: Existing directory from which canonical discovery begins.

    Returns:
        Validated binding, physical context source, and contained Workspace root.

    Raises:
        ContextNotFoundError: If no physical ancestor contains context.
        ContextInvalidError: If the start, nearest context, or root is invalid.
        ContextStorageError: If a context file or ancestor cannot be inspected.

    """
    current = _canonical_directory(start)
    while True:
        context_path = current / CONTEXT_FILENAME
        try:
            binding, metadata = _read_context_file(context_path)
        except ContextNotFoundError:
            parent = current.parent
            if parent == current:
                raise
            current = parent
            continue
        workspace_root = _resolve_workspace_root(current, binding.workspace_root)
        _require_unchanged_regular_file(context_path, metadata)
        return DiscoveredWorkspace(
            binding=binding,
            context_file=context_path,
            workspace_root=workspace_root,
        )


def write_workspace_context(
    directory: Path,
    binding: WorkspaceBinding,
    *,
    replace: bool = False,
) -> Path:
    """Atomically create or safely replace one strict Workspace context.

    An equivalent valid file is an idempotent success. Replacement requires a
    conflicting file to remain the same regular-file snapshot that was parsed.

    Args:
        directory: Existing Workspace directory receiving context.
        binding: Validated relative-root binding to serialize.
        replace: Whether a conflicting valid regular context may be replaced.

    Returns:
        Canonical physical path to the context file.

    Raises:
        ContextInvalidError: If input, context, root, or replacement is unsafe.
        ContextStorageError: If a durable filesystem operation fails.

    """
    candidate_replace: object = replace
    if type(candidate_replace) is not bool:
        message = "Workspace context replace must be a boolean."
        raise ContextInvalidError(message)
    context_directory = _canonical_directory(directory)
    validated_binding = _validated_binding(binding)
    _resolve_workspace_root(context_directory, validated_binding.workspace_root)
    context_path = context_directory / CONTEXT_FILENAME

    try:
        existing, metadata = _read_context_file(context_path)
    except ContextNotFoundError:
        serialized = _serialize_context(validated_binding).encode("utf-8")
        created = _atomic_write_bytes(
            context_path,
            serialized,
            mode=0o600,
            replace_existing=False,
        )
        if created:
            return context_path
        try:
            raced_binding, _raced_metadata = _read_context_file(context_path)
        except ContextNotFoundError as error:
            message = "The Workspace context changed during creation."
            raise ContextInvalidError(message) from error
        _resolve_workspace_root(context_directory, raced_binding.workspace_root)
        if raced_binding == validated_binding:
            return context_path
        raise _WorkspaceContextConflictError from None

    _resolve_workspace_root(context_directory, existing.workspace_root)
    if existing == validated_binding:
        return context_path
    if not replace:
        raise _WorkspaceContextConflictError

    _atomic_write_bytes(
        context_path,
        _serialize_context(validated_binding).encode("utf-8"),
        mode=0o600,
        expected_destination=metadata,
    )
    return context_path


def bind_workspace_context(
    directory: Path,
    binding: WorkspaceBinding,
    *,
    replace: bool = False,
) -> Path:
    """Durably bind a Workspace before updating conventional local Git metadata.

    A failure while updating Git's local exclude leaves the authoritative
    context durable. Retrying the equivalent binding is safe and resumes the
    idempotent exclude update.

    Args:
        directory: Existing Workspace directory receiving context.
        binding: Authoritative validated Project binding.
        replace: Whether a conflicting valid context may be replaced.

    Returns:
        Canonical physical path to the durable context file.

    Raises:
        ContextInvalidError: If input or existing context is unsafe.
        ContextStorageError: If a durable filesystem operation fails.
        WorkspaceBindingConflictError: If valid context differs without replace.

    """
    try:
        context_path = write_workspace_context(
            directory,
            binding,
            replace=replace,
        )
    except _WorkspaceContextConflictError as error:
        raise WorkspaceBindingConflictError from error
    exclude_context_from_git(context_path.parent)
    return context_path


def write_current_workspace_context(
    directory: Path,
    context: WorkspaceBinding,
) -> Path:
    """Create one context in the supplied current directory without replacing.

    This cumulative compatibility entry point delegates to
    :func:`write_workspace_context`.

    Args:
        directory: Existing Workspace directory.
        context: Validated logical binding to serialize.

    Returns:
        The canonical physical context-file path.

    Raises:
        ContextInvalidError: If the target or binding violates context rules.
        ContextStorageError: If the write cannot be completed durably.

    """
    return write_workspace_context(directory, context)


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


def _canonical_directory(value: object) -> Path:
    """Resolve one existing directory to its absolute physical path.

    Args:
        value: Candidate discovery or context-write directory.

    Returns:
        Existing canonical physical directory.

    Raises:
        ContextInvalidError: If the value does not resolve to a directory.
        ContextStorageError: If operating-system inspection fails.

    """
    if not isinstance(value, Path):
        message = "The Workspace directory must be a pathlib Path."
        raise ContextInvalidError(message)
    try:
        directory = value.resolve(strict=True)
        is_directory = directory.is_dir()
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        message = "The Workspace directory must already exist."
        raise ContextInvalidError(message) from error
    except OSError as error:
        message = "The Workspace directory is unavailable."
        raise ContextStorageError(message) from error
    if not is_directory:
        message = "The Workspace directory must already exist."
        raise ContextInvalidError(message)
    return directory


def _resolve_workspace_root(
    context_directory: Path,
    relative_root: str,
) -> Path:
    """Resolve and contain one binding root beneath its physical context.

    Args:
        context_directory: Canonical directory containing the context file.
        relative_root: Domain-validated repository-relative Workspace root.

    Returns:
        Existing canonical physical Workspace directory.

    Raises:
        ContextInvalidError: If the root is missing, not a directory, or escapes.

    """
    candidate = context_directory.joinpath(*relative_root.split("/"))
    try:
        workspace_root = candidate.resolve(strict=True)
        is_directory = workspace_root.is_dir()
    except (OSError, RuntimeError) as error:
        message = "The Workspace root from context is unavailable."
        raise ContextInvalidError(message) from error
    if not is_directory:
        message = "The Workspace root from context must be a directory."
        raise ContextInvalidError(message)
    if workspace_root != context_directory and (
        context_directory not in workspace_root.parents
    ):
        message = "The Workspace root must remain within its context directory."
        raise ContextInvalidError(message)
    return workspace_root


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
    try:
        return read_bounded_regular_file(path, maximum=maximum)
    except UnsafeDataFileError as error:
        message = "The Workspace context must be one bounded regular file."
        raise ContextInvalidError(message) from error


def _read_context_file(path: Path) -> tuple[WorkspaceBinding, os.stat_result]:
    """Read and parse one exact context file with stable identity metadata.

    Args:
        path: Exact candidate context-file path.

    Returns:
        Validated binding and the metadata snapshot that supplied its bytes.

    Raises:
        ContextNotFoundError: If the exact file does not exist.
        ContextInvalidError: If the file or contents are unsafe or malformed.
        ContextStorageError: If the file cannot be inspected or read.

    """
    try:
        snapshot = _read_regular_file_snapshot(
            path,
            maximum=_CONTEXT_MAX_BYTES,
        )
    except FileNotFoundError as error:
        raise ContextNotFoundError from error
    except ContextInvalidError:
        raise
    except OSError as error:
        message = "The Workspace context could not be read."
        raise ContextStorageError(message) from error

    try:
        text = snapshot.content.decode("utf-8")
    except UnicodeDecodeError as error:
        message = "The Workspace context must be UTF-8."
        raise ContextInvalidError(message) from error
    return _parse_context(text), snapshot.metadata


def _read_regular_file_snapshot(
    path: Path,
    *,
    maximum: int,
) -> RegularFileSnapshot:
    """Read a context file through the shared stable snapshot boundary.

    Args:
        path: Exact file path.
        maximum: Maximum accepted byte count.

    Returns:
        Validated stable file snapshot.

    Raises:
        FileNotFoundError: If the path does not exist.
        ContextInvalidError: If the path is unsafe, unstable, or oversized.
        OSError: If the operating system cannot read or inspect the path.

    """
    try:
        return read_bounded_regular_file_snapshot(path, maximum=maximum)
    except UnsafeDataFileError as error:
        message = "The Workspace context must be one bounded regular file."
        raise ContextInvalidError(message) from error


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
    try:
        return WorkspaceBinding(
            context_version=1,
            profile=values["WORKAHOLIC_PROFILE"],
            instance_id=InstanceId(values["WORKAHOLIC_INSTANCE_ID"]),
            project_id=ProjectId(values["WORKAHOLIC_PROJECT_ID"]),
            project_key=values["WORKAHOLIC_PROJECT_KEY"],
            workspace_root=values["WORKAHOLIC_WORKSPACE_ROOT"],
        )
    except ValueError as error:
        message = "Workspace context identifiers are invalid."
        raise ContextInvalidError(message) from error


def _validated_binding(value: object) -> WorkspaceBinding:
    """Reconstruct and validate a binding before serialization.

    Args:
        value: Candidate binding.

    Returns:
        A fresh validated cumulative binding.

    Raises:
        ContextInvalidError: If the value violates the cumulative context contract.

    """
    if not isinstance(value, WorkspaceBinding):
        message = "Workspace context must be a WorkspaceBinding."
        raise ContextInvalidError(message)
    try:
        return WorkspaceBinding(
            context_version=value.context_version,
            profile=value.profile,
            instance_id=value.instance_id,
            project_id=value.project_id,
            project_key=value.project_key,
            workspace_root=value.workspace_root,
        )
    except ValueError as error:
        message = "Workspace context binding is invalid."
        raise ContextInvalidError(message) from error


def _serialize_context(context: WorkspaceBinding) -> str:
    """Serialize a validated binding in canonical key order.

    Args:
        context: Validated exact-directory binding.

    Returns:
        Canonical UTF-8-compatible text with LF endings.

    """
    return (
        "WORKAHOLIC_CONTEXT_VERSION=1\n"
        f"WORKAHOLIC_PROFILE={context.profile}\n"
        f"WORKAHOLIC_INSTANCE_ID={context.instance_id}\n"
        f"WORKAHOLIC_PROJECT_ID={context.project_id}\n"
        f"WORKAHOLIC_PROJECT_KEY={context.project_key}\n"
        f"WORKAHOLIC_WORKSPACE_ROOT={context.workspace_root}\n"
    )


def _atomic_write_bytes(
    path: Path,
    content: bytes,
    *,
    mode: int,
    replace_existing: bool = True,
    expected_destination: os.stat_result | None = None,
) -> bool:
    """Atomically publish one local file after flushing its contents.

    Args:
        path: Exact destination file.
        content: Complete replacement bytes.
        mode: Permission bits for the replacement.
        replace_existing: Whether an existing destination may be replaced.
        expected_destination: Validated destination snapshot required before
            replacement.

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
            if expected_destination is not None:
                _require_unchanged_regular_file(path, expected_destination)
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


def _require_unchanged_regular_file(
    path: Path,
    expected: os.stat_result,
) -> None:
    """Require the destination to match its parsed regular-file snapshot.

    Args:
        path: Exact context path about to be atomically replaced.
        expected: Metadata captured from the bytes that passed strict parsing.

    Raises:
        ContextInvalidError: If the destination is missing, unsafe, or changed.

    """
    try:
        current = path.lstat()
    except OSError as error:
        message = "The Workspace context changed after it was read."
        raise ContextInvalidError(message) from error
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or not os.path.samestat(expected, current)
        or expected.st_size != current.st_size
        or expected.st_mtime_ns != current.st_mtime_ns
        or expected.st_ctime_ns != current.st_ctime_ns
    ):
        message = "The Workspace context changed after it was read."
        raise ContextInvalidError(message)


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
