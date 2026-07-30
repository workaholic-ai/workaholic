"""Shared non-following reads for bounded trusted and untrusted data files."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeDataFileError(Exception):
    """Report a path that is not one stable bounded regular data file."""


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
    descriptor = os.open(candidate_path, flags | no_follow)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(initial, opened):
            raise UnsafeDataFileError
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum:
        raise UnsafeDataFileError
    return content
