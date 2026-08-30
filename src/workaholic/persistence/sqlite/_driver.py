"""Private SQLite driver configuration and safe error translation."""

from __future__ import annotations

import sqlite3
import stat
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, NoReturn

from workaholic.persistence.sqlite.errors import (
    SchemaUnsupportedError,
    StorageBusyError,
    StorageUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_CONNECT_TIMEOUT_SECONDS: Final = 5.0
_BUSY_TIMEOUT_MILLISECONDS: Final = 5_000
_SQLITE_PRIMARY_RESULT_MASK: Final = 0xFF
_SQLITE_BUSY_RESULTS: Final = frozenset((sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))


@contextmanager
def _initialize_connection(
    database_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open the one exclusive transaction allowed to create an empty store.

    The exclusive lock prevents readers from observing SQLite's newly created
    file before the transactional schema is committed. Normal read and write
    transactions retain their less restrictive locking behavior.

    Args:
        database_path: Absolute initialization target.

    Yields:
        A connection holding the initialization write lock.

    Raises:
        SchemaUnsupportedError: If an existing store is unsupported.
        StorageBusyError: If bounded write-lock acquisition is exhausted.
        StorageUnavailableError: If initialization cannot complete safely.

    """
    path = _require_database_path(database_path, must_exist=False)
    _prepare_parent_directory(path)
    existed_before_connect = path.exists()
    connection = _connect(path, mode="rwc")
    if not existed_before_connect:
        try:
            path.chmod(0o600)
        except OSError as error:
            connection.close()
            with suppress(OSError):
                path.unlink()
            raise StorageUnavailableError from error
    try:
        connection.execute("BEGIN EXCLUSIVE")
        yield connection
        connection.commit()
    except SchemaUnsupportedError:
        raise
    except sqlite3.DatabaseError as error:
        _raise_mapped_database_error(error)
    finally:
        _rollback_if_active(connection)
        connection.close()


def _require_database_path(value: object, *, must_exist: bool) -> Path:
    """Validate one unambiguous local SQLite path.

    Args:
        value: Candidate database path.
        must_exist: Whether absence means an unsupported missing store.

    Returns:
        The accepted absolute Path.

    Raises:
        SchemaUnsupportedError: If a required store does not exist.
        StorageUnavailableError: If the path is malformed or unsafe.

    """
    if not isinstance(value, Path) or not value.is_absolute():
        raise StorageUnavailableError
    try:
        metadata = value.lstat()
    except FileNotFoundError as error:
        if must_exist:
            raise SchemaUnsupportedError from error
        return value
    except OSError as error:
        raise StorageUnavailableError from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise StorageUnavailableError
    return value


def _prepare_parent_directory(database_path: Path) -> None:
    """Create the trusted local data directory when initialization needs it.

    Args:
        database_path: Validated absolute database path.

    Raises:
        StorageUnavailableError: If the parent cannot be created or is unsafe.

    """
    parent = database_path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = parent.stat()
    except OSError as error:
        raise StorageUnavailableError from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise StorageUnavailableError


def _connect(
    database_path: Path,
    *,
    mode: Literal["ro", "rw", "rwc"],
) -> sqlite3.Connection:
    """Create one configured SQLite connection without implicit transactions.

    Args:
        database_path: Validated absolute database path.
        mode: SQLite URI open mode.

    Returns:
        A configured short-lived connection.

    Raises:
        StorageBusyError: If bounded connection handling is exhausted.
        StorageUnavailableError: If SQLite cannot open or configure the store.

    """
    uri = f"{database_path.as_uri()}?mode={mode}"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=_CONNECT_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
        connection.execute("PRAGMA foreign_keys = ON")
        _require_foreign_keys(connection)
    except StorageUnavailableError:
        _close_after_configuration_failure(connection)
        raise
    except sqlite3.DatabaseError as error:
        _close_after_configuration_failure(connection)
        _raise_mapped_database_error(error)
    else:
        return connection


def _close_after_configuration_failure(
    connection: sqlite3.Connection | None,
) -> None:
    """Close a connection that cannot satisfy required driver configuration.

    The configuration failure remains authoritative; a secondary close failure
    must not replace its stable public error mapping.

    Args:
        connection: Partially configured connection, when opening succeeded.

    """
    if connection is not None:
        with suppress(sqlite3.DatabaseError):
            connection.close()


def _require_foreign_keys(connection: sqlite3.Connection) -> None:
    """Require successful foreign-key configuration on one connection.

    Args:
        connection: Newly opened SQLite connection.

    Raises:
        StorageUnavailableError: If SQLite did not enable enforcement.

    """
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys != (1,):
        raise StorageUnavailableError


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    """Best-effort rollback without masking the primary operation outcome.

    Args:
        connection: Connection that may own an unfinished transaction.

    """
    if connection.in_transaction:
        with suppress(sqlite3.DatabaseError):
            connection.rollback()


def _raise_mapped_database_error(error: sqlite3.DatabaseError) -> NoReturn:
    """Raise a stable safe application failure for one SQLite exception.

    Args:
        error: Driver failure to classify without exposing its message.

    Raises:
        StorageBusyError: If SQLite reported busy or locked.
        StorageUnavailableError: For every other driver failure.

    """
    error_code: object = getattr(error, "sqlite_errorcode", None)
    if (
        isinstance(error_code, int)
        and error_code & _SQLITE_PRIMARY_RESULT_MASK in _SQLITE_BUSY_RESULTS
    ):
        raise StorageBusyError from error
    raise StorageUnavailableError from error
