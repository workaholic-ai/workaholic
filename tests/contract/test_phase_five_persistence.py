"""Storage-level conformance tests for the Phase 5 identity schema."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, cast
from unittest.mock import patch

import pytest
from tests.contract.phase_five import (
    DeterministicPhaseFiveIdentifierFactory,
    PhaseFiveIdentifierFactory,
    PhaseFiveRepository,
    PhaseFiveRepositoryFactory,
    PhaseFiveTransactionFailurePoint,
    actor_for,
    grant_mutation,
    phase_five_time,
    subject_mutation,
    token_mutation,
)
from tests.contract.phase_one import bootstrap_mutation
from tests.contract.test_phase_four_persistence import (
    PhaseFourPersistenceContract,
    _SQLitePhaseFourRepositoryFactory,
)

from workaholic.application import (
    ActivateTokenMutation,
    AddTaskDependencyMutation,
    ApproveResultMutation,
    AuthenticateToken,
    BootstrapResult,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetCurrentIdentity,
    ListProjectGrants,
    ListSubjects,
    ListTokens,
    ProjectCreationMutation,
    ReadAuditEvents,
    RejectResultMutation,
    ReleaseClaimMutation,
    RenewClaimMutation,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    SubmitHumanResultMutation,
    TaskCreationMutation,
    TaskUpdateMutation,
)
from workaholic.auth import generate_token, hash_token
from workaholic.domain import (
    AuditEventType,
    AuthenticatedActor,
    ProjectRole,
    RequestId,
    TokenId,
    TokenStatus,
)
from workaholic.persistence.sqlite import (
    SchemaUnsupportedError,
    SQLiteRepository,
    initialize_empty_store,
    validate_store_schema,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

pytestmark = pytest.mark.contract

_NOW: Final = "2026-08-29T10:00:00.000000Z"
_LATER: Final = "2026-08-29T11:00:00.000000Z"


def _zero_bytes(size: int) -> bytes:
    """Return deterministic zero-filled Token entropy."""
    return bytes(size)


def _worker_bytes(size: int) -> bytes:
    """Return deterministic worker-Token entropy."""
    return b"w" * size


def _test_token_bytes(size: int) -> bytes:
    """Return deterministic rollback-Token entropy."""
    return b"t" * size


@dataclass(frozen=True, slots=True)
class _SQLitePhaseFiveRepositoryFactory(_SQLitePhaseFourRepositoryFactory):
    """Adapt production SQLite to the cumulative Phase 5 factory."""

    def identifiers(self, namespace: str) -> PhaseFiveIdentifierFactory:
        """Construct one complete deterministic Phase 5 identity sequence."""
        return DeterministicPhaseFiveIdentifierFactory(namespace)

    def bootstrap_authenticated(
        self,
        root: Path,
        namespace: str,
    ) -> tuple[PhaseFiveRepository, BootstrapResult, AuthenticatedActor]:
        """Create one bootstrap graph and seed its active Human Token."""
        repository = SQLiteRepository(
            root / "local.db",
            clock=self.clock(offset=0),
        )
        bootstrap = repository.bootstrap_local_project(
            bootstrap_mutation(
                namespace,
                occurred_at=phase_five_time(),
            )
        )
        token_id = TokenId(f"tok_{namespace}_root")
        raw_token = generate_token(token_id, random_bytes=_zero_bytes)
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
                    str(bootstrap.instance.id),
                    str(bootstrap.subject.id),
                    hash_token(raw_token),
                    str(bootstrap.subject.id),
                    _serialize_time(phase_five_time()),
                    _serialize_time(phase_five_time()),
                    _serialize_time(phase_five_time() + timedelta(days=30)),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return repository, bootstrap, actor_for(bootstrap, token_id=token_id)

    def inject_phase_five_failure(
        self,
        point: PhaseFiveTransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Patch one SQLite identity write at an adapter-neutral boundary."""
        targets = {
            PhaseFiveTransactionFailurePoint.SUBJECT_AUDIT: (
                "workaholic.persistence.sqlite._subjects.append_audit_event"
            ),
            PhaseFiveTransactionFailurePoint.SUBJECT_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._subjects._record_replay"
            ),
            PhaseFiveTransactionFailurePoint.TOKEN_AUDIT: (
                "workaholic.persistence.sqlite._tokens.append_audit_event"
            ),
            PhaseFiveTransactionFailurePoint.TOKEN_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._tokens._record_replay"
            ),
            PhaseFiveTransactionFailurePoint.GRANT_AUDIT: (
                "workaholic.persistence.sqlite._grants.append_audit_event"
            ),
            PhaseFiveTransactionFailurePoint.GRANT_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._grants._record_replay"
            ),
        }
        return cast(
            "AbstractContextManager[None]",
            patch(
                targets[point],
                side_effect=RuntimeError(f"injected {point.value} failure"),
            ),
        )


def _serialize_time(value: datetime) -> str:
    """Serialize one UTC fixture timestamp in canonical SQLite form."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class PhaseFivePersistenceContract(PhaseFourPersistenceContract):
    """Reusable cumulative observable contract for identity persistence."""

    @pytest.fixture
    def repository_factory(self) -> PhaseFiveRepositoryFactory:
        """Provide the adapter factory under cumulative conformance."""
        message = "A concrete Phase 5 repository contract must provide its factory."
        raise NotImplementedError(message)

    def test_factory_provides_phase_five_ids_and_restart_authentication(
        self,
        repository_factory: PhaseFiveRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Identity IDs are deterministic and committed auth survives restart."""
        first = repository_factory.identifiers("identity")
        second = repository_factory.identifiers("identity")
        assert str(first.new_token_id()) == "tok_identity_1"
        assert str(first.new_audit_event_id()) == "aev_identity_1"
        assert str(second.new_token_id()) == "tok_identity_1"

        root = tmp_path / "restart"
        _, bootstrap, actor = repository_factory.bootstrap_authenticated(
            root,
            "restart",
        )
        reopened = cast("PhaseFiveRepository", repository_factory.create(root))
        assert (
            reopened.get_current_identity(GetCurrentIdentity(actor=actor)).subject.id
            == bootstrap.subject.id
        )

    def test_subject_token_grant_and_audit_lifecycle_is_secret_free(
        self,
        repository_factory: PhaseFiveRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """One identity graph is durable, attributable, and never returns secrets."""
        repository, bootstrap, actor = repository_factory.bootstrap_authenticated(
            tmp_path / "lifecycle",
            "lifecycle",
        )
        created = repository.create_subject(
            subject_mutation(actor, "worker", idempotency_key="create-worker")
        ).subject
        replayed = repository.create_subject(
            subject_mutation(actor, "worker", idempotency_key="create-worker")
        ).subject
        assert replayed == created

        token_id = TokenId("tok_worker")
        raw_token = generate_token(token_id, random_bytes=_worker_bytes)
        digest = hash_token(raw_token)
        pending = repository.issue_pending_token(
            token_mutation(actor, created.id, "worker", digest=digest)
        ).token
        assert pending.status is TokenStatus.PENDING
        active = repository.activate_token(
            ActivateTokenMutation(
                actor=actor,
                request_id=RequestId("req_activate_worker"),
                occurred_at=phase_five_time(3),
                idempotency_key="activate-worker",
                token_id=token_id,
            )
        ).token
        assert active.status is TokenStatus.ACTIVE
        authenticated = repository.authenticate_token(
            AuthenticateToken(
                token_id=token_id,
                token_digest=digest,
                expected_instance_id=bootstrap.instance.id,
                occurred_at=phase_five_time(4),
            )
        )
        assert authenticated.subject_id == created.id

        assigned = repository.assign_project_grant(
            grant_mutation(
                actor,
                created.id,
                bootstrap.project.id,
                "worker",
                role=ProjectRole.AGENT,
                idempotency_key="grant-worker",
            )
        ).grant
        assert assigned.role is ProjectRole.AGENT
        subjects = repository.list_subjects(ListSubjects(actor=actor)).subjects
        grants = repository.list_project_grants(
            ListProjectGrants(actor=actor, project=bootstrap.project.id)
        ).grants
        tokens = repository.list_tokens(
            ListTokens(actor=actor, subject=created.id)
        ).tokens
        assert {subject.id for subject in subjects} == {
            bootstrap.subject.id,
            created.id,
        }
        assert any(grant.subject_id == created.id for grant in grants)
        assert tokens == (active,)
        serialized = repr(tokens)
        assert raw_token.get_secret_value() not in serialized
        assert digest not in serialized

        events = repository.read_audit_events(
            ReadAuditEvents(actor=actor, limit=100)
        ).events
        assert {
            AuditEventType.SUBJECT_CREATED,
            AuditEventType.TOKEN_ISSUED,
            AuditEventType.PROJECT_GRANT_ASSIGNED,
        }.issubset({event.event_type for event in events})
        assert all(event.actor_subject_id == actor.subject_id for event in events[1:])

    @pytest.mark.parametrize(
        "point",
        [
            PhaseFiveTransactionFailurePoint.SUBJECT_AUDIT,
            PhaseFiveTransactionFailurePoint.SUBJECT_IDEMPOTENCY,
        ],
    )
    def test_subject_failures_roll_back_every_observable_record(
        self,
        repository_factory: PhaseFiveRepositoryFactory,
        tmp_path: Path,
        point: PhaseFiveTransactionFailurePoint,
    ) -> None:
        """Subject audit and replay failures expose no partial identity state."""
        repository, _, actor = repository_factory.bootstrap_authenticated(
            tmp_path / point.value,
            point.value,
        )
        before_events = repository.read_audit_events(
            ReadAuditEvents(actor=actor, limit=100)
        )
        mutation = subject_mutation(
            actor,
            f"rollback-{point.value}",
            idempotency_key=f"idem-{point.value}",
        )
        before = repository.list_subjects(ListSubjects(actor=actor))

        with (
            repository_factory.inject_phase_five_failure(point),
            pytest.raises(RuntimeError, match="injected"),
        ):
            repository.create_subject(mutation)
        assert repository.list_subjects(ListSubjects(actor=actor)) == before
        assert (
            repository.read_audit_events(ReadAuditEvents(actor=actor, limit=100))
            == before_events
        )
        repository.create_subject(mutation)
        assert len(repository.list_subjects(ListSubjects(actor=actor)).subjects) == (
            len(before.subjects) + 1
        )

    @pytest.mark.parametrize(
        "point",
        [
            PhaseFiveTransactionFailurePoint.TOKEN_AUDIT,
            PhaseFiveTransactionFailurePoint.TOKEN_IDEMPOTENCY,
        ],
    )
    def test_token_failures_leave_the_token_pending(
        self,
        repository_factory: PhaseFiveRepositoryFactory,
        tmp_path: Path,
        point: PhaseFiveTransactionFailurePoint,
    ) -> None:
        """Token activation is atomic with audit and replay persistence."""
        repository, bootstrap, actor = repository_factory.bootstrap_authenticated(
            tmp_path / point.value,
            point.value,
        )
        token_id = TokenId(f"tok_{point.value}")
        raw = generate_token(token_id, random_bytes=_test_token_bytes)
        repository.issue_pending_token(
            token_mutation(
                actor,
                bootstrap.subject.id,
                point.value,
                digest=hash_token(raw),
            )
        )
        mutation = ActivateTokenMutation(
            actor=actor,
            request_id=RequestId(f"req_{point.value}"),
            occurred_at=phase_five_time(4),
            idempotency_key=f"idem-{point.value}",
            token_id=token_id,
        )
        with (
            repository_factory.inject_phase_five_failure(point),
            pytest.raises(RuntimeError, match="injected"),
        ):
            repository.activate_token(mutation)
        token = repository.list_tokens(
            ListTokens(actor=actor, subject=bootstrap.subject.id)
        ).tokens[-1]
        assert token.id == token_id
        assert token.status is TokenStatus.PENDING
        assert repository.activate_token(mutation).token.status is TokenStatus.ACTIVE

    @pytest.mark.parametrize(
        "point",
        [
            PhaseFiveTransactionFailurePoint.GRANT_AUDIT,
            PhaseFiveTransactionFailurePoint.GRANT_IDEMPOTENCY,
        ],
    )
    def test_grant_failures_preserve_the_previous_role_set(
        self,
        repository_factory: PhaseFiveRepositoryFactory,
        tmp_path: Path,
        point: PhaseFiveTransactionFailurePoint,
    ) -> None:
        """Grant assignment is atomic with audit and replay persistence."""
        repository, bootstrap, actor = repository_factory.bootstrap_authenticated(
            tmp_path / point.value,
            point.value,
        )
        target = repository.create_subject(
            subject_mutation(actor, f"target-{point.value}")
        ).subject
        mutation = grant_mutation(
            actor,
            target.id,
            bootstrap.project.id,
            point.value,
            idempotency_key=f"idem-{point.value}",
        )
        before = repository.list_project_grants(
            ListProjectGrants(actor=actor, project=bootstrap.project.id)
        )
        with (
            repository_factory.inject_phase_five_failure(point),
            pytest.raises(RuntimeError, match="injected"),
        ):
            repository.assign_project_grant(mutation)
        assert (
            repository.list_project_grants(
                ListProjectGrants(actor=actor, project=bootstrap.project.id)
            )
            == before
        )
        repository.assign_project_grant(mutation)
        assert (
            len(
                repository.list_project_grants(
                    ListProjectGrants(actor=actor, project=bootstrap.project.id)
                ).grants
            )
            == len(before.grants) + 1
        )


class TestSQLitePhaseFivePersistence(PhaseFivePersistenceContract):
    """Apply the cumulative Phase 5 repository contract to SQLite."""

    @pytest.fixture
    def repository_factory(self) -> PhaseFiveRepositoryFactory:
        """Provide the production SQLite Phase 5 factory."""
        return _SQLitePhaseFiveRepositoryFactory()


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
