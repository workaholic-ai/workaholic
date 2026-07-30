"""SQLite selection of the sole trusted Phase 1 bootstrap Human."""

from __future__ import annotations

from pathlib import Path

from workaholic.application import PermissionDeniedError
from workaholic.domain import SubjectId, WorkspaceBinding
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError


class SQLiteLocalActorSelector:
    """Select the one enabled local Human administrator from SQLite."""

    def __init__(self, database_path: Path) -> None:
        """Bind actor selection to one absolute local database path.

        Args:
            database_path: Absolute Phase 1 SQLite store path.

        Raises:
            TypeError: If the path is not an absolute Path.

        """
        candidate_path: object = database_path
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            message = "SQLite actor selector database_path must be an absolute Path."
            raise TypeError(message)
        self._database_path = candidate_path

    def select(self, binding: WorkspaceBinding) -> SubjectId:
        """Select the sole enabled bootstrap Human for local operation.

        Phase 1 has one Instance and one bootstrap Human. Project and Instance
        identities remain untrusted context and are verified by the subsequent
        authorized status query, not used to choose an identity.

        Args:
            binding: Validated exact-directory Workspace binding.

        Returns:
            The sole enabled Human Instance administrator identity.

        Raises:
            PermissionDeniedError: If no unique active local Human exists.
            SchemaUnsupportedError: If the local store is missing or unsupported.
            StorageBusyError: If bounded SQLite access remains busy.
            StorageUnavailableError: If persisted identity data is malformed.

        """
        candidate_binding: object = binding
        if not isinstance(candidate_binding, WorkspaceBinding):
            raise PermissionDeniedError
        with open_read_connection(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM subjects
                WHERE kind = 'human'
                  AND enabled = 1
                  AND is_instance_admin = 1
                ORDER BY id
                LIMIT 2
                """
            ).fetchall()
        if len(rows) != 1:
            raise PermissionDeniedError
        try:
            return SubjectId(rows[0][0])
        except (IndexError, TypeError, ValueError) as error:
            raise StorageUnavailableError from error
