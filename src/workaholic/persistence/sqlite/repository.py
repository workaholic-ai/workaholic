"""Stable SQLite repository façade over focused semantic operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite._bootstrap import (
    bootstrap_local_project as _bootstrap_local_project,
)

if TYPE_CHECKING:
    from workaholic.application import BootstrapMutation, BootstrapResult


class SQLitePhaseOneRepository:
    """SQLite implementation of atomic Phase 1 semantic operations."""

    def __init__(self, database_path: Path) -> None:
        """Bind the adapter to one absolute local database path.

        Args:
            database_path: Absolute path to a schema-version-1 SQLite store.

        Raises:
            TypeError: If the value is not an absolute Path.

        """
        candidate_path: object = database_path
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            message = "SQLite repository database_path must be an absolute Path."
            raise TypeError(message)
        self._database_path = candidate_path

    @property
    def database_path(self) -> Path:
        """Return the immutable configured database path.

        Returns:
            Absolute SQLite database path.

        """
        return self._database_path

    def bootstrap_local_project(
        self,
        mutation: BootstrapMutation,
    ) -> BootstrapResult:
        """Atomically bootstrap or locate the single local Project.

        Args:
            mutation: Validated candidate identities and semantic input.

        Returns:
            The committed local identity and Owner authorization graph.

        """
        return _bootstrap_local_project(self._database_path, mutation)
