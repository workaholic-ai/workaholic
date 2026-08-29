"""Transactional unit tests for SQLite ProjectGrant lifecycle persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, TypedDict

import pytest

from workaholic.application import (
    AssignProjectGrantMutation,
    AuthorizeActor,
    BootstrapMutation,
    CreateSubjectMutation,
    GrantNotFoundError,
    IdempotencyConflictError,
    IdentityVersionConflictError,
    InvalidTransitionError,
    LastProjectOwnerError,
    ListProjectGrants,
    PermissionDeniedError,
    ProjectGrantResult,
    RevokeProjectGrantMutation,
    SetSubjectEnabledMutation,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    Permission,
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

_NOW: Final = datetime(2026, 8, 29, 16, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_PROJECT_ID: Final = ProjectId("prj_local")
_OWNER_ID: Final = SubjectId("sub_owner")


@dataclass(slots=True)
class _FixedClock:
    """Mutable deterministic repository clock."""

    value: datetime

    def now(self) -> datetime:
        """Return the current fixed UTC time."""
        return self.value


class _MutationMetadata(TypedDict):
    """Exact common identity mutation keyword shape."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None


def _repository(tmp_path: Path) -> tuple[SQLiteRepository, _FixedClock]:
    """Bootstrap one authenticated local Instance and Project."""
    clock = _FixedClock(_NOW + timedelta(minutes=1))
    repository = SQLiteRepository((tmp_path / "local.db").resolve(), clock=clock)
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
    _insert_active_token(repository, _OWNER_ID)
    return repository, clock


def _actor(subject_id: SubjectId = _OWNER_ID) -> AuthenticatedActor:
    """Build one actor matching a test Subject's active Token fixture."""
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        subject_kind=SubjectKind.HUMAN,
        token_id=TokenId(f"tok_{subject_id.value.removeprefix('sub_')}"),
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


def _insert_active_token(
    repository: SQLiteRepository,
    subject_id: SubjectId,
) -> None:
    """Install one active credential fixture for a persisted Human Subject."""
    token_id = TokenId(f"tok_{subject_id.value.removeprefix('sub_')}")
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
                hashlib.sha256(str(token_id).encode("ascii")).hexdigest(),
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
    """Serialize one test timestamp canonically."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _create_human(repository: SQLiteRepository, suffix: str) -> SubjectId:
    """Create one enabled Human Subject through authenticated administration."""
    subject_id = SubjectId(f"sub_{suffix}")
    repository.create_subject(
        CreateSubjectMutation(
            **_metadata(f"create-{suffix}"),
            subject_id=subject_id,
            kind=SubjectKind.HUMAN,
            handle=f"human-{suffix}",
            display_name=f"Human {suffix}",
        )
    )
    return subject_id


def _assign(
    repository: SQLiteRepository,
    subject_id: SubjectId,
    role: ProjectRole,
    *,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> ProjectGrantResult:
    """Assign one role through the public repository façade."""
    return repository.assign_project_grant(
        AssignProjectGrantMutation(
            **_metadata(
                f"assign-{subject_id.value}-{role.value}",
                idempotency_key=idempotency_key,
            ),
            subject=subject_id,
            project=_PROJECT_ID,
            role=role,
            expected_version=expected_version,
        )
    )


def test_grant_create_list_pagination_and_restart(tmp_path: Path) -> None:
    """Grants retain role, version, attribution, and stable handle ordering."""
    repository, clock = _repository(tmp_path)
    zulu = _create_human(repository, "zulu")
    alpha = _create_human(repository, "alpha")
    created_zulu = _assign(repository, zulu, ProjectRole.VIEWER)
    created_alpha = _assign(repository, alpha, ProjectRole.AGENT)
    first = repository.list_project_grants(
        ListProjectGrants(actor=_actor(), project="LOCAL", limit=2)
    )
    assert tuple(grant.subject_id for grant in first.grants) == (alpha, zulu)
    assert first.next_cursor is not None
    restarted = SQLiteRepository(repository.database_path, clock=clock)
    second = restarted.list_project_grants(
        ListProjectGrants(
            actor=_actor(),
            project=_PROJECT_ID,
            limit=2,
            cursor=first.next_cursor,
        )
    )
    assert tuple(grant.subject_id for grant in second.grants) == (_OWNER_ID,)
    assert created_zulu.grant.version == 1
    assert created_alpha.grant.granted_by == _OWNER_ID


def test_role_replacement_requires_version_and_fresh_semantic_change(
    tmp_path: Path,
) -> None:
    """Replacement increments once; stale and same-role requests fail closed."""
    repository, _clock = _repository(tmp_path)
    subject_id = _create_human(repository, "replace")
    created = _assign(repository, subject_id, ProjectRole.VIEWER)
    mutation = AssignProjectGrantMutation(
        **_metadata("replace", idempotency_key="replace-role"),
        subject=subject_id,
        project="LOCAL",
        role=ProjectRole.OPERATOR,
        expected_version=1,
    )
    replaced = repository.assign_project_grant(mutation)
    assert replaced.grant.version == 2
    assert replaced.grant.role is ProjectRole.OPERATOR
    assert repository.assign_project_grant(mutation) == replaced
    with pytest.raises(IdempotencyConflictError):
        repository.assign_project_grant(
            mutation.model_copy(update={"role": ProjectRole.AGENT})
        )
    with pytest.raises(IdentityVersionConflictError):
        _assign(repository, subject_id, ProjectRole.OWNER, expected_version=1)
    with pytest.raises(InvalidTransitionError):
        _assign(repository, subject_id, ProjectRole.OPERATOR, expected_version=2)
    assert created.grant.version == 1


def test_revoke_is_versioned_idempotent_and_reports_absence(tmp_path: Path) -> None:
    """Revocation deletes once while retaining a closed replay snapshot."""
    repository, _clock = _repository(tmp_path)
    subject_id = _create_human(repository, "revoke")
    _assign(repository, subject_id, ProjectRole.VIEWER)
    mutation = RevokeProjectGrantMutation(
        **_metadata("revoke", idempotency_key="revoke-grant"),
        subject=subject_id,
        project=_PROJECT_ID,
        expected_version=1,
    )
    revoked = repository.revoke_project_grant(mutation)
    assert repository.revoke_project_grant(mutation) == revoked
    with pytest.raises(GrantNotFoundError):
        repository.revoke_project_grant(
            mutation.model_copy(update={"idempotency_key": None})
        )
    with pytest.raises(IdempotencyConflictError):
        repository.revoke_project_grant(
            mutation.model_copy(update={"expected_version": 2})
        )


def test_cumulative_role_matrix_and_kind_constraint(tmp_path: Path) -> None:
    """Fresh authorization follows the exact cumulative role and kind matrix."""
    repository, _clock = _repository(tmp_path)
    subjects: dict[ProjectRole, SubjectId] = {}
    for role in ProjectRole:
        subject_id = _create_human(repository, f"role-{role.value}")
        _insert_active_token(repository, subject_id)
        _assign(repository, subject_id, role)
        subjects[role] = subject_id
    permissions = (
        Permission.VIEW_PROJECT,
        Permission.EXECUTE_AGENT,
        Permission.OPERATE_PROJECT,
        Permission.MANAGE_PROJECT_GRANTS,
    )
    role_level = {role: index for index, role in enumerate(ProjectRole)}
    for role, subject_id in subjects.items():
        for index, permission in enumerate(permissions):
            command = AuthorizeActor(
                actor=_actor(subject_id),
                permission=permission,
                project_id=_PROJECT_ID,
                occurred_at=_NOW + timedelta(minutes=3),
            )
            if role_level[role] >= index:
                assert repository.authorize_actor(command).id == subject_id
            else:
                with pytest.raises(PermissionDeniedError):
                    repository.authorize_actor(command)
    with pytest.raises(PermissionDeniedError):
        repository.authorize_actor(
            AuthorizeActor(
                actor=_actor(subjects[ProjectRole.AGENT]),
                permission=Permission.EXECUTE_AGENT,
                project_id=_PROJECT_ID,
                required_kind=SubjectKind.AGENT,
                occurred_at=_NOW + timedelta(minutes=3),
            )
        )


def test_instance_admin_without_grant_has_no_project_data_access(
    tmp_path: Path,
) -> None:
    """Administrative grant authority never implies ordinary Project visibility."""
    repository, _clock = _repository(tmp_path)
    replacement = _create_human(repository, "replacement-owner")
    _assign(repository, replacement, ProjectRole.OWNER)
    repository.revoke_project_grant(
        RevokeProjectGrantMutation(
            **_metadata("revoke-admin-owner"),
            subject=_OWNER_ID,
            project=_PROJECT_ID,
            expected_version=1,
        )
    )
    with pytest.raises(PermissionDeniedError):
        repository.authorize_actor(
            AuthorizeActor(
                actor=_actor(),
                permission=Permission.VIEW_PROJECT,
                project_id=_PROJECT_ID,
                occurred_at=_NOW + timedelta(minutes=3),
            )
        )
    page = repository.list_project_grants(
        ListProjectGrants(actor=_actor(), project=_PROJECT_ID)
    )
    assert tuple(grant.subject_id for grant in page.grants) == (replacement,)


def test_disabled_subject_cannot_receive_a_grant(tmp_path: Path) -> None:
    """Grant assignment checks target enabled state inside its write transaction."""
    repository, _clock = _repository(tmp_path)
    subject_id = _create_human(repository, "disabled")
    repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("disable-target"),
            subject=subject_id,
            expected_version=1,
            enabled=False,
        )
    )
    with pytest.raises(PermissionDeniedError):
        _assign(repository, subject_id, ProjectRole.VIEWER)


def test_last_owner_guard_covers_replace_revoke_and_subject_disable(
    tmp_path: Path,
) -> None:
    """Every route that can remove the final enabled Owner rolls back."""
    repository, _clock = _repository(tmp_path)
    with pytest.raises(LastProjectOwnerError):
        repository.assign_project_grant(
            AssignProjectGrantMutation(
                **_metadata("demote-final"),
                subject=_OWNER_ID,
                project=_PROJECT_ID,
                role=ProjectRole.OPERATOR,
                expected_version=1,
            )
        )
    with pytest.raises(LastProjectOwnerError):
        repository.revoke_project_grant(
            RevokeProjectGrantMutation(
                **_metadata("revoke-final"),
                subject=_OWNER_ID,
                project=_PROJECT_ID,
                expected_version=1,
            )
        )
    replacement = _create_human(repository, "sole-project-owner")
    _assign(repository, replacement, ProjectRole.OWNER)
    repository.revoke_project_grant(
        RevokeProjectGrantMutation(
            **_metadata("revoke-root-owner"),
            subject=_OWNER_ID,
            project=_PROJECT_ID,
            expected_version=1,
        )
    )
    with pytest.raises(LastProjectOwnerError):
        repository.set_subject_enabled(
            SetSubjectEnabledMutation(
                **_metadata("disable-final-owner"),
                subject=replacement,
                expected_version=1,
                enabled=False,
            )
        )
    grants = repository.list_project_grants(
        ListProjectGrants(actor=_actor(), project=_PROJECT_ID)
    ).grants
    assert len(grants) == 1
    assert grants[0].subject_id == replacement
    assert grants[0].role is ProjectRole.OWNER
