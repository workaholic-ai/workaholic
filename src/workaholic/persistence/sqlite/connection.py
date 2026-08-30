"""Short-lived, schema-validated SQLite read and write transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from workaholic.persistence.sqlite._driver import (
    _connect,
    _raise_mapped_database_error,
    _require_database_path,
    _rollback_if_active,
)
from workaholic.persistence.sqlite.errors import SchemaUnsupportedError
from workaholic.persistence.sqlite.schema import validate_store_schema

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextmanager
def open_read_connection(
    database_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open one read-only connection pinned to a validated schema snapshot.

    Args:
        database_path: Absolute path to an existing SQLite store.

    Yields:
        A read-only connection with foreign-key enforcement enabled.

    Raises:
        SchemaUnsupportedError: If the store is missing or not exact version 5.
        StorageBusyError: If bounded lock handling is exhausted.
        StorageUnavailableError: If the database cannot be read safely.

    """
    path = _require_database_path(database_path, must_exist=True)
    connection = _connect(path, mode="ro")
    try:
        connection.execute("BEGIN")
        validate_store_schema(connection)
        yield connection
    except SchemaUnsupportedError:
        raise
    except sqlite3.DatabaseError as error:
        _raise_mapped_database_error(error)
    finally:
        _rollback_if_active(connection)
        connection.close()


@contextmanager
def open_write_transaction(
    database_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open one validated immediate transaction and commit it on success.

    Args:
        database_path: Absolute path to an existing SQLite store.

    Yields:
        A connection owning one explicit write transaction.

    Raises:
        SchemaUnsupportedError: If the store is missing or not exact version 5.
        StorageBusyError: If bounded write-lock acquisition is exhausted.
        StorageUnavailableError: If the transaction cannot complete safely.

    """
    path = _require_database_path(database_path, must_exist=True)
    connection = _connect(path, mode="rw")
    try:
        connection.execute("BEGIN IMMEDIATE")
        validate_store_schema(connection)
        yield connection
        connection.commit()
    except SchemaUnsupportedError:
        raise
    except sqlite3.DatabaseError as error:
        _raise_mapped_database_error(error)
    finally:
        _rollback_if_active(connection)
        connection.close()
