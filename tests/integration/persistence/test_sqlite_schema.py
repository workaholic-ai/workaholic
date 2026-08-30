"""Integration tests for the fixed Phase 5 SQLite schema boundary."""

from __future__ import annotations

import json
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Never, Protocol, cast

import pytest

from workaholic.application import ApplicationErrorCode
from workaholic.persistence.sqlite import (
    SCHEMA_VERSION,
    SchemaUnsupportedError,
    StorageBusyError,
    StorageUnavailableError,
    initialize_empty_store,
    open_read_connection,
    open_write_transaction,
    validate_store_schema,
)
from workaholic.persistence.sqlite import schema as sqlite_schema
from workaholic.persistence.sqlite._driver import _connect
from workaholic.persistence.sqlite._records import (
    EVENT_PAYLOAD_JSON_MAX_LENGTH,
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    STRUCTURED_COLLECTION_JSON_MAX_LENGTH,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_TIMESTAMP = "2026-07-30T10:30:00.000000Z"
_LATER_TIMESTAMP = "2026-07-30T10:35:00.000000Z"
_LEASE_EXPIRY = "2026-07-30T10:45:00.000000Z"
_APPLICATION_TABLES = {
    "audit_events",
    "idempotency_records",
    "instances",
    "project_grants",
    "projects",
    "store_metadata",
    "subjects",
    "task_attempts",
    "task_claims",
    "task_dependencies",
    "task_events",
    "task_results",
    "tasks",
    "tokens",
}
_IDEMPOTENCY_OPERATIONS = (
    "bootstrap.local_project",
    "project.create",
    "task.create",
    "task.update",
    "task.block",
    "task.unblock",
    "task.cancel",
    "task.dependency.add",
    "task.dependency.remove",
    "task.result.submit",
    "task.result.approve",
    "task.result.reject",
    "task.claim",
    "task.claim.next",
    "task.claim.renew",
    "task.claim.release",
    "task.progress.report",
    "task.result.submit.agent",
    "subject.create",
    "subject.update",
    "subject.enable",
    "subject.disable",
    "subject.admin.grant",
    "subject.admin.revoke",
    "project.grant.assign",
    "project.grant.revoke",
    "token.activate",
    "token.revoke",
    "auth.recover.local",
)
_EXPECTED_COLUMNS = {
    "audit_events": (
        "cursor",
        "id",
        "instance_id",
        "actor_subject_id",
        "actor_kind",
        "actor_token_id",
        "request_id",
        "event_type",
        "occurred_at",
        "payload_json",
    ),
    "idempotency_records": (
        "subject_scope",
        "operation",
        "caller_key",
        "request_fingerprint",
        "outcome_json",
        "created_at",
    ),
    "instances": ("id", "created_at"),
    "project_grants": (
        "instance_id",
        "subject_id",
        "project_id",
        "role",
        "version",
        "granted_by",
        "created_at",
        "updated_at",
    ),
    "projects": (
        "id",
        "instance_id",
        "key",
        "name",
        "next_task_number",
        "created_at",
    ),
    "store_metadata": ("singleton", "schema_version"),
    "subjects": (
        "id",
        "instance_id",
        "kind",
        "handle",
        "display_name",
        "enabled",
        "is_instance_admin",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "task_dependencies": ("task_uid", "prerequisite_uid", "project_id"),
    "task_attempts": (
        "id",
        "task_uid",
        "project_id",
        "subject_id",
        "status",
        "started_at",
        "ended_at",
        "lease_expires_at",
    ),
    "task_claims": (
        "task_uid",
        "project_id",
        "subject_id",
        "attempt_id",
        "claimed_at",
        "lease_expires_at",
    ),
    "task_events": (
        "cursor",
        "id",
        "task_uid",
        "project_id",
        "actor_subject_id",
        "actor_kind",
        "attempt_id",
        "request_id",
        "event_type",
        "occurred_at",
        "payload_json",
    ),
    "task_results": (
        "id",
        "task_uid",
        "submitted_by",
        "attempt_id",
        "submitted_at",
        "comment",
        "summary",
        "criteria_json",
        "artifacts_json",
        "proposed_follow_ups_json",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "review_comment",
        "rejection_reason",
    ),
    "tasks": (
        "uid",
        "project_id",
        "number",
        "key",
        "title",
        "objective",
        "state",
        "priority",
        "available_at",
        "approval",
        "acceptance_json",
        "context_json",
        "blocking_reason",
        "current_result_id",
        "version",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "tokens": (
        "id",
        "instance_id",
        "subject_id",
        "token_hash",
        "created_by",
        "created_at",
        "activated_at",
        "expires_at",
        "revoked_at",
        "revoked_by",
    ),
}


class _ConnectionOpener(Protocol):
    """Callable shape shared by read and write connection boundaries."""

    def __call__(
        self,
        database_path: Path,
    ) -> AbstractContextManager[sqlite3.Connection]:
        """Return a connection context manager for one database path."""
        ...


class _ConfigurationFailureConnection:
    """Record closure after simulating a driver-configuration failure."""

    def __init__(self) -> None:
        """Initialize an open fake connection."""
        self.closed = False

    def execute(self, _statement: str) -> Never:
        """Fail the first required configuration statement.

        Args:
            _statement: SQL statement supplied by the driver.

        Raises:
            sqlite3.OperationalError: Always.

        """
        message = "private configuration detail"
        raise sqlite3.OperationalError(message)

    def close(self) -> None:
        """Record deterministic resource release."""
        self.closed = True


def _application_tables(connection: sqlite3.Connection) -> set[str]:
    """Read application-owned table names from one connection.

    Args:
        connection: Open SQLite connection.

    Returns:
        Non-SQLite table names in the current schema.

    """
    return {
        cast("str", row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _open_physical_database(database_path: Path) -> sqlite3.Connection:
    """Open a test-only physical connection with foreign keys enabled.

    Args:
        database_path: Existing integration-test database.

    Returns:
        A raw connection for asserting physical constraints.

    """
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    return connection


def _seed_authorization_graph(connection: sqlite3.Connection) -> None:
    """Insert the valid Instance, Subject, Project, and Owner grant fixture.

    Args:
        connection: Connection owning the fixture transaction.

    """
    connection.execute(
        "INSERT INTO instances (id, created_at) VALUES (?, ?)",
        ("ins_local", _TIMESTAMP),
    )
    connection.execute(
        """
        INSERT INTO subjects (
            id, instance_id, kind, handle, display_name, enabled,
            is_instance_admin, version, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sub_local",
            "ins_local",
            "human",
            "local-operator",
            "Local operator",
            1,
            1,
            1,
            "sub_local",
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, instance_id, key, name, next_task_number, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("prj_acme", "ins_local", "ACME", "Acme", 2, _TIMESTAMP),
    )
    connection.execute(
        """
        INSERT INTO project_grants (
            instance_id, subject_id, project_id, role, version, granted_by,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ins_local",
            "sub_local",
            "prj_acme",
            "owner",
            1,
            "sub_local",
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


def _insert_task(
    connection: sqlite3.Connection,
    *,
    uid: str = "tsk_first",
    project_id: str = "prj_acme",
    number: int = 1,
    key: str = "ACME-1",
) -> None:
    """Insert one valid initial Task physical row.

    Args:
        connection: Connection owning the test transaction.
        uid: Canonical Task identity.
        project_id: Owning Project identity.
        number: Project-local Task number.
        key: Stable Human-readable Task key.

    """
    connection.execute(
        """
        INSERT INTO tasks (
            uid, project_id, number, key, title, objective, state, priority,
            version, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            project_id,
            number,
            key,
            "First task",
            "Complete the first task.",
            "open",
            50,
            1,
            "sub_local",
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


def _insert_event(
    connection: sqlite3.Connection,
    *,
    cursor: int | None = None,
    event_id: str = "evt_first",
) -> None:
    """Insert one valid Task-created event row.

    Args:
        connection: Connection owning the test transaction.
        cursor: Optional explicit ordered cursor.
        event_id: Globally unique event identity.

    """
    values: tuple[object, ...] = (
        event_id,
        "tsk_first",
        "prj_acme",
        "sub_local",
        "req_first",
        "task_created",
        _TIMESTAMP,
        '{"title":"First task"}',
    )
    if cursor is None:
        connection.execute(
            """
            INSERT INTO task_events (
                id, task_uid, project_id, actor_subject_id, request_id,
                event_type, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return
    connection.execute(
        """
        INSERT INTO task_events (
            cursor, id, task_uid, project_id, actor_subject_id, request_id,
            event_type, occurred_at, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cursor, *values),
    )


def _insert_attempt(  # noqa: PLR0913 - explicit physical-record fixture.
    connection: sqlite3.Connection,
    *,
    attempt_id: str = "atm_first",
    task_uid: str = "tsk_first",
    project_id: str = "prj_acme",
    subject_id: str = "sub_local",
    status: str = "active",
    ended_at: str | None = None,
) -> None:
    """Insert one physical Attempt fixture through exact schema columns.

    Args:
        connection: Connection owning the test fixture.
        attempt_id: Opaque Attempt identity.
        task_uid: Owning Task identity.
        project_id: Owning Project identity.
        subject_id: Owning bootstrap Subject identity.
        status: Persisted Attempt state.
        ended_at: Nullable terminal time.

    """
    connection.execute(
        """
        INSERT INTO task_attempts (
            id, task_uid, project_id, subject_id, status, started_at,
            ended_at, lease_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            task_uid,
            project_id,
            subject_id,
            status,
            _TIMESTAMP,
            ended_at,
            _LEASE_EXPIRY,
        ),
    )


def _insert_claim(
    connection: sqlite3.Connection,
    *,
    task_uid: str = "tsk_first",
    project_id: str = "prj_acme",
    subject_id: str = "sub_local",
    attempt_id: str | None = "atm_first",
) -> None:
    """Insert one physical Human or Agent Claim fixture.

    Args:
        connection: Connection owning the test fixture.
        task_uid: Claimed Task identity.
        project_id: Claimed Task Project.
        subject_id: Current owner Subject.
        attempt_id: Null Human token or Agent Attempt identity.

    """
    connection.execute(
        """
        INSERT INTO task_claims (
            task_uid, project_id, subject_id, attempt_id, claimed_at,
            lease_expires_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_uid,
            project_id,
            subject_id,
            attempt_id,
            _TIMESTAMP,
            _LEASE_EXPIRY,
        ),
    )


def _set_short_busy_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten SQLite lock waits for deterministic contention tests.

    Args:
        monkeypatch: Active pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        "workaholic.persistence.sqlite._driver._CONNECT_TIMEOUT_SECONDS",
        0.025,
    )
    monkeypatch.setattr(
        "workaholic.persistence.sqlite._driver._BUSY_TIMEOUT_MILLISECONDS",
        25,
    )


def test_empty_store_initialization_is_atomic_private_and_reopenable(
    tmp_path: Path,
) -> None:
    """Initialization creates the exact version once and accepts safe retries."""
    database_path = tmp_path / "nested" / "local.db"

    initialize_empty_store(database_path)
    initialize_empty_store(database_path)

    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    with open_read_connection(database_path) as connection:
        assert _application_tables(connection) == _APPLICATION_TABLES
        assert connection.execute(
            "SELECT singleton, schema_version FROM store_metadata"
        ).fetchall() == [(1, SCHEMA_VERSION)]
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_empty_store_has_exact_columns_indexes_and_foreign_keys(
    tmp_path: Path,
) -> None:
    """The clean schema exposes every required physical integrity boundary."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)

    with open_read_connection(database_path) as connection:
        actual_columns = {
            table: tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM pragma_table_info(?) ORDER BY cid",
                    (table,),
                )
            )
            for table in _APPLICATION_TABLES
        }
        assert actual_columns == _EXPECTED_COLUMNS
        strict_tables = {
            cast("str", row[0]): row[1]
            for row in connection.execute(
                """
                SELECT name, strict
                FROM pragma_table_list
                WHERE schema = 'main' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        assert strict_tables == dict.fromkeys(_APPLICATION_TABLES, 1)

        expected_unique_indexes = {
            "audit_events": {("id",)},
            "idempotency_records": {
                ("subject_scope", "operation", "caller_key"),
            },
            "instances": {("id",)},
            "project_grants": {
                ("subject_id", "project_id"),
                ("instance_id", "subject_id", "project_id"),
            },
            "projects": {
                ("id",),
                ("id", "instance_id"),
                ("instance_id", "key"),
            },
            "store_metadata": set(),
            "subjects": {
                ("id",),
                ("id", "kind"),
                ("id", "instance_id"),
                ("id", "instance_id", "kind"),
                ("instance_id", "handle"),
            },
            "task_dependencies": {("task_uid", "prerequisite_uid")},
            "task_attempts": {
                ("id",),
                ("id", "task_uid", "subject_id"),
                ("id", "task_uid", "project_id"),
                ("id", "task_uid", "project_id", "subject_id"),
            },
            "task_claims": {("task_uid",), ("attempt_id",)},
            "task_events": {("id",)},
            "task_results": {("id",), ("id", "task_uid")},
            "tasks": {
                ("key",),
                ("project_id", "key"),
                ("project_id", "number"),
                ("uid",),
                ("uid", "project_id"),
            },
            "tokens": {
                ("id",),
                ("id", "instance_id", "subject_id"),
                ("token_hash",),
            },
        }
        actual_unique_indexes: dict[str, set[tuple[str, ...]]] = {}
        for table in _APPLICATION_TABLES:
            index_names = connection.execute(
                """
                SELECT name
                FROM pragma_index_list(?)
                WHERE [unique] = 1
                """,
                (table,),
            ).fetchall()
            actual_unique_indexes[table] = {
                tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                        (index_name,),
                    )
                )
                for (index_name,) in index_names
            }
        assert actual_unique_indexes == expected_unique_indexes

        expected_query_indexes = {
            "idx_audit_events_instance_cursor": ("instance_id", "cursor"),
            "idx_project_grants_project_subject": (
                "instance_id",
                "project_id",
                "subject_id",
            ),
            "idx_project_grants_subject_project": (
                "instance_id",
                "subject_id",
                "project_id",
            ),
            "idx_subjects_instance_admin": (
                "instance_id",
                "enabled",
                "is_instance_admin",
                "id",
            ),
            "idx_subjects_instance_handle": ("instance_id", "handle", "id"),
            "idx_task_attempts_active_lease": (
                "status",
                "lease_expires_at",
                "task_uid",
            ),
            "idx_task_attempts_task_history": ("task_uid", "started_at", "id"),
            "idx_task_claims_lease_expiry": ("lease_expires_at", "task_uid"),
            "idx_task_claims_owner": ("subject_id", "attempt_id", "task_uid"),
            "idx_task_claims_project_task": ("project_id", "task_uid"),
            "idx_task_dependencies_prerequisite": (
                "prerequisite_uid",
                "project_id",
                "task_uid",
            ),
            "idx_task_events_project_cursor": ("project_id", "cursor"),
            "idx_task_events_task_cursor": ("task_uid", "cursor"),
            "idx_task_results_task": ("task_uid", "submitted_at", "id"),
            "idx_tasks_readiness": (
                "project_id",
                "state",
                "available_at",
                "priority",
                "number",
            ),
            "idx_tokens_active_expiry": (
                "instance_id",
                "activated_at",
                "revoked_at",
                "expires_at",
                "id",
            ),
            "idx_tokens_subject_created": (
                "instance_id",
                "subject_id",
                "created_at",
                "id",
            ),
        }
        actual_query_indexes = {
            index_name: tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (index_name,),
                )
            )
            for (index_name,) in connection.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'index' AND name LIKE 'idx_%'
                ORDER BY name
                """
            )
        }
        assert actual_query_indexes == expected_query_indexes
        assert connection.execute(
            """
            SELECT name, [desc]
            FROM pragma_index_xinfo('idx_tasks_readiness')
            WHERE key = 1
            ORDER BY seqno
            """
        ).fetchall() == [
            ("project_id", 0),
            ("state", 0),
            ("available_at", 0),
            ("priority", 1),
            ("number", 0),
        ]

        actual_foreign_keys = {
            (table, row[3], row[2], row[4], row[6])
            for table in _APPLICATION_TABLES
            for row in connection.execute(
                "SELECT * FROM pragma_foreign_key_list(?)",
                (table,),
            )
        }
        assert actual_foreign_keys == {
            ("audit_events", "actor_kind", "subjects", "kind", "RESTRICT"),
            (
                "audit_events",
                "actor_subject_id",
                "subjects",
                "id",
                "RESTRICT",
            ),
            (
                "audit_events",
                "actor_subject_id",
                "tokens",
                "subject_id",
                "RESTRICT",
            ),
            ("audit_events", "actor_token_id", "tokens", "id", "RESTRICT"),
            (
                "audit_events",
                "instance_id",
                "subjects",
                "instance_id",
                "RESTRICT",
            ),
            (
                "audit_events",
                "instance_id",
                "tokens",
                "instance_id",
                "RESTRICT",
            ),
            ("project_grants", "granted_by", "subjects", "id", "RESTRICT"),
            (
                "project_grants",
                "instance_id",
                "projects",
                "instance_id",
                "RESTRICT",
            ),
            (
                "project_grants",
                "instance_id",
                "subjects",
                "instance_id",
                "RESTRICT",
            ),
            ("project_grants", "project_id", "projects", "id", "RESTRICT"),
            ("project_grants", "subject_id", "subjects", "id", "RESTRICT"),
            ("projects", "instance_id", "instances", "id", "RESTRICT"),
            ("subjects", "created_by", "subjects", "id", "RESTRICT"),
            ("subjects", "instance_id", "instances", "id", "RESTRICT"),
            (
                "subjects",
                "instance_id",
                "subjects",
                "instance_id",
                "RESTRICT",
            ),
            ("task_attempts", "project_id", "tasks", "project_id", "RESTRICT"),
            ("task_attempts", "subject_id", "subjects", "id", "RESTRICT"),
            ("task_attempts", "task_uid", "tasks", "uid", "RESTRICT"),
            ("task_claims", "attempt_id", "task_attempts", "id", "RESTRICT"),
            (
                "task_claims",
                "project_id",
                "task_attempts",
                "project_id",
                "RESTRICT",
            ),
            ("task_claims", "project_id", "tasks", "project_id", "RESTRICT"),
            (
                "task_claims",
                "subject_id",
                "task_attempts",
                "subject_id",
                "RESTRICT",
            ),
            ("task_claims", "subject_id", "subjects", "id", "RESTRICT"),
            ("task_claims", "task_uid", "task_attempts", "task_uid", "RESTRICT"),
            ("task_claims", "task_uid", "tasks", "uid", "RESTRICT"),
            (
                "task_dependencies",
                "prerequisite_uid",
                "tasks",
                "uid",
                "RESTRICT",
            ),
            ("task_dependencies", "project_id", "tasks", "project_id", "RESTRICT"),
            ("task_dependencies", "task_uid", "tasks", "uid", "RESTRICT"),
            ("task_events", "actor_kind", "subjects", "kind", "RESTRICT"),
            ("task_events", "actor_subject_id", "subjects", "id", "RESTRICT"),
            ("task_events", "attempt_id", "task_attempts", "id", "RESTRICT"),
            ("task_events", "project_id", "tasks", "project_id", "RESTRICT"),
            (
                "task_events",
                "project_id",
                "task_attempts",
                "project_id",
                "RESTRICT",
            ),
            ("task_events", "task_uid", "tasks", "uid", "RESTRICT"),
            ("task_events", "task_uid", "task_attempts", "task_uid", "RESTRICT"),
            ("task_results", "attempt_id", "task_attempts", "id", "RESTRICT"),
            ("task_results", "reviewed_by", "subjects", "id", "RESTRICT"),
            ("task_results", "submitted_by", "subjects", "id", "RESTRICT"),
            (
                "task_results",
                "submitted_by",
                "task_attempts",
                "subject_id",
                "RESTRICT",
            ),
            ("task_results", "task_uid", "tasks", "uid", "RESTRICT"),
            (
                "task_results",
                "task_uid",
                "task_attempts",
                "task_uid",
                "RESTRICT",
            ),
            ("tasks", "created_by", "subjects", "id", "RESTRICT"),
            ("tasks", "current_result_id", "task_results", "id", "RESTRICT"),
            ("tasks", "project_id", "projects", "id", "RESTRICT"),
            ("tasks", "uid", "task_results", "task_uid", "RESTRICT"),
            ("tokens", "created_by", "subjects", "id", "RESTRICT"),
            (
                "tokens",
                "instance_id",
                "subjects",
                "instance_id",
                "RESTRICT",
            ),
            ("tokens", "revoked_by", "subjects", "id", "RESTRICT"),
            ("tokens", "subject_id", "subjects", "id", "RESTRICT"),
        }


@pytest.mark.parametrize("opener", [open_read_connection, open_write_transaction])
def test_normal_open_does_not_create_a_missing_database(
    opener: _ConnectionOpener,
    tmp_path: Path,
) -> None:
    """Normal reads and writes reject a missing schema without creating it."""
    database_path = tmp_path / "missing.db"

    with (
        pytest.raises(SchemaUnsupportedError) as captured,
        opener(database_path),
    ):
        pytest.fail("a missing database must not be opened")

    assert captured.value.code is ApplicationErrorCode.SCHEMA_UNSUPPORTED
    assert not database_path.exists()


def test_read_connection_is_physically_read_only(tmp_path: Path) -> None:
    """A read connection cannot accidentally mutate a validated store."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)

    with (
        pytest.raises(StorageUnavailableError) as captured,
        open_read_connection(database_path) as connection,
    ):
        connection.execute(
            "INSERT INTO instances (id, created_at) VALUES (?, ?)",
            ("ins_forbidden", _TIMESTAMP),
        )

    assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
    with open_read_connection(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM instances").fetchone() == (0,)


def test_write_transaction_commits_success_and_rolls_back_failure(
    tmp_path: Path,
) -> None:
    """The write boundary commits one outcome or none of its statements."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)

    with open_write_transaction(database_path) as connection:
        connection.execute(
            "INSERT INTO instances (id, created_at) VALUES (?, ?)",
            ("ins_committed", _TIMESTAMP),
        )

    def execute_failing_transaction() -> None:
        """Insert one row and fail before the transaction can commit."""
        with open_write_transaction(database_path) as connection:
            connection.execute(
                "INSERT INTO instances (id, created_at) VALUES (?, ?)",
                ("ins_rolled_back", _TIMESTAMP),
            )
            message = "injected application failure"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="injected"):
        execute_failing_transaction()

    with open_read_connection(database_path) as connection:
        assert connection.execute(
            "SELECT id FROM instances ORDER BY id"
        ).fetchall() == [("ins_committed",)]


def test_foreign_keys_are_enabled_on_write_connections(tmp_path: Path) -> None:
    """A dangling Project reference fails and rolls back through the safe boundary."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)

    with (
        pytest.raises(StorageUnavailableError),
        open_write_transaction(database_path) as connection,
    ):
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("prj_orphan", "ins_missing", "ORPHAN", "Orphan", 1, _TIMESTAMP),
        )

    with open_read_connection(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone() == (0,)


def test_checked_boolean_enum_and_project_key_constraints(tmp_path: Path) -> None:
    """Physical constraints backstop core runtime validation categories."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        connection.commit()
        invalid_subject_rows = [
            ("sub_kind", "robot", "invalid-kind", "Invalid kind", 1, 0, 1),
            ("sub_disabled", "human", "disabled", "Disabled", 2, 0, 1),
            ("sub_admin", "human", "admin", "Admin", 1, -1, 1),
            ("sub_version", "agent", "version", "Version", 1, 0, 0),
        ]
        for row in invalid_subject_rows:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO subjects (
                        id, instance_id, kind, handle, display_name, enabled,
                        is_instance_admin, version, created_by, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row[0],
                        "ins_local",
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[0],
                        _TIMESTAMP,
                        _TIMESTAMP,
                    ),
                )
            connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO projects (
                    id, instance_id, key, name, next_task_number, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("prj_duplicate", "ins_local", "ACME", "Duplicate", 1, _TIMESTAMP),
            )
    finally:
        connection.close()


def test_project_name_round_trips_and_rejects_invalid_storage(
    tmp_path: Path,
) -> None:
    """Required normalized Project names survive storage with physical bounds."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("prj_cafe", "ins_local", "CAFE", "Café", 1, _TIMESTAMP),
        )
        connection.commit()

        assert connection.execute(
            "SELECT key, name FROM projects ORDER BY key"
        ).fetchall() == [("ACME", "Acme"), ("CAFE", "Café")]

        for index, invalid_name in enumerate(("", " padded", "X" * 201)):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, instance_id, key, name, next_task_number, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"prj_invalid_{index}",
                        "ins_local",
                        f"BAD{index}",
                        invalid_name,
                        1,
                        _TIMESTAMP,
                    ),
                )
            connection.rollback()
    finally:
        connection.close()


def test_phase_three_task_state_schedule_review_and_json_constraints(
    tmp_path: Path,
) -> None:
    """Complete Task columns enforce lifecycle coupling and bounded JSON arrays."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        connection.execute(
            """
            UPDATE tasks
            SET state = ?, available_at = ?, approval = ?, acceptance_json = ?,
                context_json = ?, blocking_reason = ?, version = ?
            WHERE uid = ?
            """,
            (
                "blocked",
                _TIMESTAMP,
                "human",
                '[{"id":"ac_done","required":true,"text":"Done"}]',
                '[{"uri":"workspace://repo/spec.md","version":null}]',
                "Waiting for input",
                2,
                "tsk_first",
            ),
        )
        connection.commit()

        assert connection.execute(
            """
            SELECT state, available_at, approval, acceptance_json, context_json,
                   blocking_reason, current_result_id, version
            FROM tasks WHERE uid = 'tsk_first'
            """
        ).fetchone() == (
            "blocked",
            _TIMESTAMP,
            "human",
            '[{"id":"ac_done","required":true,"text":"Done"}]',
            '[{"uri":"workspace://repo/spec.md","version":null}]',
            "Waiting for input",
            None,
            2,
        )

        invalid_updates = (
            ("state = 'unknown'", ()),
            ("state = 'open'", ()),
            ("approval = 'agent'", ()),
            ("available_at = '2026-07-30T10:30:00+00:00'", ()),
            ("acceptance_json = '{}'", ()),
            (
                "context_json = ?",
                (json.dumps(["x" * STRUCTURED_COLLECTION_JSON_MAX_LENGTH]),),
            ),
        )
        for assignment, parameters in invalid_updates:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"UPDATE tasks SET {assignment} WHERE uid = 'tsk_first'",  # noqa: S608
                    parameters,
                )
            connection.rollback()
    finally:
        connection.close()


def test_phase_three_dependency_constraints_enforce_identity_and_project(
    tmp_path: Path,
) -> None:
    """Dependency edges are unique, non-self, and constrained to one Project."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("prj_other", "ins_local", "OTHER", "Other", 2, _TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO project_grants (
                instance_id, subject_id, project_id, role, version, granted_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ins_local",
                "sub_local",
                "prj_other",
                "owner",
                1,
                "sub_local",
                _TIMESTAMP,
                _TIMESTAMP,
            ),
        )
        _insert_task(connection)
        _insert_task(connection, uid="tsk_second", number=2, key="ACME-2")
        _insert_task(
            connection,
            uid="tsk_other",
            project_id="prj_other",
            key="OTHER-1",
        )
        connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            ("tsk_first", "tsk_second", "prj_acme"),
        )
        connection.commit()

        assert connection.execute(
            "SELECT task_uid, prerequisite_uid, project_id FROM task_dependencies"
        ).fetchall() == [("tsk_first", "tsk_second", "prj_acme")]
        for row in (
            ("tsk_first", "tsk_second", "prj_acme"),
            ("tsk_first", "tsk_first", "prj_acme"),
            ("tsk_first", "tsk_other", "prj_acme"),
            ("tsk_missing", "tsk_second", "prj_acme"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO task_dependencies (
                        task_uid, prerequisite_uid, project_id
                    ) VALUES (?, ?, ?)
                    """,
                    row,
                )
            connection.rollback()
    finally:
        connection.close()


def test_phase_four_attempt_state_and_claim_ownership_constraints(
    tmp_path: Path,
) -> None:
    """Attempts and Claims enforce lifecycle, uniqueness, and exact ownership."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        _insert_task(connection, uid="tsk_second", number=2, key="ACME-2")
        connection.execute(
            """
            INSERT INTO subjects (
                id, instance_id, kind, handle, display_name, enabled,
                is_instance_admin, version, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub_other",
                "ins_local",
                "human",
                "other-operator",
                "Other operator",
                1,
                0,
                1,
                "sub_local",
                _TIMESTAMP,
                _TIMESTAMP,
            ),
        )
        _insert_attempt(connection)
        _insert_claim(connection)
        _insert_claim(
            connection,
            task_uid="tsk_second",
            attempt_id=None,
        )
        connection.commit()

        assert connection.execute(
            """
            SELECT task_uid, subject_id, attempt_id, lease_expires_at
            FROM task_claims ORDER BY task_uid
            """
        ).fetchall() == [
            ("tsk_first", "sub_local", "atm_first", _LEASE_EXPIRY),
            ("tsk_second", "sub_local", None, _LEASE_EXPIRY),
        ]

        invalid_attempts = (
            ("atm_active_ended", "active", _LATER_TIMESTAMP),
            ("atm_released_open", "released", None),
            ("atm_expired_early", "expired", _LATER_TIMESTAMP),
            ("atm_submitted_late", "submitted", _LEASE_EXPIRY),
        )
        for attempt_id, status, ended_at in invalid_attempts:
            with pytest.raises(sqlite3.IntegrityError):
                _insert_attempt(
                    connection,
                    attempt_id=attempt_id,
                    status=status,
                    ended_at=ended_at,
                )
            connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            _insert_claim(connection, attempt_id=None)
        connection.rollback()

        connection.execute("DELETE FROM task_claims")
        connection.commit()
        for task_uid, subject_id in (
            ("tsk_second", "sub_local"),
            ("tsk_first", "sub_other"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_claim(
                    connection,
                    task_uid=task_uid,
                    subject_id=subject_id,
                )
            connection.rollback()
    finally:
        connection.close()


def test_phase_four_attempt_foreign_keys_bind_results_and_events(
    tmp_path: Path,
) -> None:
    """Agent Results and events can reference only their exact owning Attempt."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        _insert_attempt(
            connection,
            status="submitted",
            ended_at=_LATER_TIMESTAMP,
        )
        connection.execute(
            """
            INSERT INTO task_results (
                id, task_uid, submitted_by, attempt_id, submitted_at,
                review_status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "res_agent",
                "tsk_first",
                "sub_local",
                "atm_first",
                _LATER_TIMESTAMP,
                "not_required",
            ),
        )
        connection.execute(
            """
            INSERT INTO task_events (
                id, task_uid, project_id, actor_subject_id, actor_kind,
                attempt_id, request_id, event_type, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_agent",
                "tsk_first",
                "prj_acme",
                "sub_local",
                "human",
                "atm_first",
                "req_agent",
                "result_submitted",
                _LATER_TIMESTAMP,
                "{}",
            ),
        )
        connection.commit()

        assert connection.execute(
            "SELECT attempt_id FROM task_results WHERE id = 'res_agent'"
        ).fetchone() == ("atm_first",)
        assert connection.execute(
            "SELECT attempt_id FROM task_events WHERE id = 'evt_agent'"
        ).fetchone() == ("atm_first",)

        for table, identity in (
            ("task_results", "res_invalid"),
            ("task_events", "evt_invalid"),
        ):
            if table == "task_results":
                statement = """
                    INSERT INTO task_results (
                        id, task_uid, submitted_by, attempt_id, submitted_at,
                        review_status
                    ) VALUES (?, 'tsk_first', 'sub_local', ?, ?, 'pending')
                """
                parameters = (identity, "atm_missing", _LATER_TIMESTAMP)
            else:
                statement = """
                    INSERT INTO task_events (
                        id, task_uid, project_id, actor_subject_id, attempt_id,
                        request_id, event_type, occurred_at, payload_json
                    ) VALUES (?, 'tsk_first', 'prj_acme', 'sub_local', ?,
                              'req_invalid', 'progress_reported', ?, '{}')
                """
                parameters = (identity, "atm_missing", _LATER_TIMESTAMP)
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)
            connection.rollback()

        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_phase_three_result_review_and_current_selection_constraints(
    tmp_path: Path,
) -> None:
    """Results retain closed content, normalized reviews, and Task ownership."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        connection.execute(
            """
            INSERT INTO task_results (
                id, task_uid, submitted_by, attempt_id, submitted_at, comment,
                summary, criteria_json, artifacts_json,
                proposed_follow_ups_json, review_status, reviewed_by,
                reviewed_at, review_comment, rejection_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "res_pending",
                "tsk_first",
                "sub_local",
                None,
                _TIMESTAMP,
                "Completed manually",
                "Done",
                "[]",
                "[]",
                "[]",
                "pending",
                None,
                None,
                None,
                None,
            ),
        )
        connection.execute(
            "UPDATE tasks SET state = 'review', current_result_id = 'res_pending'"
        )
        connection.commit()

        assert connection.execute(
            "SELECT current_result_id FROM tasks WHERE uid = 'tsk_first'"
        ).fetchone() == ("res_pending",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE tasks SET current_result_id = 'res_missing' "
                "WHERE uid = 'tsk_first'"
            )
        connection.rollback()

        invalid_rows = (
            ("res_status", "unknown", None, None, None, None, "[]"),
            (
                "res_pending_reviewed",
                "pending",
                "sub_local",
                _TIMESTAMP,
                None,
                None,
                "[]",
            ),
            (
                "res_approved_unattributed",
                "approved",
                None,
                None,
                "Approved",
                None,
                "[]",
            ),
            (
                "res_rejected_no_reason",
                "rejected",
                "sub_local",
                _TIMESTAMP,
                None,
                None,
                "[]",
            ),
            ("res_bad_json", "pending", None, None, None, None, "{}"),
            (
                "res_large_json",
                "pending",
                None,
                None,
                None,
                None,
                json.dumps(["x" * STRUCTURED_COLLECTION_JSON_MAX_LENGTH]),
            ),
        )
        for (
            result_id,
            status,
            reviewer,
            reviewed_at,
            review_comment,
            reason,
            criteria_json,
        ) in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO task_results (
                        id, task_uid, submitted_by, attempt_id, submitted_at,
                        criteria_json, artifacts_json, proposed_follow_ups_json,
                        review_status, reviewed_by, reviewed_at, review_comment,
                        rejection_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_id,
                        "tsk_first",
                        "sub_local",
                        None,
                        _TIMESTAMP,
                        criteria_json,
                        "[]",
                        "[]",
                        status,
                        reviewer,
                        reviewed_at,
                        review_comment,
                        reason,
                    ),
                )
            connection.rollback()
    finally:
        connection.close()


def test_phase_four_event_types_snapshots_and_payload_bounds(tmp_path: Path) -> None:
    """Every Phase 4 event type accepts Human-kind snapshots and object JSON."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        event_types = (
            "task_created",
            "task_updated",
            "task_blocked",
            "task_unblocked",
            "result_submitted",
            "review_approved",
            "review_rejected",
            "task_completed",
            "task_cancelled",
            "task_claimed",
            "claim_renewed",
            "claim_released",
            "claim_expired",
            "progress_reported",
            "observation_added",
        )
        for index, event_type in enumerate(event_types):
            connection.execute(
                """
                INSERT INTO task_events (
                    id, task_uid, project_id, actor_subject_id, actor_kind,
                    attempt_id, request_id, event_type, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt_{index}",
                    "tsk_first",
                    "prj_acme",
                    "sub_local",
                    "human",
                    None,
                    f"req_{index}",
                    event_type,
                    _TIMESTAMP,
                    "{}",
                ),
            )
        connection.commit()
        assert connection.execute(
            "SELECT event_type FROM task_events ORDER BY cursor"
        ).fetchall() == [(event_type,) for event_type in event_types]

        invalid_rows = (
            ("evt_kind", "agent", None, "task_updated", "{}"),
            ("evt_attempt", "human", "bad", "task_updated", "{}"),
            ("evt_type", "human", None, "unknown", "{}"),
            ("evt_array", "human", None, "task_updated", "[]"),
            (
                "evt_large",
                "human",
                None,
                "task_updated",
                json.dumps({"value": "x" * EVENT_PAYLOAD_JSON_MAX_LENGTH}),
            ),
        )
        for event_id, actor_kind, attempt_id, event_type, payload_json in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO task_events (
                        id, task_uid, project_id, actor_subject_id, actor_kind,
                        attempt_id, request_id, event_type, occurred_at,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        "tsk_first",
                        "prj_acme",
                        "sub_local",
                        actor_kind,
                        attempt_id,
                        f"req_{event_id}",
                        event_type,
                        _TIMESTAMP,
                        payload_json,
                    ),
                )
            connection.rollback()
    finally:
        connection.close()


def test_idempotency_operation_constraint_includes_every_phase_four_mutation(
    tmp_path: Path,
) -> None:
    """The closed semantic operation set includes every cumulative mutation."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        for index, operation in enumerate(_IDEMPOTENCY_OPERATIONS):
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    subject_scope, operation, caller_key, request_fingerprint,
                    outcome_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub_local",
                    operation,
                    f"operation-{index}",
                    "fingerprint",
                    "{}",
                    _TIMESTAMP,
                ),
            )
        assert connection.execute(
            "SELECT operation FROM idempotency_records ORDER BY operation"
        ).fetchall() == [(value,) for value in sorted(_IDEMPOTENCY_OPERATIONS)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    subject_scope, operation, caller_key, request_fingerprint,
                    outcome_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub_local",
                    "remote.sync",
                    "invalid-1",
                    "fingerprint",
                    "{}",
                    _TIMESTAMP,
                ),
            )
        for caller_key, outcome_json in (
            ("invalid-shape", "[]"),
            (
                "invalid-size",
                json.dumps({"value": "x" * IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH}),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        subject_scope, operation, caller_key,
                        request_fingerprint, outcome_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "sub_local",
                        "task.update",
                        caller_key,
                        "fingerprint",
                        outcome_json,
                        _TIMESTAMP,
                    ),
                )
    finally:
        connection.close()


def test_task_event_and_idempotency_uniqueness_constraints(tmp_path: Path) -> None:
    """Task numbers, Human keys, event identities, and caller scopes are unique."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = _open_physical_database(database_path)
    try:
        _seed_authorization_graph(connection)
        _insert_task(connection)
        _insert_event(connection)
        connection.execute(
            """
            INSERT INTO idempotency_records (
                subject_scope, operation, caller_key, request_fingerprint,
                outcome_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "sub_local",
                "task.create",
                "caller-key",
                "fingerprint",
                '{"task_uid":"tsk_first"}',
                _TIMESTAMP,
            ),
        )
        connection.commit()

        conflicting_statements = [
            (
                """
                INSERT INTO tasks (
                    uid, project_id, number, key, title, objective, state,
                    priority, version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tsk_number",
                    "prj_acme",
                    1,
                    "ACME-2",
                    "Duplicate number",
                    "Must fail.",
                    "open",
                    50,
                    1,
                    "sub_local",
                    _TIMESTAMP,
                    _TIMESTAMP,
                ),
            ),
            (
                """
                INSERT INTO tasks (
                    uid, project_id, number, key, title, objective, state,
                    priority, version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tsk_key",
                    "prj_acme",
                    2,
                    "ACME-1",
                    "Duplicate key",
                    "Must fail.",
                    "open",
                    50,
                    1,
                    "sub_local",
                    _TIMESTAMP,
                    _TIMESTAMP,
                ),
            ),
            (
                """
                INSERT INTO task_events (
                    cursor, id, task_uid, project_id, actor_subject_id,
                    request_id, event_type, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "evt_cursor",
                    "tsk_first",
                    "prj_acme",
                    "sub_local",
                    "req_cursor",
                    "task_created",
                    _TIMESTAMP,
                    "{}",
                ),
            ),
            (
                """
                INSERT INTO task_events (
                    id, task_uid, project_id, actor_subject_id, request_id,
                    event_type, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_first",
                    "tsk_first",
                    "prj_acme",
                    "sub_local",
                    "req_duplicate",
                    "task_created",
                    _TIMESTAMP,
                    "{}",
                ),
            ),
            (
                """
                INSERT INTO task_events (
                    id, task_uid, project_id, actor_subject_id, request_id,
                    event_type, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_invalid_json",
                    "tsk_first",
                    "prj_acme",
                    "sub_local",
                    "req_invalid_json",
                    "task_created",
                    _TIMESTAMP,
                    "{invalid}",
                ),
            ),
            (
                """
                INSERT INTO idempotency_records (
                    subject_scope, operation, caller_key, request_fingerprint,
                    outcome_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub_local",
                    "task.create",
                    "caller-key",
                    "different",
                    "{}",
                    _TIMESTAMP,
                ),
            ),
        ]
        for statement, parameters in conflicting_statements:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)
            connection.rollback()
    finally:
        connection.close()


def _build_invalid_store(database_path: Path, scenario: str) -> None:
    """Construct one deliberately unsupported metadata scenario.

    Args:
        database_path: Target test database.
        scenario: Missing, malformed, multiple, or numeric version case.

    """
    connection = sqlite3.connect(database_path)
    try:
        if scenario == "missing":
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        elif scenario == "malformed":
            connection.execute(
                "CREATE TABLE store_metadata (singleton TEXT, schema_version TEXT)"
            )
            connection.execute("INSERT INTO store_metadata VALUES ('one', 'one')")
        elif scenario == "multiple":
            connection.execute(
                """
                CREATE TABLE store_metadata (
                    singleton INTEGER,
                    schema_version INTEGER
                )
                """
            )
            connection.executemany(
                "INSERT INTO store_metadata VALUES (?, ?)",
                [(1, 4), (2, 4)],
            )
        else:
            connection.execute(
                """
                CREATE TABLE store_metadata (
                    singleton INTEGER,
                    schema_version INTEGER
                )
                """
            )
            connection.execute(
                "INSERT INTO store_metadata VALUES (1, ?)",
                (int(scenario),),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "scenario",
    ["missing", "malformed", "multiple", "0", "1", "2", "3", "4", "6"],
)
def test_schema_validation_rejects_without_modifying_the_store(
    scenario: str,
    tmp_path: Path,
) -> None:
    """Missing, malformed, older, and newer stores remain unchanged."""
    database_path = tmp_path / f"{scenario}.db"
    _build_invalid_store(database_path, scenario)
    original_bytes = database_path.read_bytes()
    connection = sqlite3.connect(database_path)
    original_schema = connection.execute(
        "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()

    with pytest.raises(SchemaUnsupportedError) as captured:
        validate_store_schema(connection)

    assert captured.value.code is ApplicationErrorCode.SCHEMA_UNSUPPORTED
    assert (
        connection.execute(
            "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        == original_schema
    )
    connection.close()
    assert database_path.read_bytes() == original_bytes


def test_validation_runtime_checks_connection_type() -> None:
    """Schema validation does not trust its Connection type hint alone."""
    with pytest.raises(SchemaUnsupportedError):
        validate_store_schema(cast("sqlite3.Connection", object()))


def test_initialization_never_repairs_an_unsupported_store(tmp_path: Path) -> None:
    """Calling initialization on version 2 leaves every schema object unchanged."""
    database_path = tmp_path / "phase-two.db"
    _build_invalid_store(database_path, "2")
    original_bytes = database_path.read_bytes()
    connection = sqlite3.connect(database_path)
    original_schema = connection.execute(
        "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()
    connection.close()

    with pytest.raises(SchemaUnsupportedError):
        initialize_empty_store(database_path)

    assert database_path.read_bytes() == original_bytes
    connection = sqlite3.connect(database_path)
    try:
        assert (
            connection.execute(
                "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
            ).fetchall()
            == original_schema
        )
    finally:
        connection.close()


@pytest.mark.parametrize("opener", [open_read_connection, open_write_transaction])
def test_normal_open_rejects_an_unsupported_store(
    opener: _ConnectionOpener,
    tmp_path: Path,
) -> None:
    """Every normal connection validates schema before exposing state."""
    database_path = tmp_path / "phase-two.db"
    _build_invalid_store(database_path, "2")

    with pytest.raises(SchemaUnsupportedError), opener(database_path):
        pytest.fail("unsupported state must not be exposed")


def test_concurrent_first_initialization_produces_one_valid_schema(
    tmp_path: Path,
) -> None:
    """Concurrent creators serialize into a single complete version-5 store."""
    database_path = tmp_path / "concurrent" / "local.db"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                initialize_empty_store,
                [database_path] * 4,
            )
        )

    assert results == [None] * 4
    with open_read_connection(database_path) as connection:
        validate_store_schema(connection)
        assert _application_tables(connection) == _APPLICATION_TABLES
        assert connection.execute("SELECT count(*) FROM store_metadata").fetchone() == (
            1,
        )


def test_reader_cannot_observe_uncommitted_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A reader waits for the complete schema instead of seeing an empty file."""
    database_path = tmp_path / "concurrent" / "local.db"
    schema_creation_started = Event()
    allow_schema_creation = Event()
    reader_finished = Event()
    create_schema = sqlite_schema._create_schema

    def pause_before_schema(connection: sqlite3.Connection) -> None:
        """Expose the post-connect, pre-schema window to the concurrent reader."""
        schema_creation_started.set()
        assert allow_schema_creation.wait(timeout=5)
        create_schema(connection)

    def read_initialized_store() -> int:
        """Read only after initialization publishes one complete schema."""
        try:
            with open_read_connection(database_path) as connection:
                row = connection.execute(
                    "SELECT schema_version FROM store_metadata"
                ).fetchone()
                assert row is not None
                return cast("int", row[0])
        finally:
            reader_finished.set()

    monkeypatch.setattr(sqlite_schema, "_create_schema", pause_before_schema)
    with ThreadPoolExecutor(max_workers=2) as executor:
        initializer = executor.submit(initialize_empty_store, database_path)
        assert schema_creation_started.wait(timeout=5)
        reader = executor.submit(read_initialized_store)
        assert not reader_finished.wait(timeout=0.1)
        allow_schema_creation.set()

        assert initializer.result(timeout=5) is None
        assert reader.result(timeout=5) == SCHEMA_VERSION


def test_initialization_failure_rolls_back_every_schema_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An injected DDL failure cannot expose a partial valid store."""
    database_path = tmp_path / "local.db"

    def fail_after_partial_schema(connection: sqlite3.Connection) -> None:
        """Create one transactional table and then simulate a fatal failure."""
        connection.execute("CREATE TABLE partial_state (value TEXT)")
        message = "injected initialization failure"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite.schema._create_schema",
        fail_after_partial_schema,
    )

    with pytest.raises(RuntimeError, match="injected"):
        initialize_empty_store(database_path)

    connection = sqlite3.connect(database_path)
    try:
        assert _application_tables(connection) == set()
        with pytest.raises(SchemaUnsupportedError):
            validate_store_schema(connection)
    finally:
        connection.close()


def test_initialization_permission_failure_removes_new_empty_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failure to make a new store private removes the file created by SQLite."""
    database_path = tmp_path / "local.db"

    def fail_chmod(_path: Path, _mode: int) -> None:
        """Simulate a filesystem that rejects private database permissions."""
        message = "private permission detail"
        raise PermissionError(message)

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    with pytest.raises(StorageUnavailableError):
        initialize_empty_store(database_path)

    assert not database_path.exists()


def test_initialization_rejects_unusable_parent_path(tmp_path: Path) -> None:
    """A regular file cannot be treated as the database parent directory."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("preserved", encoding="utf-8")

    with pytest.raises(StorageUnavailableError):
        initialize_empty_store(parent / "local.db")

    assert parent.read_text(encoding="utf-8") == "preserved"


def test_sqlite_open_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unexpected driver-open details map to the stable storage error."""

    def fail_connect(*_arguments: object, **_keywords: object) -> sqlite3.Connection:
        """Simulate an unexpected driver failure before a connection exists."""
        message = "private driver detail"
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._driver.sqlite3.connect",
        fail_connect,
    )

    with pytest.raises(StorageUnavailableError) as captured:
        initialize_empty_store(tmp_path / "local.db")

    assert "private driver detail" not in captured.value.safe_message


def test_configuration_failure_closes_the_open_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A partially configured connection is never left for garbage collection."""
    failing_connection = _ConfigurationFailureConnection()

    def return_failing_connection(
        *_arguments: object,
        **_keywords: object,
    ) -> sqlite3.Connection:
        """Return the controlled connection boundary.

        Returns:
            Fake connection cast to the standard-library boundary type.

        """
        return cast("sqlite3.Connection", failing_connection)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._driver.sqlite3.connect",
        return_failing_connection,
    )

    with pytest.raises(StorageUnavailableError) as captured:
        _connect(tmp_path / "local.db", mode="rwc")

    assert failing_connection.closed
    assert "private configuration detail" not in captured.value.safe_message


def test_initialization_lock_timeout_maps_to_retryable_storage_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Initialization uses the same bounded contention contract as mutations."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    blocker = sqlite3.connect(database_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    _set_short_busy_timeout(monkeypatch)
    try:
        with pytest.raises(StorageBusyError):
            initialize_empty_store(database_path)
    finally:
        blocker.rollback()
        blocker.close()


def test_bounded_lock_timeout_maps_to_retryable_storage_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exhausted immediate-lock acquisition returns the stable retry guidance."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    blocker = sqlite3.connect(database_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    _set_short_busy_timeout(monkeypatch)
    try:
        with (
            pytest.raises(StorageBusyError) as captured,
            open_write_transaction(database_path),
        ):
            pytest.fail("a second writer must not acquire the lock")
    finally:
        blocker.rollback()
        blocker.close()

    assert captured.value.code is ApplicationErrorCode.STORAGE_BUSY
    assert captured.value.retryable
    assert "locked" not in captured.value.safe_message.lower()


@pytest.mark.parametrize(
    "database_path",
    [
        Path("relative.db"),
        cast("Path", object()),
    ],
)
def test_database_paths_are_runtime_validated(database_path: Path) -> None:
    """Relative and non-Path storage locations are rejected safely."""
    with pytest.raises(StorageUnavailableError):
        initialize_empty_store(database_path)


def test_symlink_database_target_is_rejected(tmp_path: Path) -> None:
    """Initialization cannot be redirected through a database-file symlink."""
    target = tmp_path / "target.db"
    target.write_bytes(b"preserved")
    linked = tmp_path / "linked.db"
    linked.symlink_to(target)

    with pytest.raises(StorageUnavailableError):
        initialize_empty_store(linked)

    assert target.read_bytes() == b"preserved"
