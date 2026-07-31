"""Semantic persistence contracts and backend adapters."""

from workaholic.persistence.sqlite import (
    SCHEMA_VERSION,
    SchemaUnsupportedError,
    SQLiteRepository,
    StorageBusyError,
    StorageUnavailableError,
    initialize_empty_store,
    open_read_connection,
    open_write_transaction,
    validate_store_schema,
)

__all__ = [
    "SCHEMA_VERSION",
    "SQLiteRepository",
    "SchemaUnsupportedError",
    "StorageBusyError",
    "StorageUnavailableError",
    "initialize_empty_store",
    "open_read_connection",
    "open_write_transaction",
    "validate_store_schema",
]
