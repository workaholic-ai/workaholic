"""Phase 5 Agent authorization tests for SQLite execution mutations."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from workaholic.application import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    BootstrapMutation,
    ClaimNextTaskMutation,
    LeaseLostError,
    PermissionDeniedError,
    ReleaseClaimMutation,
    RenewClaimMutation,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskCreationMutation,
    TaskResultInput,
)
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
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
    TaskProgress,
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
    """Create one token-backed Project with the Agent authorization matrix.

    Args:
        tmp_path: Isolated pytest-owned directory.

    Returns:
        Initialized repository with Human and Agent role variants.

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
    try:
        identities = (
            (_OWNER_ID, SubjectKind.HUMAN),
            (SubjectId("sub_agent"), SubjectKind.AGENT),
            (SubjectId("sub_operator"), SubjectKind.AGENT),
            (SubjectId("sub_agentowner"), SubjectKind.AGENT),
            (SubjectId("sub_foreign"), SubjectKind.AGENT),
            (SubjectId("sub_viewer"), SubjectKind.AGENT),
            (SubjectId("sub_humanrunner"), SubjectKind.HUMAN),
            (SubjectId("sub_ungranted"), SubjectKind.AGENT),
        )
        for subject_id, kind in identities:
            if subject_id != _OWNER_ID:
                _insert_subject(connection, subject_id=subject_id, kind=kind)
            _insert_token(connection, subject_id=subject_id, suffix="primary")
        _insert_token(
            connection,
            subject_id=SubjectId("sub_agent"),
            suffix="secondary",
        )
        for subject_id, role in (
            (SubjectId("sub_agent"), ProjectRole.AGENT),
            (SubjectId("sub_operator"), ProjectRole.OPERATOR),
            (SubjectId("sub_agentowner"), ProjectRole.OWNER),
            (SubjectId("sub_foreign"), ProjectRole.OWNER),
            (SubjectId("sub_viewer"), ProjectRole.VIEWER),
            (SubjectId("sub_humanrunner"), ProjectRole.AGENT),
        ):
            _insert_grant(connection, subject_id=subject_id, role=role)
        connection.commit()
    finally:
        connection.close()
    return repository


def _insert_subject(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
    kind: SubjectKind,
) -> None:
    """Insert one enabled non-administrator Subject fixture.

    Args:
        connection: Caller-owned setup transaction.
        subject_id: New Subject identity.
        kind: Exact Human or Agent kind.

    """
    connection.execute(
        """
        INSERT INTO subjects (
            id, instance_id, kind, handle, display_name, enabled,
            is_instance_admin, version, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?)
        """,
        (
            str(subject_id),
            str(_INSTANCE_ID),
            kind.value,
            subject_id.value.removeprefix("sub_"),
            subject_id.value,
            str(_OWNER_ID),
            _timestamp(_NOW),
            _timestamp(_NOW),
        ),
    )


def _insert_token(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
    suffix: str,
) -> None:
    """Insert one active hash-only Token fixture.

    Args:
        connection: Caller-owned setup transaction.
        subject_id: Token-owning Subject.
        suffix: Stable per-Subject Token discriminator.

    """
    token_id = _token_id(subject_id, suffix=suffix)
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


def _insert_grant(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
    role: ProjectRole,
) -> None:
    """Insert one Project grant fixture.

    Args:
        connection: Caller-owned setup transaction.
        subject_id: Granted Subject identity.
        role: Cumulative Project role.

    """
    connection.execute(
        """
        INSERT INTO project_grants (
            instance_id, subject_id, project_id, role, version,
            granted_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            str(_INSTANCE_ID),
            str(subject_id),
            str(_PROJECT_ID),
            role.value,
            str(_OWNER_ID),
            _timestamp(_NOW),
            _timestamp(_NOW),
        ),
    )


def _timestamp(value: datetime) -> str:
    """Serialize one fixture time to canonical SQLite text.

    Args:
        value: Aware UTC time.

    Returns:
        Canonical microsecond UTC representation.

    """
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _token_id(subject_id: SubjectId, *, suffix: str = "primary") -> TokenId:
    """Build one stable fixture Token identity.

    Args:
        subject_id: Token-owning Subject.
        suffix: Stable Token discriminator.

    Returns:
        Valid opaque Token identity.

    """
    handle = subject_id.value.removeprefix("sub_")
    return TokenId(f"tok_{handle}_{suffix}")


def _actor(
    handle: str,
    *,
    kind: SubjectKind = SubjectKind.AGENT,
    variant: str = "primary",
) -> AuthenticatedActor:
    """Build an actor matching one persisted Subject and Token.

    Args:
        handle: Subject fixture handle without its prefix.
        kind: Claimed Subject kind, validated against persistence.
        variant: Selected Token discriminator.

    Returns:
        Secret-free authenticated actor context.

    """
    subject_id = SubjectId(f"sub_{handle}")
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        subject_kind=kind,
        token_id=_token_id(subject_id, suffix=variant),
    )


def _create_task(
    repository: SQLiteRepository,
    suffix: str,
    *,
    at: datetime = _NOW + timedelta(minutes=1),
) -> Task:
    """Create one ready Task through the authenticated bootstrap Owner.

    Args:
        repository: Initialized repository.
        suffix: Stable identity suffix.
        at: Authoritative creation time.

    Returns:
        Persisted open Task.

    """
    owner = _actor("owner", kind=SubjectKind.HUMAN)
    return repository.create_task(
        TaskCreationMutation(
            task_id=TaskId(f"tsk_{suffix}"),
            event_id=TaskEventId(f"evt_{suffix}_created"),
            request_id=RequestId(f"req_{suffix}_created"),
            project_id=_PROJECT_ID,
            actor_subject_id=owner.subject_id,
            actor=owner,
            occurred_at=at,
            title=f"Task {suffix}",
            objective=f"Complete {suffix}.",
            priority=50,
        )
    )


def _claim_mutation(  # noqa: PLR0913 - explicit authorization fixture.
    actor: AuthenticatedActor,
    suffix: str,
    *,
    at: datetime = _NOW + timedelta(minutes=2),
    attempt_id: AttemptId | None = None,
    duration: int = 900,
    idempotency_key: str | None = None,
) -> ClaimNextTaskMutation:
    """Build one authenticated Agent pull mutation.

    Args:
        actor: Agent execution actor.
        suffix: Stable identity suffix.
        at: Authoritative pull time.
        attempt_id: Optional exact Attempt identity override.
        duration: Positive Agent Lease duration in seconds.
        idempotency_key: Optional caller replay key.

    Returns:
        Validated Agent pull mutation.

    """
    return ClaimNextTaskMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=actor.subject_id,
        actor=actor,
        request_id=RequestId(f"req_{suffix}_claim"),
        occurred_at=at,
        attempt_id=attempt_id or AttemptId(f"atm_{suffix}"),
        lease_duration_seconds=duration,
        task_claimed_event_id=TaskEventId(f"evt_{suffix}_claimed"),
        claim_expired_event_id=TaskEventId(f"evt_{suffix}_expired"),
        idempotency_key=idempotency_key,
    )


def _event_kinds(repository: SQLiteRepository, *event_ids: TaskEventId) -> list[str]:
    """Read immutable actor-kind snapshots for selected Task events.

    Args:
        repository: Initialized repository.
        event_ids: Event identities in expected output order.

    Returns:
        Persisted actor-kind values in input order.

    """
    connection = sqlite3.connect(repository.database_path)
    try:
        return [
            connection.execute(
                "SELECT actor_kind FROM task_events WHERE id = ?",
                (str(event_id),),
            ).fetchone()[0]
            for event_id in event_ids
        ]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("handle", "kind", "outcome"),
    [
        ("agent", SubjectKind.AGENT, "allowed"),
        ("operator", SubjectKind.AGENT, "allowed"),
        ("agentowner", SubjectKind.AGENT, "allowed"),
        ("viewer", SubjectKind.AGENT, "denied"),
        ("humanrunner", SubjectKind.HUMAN, "denied"),
        ("ungranted", SubjectKind.AGENT, "denied"),
    ],
)
def test_agent_pull_requires_agent_kind_and_cumulative_execution_permission(
    tmp_path: Path,
    handle: str,
    kind: SubjectKind,
    outcome: str,
) -> None:
    """Agent, Operator, and Owner roles execute only for Agent Subjects."""
    repository = _repository(tmp_path)
    _create_task(repository, "matrix")
    actor = _actor(handle, kind=kind)
    mutation = _claim_mutation(actor, handle)

    if outcome == "denied":
        with pytest.raises(PermissionDeniedError):
            repository.claim_next_task(mutation)
        return

    claimed = repository.claim_next_task(mutation)
    assert claimed.claim is not None
    assert claimed.claim.subject_id == actor.subject_id
    assert claimed.attempt is not None
    assert claimed.attempt.subject_id == actor.subject_id
    assert _event_kinds(repository, mutation.task_claimed_event_id) == ["agent"]


def test_second_token_for_same_agent_can_continue_exact_attempt(
    tmp_path: Path,
) -> None:
    """Attempt ownership binds to Subject, not the Token used to acquire it."""
    repository = _repository(tmp_path)
    task = _create_task(repository, "continuation")
    primary = _actor("agent")
    secondary = _actor("agent", variant="secondary")
    claim = repository.claim_next_task(_claim_mutation(primary, "continuation"))
    assert claim.attempt is not None
    progress = ReportTaskProgressMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=secondary.subject_id,
        actor=secondary,
        request_id=RequestId("req_continuation_progress"),
        occurred_at=_NOW + timedelta(minutes=3),
        attempt_id=claim.attempt.id,
        progress=TaskProgress(message="Still working.", percent_complete=50),
        progress_reported_event_id=TaskEventId("evt_continuation_progress"),
    )
    reported = repository.report_task_progress(progress)
    renewed = repository.renew_claim(
        RenewClaimMutation(
            project_id=task.project_id,
            task_uid=task.uid,
            actor_subject_id=secondary.subject_id,
            actor=secondary,
            request_id=RequestId("req_continuation_renew"),
            occurred_at=_NOW + timedelta(minutes=4),
            attempt_id=claim.attempt.id,
            lease_duration_seconds=1800,
            claim_renewed_event_id=TaskEventId("evt_continuation_renew"),
        )
    )
    released = repository.release_claim(
        ReleaseClaimMutation(
            project_id=task.project_id,
            task_uid=task.uid,
            actor_subject_id=secondary.subject_id,
            actor=secondary,
            request_id=RequestId("req_continuation_release"),
            occurred_at=_NOW + timedelta(minutes=5),
            attempt_id=claim.attempt.id,
            claim_released_event_id=TaskEventId("evt_continuation_release"),
        )
    )

    assert reported.attempt.id == claim.attempt.id
    assert renewed.attempt is not None
    assert renewed.attempt.id == claim.attempt.id
    assert released.claim is None
    assert released.attempt is not None
    assert released.attempt.status is AttemptStatus.RELEASED
    assert _event_kinds(
        repository,
        progress.progress_reported_event_id,
        TaskEventId("evt_continuation_renew"),
        TaskEventId("evt_continuation_release"),
    ) == ["agent", "agent", "agent"]


@pytest.mark.parametrize("operation", ["heartbeat", "progress", "release", "submit"])
def test_foreign_agent_owner_cannot_mutate_another_subject_attempt(
    tmp_path: Path,
    operation: str,
) -> None:
    """Even a stronger foreign role receives the non-disclosing Lease error."""
    repository = _repository(tmp_path)
    task = _create_task(repository, f"foreign-{operation}")
    owner = _actor("agent")
    foreign = _actor("foreign")
    claimed = repository.claim_next_task(_claim_mutation(owner, f"foreign-{operation}"))
    assert claimed.attempt is not None
    attempt_id = claimed.attempt.id
    with pytest.raises(LeaseLostError):
        _invoke_foreign_operation(repository, task, foreign, attempt_id, operation)


def _invoke_foreign_operation(
    repository: SQLiteRepository,
    task: Task,
    actor: AuthenticatedActor,
    attempt_id: AttemptId,
    operation: str,
) -> None:
    """Invoke one foreign Attempt mutation selected by the matrix.

    Args:
        repository: Initialized repository.
        task: Task owned by another Agent Subject.
        actor: Authenticated foreign Agent.
        attempt_id: Foreign current Attempt identity.
        operation: Closed test operation label.

    """
    occurred_at = _NOW + timedelta(minutes=3)
    if operation == "heartbeat":
        repository.renew_claim(
            RenewClaimMutation(
                project_id=task.project_id,
                task_uid=task.uid,
                actor_subject_id=actor.subject_id,
                actor=actor,
                request_id=RequestId("req_foreign_renew"),
                occurred_at=occurred_at,
                attempt_id=attempt_id,
                lease_duration_seconds=1800,
                claim_renewed_event_id=TaskEventId("evt_foreign_renew"),
            )
        )
        return
    if operation == "progress":
        repository.report_task_progress(
            ReportTaskProgressMutation(
                task_uid=task.uid,
                project_id=task.project_id,
                actor_subject_id=actor.subject_id,
                actor=actor,
                request_id=RequestId("req_foreign_progress"),
                occurred_at=occurred_at,
                attempt_id=attempt_id,
                progress=TaskProgress(message="Foreign write."),
                progress_reported_event_id=TaskEventId("evt_foreign_progress"),
            )
        )
        return
    if operation == "release":
        repository.release_claim(
            ReleaseClaimMutation(
                project_id=task.project_id,
                task_uid=task.uid,
                actor_subject_id=actor.subject_id,
                actor=actor,
                request_id=RequestId("req_foreign_release"),
                occurred_at=occurred_at,
                attempt_id=attempt_id,
                claim_released_event_id=TaskEventId("evt_foreign_release"),
            )
        )
        return
    if operation != "submit":
        raise AssertionError
    repository.submit_agent_result(
        _submission(task, actor, attempt_id, suffix="foreign")
    )


def _submission(
    task: Task,
    actor: AuthenticatedActor,
    attempt_id: AttemptId,
    *,
    suffix: str,
) -> SubmitAgentResultMutation:
    """Build one authenticated Agent submission mutation.

    Args:
        task: Current claimed Task snapshot.
        actor: Agent execution actor.
        attempt_id: Exact Attempt owner token.
        suffix: Stable identity suffix.

    Returns:
        Validated Agent submission mutation.

    """
    return SubmitAgentResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=actor.subject_id,
        actor=actor,
        request_id=RequestId(f"req_{suffix}_submit"),
        occurred_at=_NOW + timedelta(minutes=3),
        expected_version=task.version,
        attempt_id=attempt_id,
        result_id=ResultId(f"res_{suffix}"),
        result_submitted_event_id=TaskEventId(f"evt_{suffix}_submitted"),
        task_completed_event_id=TaskEventId(f"evt_{suffix}_completed"),
        result=TaskResultInput(summary="Completed by the owning Agent."),
    )


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ("revoke-token", AuthenticationFailedError),
        ("disable-subject", AuthenticationFailedError),
        ("remove-grant", PermissionDeniedError),
    ],
)
def test_agent_execution_revalidates_current_authorization_without_releasing_lock(
    tmp_path: Path,
    change: str,
    expected_error: type[Exception],
) -> None:
    """Credential or grant changes deny writes without rewriting ownership."""
    repository = _repository(tmp_path)
    task = _create_task(repository, f"auth-{change}")
    actor = _actor("agent")
    claimed = repository.claim_next_task(_claim_mutation(actor, f"auth-{change}"))
    assert claimed.attempt is not None
    before = _ownership_rows(repository, task)
    connection = sqlite3.connect(repository.database_path)
    try:
        if change == "revoke-token":
            connection.execute(
                "UPDATE tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                (
                    _timestamp(_NOW + timedelta(minutes=3)),
                    str(_OWNER_ID),
                    str(actor.token_id),
                ),
            )
        elif change == "disable-subject":
            connection.execute(
                "UPDATE subjects SET enabled = 0 WHERE id = ?",
                (str(actor.subject_id),),
            )
        else:
            connection.execute(
                "DELETE FROM project_grants WHERE subject_id = ? AND project_id = ?",
                (str(actor.subject_id), str(task.project_id)),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(expected_error):
        repository.renew_claim(
            RenewClaimMutation(
                project_id=task.project_id,
                task_uid=task.uid,
                actor_subject_id=actor.subject_id,
                actor=actor,
                request_id=RequestId(f"req_{change}_renew"),
                occurred_at=_NOW + timedelta(minutes=4),
                attempt_id=claimed.attempt.id,
                lease_duration_seconds=1800,
                claim_renewed_event_id=TaskEventId(f"evt_{change}_renew"),
            )
        )
    assert _ownership_rows(repository, task) == before


def _ownership_rows(repository: SQLiteRepository, task: Task) -> tuple[object, object]:
    """Read exact Claim and Attempt rows for rollback assertions.

    Args:
        repository: Initialized repository.
        task: Owned Task.

    Returns:
        Current Claim and Attempt rows.

    """
    connection = sqlite3.connect(repository.database_path)
    try:
        return (
            connection.execute(
                "SELECT * FROM task_claims WHERE task_uid = ?",
                (str(task.uid),),
            ).fetchone(),
            connection.execute(
                "SELECT * FROM task_attempts WHERE task_uid = ?",
                (str(task.uid),),
            ).fetchone(),
        )
    finally:
        connection.close()


def test_expired_agent_attempt_is_materialized_before_foreign_reclaim(
    tmp_path: Path,
) -> None:
    """A stale Agent lock becomes expired before another Agent acquires it."""
    repository = _repository(tmp_path)
    _create_task(repository, "reclaim")
    first = _actor("agent")
    second = _actor("foreign")
    initial = repository.claim_next_task(
        _claim_mutation(first, "reclaim-first", duration=60)
    )
    reclaimed_mutation = _claim_mutation(
        second,
        "reclaim-second",
        at=_NOW + timedelta(minutes=4),
    )
    reclaimed = repository.claim_next_task(reclaimed_mutation)

    assert initial.attempt is not None
    assert reclaimed.attempt is not None
    assert reclaimed.attempt.subject_id == second.subject_id
    connection = sqlite3.connect(repository.database_path)
    try:
        row = connection.execute(
            "SELECT status, ended_at FROM task_attempts WHERE id = ?",
            (str(initial.attempt.id),),
        ).fetchone()
    finally:
        connection.close()
    assert initial.claim is not None
    assert row == (
        AttemptStatus.EXPIRED.value,
        _timestamp(initial.claim.lease_expires_at),
    )
    assert _event_kinds(
        repository,
        reclaimed_mutation.claim_expired_event_id,
        reclaimed_mutation.task_claimed_event_id,
    ) == ["agent", "agent"]


def test_owning_agent_submission_releases_lock_and_records_agent_events(
    tmp_path: Path,
) -> None:
    """The exact active Agent can submit, terminalize, and release atomically."""
    repository = _repository(tmp_path)
    task = _create_task(repository, "submit")
    actor = _actor("operator")
    claimed = repository.claim_next_task(_claim_mutation(actor, "submit"))
    assert claimed.attempt is not None
    mutation = _submission(task, actor, claimed.attempt.id, suffix="submit")
    submitted = repository.submit_agent_result(mutation)

    assert submitted.task.state is TaskState.DONE
    assert submitted.attempt is not None
    assert submitted.attempt.status is AttemptStatus.SUBMITTED
    assert _ownership_rows(repository, task)[0] is None
    assert _event_kinds(
        repository,
        mutation.result_submitted_event_id,
        TaskEventId("evt_submit_completed"),
    ) == ["agent", "agent"]


def test_idempotent_agent_replay_revalidates_token_before_returning(
    tmp_path: Path,
) -> None:
    """Revocation prevents recovery of a prior Agent outcome by replay key."""
    repository = _repository(tmp_path)
    _create_task(repository, "replay")
    actor = _actor("agent")
    mutation = _claim_mutation(
        actor,
        "replay",
        idempotency_key="agent-claim-replay",
    )
    claimed = repository.claim_next_task(mutation)
    assert repository.claim_next_task(mutation) == claimed
    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(
            "UPDATE tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?",
            (
                _timestamp(_NOW + timedelta(minutes=3)),
                str(_OWNER_ID),
                str(actor.token_id),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AuthenticationFailedError):
        repository.claim_next_task(mutation)


def test_token_backed_agent_path_rejects_missing_or_mismatched_actor(
    tmp_path: Path,
) -> None:
    """Client attribution cannot replace authenticated Agent composition."""
    repository = _repository(tmp_path)
    _create_task(repository, "binding")
    agent = _actor("agent")
    mutation = _claim_mutation(agent, "binding").model_copy(update={"actor": None})
    with pytest.raises(AuthenticationRequiredError):
        repository.claim_next_task(mutation)
    with pytest.raises(PermissionDeniedError):
        repository.claim_next_task(
            mutation.model_copy(
                update={
                    "actor": agent,
                    "actor_subject_id": SubjectId("sub_foreign"),
                }
            )
        )
