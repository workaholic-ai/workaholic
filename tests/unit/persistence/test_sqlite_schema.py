"""Focused unit tests for the disposable Phase 5 SQLite schema."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

import pytest

from workaholic.persistence.sqlite import SCHEMA_VERSION, initialize_empty_store

if TYPE_CHECKING:
    from pathlib import Path

_NOW: Final = "2026-08-29T10:00:00.000000Z"
_LATER: Final = "2026-08-29T11:00:00.000000Z"


def _open_store(tmp_path: Path) -> sqlite3.Connection:
    """Create and open one isolated schema with foreign keys enabled.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Writable connection to a freshly initialized store.

    """
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _seed_subject_graph(connection: sqlite3.Connection) -> None:
    """Insert one valid Instance and its bootstrap Subject.

    Args:
        connection: Writable Phase 5 connection.

    """
    connection.execute(
        "INSERT INTO instances (id, created_at) VALUES (?, ?)",
        ("ins_local", _NOW),
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
            _NOW,
            _NOW,
        ),
    )


def test_schema_version_five_exposes_identity_tables_without_raw_secrets(
    tmp_path: Path,
) -> None:
    """The exact schema adds Token metadata and audit storage only."""
    connection = _open_store(tmp_path)
    try:
        assert SCHEMA_VERSION == 5
        assert connection.execute(
            "SELECT schema_version FROM store_metadata WHERE singleton = 1"
        ).fetchone() == (5,)
        token_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tokens)")
        }
        audit_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_events)")
        }
        assert token_columns == {
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
        }
        assert not {"raw_token", "secret", "credential"}.intersection(token_columns)
        assert {"cursor", "actor_token_id", "payload_json"} <= audit_columns
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("kind", "handle", "version"),
    [
        ("service", "valid-handle", 1),
        ("human", "Uppercase", 1),
        ("agent", "a", 1),
        ("agent", "valid-handle", 0),
    ],
)
def test_subject_constraints_reject_noncanonical_identity_rows(
    kind: str,
    handle: str,
    version: int,
    tmp_path: Path,
) -> None:
    """Subject kinds, handles, and optimistic versions are physical invariants."""
    connection = _open_store(tmp_path)
    try:
        connection.execute(
            "INSERT INTO instances (id, created_at) VALUES (?, ?)",
            ("ins_local", _NOW),
        )
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
                    "sub_invalid",
                    "ins_local",
                    kind,
                    handle,
                    "Invalid",
                    1,
                    0,
                    version,
                    "sub_invalid",
                    _NOW,
                    _NOW,
                ),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "token_hash",
    ["a" * 63, "A" * 64, "g" * 64],
)
def test_token_hash_constraints_accept_only_lowercase_sha256(
    token_hash: str,
    tmp_path: Path,
) -> None:
    """Token storage accepts only a full lowercase SHA-256 digest."""
    connection = _open_store(tmp_path)
    try:
        _seed_subject_graph(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO tokens (
                    id, instance_id, subject_id, token_hash, created_by,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tok_invalid",
                    "ins_local",
                    "sub_local",
                    token_hash,
                    "sub_local",
                    _NOW,
                    _LATER,
                ),
            )
    finally:
        connection.close()
