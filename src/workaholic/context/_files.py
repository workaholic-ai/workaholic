"""Compatibility exports for the shared stable data-file read boundary."""

from workaholic.auth._files import (
    RegularFileSnapshot,
    UnsafeDataFileError,
    read_bounded_regular_file,
    read_bounded_regular_file_snapshot,
)

__all__ = [
    "RegularFileSnapshot",
    "UnsafeDataFileError",
    "read_bounded_regular_file",
    "read_bounded_regular_file_snapshot",
]
