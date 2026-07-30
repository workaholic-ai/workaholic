"""Phase 1 SQLite schema and short-lived connection boundaries."""

from workaholic.persistence.sqlite.actor import SQLiteLocalActorSelector
from workaholic.persistence.sqlite.connection import (
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite.errors import (
    SchemaUnsupportedError,
    StorageBusyError,
    StorageUnavailableError,
)
from workaholic.persistence.sqlite.repository import SQLitePhaseOneRepository
from workaholic.persistence.sqlite.schema import (
    SCHEMA_VERSION,
    initialize_empty_store,
    validate_store_schema,
)

__all__ = [
    "SCHEMA_VERSION",
    "SQLiteLocalActorSelector",
    "SQLitePhaseOneRepository",
    "SchemaUnsupportedError",
    "StorageBusyError",
    "StorageUnavailableError",
    "initialize_empty_store",
    "open_read_connection",
    "open_write_transaction",
    "validate_store_schema",
]
