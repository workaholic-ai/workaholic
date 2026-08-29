"""Storage-level conformance tests for the Phase 5 identity schema."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Final

import pytest

from workaholic.application import (
    AddTaskDependencyMutation,
    ApproveResultMutation,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    ProjectCreationMutation,
    RejectResultMutation,
    ReleaseClaimMutation,
    RenewClaimMutation,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    SubmitHumanResultMutation,
    TaskCreationMutation,
    TaskUpdateMutation,
)
from workaholic.persistence.sqlite import (
    SchemaUnsupportedError,
    SQLiteRepository,
    initialize_empty_store,
    validate_store_schema,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.contract

_NOW: Final = "2026-08-29T10:00:00.000000Z"
_LATER: Final = "2026-08-29T11:00:00.000000Z"


def _connect_phase_five(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create one exact store and return its writable physical connection.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Store path and writable foreign-key-enforcing connection.

    """
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return database_path, connection


def _insert_instance_and_subject(
    connection: sqlite3.Connection,
    *,
    instance_id: str,
    subject_id: str,
    kind: str,
    handle: str,
) -> None:
    """Insert one isolated Instance with a self-created bootstrap Subject.

    Args:
        connection: Writable Phase 5 connection.
        instance_id: Opaque Instance identity.
        subject_id: Opaque Subject identity.
        kind: Closed Human or Agent kind.
        handle: Canonical Instance-scoped handle.

    """
    connection.execute(
        "INSERT INTO instances (id, created_at) VALUES (?, ?)",
        (instance_id, _NOW),
    )
    connection.execute(
        """
        INSERT INTO subjects (
            id, instance_id, kind, handle, display_name, enabled,
            is_instance_admin, version, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subject_id,
            instance_id,
            kind,
            handle,
            handle,
            1,
            int(kind == "human"),
            1,
            subject_id,
            _NOW,
            _NOW,
        ),
    )


def test_sqlite_repository_exposes_current_identity_lifecycle_ports(
    tmp_path: Path,
) -> None:
    """The concrete façade keeps current identity entry points explicit."""
    repository = SQLiteRepository((tmp_path / "local.db").resolve())
    for method_name in (
        "authenticate_token",
        "authorize_actor",
        "get_current_identity",
        "create_subject",
        "list_subjects",
        "update_subject",
        "set_subject_enabled",
        "set_instance_admin",
        "issue_pending_token",
        "activate_token",
        "list_tokens",
        "revoke_token",
        "assign_project_grant",
        "list_project_grants",
        "revoke_project_grant",
        "read_audit_events",
    ):
        assert callable(getattr(repository, method_name))


def test_task_mutations_carry_secret_free_internal_actor_context() -> None:
    """Every Phase 5 task boundary accepts but never serializes its actor."""
    for mutation_type in (
        ProjectCreationMutation,
        TaskCreationMutation,
        TaskUpdateMutation,
        AddTaskDependencyMutation,
        SubmitHumanResultMutation,
        ApproveResultMutation,
        RejectResultMutation,
        ClaimTaskMutation,
        ClaimNextTaskMutation,
        RenewClaimMutation,
        ReleaseClaimMutation,
        ReportTaskProgressMutation,
        SubmitAgentResultMutation,
    ):
        actor_field = mutation_type.model_fields["actor"]
        assert actor_field.default is None
        assert actor_field.exclude is True
        assert actor_field.repr is False


def test_version_four_store_is_rejected_without_any_mutation(tmp_path: Path) -> None:
    """The disposable-schema policy never upgrades or resets a Phase 4 store."""
    database_path = tmp_path / "phase-four.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE store_metadata (
            singleton INTEGER PRIMARY KEY,
            schema_version INTEGER NOT NULL
        )
        """
    )
    connection.execute("INSERT INTO store_metadata VALUES (1, 4)")
    connection.commit()
    before = database_path.read_bytes()

    with pytest.raises(SchemaUnsupportedError):
        validate_store_schema(connection)
    connection.close()
    assert database_path.read_bytes() == before

    with pytest.raises(SchemaUnsupportedError):
        initialize_empty_store(database_path)
    assert database_path.read_bytes() == before


def test_instance_scoped_handles_and_grants_cannot_cross_instances(
    tmp_path: Path,
) -> None:
    """Composite foreign keys prevent accidental cross-tenant identity graphs."""
    _, connection = _connect_phase_five(tmp_path)
    try:
        _insert_instance_and_subject(
            connection,
            instance_id="ins_alpha",
            subject_id="sub_alpha",
            kind="human",
            handle="operator",
        )
        _insert_instance_and_subject(
            connection,
            instance_id="ins_beta",
            subject_id="sub_beta",
            kind="human",
            handle="operator",
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("prj_alpha", "ins_alpha", "ALPHA", "Alpha", 1, _NOW),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO project_grants (
                    instance_id, subject_id, project_id, role, version,
                    granted_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ins_alpha",
                    "sub_beta",
                    "prj_alpha",
                    "viewer",
                    1,
                    "sub_alpha",
                    _NOW,
                    _NOW,
                ),
            )
        connection.rollback()
    finally:
        connection.close()


def test_audit_actor_token_must_belong_to_the_actor_and_instance(
    tmp_path: Path,
) -> None:
    """Audit attribution cannot link a bearer Token to a different Subject."""
    _, connection = _connect_phase_five(tmp_path)
    try:
        _insert_instance_and_subject(
            connection,
            instance_id="ins_local",
            subject_id="sub_owner",
            kind="human",
            handle="local-operator",
        )
        connection.execute(
            """
            INSERT INTO subjects (
                id, instance_id, kind, handle, display_name, enabled,
                is_instance_admin, version, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sub_agent",
                "ins_local",
                "agent",
                "build-agent",
                "Build agent",
                1,
                0,
                1,
                "sub_owner",
                _NOW,
                _NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO tokens (
                id, instance_id, subject_id, token_hash, created_by,
                created_at, activated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tok_agent",
                "ins_local",
                "sub_agent",
                "a" * 64,
                "sub_owner",
                _NOW,
                _NOW,
                _LATER,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, instance_id, actor_subject_id, actor_kind, actor_token_id,
                request_id, event_type, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aev_valid",
                "ins_local",
                "sub_agent",
                "agent",
                "tok_agent",
                "req_valid",
                "token_issued",
                _NOW,
                "{}",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, instance_id, actor_subject_id, actor_kind,
                    actor_token_id, request_id, event_type, occurred_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "aev_invalid",
                    "ins_local",
                    "sub_owner",
                    "human",
                    "tok_agent",
                    "req_invalid",
                    "token_revoked",
                    _NOW,
                    "{}",
                ),
            )
    finally:
        connection.close()
