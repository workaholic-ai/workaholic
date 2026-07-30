"""Transactional Phase 2 SQLite schema creation and exact validation."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

from workaholic.persistence.sqlite._driver import _initialize_connection
from workaholic.persistence.sqlite.errors import SchemaUnsupportedError

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION: Final = 2

_SCHEMA_STATEMENTS: Final = (
    """
    CREATE TABLE store_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
    ) STRICT
    """,
    """
    CREATE TABLE instances (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'ins_*'),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            )
    ) STRICT
    """,
    """
    CREATE TABLE subjects (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'sub_*'),
        kind TEXT NOT NULL CHECK (kind IN ('human')),
        display_name TEXT NOT NULL
            CHECK (
                length(display_name) BETWEEN 1 AND 200
                AND display_name = trim(display_name)
            ),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        is_instance_admin INTEGER NOT NULL CHECK (is_instance_admin IN (0, 1))
    ) STRICT
    """,
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'prj_*'),
        instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE RESTRICT,
        key TEXT NOT NULL
            CHECK (
                length(key) BETWEEN 2 AND 16
                AND key NOT GLOB '*[^A-Z0-9]*'
                AND substr(key, 1, 1) GLOB '[A-Z]'
            ),
        name TEXT NOT NULL
            CHECK (
                length(name) BETWEEN 1 AND 200
                AND name = trim(name)
            ),
        next_task_number INTEGER NOT NULL
            CHECK (next_task_number >= 1),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        UNIQUE (instance_id, key)
    ) STRICT
    """,
    """
    CREATE TABLE project_grants (
        subject_id TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        project_id TEXT NOT NULL
            REFERENCES projects(id) ON DELETE RESTRICT,
        role TEXT NOT NULL CHECK (role IN ('owner')),
        PRIMARY KEY (subject_id, project_id)
    ) STRICT
    """,
    """
    CREATE TABLE tasks (
        uid TEXT PRIMARY KEY
            CHECK (length(uid) BETWEEN 5 AND 132 AND uid GLOB 'tsk_*'),
        project_id TEXT NOT NULL
            REFERENCES projects(id) ON DELETE RESTRICT,
        number INTEGER NOT NULL CHECK (number >= 1),
        key TEXT NOT NULL
            CHECK (
                length(key) BETWEEN 4 AND 37
                AND key NOT GLOB '*[^A-Z0-9-]*'
                AND key GLOB ('*-' || CAST(number AS TEXT))
            ),
        title TEXT NOT NULL
            CHECK (
                length(title) BETWEEN 1 AND 200
                AND title = trim(title)
            ),
        objective TEXT NOT NULL
            CHECK (
                length(objective) BETWEEN 1 AND 4000
                AND objective = trim(objective)
            ),
        state TEXT NOT NULL CHECK (state IN ('open')),
        priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
        version INTEGER NOT NULL CHECK (version >= 1),
        created_by TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        updated_at TEXT NOT NULL
            CHECK (
                length(updated_at) BETWEEN 20 AND 27
                AND substr(updated_at, 11, 1) = 'T'
                AND substr(updated_at, -1, 1) = 'Z'
                AND updated_at >= created_at
            ),
        UNIQUE (project_id, number),
        UNIQUE (project_id, key),
        UNIQUE (key),
        UNIQUE (uid, project_id)
    ) STRICT
    """,
    """
    CREATE TABLE task_events (
        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE
            CHECK (length(id) BETWEEN 5 AND 132 AND id GLOB 'evt_*'),
        task_uid TEXT NOT NULL,
        project_id TEXT NOT NULL,
        actor_subject_id TEXT NOT NULL
            REFERENCES subjects(id) ON DELETE RESTRICT,
        request_id TEXT NOT NULL
            CHECK (
                length(request_id) BETWEEN 5 AND 132
                AND request_id GLOB 'req_*'
            ),
        event_type TEXT NOT NULL CHECK (event_type IN ('task_created')),
        occurred_at TEXT NOT NULL
            CHECK (
                length(occurred_at) BETWEEN 20 AND 27
                AND substr(occurred_at, 11, 1) = 'T'
                AND substr(occurred_at, -1, 1) = 'Z'
            ),
        payload_json TEXT NOT NULL
            CHECK (
                length(payload_json) BETWEEN 2 AND 16384
                AND substr(payload_json, 1, 1) = '{'
                AND substr(payload_json, -1, 1) = '}'
                AND json_valid(payload_json)
                AND json_type(payload_json) = 'object'
            ),
        FOREIGN KEY (task_uid, project_id)
            REFERENCES tasks(uid, project_id) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE idempotency_records (
        subject_scope TEXT NOT NULL
            CHECK (
                length(subject_scope) BETWEEN 1 AND 200
                AND subject_scope = trim(subject_scope)
            ),
        operation TEXT NOT NULL
            CHECK (
                operation IN (
                    'bootstrap.local_project',
                    'project.create',
                    'task.create'
                )
            ),
        caller_key TEXT NOT NULL
            CHECK (
                length(caller_key) BETWEEN 1 AND 200
                AND caller_key = trim(caller_key)
            ),
        request_fingerprint TEXT NOT NULL
            CHECK (
                length(request_fingerprint) BETWEEN 1 AND 256
                AND request_fingerprint = trim(request_fingerprint)
            ),
        outcome_json TEXT NOT NULL
            CHECK (
                length(outcome_json) BETWEEN 2 AND 16384
                AND substr(outcome_json, 1, 1) = '{'
                AND substr(outcome_json, -1, 1) = '}'
                AND json_valid(outcome_json)
                AND json_type(outcome_json) = 'object'
            ),
        created_at TEXT NOT NULL
            CHECK (
                length(created_at) BETWEEN 20 AND 27
                AND substr(created_at, 11, 1) = 'T'
                AND substr(created_at, -1, 1) = 'Z'
            ),
        PRIMARY KEY (subject_scope, operation, caller_key)
    ) STRICT
    """,
    """
    INSERT INTO store_metadata (singleton, schema_version)
    VALUES (1, 2)
    """,
)


def initialize_empty_store(database_path: Path) -> None:
    """Atomically create or accept one empty Phase 2 SQLite store.

    Concurrent callers serialize through a bounded immediate transaction. An
    existing nonempty store is validated and never repaired or migrated.

    Args:
        database_path: Absolute target path for the SQLite database.

    Raises:
        SchemaUnsupportedError: If an existing store is not exact version 2.
        StorageBusyError: If another writer outlives the bounded lock wait.
        StorageUnavailableError: If storage cannot be initialized safely.

    """
    with _initialize_connection(database_path) as connection:
        if _contains_application_schema(connection):
            validate_store_schema(connection)
            return
        _create_schema(connection)
        validate_store_schema(connection)


def validate_store_schema(connection: sqlite3.Connection) -> None:
    """Require exactly one supported schema-version metadata row.

    Validation is strictly read-only. It does not create, repair, migrate, or
    otherwise interpret a missing or unsupported store.

    Args:
        connection: Open SQLite connection to inspect.

    Raises:
        SchemaUnsupportedError: If metadata is absent, malformed, or not version 2.

    """
    candidate: object = connection
    if not isinstance(candidate, sqlite3.Connection):
        raise SchemaUnsupportedError
    try:
        rows = connection.execute(
            """
            SELECT singleton, schema_version
            FROM store_metadata
            ORDER BY singleton
            LIMIT 2
            """
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaUnsupportedError from error
    if len(rows) != 1:
        raise SchemaUnsupportedError
    singleton, schema_version = rows[0]
    if (
        type(singleton) is not int
        or singleton != 1
        or type(schema_version) is not int
        or schema_version != SCHEMA_VERSION
    ):
        raise SchemaUnsupportedError


def _contains_application_schema(connection: sqlite3.Connection) -> bool:
    """Return whether a transaction sees any non-SQLite schema object.

    Args:
        connection: Initialization transaction connection.

    Returns:
        Whether application-owned schema content already exists.

    """
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
          AND type IN ('table', 'index', 'view', 'trigger')
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _create_schema(connection: sqlite3.Connection) -> None:
    """Execute the fixed schema inside the caller-owned transaction.

    Args:
        connection: Initialization transaction connection.

    """
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
