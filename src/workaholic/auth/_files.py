"""Shared stable bounded reads for security-sensitive local data files."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class UnsafeDataFileError(Exception):
    """Report a path that is not one stable bounded regular data file."""


@dataclass(frozen=True, slots=True)
class RegularFileSnapshot:
    """Contents and identity metadata from one stable bounded file read."""

    content: bytes
    metadata: os.stat_result


def read_bounded_regular_file(path: Path, *, maximum: int) -> bytes:
    """Read one bounded regular file without following its final symlink.

    Args:
        path: Exact file path to inspect and read.
        maximum: Maximum accepted byte count.

    Returns:
        File contents up to the accepted bound.

    Raises:
        FileNotFoundError: If the path does not exist.
        UnsafeDataFileError: If the path is unsafe, unstable, or oversized.
        OSError: If the operating system cannot read or inspect the path.

    """
    return read_bounded_regular_file_snapshot(path, maximum=maximum).content


def read_bounded_regular_file_snapshot(
    path: Path,
    *,
    maximum: int,
) -> RegularFileSnapshot:
    """Read one stable bounded regular file and preserve validated identity.

    Args:
        path: Exact file path to inspect and read.
        maximum: Maximum accepted byte count.

    Returns:
        Complete contents and final descriptor metadata from the stable read.

    Raises:
        FileNotFoundError: If the path does not exist.
        UnsafeDataFileError: If the path is unsafe, unstable, or oversized.
        OSError: If the operating system cannot read or inspect the path.

    """
    candidate_path: object = path
    if not isinstance(candidate_path, Path):
        message = "Data file path must be a pathlib Path."
        raise TypeError(message)
    if type(maximum) is not int or maximum < 1:
        message = "Data file size limit must be a positive integer."
        raise ValueError(message)

    initial = candidate_path.lstat()
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise UnsafeDataFileError

    flags = os.O_RDONLY
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate_path, flags | no_follow)
    except FileNotFoundError as error:
        raise UnsafeDataFileError from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_state(initial, opened):
            raise UnsafeDataFileError
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
        completed = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(content) > maximum or not _same_file_state(opened, completed):
        raise UnsafeDataFileError
    return RegularFileSnapshot(content=content, metadata=completed)


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    """Return whether two snapshots describe one unchanged file state.

    Args:
        left: Earlier file metadata.
        right: Later file metadata.

    Returns:
        ``True`` only when identity, size, and change timestamps match.

    """
    return (
        os.path.samestat(left, right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )
