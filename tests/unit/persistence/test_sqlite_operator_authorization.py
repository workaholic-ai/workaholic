"""Phase 5 Operator authorization tests for SQLite Task and Human writes."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from workaholic.application import (
    AddTaskDependencyMutation,
    ApproveResultMutation,
    AuthenticationFailedError,
    AuthenticationRequiredError,
    BootstrapMutation,
    ClaimTaskMutation,
    PermissionDeniedError,
    ProjectCreationMutation,
    ReleaseClaimMutation,
    RenewClaimMutation,
    SubmitHumanResultMutation,
    TaskCreationMutation,
    TaskLockedError,
    TaskResultInput,
    TaskUpdateMutation,
    TaskUpdatePatch,
)
from workaholic.domain import (
    ApprovalRequirement,
    AuthenticatedActor,
    InstanceId,
    ProjectId,
    ProjectRole,
    RequestId,
    ResultId,
    SubjectId,
    SubjectKind,
    Task,
    TaskEventId,
    TaskId,
    TaskState,
    TokenId,
)
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from pathlib import Path

_NOW: Final = datetime(2026, 8, 29, 10, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_PROJECT_ID: Final = ProjectId("prj_local")
_OWNER_ID: Final = SubjectId("sub_owner")


def _repository(tmp_path: Path) -> SQLiteRepository:
    """Create one Project with real Tokens and the complete role/kind matrix.

    Args:
        tmp_path: Isolated pytest-owned directory.

    Returns:
        Initialized repository with one active Token per Subject.

    """
    repository = SQLiteRepository((tmp_path / "local.db").resolve())
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
    connection = sqlite3.connect(repository.database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    timestamp = _timestamp(_NOW)
    try:
        identities = (
            (_OWNER_ID, SubjectKind.HUMAN, True),
            (SubjectId("sub_viewer"), SubjectKind.HUMAN, False),
            (SubjectId("sub_runner"), SubjectKind.HUMAN, False),
            (SubjectId("sub_operator"), SubjectKind.HUMAN, False),
            (SubjectId("sub_agentop"), SubjectKind.AGENT, False),
            (SubjectId("sub_admin"), SubjectKind.HUMAN, True),
            (SubjectId("sub_secondop"), SubjectKind.HUMAN, False),
        )
        for subject_id, kind, is_admin in identities:
            if subject_id != _OWNER_ID:
                connection.execute(
                    """
                    INSERT INTO subjects (
                        id, instance_id, kind, handle, display_name, enabled,
                        is_instance_admin, version, created_by, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(subject_id),
                        str(_INSTANCE_ID),
                        kind.value,
                        subject_id.value.removeprefix("sub_"),
                        subject_id.value,
                        1,
                        int(is_admin),
                        1,
                        str(_OWNER_ID),
                        timestamp,
                        timestamp,
                    ),
                )
            _insert_token(connection, subject_id=subject_id)
        for subject_id, role in (
            (SubjectId("sub_viewer"), ProjectRole.VIEWER),
            (SubjectId("sub_runner"), ProjectRole.AGENT),
            (SubjectId("sub_operator"), ProjectRole.OPERATOR),
            (SubjectId("sub_agentop"), ProjectRole.OPERATOR),
            (SubjectId("sub_secondop"), ProjectRole.OPERATOR),
        ):
            connection.execute(
                """
                INSERT INTO project_grants (
                    instance_id, subject_id, project_id, role, version,
                    granted_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(_INSTANCE_ID),
                    str(subject_id),
                    str(_PROJECT_ID),
                    role.value,
                    1,
                    str(_OWNER_ID),
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return repository


def _insert_token(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
) -> None:
    """Insert one active hash-only Token for an authorization fixture.

    Args:
        connection: Caller-owned setup transaction.
        subject_id: Token-owning Subject.

    """
    token_id = _token_id(subject_id)
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
            hashlib.sha256(str(token_id).encode("ascii")).hexdigest(),
            str(_OWNER_ID),
            _timestamp(_NOW),
            _timestamp(_NOW),
            _timestamp(_NOW + timedelta(days=30)),
        ),
    )


def _timestamp(value: datetime) -> str:
    """Serialize one fixture time to the canonical SQLite representation."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _token_id(subject_id: SubjectId) -> TokenId:
    """Derive one stable non-secret fixture Token identity."""
    return TokenId(f"tok_{subject_id.value.removeprefix('sub_')}")


def _actor(
    suffix: str,
    *,
    kind: SubjectKind = SubjectKind.HUMAN,
) -> AuthenticatedActor:
    """Build an actor matching one persisted fixture Subject and Token."""
    subject_id = SubjectId(f"sub_{suffix}")
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        subject_kind=kind,
        token_id=_token_id(subject_id),
    )


def _create_task(  # noqa: PLR0913 - explicit fixture controls aid tests.
    repository: SQLiteRepository,
    actor: AuthenticatedActor,
    suffix: str,
    *,
    at: datetime | None = None,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    idempotency_key: str | None = None,
) -> Task:
    """Create one authenticated Task with deterministic identities."""
    occurred_at = _NOW + timedelta(minutes=1) if at is None else at
    return repository.create_task(
        TaskCreationMutation(
            task_id=TaskId(f"tsk_{suffix}"),
            event_id=TaskEventId(f"evt_{suffix}_created"),
            request_id=RequestId(f"req_{suffix}_created"),
            project_id=_PROJECT_ID,
            actor_subject_id=actor.subject_id,
            actor=actor,
            occurred_at=occurred_at,
            title=f"Task {suffix}",
            objective=f"Complete {suffix}.",
            priority=50,
            approval=approval,
            idempotency_key=idempotency_key,
        )
    )


def _event_actor_kind(
    repository: SQLiteRepository,
    event_id: TaskEventId,
) -> str:
    """Read one immutable TaskEvent actor-kind snapshot."""
    connection = sqlite3.connect(repository.database_path)
    try:
        row = connection.execute(
            "SELECT actor_kind FROM task_events WHERE id = ?",
            (str(event_id),),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert isinstance(row[0], str)
    return row[0]


@pytest.mark.parametrize(
    ("suffix", "kind", "outcome"),
    [
        ("viewer", SubjectKind.HUMAN, "denied"),
        ("runner", SubjectKind.HUMAN, "denied"),
        ("operator", SubjectKind.HUMAN, "allowed"),
        ("agentop", SubjectKind.AGENT, "allowed"),
        ("owner", SubjectKind.HUMAN, "allowed"),
        ("admin", SubjectKind.HUMAN, "denied"),
    ],
)
def test_task_creation_enforces_cumulative_operator_permission(
    tmp_path: Path,
    suffix: str,
    kind: SubjectKind,
    outcome: str,
) -> None:
    """Operator and Owner grants authorize writes independently of admin state."""
    repository = _repository(tmp_path)
    actor = _actor(suffix, kind=kind)
    if outcome == "denied":
        with pytest.raises(PermissionDeniedError):
            _create_task(repository, actor, suffix)
        return

    task = _create_task(repository, actor, suffix)

    assert task.created_by == actor.subject_id
    assert (
        _event_actor_kind(
            repository,
            TaskEventId(f"evt_{suffix}_created"),
        )
        == kind.value
    )


def test_agent_operator_can_edit_definition_and_dependency_graph(
    tmp_path: Path,
) -> None:
    """An Agent explicitly granted Operator may use non-execution writes."""
    repository = _repository(tmp_path)
    owner = _actor("owner")
    agent_operator = _actor("agentop", kind=SubjectKind.AGENT)
    target = _create_task(repository, owner, "target")
    prerequisite = _create_task(
        repository,
        owner,
        "prerequisite",
        at=_NOW + timedelta(minutes=2),
    )
    updated = repository.update_task_if_version(
        TaskUpdateMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=agent_operator.subject_id,
            actor=agent_operator,
            event_id=TaskEventId("evt_agent_update"),
            claim_expired_event_id=TaskEventId("evt_agent_update_expired"),
            request_id=RequestId("req_agent_update"),
            occurred_at=_NOW + timedelta(minutes=3),
            expected_version=target.version,
            patch=TaskUpdatePatch(priority=80),
        )
    ).task
    dependant = repository.add_task_dependency(
        AddTaskDependencyMutation(
            task_uid=updated.uid,
            project_id=updated.project_id,
            actor_subject_id=agent_operator.subject_id,
            actor=agent_operator,
            event_id=TaskEventId("evt_agent_dependency"),
            claim_expired_event_id=TaskEventId("evt_agent_dependency_expired"),
            request_id=RequestId("req_agent_dependency"),
            occurred_at=_NOW + timedelta(minutes=4),
            expected_version=updated.version,
            prerequisite_uid=prerequisite.uid,
        )
    ).task

    assert dependant.depends_on == (prerequisite.uid,)
    assert _event_actor_kind(repository, TaskEventId("evt_agent_update")) == "agent"
    assert _event_actor_kind(repository, TaskEventId("evt_agent_dependency")) == "agent"


def test_human_claim_path_requires_human_operator_and_exact_owner(
    tmp_path: Path,
) -> None:
    """Human Claims reject Agent kind and remain exclusive across stronger roles."""
    repository = _repository(tmp_path)
    owner = _actor("owner")
    operator = _actor("operator")
    agent_operator = _actor("agentop", kind=SubjectKind.AGENT)
    task = _create_task(repository, owner, "claim")
    other = _create_task(
        repository,
        owner,
        "agent-human-claim",
        at=_NOW + timedelta(minutes=2),
    )
    with pytest.raises(PermissionDeniedError):
        repository.claim_task(
            ClaimTaskMutation(
                project_id=_PROJECT_ID,
                task_uid=other.uid,
                actor_subject_id=agent_operator.subject_id,
                actor=agent_operator,
                request_id=RequestId("req_agent_human_claim"),
                occurred_at=_NOW + timedelta(minutes=3),
                lease_duration_seconds=3600,
                task_claimed_event_id=TaskEventId("evt_agent_human_claim"),
                claim_expired_event_id=TaskEventId("evt_agent_human_expired"),
            )
        )
    claimed = repository.claim_task(
        ClaimTaskMutation(
            project_id=_PROJECT_ID,
            task_uid=task.uid,
            actor_subject_id=operator.subject_id,
            actor=operator,
            request_id=RequestId("req_human_claim"),
            occurred_at=_NOW + timedelta(minutes=3),
            lease_duration_seconds=3600,
            task_claimed_event_id=TaskEventId("evt_human_claim"),
            claim_expired_event_id=TaskEventId("evt_human_expired"),
        )
    )
    assert claimed.claim is not None
    assert claimed.claim.attempt_id is None
    with pytest.raises(TaskLockedError):
        repository.update_task_if_version(
            TaskUpdateMutation(
                task_uid=task.uid,
                project_id=task.project_id,
                actor_subject_id=owner.subject_id,
                actor=owner,
                event_id=TaskEventId("evt_foreign_update"),
                claim_expired_event_id=TaskEventId("evt_foreign_expired"),
                request_id=RequestId("req_foreign_update"),
                occurred_at=_NOW + timedelta(minutes=4),
                expected_version=task.version,
                patch=TaskUpdatePatch(priority=90),
            )
        )
    renewed = repository.renew_claim(
        RenewClaimMutation(
            project_id=_PROJECT_ID,
            task_uid=task.uid,
            actor_subject_id=operator.subject_id,
            actor=operator,
            request_id=RequestId("req_human_renew"),
            occurred_at=_NOW + timedelta(minutes=5),
            attempt_id=None,
            lease_duration_seconds=7200,
            claim_renewed_event_id=TaskEventId("evt_human_renew"),
        )
    )
    assert renewed.claim is not None
    released = repository.release_claim(
        ReleaseClaimMutation(
            project_id=_PROJECT_ID,
            task_uid=task.uid,
            actor_subject_id=operator.subject_id,
            actor=operator,
            request_id=RequestId("req_human_release"),
            occurred_at=_NOW + timedelta(minutes=6),
            attempt_id=None,
            claim_released_event_id=TaskEventId("evt_human_release"),
        )
    )
    assert released.claim is None


def test_operator_result_submission_and_review_record_real_actor_kinds(
    tmp_path: Path,
) -> None:
    """Attempt-free Operator work and review preserve each real Subject kind."""
    repository = _repository(tmp_path)
    owner = _actor("owner")
    viewer = _actor("viewer")
    operator = _actor("operator")
    agent_operator = _actor("agentop", kind=SubjectKind.AGENT)
    task = _create_task(
        repository,
        owner,
        "review",
        approval=ApprovalRequirement.HUMAN,
    )
    denied = SubmitHumanResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=viewer.subject_id,
        actor=viewer,
        result_id=ResultId("res_denied"),
        result_submitted_event_id=TaskEventId("evt_denied_submitted"),
        claim_expired_event_id=TaskEventId("evt_denied_expired"),
        request_id=RequestId("req_denied_submit"),
        occurred_at=_NOW + timedelta(minutes=2),
        expected_version=task.version,
        comment="Not authorized.",
        result=TaskResultInput(),
    )
    with pytest.raises(PermissionDeniedError):
        repository.submit_human_result(denied)
    submitted = repository.submit_human_result(
        denied.model_copy(
            update={
                "actor_subject_id": agent_operator.subject_id,
                "actor": agent_operator,
                "result_id": ResultId("res_agent_operator"),
                "result_submitted_event_id": TaskEventId("evt_agent_submitted"),
                "claim_expired_event_id": TaskEventId("evt_agent_expired"),
                "request_id": RequestId("req_agent_submit"),
                "comment": "Completed as an Operator.",
            }
        )
    )
    assert submitted.task.state is TaskState.REVIEW
    approved = repository.approve_result(
        ApproveResultMutation(
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=operator.subject_id,
            actor=operator,
            review_approved_event_id=TaskEventId("evt_operator_approved"),
            task_completed_event_id=TaskEventId("evt_operator_completed"),
            request_id=RequestId("req_operator_approve"),
            occurred_at=_NOW + timedelta(minutes=3),
            expected_version=submitted.task.version,
            comment="Verified.",
        )
    )
    assert approved.task.state is TaskState.DONE
    assert _event_actor_kind(repository, TaskEventId("evt_agent_submitted")) == "agent"
    assert (
        _event_actor_kind(repository, TaskEventId("evt_operator_approved")) == "human"
    )


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ("revoke-token", AuthenticationFailedError),
        ("disable-subject", AuthenticationFailedError),
        ("remove-grant", PermissionDeniedError),
    ],
)
def test_authorization_revalidates_token_subject_and_grant_in_write_transaction(
    tmp_path: Path,
    change: str,
    expected_error: type[Exception],
) -> None:
    """The next write observes each current authorization-state change."""
    repository = _repository(tmp_path)
    operator = _actor("operator")
    connection = sqlite3.connect(repository.database_path)
    try:
        if change == "revoke-token":
            connection.execute(
                "UPDATE tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                (
                    _timestamp(_NOW + timedelta(minutes=1)),
                    str(_OWNER_ID),
                    str(operator.token_id),
                ),
            )
        elif change == "disable-subject":
            connection.execute(
                "UPDATE subjects SET enabled = 0 WHERE id = ?",
                (str(operator.subject_id),),
            )
        else:
            connection.execute(
                "DELETE FROM project_grants WHERE subject_id = ?",
                (str(operator.subject_id),),
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(expected_error):
        _create_task(
            repository,
            operator,
            f"after-{change}",
            at=_NOW + timedelta(minutes=2),
        )


def test_idempotent_replay_revalidates_authentication_before_returning(
    tmp_path: Path,
) -> None:
    """A revoked Token cannot recover a prior successful write through replay."""
    repository = _repository(tmp_path)
    operator = _actor("operator")
    mutation = TaskCreationMutation(
        task_id=TaskId("tsk_replay"),
        event_id=TaskEventId("evt_replay_created"),
        request_id=RequestId("req_replay_created"),
        project_id=_PROJECT_ID,
        actor_subject_id=operator.subject_id,
        actor=operator,
        occurred_at=_NOW + timedelta(minutes=1),
        title="Replay",
        objective="Revalidate before replay.",
        priority=50,
        idempotency_key="operator-replay",
    )
    created = repository.create_task(mutation)
    assert repository.create_task(mutation) == created
    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(
            "UPDATE tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?",
            (
                _timestamp(_NOW + timedelta(minutes=2)),
                str(_OWNER_ID),
                str(operator.token_id),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AuthenticationFailedError):
        repository.create_task(mutation)


def test_project_creation_requires_authenticated_instance_administrator(
    tmp_path: Path,
) -> None:
    """Project creation grants Owner and audits the authenticating Token."""
    repository = _repository(tmp_path)
    admin = _actor("admin")
    operator = _actor("operator")
    mutation = ProjectCreationMutation(
        project_id=ProjectId("prj_admin_created"),
        request_id=RequestId("req_admin_project"),
        instance_id=_INSTANCE_ID,
        actor_subject_id=admin.subject_id,
        actor=admin,
        occurred_at=_NOW + timedelta(minutes=2),
        project_key="ADMIN",
        project_name="Admin created",
        idempotency_key="admin-project",
    )
    created = repository.create_project(mutation)
    assert created.grant.subject_id == admin.subject_id
    assert created.grant.role is ProjectRole.OWNER
    connection = sqlite3.connect(repository.database_path)
    try:
        audit = connection.execute(
            """
            SELECT actor_subject_id, actor_kind, actor_token_id
            FROM audit_events
            WHERE event_type = 'project_created' AND request_id = ?
            """,
            (str(mutation.request_id),),
        ).fetchone()
    finally:
        connection.close()
    assert audit == (str(admin.subject_id), "human", str(admin.token_id))
    with pytest.raises(PermissionDeniedError):
        repository.create_project(
            mutation.model_copy(
                update={
                    "project_id": ProjectId("prj_operator_denied"),
                    "request_id": RequestId("req_operator_project"),
                    "actor_subject_id": operator.subject_id,
                    "actor": operator,
                    "project_key": "DENIED",
                    "idempotency_key": None,
                }
            )
        )


def test_token_backed_store_rejects_missing_or_mismatched_actor_context(
    tmp_path: Path,
) -> None:
    """Legacy attribution cannot bypass Phase 5 authentication composition."""
    repository = _repository(tmp_path)
    operator = _actor("operator")
    mutation = TaskCreationMutation(
        task_id=TaskId("tsk_missing_actor"),
        event_id=TaskEventId("evt_missing_actor"),
        request_id=RequestId("req_missing_actor"),
        project_id=_PROJECT_ID,
        actor_subject_id=operator.subject_id,
        occurred_at=_NOW + timedelta(minutes=1),
        title="Missing actor",
        objective="Reject legacy attribution.",
        priority=50,
    )
    with pytest.raises(AuthenticationRequiredError):
        repository.create_task(mutation)
    with pytest.raises(PermissionDeniedError):
        repository.create_task(
            mutation.model_copy(
                update={
                    "task_id": TaskId("tsk_mismatch"),
                    "event_id": TaskEventId("evt_mismatch"),
                    "request_id": RequestId("req_mismatch"),
                    "actor_subject_id": _OWNER_ID,
                    "actor": operator,
                }
            )
        )
