"""Unit-level SQLite tests for durable Token lifecycle semantics."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, TypedDict, cast

import pytest

from workaholic.application import (
    ActivateTokenMutation,
    AuthenticateToken,
    AuthenticationFailedError,
    BootstrapMutation,
    CreateSubjectMutation,
    GetCurrentIdentity,
    IdempotencyConflictError,
    InvalidInputError,
    InvalidTransitionError,
    IssueTokenMutation,
    ListTokens,
    PermissionDeniedError,
    RecoverLocalMutation,
    RevokeTokenMutation,
    SetSubjectEnabledMutation,
    TokenNotFoundError,
)
from workaholic.auth import generate_token, hash_token
from workaholic.domain import (
    AuditEventType,
    AuthenticatedActor,
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
    TokenStatus,
)
from workaholic.persistence.sqlite import SQLiteRepository, StorageUnavailableError

if TYPE_CHECKING:
    from pathlib import Path

_NOW: Final = datetime(2026, 8, 29, 12, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_OWNER_ID: Final = SubjectId("sub_owner")
_OWNER_TOKEN_ID: Final = TokenId("tok_owner")


@dataclass(slots=True)
class _FixedClock:
    """Mutable authoritative clock for deterministic lifecycle projections."""

    value: datetime

    def now(self) -> datetime:
        """Return the current fixed UTC time."""
        return self.value


class _MutationMetadata(TypedDict):
    """Exact keyword shape shared by identity mutation commands."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, _FixedClock]:
    """Bootstrap one repository with an active administrator Token fixture."""
    clock = _FixedClock(_NOW + timedelta(minutes=1))
    repository = SQLiteRepository((tmp_path / "local.db").resolve(), clock=clock)
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=_INSTANCE_ID,
            project_id=ProjectId("prj_local"),
            subject_id=_OWNER_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW,
            project_key="LOCAL",
            project_name="Local",
        )
    )
    _insert_active_token(
        repository,
        token_id=_OWNER_TOKEN_ID,
        subject_id=_OWNER_ID,
        digest=_digest_for(_OWNER_TOKEN_ID, fill=1),
        expires_at=_NOW + timedelta(days=30),
    )
    return repository, clock


def _insert_active_token(
    repository: SQLiteRepository,
    *,
    token_id: TokenId,
    subject_id: SubjectId,
    digest: str,
    expires_at: datetime,
) -> None:
    """Insert one trusted bootstrap fixture unavailable before provisioning."""
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
                str(token_id),
                str(_INSTANCE_ID),
                str(subject_id),
                digest,
                str(_OWNER_ID),
                _timestamp(_NOW),
                _timestamp(_NOW),
                _timestamp(expires_at),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _timestamp(value: datetime) -> str:
    """Serialize one fixed test timestamp canonically."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_token_times(
    repository: SQLiteRepository,
    token_id: TokenId,
) -> tuple[object, ...] | None:
    """Read persisted lifecycle timestamps while closing the test connection."""
    connection = sqlite3.connect(repository.database_path)
    try:
        return cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT activated_at, revoked_at FROM tokens WHERE id = ?",
                (str(token_id),),
            ).fetchone(),
        )
    finally:
        connection.close()


def _read_token_hash(
    repository: SQLiteRepository,
    token_id: TokenId,
) -> tuple[object, ...] | None:
    """Read one stored digest while closing the test connection."""
    connection = sqlite3.connect(repository.database_path)
    try:
        return cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT token_hash FROM tokens WHERE id = ?",
                (str(token_id),),
            ).fetchone(),
        )
    finally:
        connection.close()


def _digest_for(token_id: TokenId, *, fill: int) -> str:
    """Generate a canonical runtime Token and return only its digest."""
    raw = generate_token(token_id, random_bytes=lambda size: bytes([fill]) * size)
    return hash_token(raw)


def _actor(
    *,
    subject_id: SubjectId = _OWNER_ID,
    kind: SubjectKind = SubjectKind.HUMAN,
    token_id: TokenId = _OWNER_TOKEN_ID,
) -> AuthenticatedActor:
    """Build one secret-free actor matching a persisted test Token."""
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        subject_kind=kind,
        token_id=token_id,
    )


def _metadata(
    suffix: str,
    *,
    actor: AuthenticatedActor | None = None,
    at: datetime | None = None,
    idempotency_key: str | None = None,
) -> _MutationMetadata:
    """Build common authenticated mutation metadata."""
    return {
        "actor": _actor() if actor is None else actor,
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": _NOW + timedelta(minutes=2) if at is None else at,
        "idempotency_key": idempotency_key,
    }


def _create_agent(repository: SQLiteRepository, suffix: str = "agent") -> SubjectId:
    """Create one enabled Agent target through authenticated administration."""
    subject_id = SubjectId(f"sub_{suffix}")
    repository.create_subject(
        CreateSubjectMutation(
            **_metadata(f"create-{suffix}"),
            subject_id=subject_id,
            kind=SubjectKind.AGENT,
            handle=f"build-{suffix}",
            display_name=f"Build {suffix}",
        )
    )
    return subject_id


def _issue(  # noqa: PLR0913 - explicit lifecycle inputs keep tests legible.
    repository: SQLiteRepository,
    *,
    token_id: TokenId,
    subject: SubjectId,
    fill: int,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Issue one pending Token and return its separately generated digest."""
    selected_created_at = created_at or _NOW + timedelta(minutes=2)
    selected_expiry = expires_at or selected_created_at + timedelta(hours=1)
    digest = _digest_for(token_id, fill=fill)
    result = repository.issue_pending_token(
        IssueTokenMutation(
            **_metadata(
                f"issue-{token_id.value}",
                at=selected_created_at,
            ),
            token_id=token_id,
            subject=subject,
            token_digest=digest,
            expires_at=selected_expiry,
        )
    )
    assert result.token.status is TokenStatus.PENDING
    assert "hash" not in result.model_dump(mode="json")["token"]
    return digest


def test_pending_activation_authentication_and_current_identity(tmp_path: Path) -> None:
    """Pending Tokens fail closed until one atomic successful activation."""
    repository, clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_agent")
    digest = _issue(repository, token_id=token_id, subject=agent_id, fill=2)
    command = AuthenticateToken(
        token_id=token_id,
        token_digest=digest,
        expected_instance_id=_INSTANCE_ID,
        occurred_at=_NOW + timedelta(minutes=3),
    )
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(command)

    activated = repository.activate_token(
        ActivateTokenMutation(
            **_metadata(
                "activate",
                at=_NOW + timedelta(minutes=3),
                idempotency_key="activation-key",
            ),
            token_id=token_id,
        )
    )
    assert activated.token.status is TokenStatus.ACTIVE
    actor = repository.authenticate_token(command)
    assert actor == _actor(
        subject_id=agent_id,
        kind=SubjectKind.AGENT,
        token_id=token_id,
    )
    clock.value = _NOW + timedelta(minutes=4)
    current = repository.get_current_identity(GetCurrentIdentity(actor=actor))
    assert current.subject.id == agent_id
    assert current.token.status is TokenStatus.ACTIVE


def test_authentication_collapses_digest_id_instance_and_lifecycle_failures(
    tmp_path: Path,
) -> None:
    """Every invalid credential state has the same non-disclosing error."""
    repository, _clock = _repository(tmp_path)
    failures = (
        AuthenticateToken(
            token_id=_OWNER_TOKEN_ID,
            token_digest="0" * 64,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW + timedelta(minutes=1),
        ),
        AuthenticateToken(
            token_id=TokenId("tok_missing"),
            token_digest="0" * 64,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW + timedelta(minutes=1),
        ),
        AuthenticateToken(
            token_id=_OWNER_TOKEN_ID,
            token_digest=_digest_for(_OWNER_TOKEN_ID, fill=1),
            expected_instance_id=InstanceId("ins_other"),
            occurred_at=_NOW + timedelta(minutes=1),
        ),
        AuthenticateToken(
            token_id=_OWNER_TOKEN_ID,
            token_digest=_digest_for(_OWNER_TOKEN_ID, fill=1),
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW + timedelta(days=30),
        ),
    )
    for command in failures:
        with pytest.raises(AuthenticationFailedError) as captured:
            repository.authenticate_token(command)
        assert str(captured.value) == "The supplied credential is not valid."


def test_activation_is_exactly_once_and_rejects_invalid_transitions(
    tmp_path: Path,
) -> None:
    """Activation consumes its public key once and cannot revive a Token."""
    repository, _clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_once")
    _issue(repository, token_id=token_id, subject=agent_id, fill=3)
    mutation = ActivateTokenMutation(
        **_metadata(
            "activate-once",
            at=_NOW + timedelta(minutes=3),
            idempotency_key="activate-once",
        ),
        token_id=token_id,
    )
    first = repository.activate_token(mutation)
    assert repository.activate_token(mutation) == first
    with pytest.raises(IdempotencyConflictError):
        repository.activate_token(
            mutation.model_copy(update={"token_id": TokenId("tok_other")})
        )
    with pytest.raises(InvalidTransitionError):
        repository.activate_token(mutation.model_copy(update={"idempotency_key": None}))


def test_self_listing_revocation_and_admin_visibility(tmp_path: Path) -> None:
    """A Subject sees itself while administrators may inspect any target."""
    repository, clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    first_id = TokenId("tok_agent-one")
    second_id = TokenId("tok_agent-two")
    first_digest = _issue(
        repository,
        token_id=first_id,
        subject=agent_id,
        fill=4,
    )
    _issue(
        repository,
        token_id=second_id,
        subject=agent_id,
        fill=5,
        created_at=_NOW + timedelta(minutes=3),
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-agent", at=_NOW + timedelta(minutes=4)),
            token_id=first_id,
        )
    )
    agent = repository.authenticate_token(
        AuthenticateToken(
            token_id=first_id,
            token_digest=first_digest,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW + timedelta(minutes=5),
        )
    )
    clock.value = _NOW + timedelta(minutes=5)
    self_page = repository.list_tokens(ListTokens(actor=agent, limit=1))
    assert tuple(item.subject_id for item in self_page.tokens) == (agent_id,)
    assert self_page.next_cursor is not None
    next_page = repository.list_tokens(
        ListTokens(actor=agent, limit=1, cursor=self_page.next_cursor)
    )
    assert len(next_page.tokens) == 1
    admin_page = repository.list_tokens(
        ListTokens(actor=_actor(), subject="build-agent")
    )
    assert len(admin_page.tokens) == 2

    revoked = repository.revoke_token(
        RevokeTokenMutation(
            **_metadata(
                "self-revoke",
                actor=agent,
                at=_NOW + timedelta(minutes=6),
            ),
            token_id=first_id,
        )
    )
    assert revoked.token.status is TokenStatus.REVOKED
    with pytest.raises(AuthenticationFailedError):
        repository.list_tokens(ListTokens(actor=agent))


def test_administrator_revocation_is_monotonic_and_idempotent(tmp_path: Path) -> None:
    """Pending compensation and repeated admin revocation retain one outcome."""
    repository, _clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_pending-revoke")
    _issue(repository, token_id=token_id, subject=agent_id, fill=14)
    mutation = RevokeTokenMutation(
        **_metadata("revoke-pending", idempotency_key="revoke-pending"),
        token_id=token_id,
    )
    first = repository.revoke_token(mutation)
    replay = repository.revoke_token(mutation)
    repeated = repository.revoke_token(
        mutation.model_copy(update={"idempotency_key": None})
    )
    assert replay == first
    assert repeated.token.status is TokenStatus.REVOKED
    assert repeated.token.revoked_at == first.token.revoked_at
    with pytest.raises(IdempotencyConflictError):
        repository.revoke_token(
            mutation.model_copy(update={"token_id": TokenId("tok_other")})
        )


def test_non_admin_foreign_token_access_is_non_disclosing(tmp_path: Path) -> None:
    """Foreign Token IDs and Subjects are indistinguishable to ordinary actors."""
    repository, clock = _repository(tmp_path)
    first_agent = _create_agent(repository, "one")
    second_agent = _create_agent(repository, "two")
    token_id = TokenId("tok_agent-one")
    digest = _issue(
        repository,
        token_id=token_id,
        subject=first_agent,
        fill=6,
    )
    _issue(
        repository,
        token_id=TokenId("tok_agent-two"),
        subject=second_agent,
        fill=7,
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-one", at=_NOW + timedelta(minutes=3)),
            token_id=token_id,
        )
    )
    actor = repository.authenticate_token(
        AuthenticateToken(
            token_id=token_id,
            token_digest=digest,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW + timedelta(minutes=4),
        )
    )
    clock.value = _NOW + timedelta(minutes=4)
    with pytest.raises(PermissionDeniedError):
        repository.list_tokens(ListTokens(actor=actor, subject=second_agent))
    with pytest.raises(TokenNotFoundError):
        repository.revoke_token(
            RevokeTokenMutation(
                **_metadata("foreign", actor=actor),
                token_id=TokenId("tok_agent-two"),
            )
        )
    with pytest.raises(TokenNotFoundError):
        repository.revoke_token(
            RevokeTokenMutation(
                **_metadata("missing", actor=actor),
                token_id=TokenId("tok_missing"),
            )
        )


def test_expiry_is_derived_without_background_database_writes(tmp_path: Path) -> None:
    """Exact expiry changes projection and authentication without row mutation."""
    repository, clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_short")
    expiry = _NOW + timedelta(minutes=5)
    digest = _issue(
        repository,
        token_id=token_id,
        subject=agent_id,
        fill=8,
        expires_at=expiry,
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-short", at=_NOW + timedelta(minutes=3)),
            token_id=token_id,
        )
    )
    before = _read_token_times(repository, token_id)
    clock.value = expiry
    page = repository.list_tokens(ListTokens(actor=_actor(), subject=agent_id))
    assert page.tokens[0].status is TokenStatus.EXPIRED
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(
            AuthenticateToken(
                token_id=token_id,
                token_digest=digest,
                expected_instance_id=_INSTANCE_ID,
                occurred_at=expiry,
            )
        )
    after = _read_token_times(repository, token_id)
    assert after == before


def test_pending_issue_rejects_public_idempotency_and_digest_collisions(
    tmp_path: Path,
) -> None:
    """Pending state neither consumes provisioning keys nor reuses a digest."""
    repository, _clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_collision")
    digest = _issue(repository, token_id=token_id, subject=agent_id, fill=9)
    with pytest.raises(InvalidInputError):
        repository.issue_pending_token(
            IssueTokenMutation(
                **_metadata("keyed-pending", idempotency_key="not-yet"),
                token_id=TokenId("tok_keyed"),
                subject=agent_id,
                token_digest=_digest_for(TokenId("tok_keyed"), fill=10),
                expires_at=_NOW + timedelta(hours=1),
            )
        )
    with pytest.raises(StorageUnavailableError):
        repository.issue_pending_token(
            IssueTokenMutation(
                **_metadata("digest-collision"),
                token_id=TokenId("tok_collision-two"),
                subject=agent_id,
                token_digest=digest,
                expires_at=_NOW + timedelta(hours=1),
            )
        )


def test_disabled_subject_invalidates_tokens_and_cannot_receive_more(
    tmp_path: Path,
) -> None:
    """Disablement immediately closes active Tokens and new issuance."""
    repository, _clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_disabled")
    digest = _issue(repository, token_id=token_id, subject=agent_id, fill=11)
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-disabled", at=_NOW + timedelta(minutes=3)),
            token_id=token_id,
        )
    )
    repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("disable-agent", at=_NOW + timedelta(minutes=4)),
            subject=agent_id,
            expected_version=1,
            enabled=False,
        )
    )
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(
            AuthenticateToken(
                token_id=token_id,
                token_digest=digest,
                expected_instance_id=_INSTANCE_ID,
                occurred_at=_NOW + timedelta(minutes=5),
            )
        )
    with pytest.raises(PermissionDeniedError):
        _issue(
            repository,
            token_id=TokenId("tok_disabled-two"),
            subject=agent_id,
            fill=12,
        )


def test_token_metadata_restart_and_repr_never_expose_digest(tmp_path: Path) -> None:
    """Restarted reads expose lifecycle facts without recoverable Token material."""
    repository, clock = _repository(tmp_path)
    agent_id = _create_agent(repository)
    token_id = TokenId("tok_restart")
    digest = _issue(repository, token_id=token_id, subject=agent_id, fill=13)
    restarted = SQLiteRepository(repository.database_path, clock=clock)
    page = restarted.list_tokens(ListTokens(actor=_actor(), subject=agent_id))
    rendered = repr(page)
    serialized = page.model_dump_json()
    assert digest not in rendered
    assert digest not in serialized
    assert "token_hash" not in rendered
    assert "token_hash" not in serialized
    stored = _read_token_hash(repository, token_id)
    assert stored == (digest,)
    assert hashlib.sha256(digest.encode("ascii")).hexdigest() not in serialized


def test_local_recovery_atomically_replaces_only_bootstrap_human_tokens(
    tmp_path: Path,
) -> None:
    """Recovery revokes every bootstrap Token and activates one replacement."""
    repository, clock = _repository(tmp_path)
    second_owner_id = TokenId("tok_owner-second")
    second_owner_digest = _issue(
        repository,
        token_id=second_owner_id,
        subject=_OWNER_ID,
        fill=15,
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-owner-second", at=_NOW + timedelta(minutes=3)),
            token_id=second_owner_id,
        )
    )
    agent_id = _create_agent(repository, "recovery-agent")
    agent_token_id = TokenId("tok_recovery-agent")
    agent_digest = _issue(
        repository,
        token_id=agent_token_id,
        subject=agent_id,
        fill=16,
        created_at=_NOW + timedelta(minutes=4),
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-recovery-agent", at=_NOW + timedelta(minutes=5)),
            token_id=agent_token_id,
        )
    )
    occurred_at = _NOW + timedelta(minutes=10)
    replacement_id = TokenId("tok_recovered")
    replacement_digest = _digest_for(replacement_id, fill=17)
    clock.value = occurred_at

    result = repository.recover_local(
        RecoverLocalMutation(
            instance_id=_INSTANCE_ID,
            bootstrap_handle="local-operator",
            token_id=replacement_id,
            token_digest=replacement_digest,
            request_id=RequestId("req_recover-local"),
            occurred_at=occurred_at,
            expires_at=occurred_at + timedelta(days=30),
        )
    )

    assert result.subject.id == _OWNER_ID
    assert result.token.id == replacement_id
    assert result.token.status is TokenStatus.ACTIVE
    assert result.token.activated_at == occurred_at
    for old_token, old_digest in (
        (_OWNER_TOKEN_ID, _digest_for(_OWNER_TOKEN_ID, fill=1)),
        (second_owner_id, second_owner_digest),
    ):
        with pytest.raises(AuthenticationFailedError):
            repository.authenticate_token(
                AuthenticateToken(
                    token_id=old_token,
                    token_digest=old_digest,
                    expected_instance_id=_INSTANCE_ID,
                    occurred_at=occurred_at,
                )
            )
    replacement_actor = repository.authenticate_token(
        AuthenticateToken(
            token_id=replacement_id,
            token_digest=replacement_digest,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=occurred_at,
        )
    )
    assert replacement_actor.subject_id == _OWNER_ID
    agent_actor = repository.authenticate_token(
        AuthenticateToken(
            token_id=agent_token_id,
            token_digest=agent_digest,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=occurred_at,
        )
    )
    assert agent_actor.subject_id == agent_id

    connection = sqlite3.connect(repository.database_path)
    try:
        audit_rows = connection.execute(
            """
            SELECT event_type, actor_token_id
            FROM audit_events
            WHERE request_id = ?
            ORDER BY cursor
            """,
            ("req_recover-local",),
        ).fetchall()
    finally:
        connection.close()
    assert audit_rows == [
        (AuditEventType.TOKEN_REVOKED.value, None),
        (AuditEventType.TOKEN_REVOKED.value, None),
        (AuditEventType.TOKEN_ISSUED.value, None),
    ]
