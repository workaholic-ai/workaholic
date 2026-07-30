"""Integration tests for the fixed Phase 1 SQLite schema boundary."""

from __future__ import annotations

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
from workaholic.persistence.sqlite._driver import _connect

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

_TIMESTAMP = "2026-07-30T10:30:00Z"
_APPLICATION_TABLES = {
    "idempotency_records",
    "instances",
    "project_grants",
    "projects",
    "store_metadata",
    "subjects",
    "task_events",
    "tasks",
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
            id, kind, display_name, enabled, is_instance_admin
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("sub_local", "human", "Local operator", 1, 1),
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, instance_id, key, next_task_number, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("prj_acme", "ins_local", "ACME", 2, _TIMESTAMP),
    )
    connection.execute(
        """
        INSERT INTO project_grants (subject_id, project_id, role)
        VALUES (?, ?, ?)
        """,
        ("sub_local", "prj_acme", "owner"),
    )


def _insert_task(
    connection: sqlite3.Connection,
    *,
    uid: str = "tsk_first",
    number: int = 1,
    key: str = "ACME-1",
) -> None:
    """Insert one valid initial Task physical row.

    Args:
        connection: Connection owning the test transaction.
        uid: Canonical Task identity.
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
            "prj_acme",
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
                id, instance_id, key, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("prj_orphan", "ins_missing", "ORPHAN", 1, _TIMESTAMP),
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
            ("sub_agent", "agent", "Agent", 1, 0),
            ("sub_disabled", "human", "Disabled", 2, 0),
            ("sub_admin", "human", "Admin", 1, -1),
        ]
        for row in invalid_subject_rows:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO subjects (
                        id, kind, display_name, enabled, is_instance_admin
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    row,
                )
            connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO projects (
                    id, instance_id, key, next_task_number, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("prj_duplicate", "ins_local", "ACME", 1, _TIMESTAMP),
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
                "task.add",
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
                    "task.add",
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
                [(1, 1), (2, 1)],
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


@pytest.mark.parametrize("scenario", ["missing", "malformed", "multiple", "0", "2"])
def test_schema_validation_rejects_without_modifying_the_store(
    scenario: str,
    tmp_path: Path,
) -> None:
    """Missing, malformed, older, and newer versions remain byte-identical."""
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
    database_path = tmp_path / "future.db"
    _build_invalid_store(database_path, "2")
    original_bytes = database_path.read_bytes()

    with pytest.raises(SchemaUnsupportedError):
        initialize_empty_store(database_path)

    assert database_path.read_bytes() == original_bytes


@pytest.mark.parametrize("opener", [open_read_connection, open_write_transaction])
def test_normal_open_rejects_an_unsupported_store(
    opener: _ConnectionOpener,
    tmp_path: Path,
) -> None:
    """Every normal connection validates schema before exposing state."""
    database_path = tmp_path / "future.db"
    _build_invalid_store(database_path, "2")

    with pytest.raises(SchemaUnsupportedError), opener(database_path):
        pytest.fail("unsupported state must not be exposed")


def test_concurrent_first_initialization_produces_one_valid_schema(
    tmp_path: Path,
) -> None:
    """Concurrent creators serialize into a single complete version-1 store."""
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
