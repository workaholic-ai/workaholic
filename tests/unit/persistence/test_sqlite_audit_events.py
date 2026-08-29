"""Transactional tests for append-only Phase 5 administrative audit events."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, TypedDict

import pytest

from workaholic.application import (
    ActivateTokenMutation,
    AssignProjectGrantMutation,
    BootstrapMutation,
    CreateSubjectMutation,
    IssueTokenMutation,
    LastInstanceAdminError,
    ProjectCreationMutation,
    ReadAuditEvents,
    RevokeProjectGrantMutation,
    RevokeTokenMutation,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    UpdateSubjectMutation,
)
from workaholic.auth import generate_token, hash_token
from workaholic.domain import (
    AuditEventType,
    AuthenticatedActor,
    InstanceId,
    ProjectId,
    ProjectRole,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
)
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from pathlib import Path

_NOW: Final = datetime(2026, 8, 29, 10, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_PROJECT_ID: Final = ProjectId("prj_local")
_OWNER_ID: Final = SubjectId("sub_owner")
_OWNER_TOKEN_ID: Final = TokenId("tok_owner")


@dataclass(slots=True)
class _FixedClock:
    """Return a deterministic authorization time for repository reads."""

    value: datetime

    def now(self) -> datetime:
        """Return the configured timezone-aware UTC timestamp."""
        return self.value


class _MutationMetadata(TypedDict):
    """Exact common metadata accepted by identity mutation commands."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None


def _repository(tmp_path: Path) -> SQLiteRepository:
    """Bootstrap an isolated store and install one active admin Token."""
    repository = SQLiteRepository(
        (tmp_path / "local.db").resolve(),
        clock=_FixedClock(_NOW + timedelta(minutes=1)),
    )
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
            subject_id=_OWNER_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW,
            project_key="LOCAL",
            project_name="Local",
        )
    )
    _insert_active_token(repository)
    return repository


def _insert_active_token(repository: SQLiteRepository) -> None:
    """Install one hash-only credential fixture without creating an audit event."""
    connection = sqlite3.connect(repository.database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute(
            """
            INSERT INTO tokens (
                id, instance_id, subject_id, token_hash, created_by,
                created_at, activated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(_OWNER_TOKEN_ID),
                str(_INSTANCE_ID),
                str(_OWNER_ID),
                hashlib.sha256(b"fixture-owner-token").hexdigest(),
                str(_OWNER_ID),
                _timestamp(_NOW),
                _timestamp(_NOW),
                _timestamp(_NOW + timedelta(days=30)),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _timestamp(value: datetime) -> str:
    """Serialize one test timestamp in canonical persisted form."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _actor() -> AuthenticatedActor:
    """Return the persisted bootstrap administrator actor context."""
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=_OWNER_ID,
        subject_kind=SubjectKind.HUMAN,
        token_id=_OWNER_TOKEN_ID,
    )


def _metadata(
    suffix: str,
    *,
    minute: int,
    idempotency_key: str | None = None,
) -> _MutationMetadata:
    """Build deterministic mutation attribution for an audit assertion."""
    return {
        "actor": _actor(),
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": _NOW + timedelta(minutes=minute),
        "idempotency_key": idempotency_key,
    }


def _create_agent(
    repository: SQLiteRepository,
    suffix: str,
    *,
    minute: int,
    idempotency_key: str | None = None,
) -> SubjectId:
    """Create one audited Agent Subject and return its identity."""
    subject_id = SubjectId(f"sub_{suffix}")
    repository.create_subject(
        CreateSubjectMutation(
            **_metadata(
                f"create-{suffix}",
                minute=minute,
                idempotency_key=idempotency_key,
            ),
            subject_id=subject_id,
            kind=SubjectKind.AGENT,
            handle=f"agent-{suffix}",
            display_name=f"Agent {suffix}",
        )
    )
    return subject_id


def test_administrative_mutations_emit_exact_order_payload_and_attribution(
    tmp_path: Path,
) -> None:
    """Every accepted baseline mutation appends its closed non-secret event."""
    repository = _repository(tmp_path)
    agent_id = _create_agent(repository, "build", minute=2)
    updated = repository.update_subject(
        UpdateSubjectMutation(
            **_metadata("update-build", minute=3),
            subject=agent_id,
            expected_version=1,
            display_name="Build agent",
        )
    ).subject
    disabled = repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("disable-build", minute=4),
            subject=agent_id,
            expected_version=updated.version,
            enabled=False,
        )
    ).subject
    enabled = repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("enable-build", minute=5),
            subject=agent_id,
            expected_version=disabled.version,
            enabled=True,
        )
    ).subject
    administrator = repository.set_instance_admin(
        SetInstanceAdminMutation(
            **_metadata("admin-build", minute=6),
            subject=agent_id,
            expected_version=enabled.version,
            is_instance_admin=True,
        )
    ).subject
    ordinary = repository.set_instance_admin(
        SetInstanceAdminMutation(
            **_metadata("unadmin-build", minute=7),
            subject=agent_id,
            expected_version=administrator.version,
            is_instance_admin=False,
        )
    ).subject
    grant = repository.assign_project_grant(
        AssignProjectGrantMutation(
            **_metadata("grant-build", minute=8),
            project=_PROJECT_ID,
            subject=agent_id,
            role=ProjectRole.AGENT,
        )
    ).grant
    repository.revoke_project_grant(
        RevokeProjectGrantMutation(
            **_metadata("revoke-grant", minute=9),
            project=_PROJECT_ID,
            subject=agent_id,
            expected_version=grant.version,
        )
    )

    token_id = TokenId("tok_build")
    expires_at = _NOW + timedelta(days=7)
    repository.issue_pending_token(
        IssueTokenMutation(
            **_metadata("issue-build", minute=10),
            token_id=token_id,
            subject=agent_id,
            token_digest=hash_token(
                generate_token(token_id, random_bytes=lambda size: bytes([7]) * size)
            ),
            expires_at=expires_at,
        )
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-build", minute=11),
            token_id=token_id,
        )
    )
    repository.revoke_token(
        RevokeTokenMutation(
            **_metadata("revoke-token", minute=12),
            token_id=token_id,
        )
    )
    repository.create_project(
        ProjectCreationMutation(
            project_id=ProjectId("prj_second"),
            request_id=RequestId("req_project-second"),
            instance_id=_INSTANCE_ID,
            actor_subject_id=_OWNER_ID,
            occurred_at=_NOW + timedelta(minutes=13),
            project_key="SECOND",
            project_name="Second",
        )
    )

    page = repository.read_audit_events(ReadAuditEvents(actor=_actor(), limit=100))
    assert tuple(event.cursor for event in page.events) == tuple(range(1, 13))
    assert tuple(event.event_type for event in page.events) == (
        AuditEventType.INSTANCE_BOOTSTRAPPED,
        AuditEventType.SUBJECT_CREATED,
        AuditEventType.SUBJECT_UPDATED,
        AuditEventType.SUBJECT_DISABLED,
        AuditEventType.SUBJECT_ENABLED,
        AuditEventType.INSTANCE_ADMIN_GRANTED,
        AuditEventType.INSTANCE_ADMIN_REVOKED,
        AuditEventType.PROJECT_GRANT_ASSIGNED,
        AuditEventType.PROJECT_GRANT_REVOKED,
        AuditEventType.TOKEN_ISSUED,
        AuditEventType.TOKEN_REVOKED,
        AuditEventType.PROJECT_CREATED,
    )
    assert page.events[0].actor_token_id is None
    assert page.events[0].payload == {
        "instance_id": str(_INSTANCE_ID),
        "subject_id": str(_OWNER_ID),
        "project_id": str(_PROJECT_ID),
        "project_key": "LOCAL",
        "grant_role": ProjectRole.OWNER.value,
    }
    assert page.events[1].actor_kind is SubjectKind.HUMAN
    assert page.events[1].actor_token_id == _OWNER_TOKEN_ID
    assert page.events[1].request_id == RequestId("req_create-build")
    assert page.events[2].payload == {
        "subject_id": str(agent_id),
        "changed_fields": ("display_name",),
        "version": 2,
    }
    assert page.events[8].payload == {
        "project_id": str(_PROJECT_ID),
        "subject_id": str(agent_id),
        "previous_role": ProjectRole.AGENT.value,
        "previous_version": grant.version,
    }
    assert page.events[9].payload == {
        "token_id": str(token_id),
        "subject_id": str(agent_id),
        "expires_at": _timestamp(expires_at),
    }
    assert page.events[-1].actor_token_id is None
    assert ordinary.version == 6


def test_audit_pagination_is_stable_across_replay_and_restart(tmp_path: Path) -> None:
    """Cursors are ascending, replay-safe, and durable across repository restart."""
    repository = _repository(tmp_path)
    mutation = CreateSubjectMutation(
        **_metadata("create-replay", minute=2, idempotency_key="create-replay"),
        subject_id=SubjectId("sub_replay"),
        kind=SubjectKind.AGENT,
        handle="agent-replay",
        display_name="Replay agent",
    )
    repository.create_subject(mutation)
    repository.create_subject(mutation)

    first = repository.read_audit_events(ReadAuditEvents(actor=_actor(), limit=1))
    restarted = SQLiteRepository(
        repository.database_path,
        clock=_FixedClock(_NOW + timedelta(minutes=3)),
    )
    second = restarted.read_audit_events(
        ReadAuditEvents(actor=_actor(), after=first.next_cursor, limit=1)
    )
    empty = restarted.read_audit_events(
        ReadAuditEvents(actor=_actor(), after=second.next_cursor, limit=1)
    )
    assert tuple(event.event_type for event in first.events) == (
        AuditEventType.INSTANCE_BOOTSTRAPPED,
    )
    assert tuple(event.event_type for event in second.events) == (
        AuditEventType.SUBJECT_CREATED,
    )
    assert empty.events == ()
    assert empty.next_cursor == second.next_cursor == 2


def test_rejected_mutation_rolls_back_without_audit_event(tmp_path: Path) -> None:
    """Invariant failures append neither state nor administrative history."""
    repository = _repository(tmp_path)
    before = repository.read_audit_events(ReadAuditEvents(actor=_actor())).events

    with pytest.raises(LastInstanceAdminError):
        repository.set_instance_admin(
            SetInstanceAdminMutation(
                **_metadata("remove-last-admin", minute=2),
                subject=_OWNER_ID,
                expected_version=1,
                is_instance_admin=False,
            )
        )

    after = repository.read_audit_events(ReadAuditEvents(actor=_actor())).events
    assert after == before


def test_concurrent_mutations_allocate_unique_gapless_audit_cursors(
    tmp_path: Path,
) -> None:
    """Independent writers serialize event cursor allocation through SQLite."""
    repository = _repository(tmp_path)

    def create(index: int) -> None:
        """Create one unique Agent from an independent repository connection."""
        _create_agent(
            SQLiteRepository(repository.database_path),
            f"parallel-{index}",
            minute=2 + index,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        tuple(executor.map(create, range(8)))

    page = repository.read_audit_events(ReadAuditEvents(actor=_actor(), limit=100))
    assert tuple(event.cursor for event in page.events) == tuple(range(1, 10))
    assert len({event.id for event in page.events}) == 9


def test_audit_and_idempotency_records_never_store_credentials(tmp_path: Path) -> None:
    """Audit payloads and replay outcomes exclude raw and hashed credentials."""
    repository = _repository(tmp_path)
    agent_id = _create_agent(repository, "secret-scan", minute=2)
    token_id = TokenId("tok_secret_scan")
    raw_token = generate_token(
        token_id,
        random_bytes=lambda size: bytes([13]) * size,
    )
    digest = hash_token(raw_token)
    repository.issue_pending_token(
        IssueTokenMutation(
            **_metadata("issue-secret", minute=3),
            token_id=token_id,
            subject=agent_id,
            token_digest=digest,
            expires_at=_NOW + timedelta(days=1),
        )
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-secret", minute=4, idempotency_key="activate"),
            token_id=token_id,
        )
    )

    connection = sqlite3.connect(repository.database_path)
    try:
        persisted = "\n".join(
            str(value)
            for row in connection.execute(
                """
                SELECT payload_json FROM audit_events
                UNION ALL
                SELECT outcome_json FROM idempotency_records
                """
            )
            for value in row
        )
    finally:
        connection.close()
    assert raw_token.get_secret_value() not in persisted
    assert digest not in persisted
