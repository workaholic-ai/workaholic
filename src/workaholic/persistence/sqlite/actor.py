"""SQLite selection of the sole trusted Phase 1 bootstrap Human."""

from __future__ import annotations

from pathlib import Path

from workaholic.application import NotInitializedError, PermissionDeniedError
from workaholic.domain import InstanceId, SubjectId, WorkspaceBinding
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError


class SQLiteLocalActorSelector:
    """Select the one enabled local Human administrator from SQLite."""

    def __init__(self, database_path: Path) -> None:
        """Bind actor selection to one absolute local database path.

        Args:
            database_path: Absolute Phase 2 SQLite store path.

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
        _instance_id, subject_id = self.select_local()
        return subject_id

    def select_local(self) -> tuple[InstanceId, SubjectId]:
        """Select the initialized Instance and sole active bootstrap Human.

        Returns:
            Exact trusted local Instance and Subject identities.

        Raises:
            NotInitializedError: If the profile has no initialized Instance.
            PermissionDeniedError: If no unique active local Human exists.
            StorageUnavailableError: If singleton identity state is malformed.

        """
        instance_id = self.select_instance()
        with open_read_connection(self._database_path) as connection:
            subject_rows = connection.execute(
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
        if len(subject_rows) != 1:
            raise PermissionDeniedError
        try:
            return (
                instance_id,
                SubjectId(subject_rows[0][0]),
            )
        except (IndexError, TypeError, ValueError) as error:
            raise StorageUnavailableError from error

    def select_instance(self) -> InstanceId:
        """Read only the singleton Instance identity before authentication.

        Returns:
            Exact initialized Instance identity.

        Raises:
            NotInitializedError: If no Instance exists.
            StorageUnavailableError: If singleton state is malformed.

        """
        if not self._database_path.exists():
            raise NotInitializedError
        with open_read_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT id FROM instances ORDER BY id LIMIT 2"
            ).fetchall()
        if not rows:
            raise NotInitializedError
        if len(rows) != 1:
            raise StorageUnavailableError
        try:
            return InstanceId(rows[0][0])
        except (IndexError, TypeError, ValueError) as error:
            raise StorageUnavailableError from error

    def has_tokens(self) -> bool:
        """Return whether the initialized store contains any Token row."""
        with open_read_connection(self._database_path) as connection:
            row = connection.execute("SELECT 1 FROM tokens LIMIT 1").fetchone()
        return row is not None

    def select_bootstrap_subject(
        self,
        *,
        instance_id: InstanceId,
        handle: str,
    ) -> SubjectId:
        """Select the exact bootstrap Human for explicit local recovery only.

        Args:
            instance_id: Confirmed local Instance identity.
            handle: Confirmed immutable bootstrap handle.

        Returns:
            Exact enabled Human administrator Subject identity.

        Raises:
            PermissionDeniedError: If the confirmation or durable state differs.
            StorageUnavailableError: If singleton state is malformed.

        """
        candidate_instance: object = instance_id
        candidate_handle: object = handle
        if (
            not isinstance(candidate_instance, InstanceId)
            or candidate_handle != "local-operator"
        ):
            raise PermissionDeniedError
        with open_read_connection(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM subjects
                WHERE instance_id = ? AND handle = ? AND kind = 'human'
                  AND enabled = 1 AND is_instance_admin = 1
                LIMIT 2
                """,
                (str(candidate_instance), candidate_handle),
            ).fetchall()
        if len(rows) != 1:
            raise PermissionDeniedError
        try:
            return SubjectId(rows[0][0])
        except (IndexError, TypeError, ValueError) as error:
            raise StorageUnavailableError from error
