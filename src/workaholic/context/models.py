"""Validated local filesystem locations owned by the context boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workaholic.context.errors import ContextInvalidError


@dataclass(frozen=True, slots=True)
class LocalDataPaths:
    """Absolute user data and default SQLite paths for local operation."""

    data_directory: Path
    database_path: Path

    def __post_init__(self) -> None:
        """Validate absolute and internally consistent local paths."""
        data_directory: object = self.data_directory
        database_path: object = self.database_path
        if not isinstance(data_directory, Path) or not isinstance(database_path, Path):
            message = "Local data paths must be pathlib Paths."
            raise ContextInvalidError(message)
        if not data_directory.is_absolute() or not database_path.is_absolute():
            message = "Local data paths must be absolute."
            raise ContextInvalidError(message)
        if database_path != data_directory / "local.db":
            message = "The local database must be named local.db in the data directory."
            raise ContextInvalidError(message)
