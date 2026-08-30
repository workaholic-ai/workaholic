"""Transactional tests for SQLite Subject lifecycle persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, TypedDict

import pytest

from workaholic.application import (
    AuthenticationFailedError,
    BootstrapMutation,
    CreateSubjectMutation,
    IdempotencyConflictError,
    IdentityVersionConflictError,
    InvalidInputError,
    LastInstanceAdminError,
    ListSubjects,
    PermissionDeniedError,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    SubjectHandleConflictError,
    SubjectNotFoundError,
    SubjectResult,
    UpdateSubjectMutation,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    ProjectId,
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
_OWNER_ID: Final = SubjectId("sub_owner")


class _MutationMetadata(TypedDict):
    """Exact keyword shape shared by Phase 5 identity mutations."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None


def _repository(tmp_path: Path) -> SQLiteRepository:
    """Create and bootstrap one isolated Phase 5 repository."""
    repository = SQLiteRepository((tmp_path / "local.db").resolve())
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
    _insert_active_token(repository, _OWNER_ID)
    return repository


def _insert_active_token(
    repository: SQLiteRepository,
    subject_id: SubjectId,
) -> None:
    """Install one active Task 9 credential fixture for a persisted Subject."""
    token_id = f"tok_{subject_id.value.removeprefix('sub_') or 'actor'}"
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
                token_id,
                str(_INSTANCE_ID),
                str(subject_id),
                hashlib.sha256(token_id.encode("ascii")).hexdigest(),
                str(_OWNER_ID),
                _NOW.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                _NOW.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                (_NOW + timedelta(days=365))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _actor(
    subject_id: SubjectId = _OWNER_ID,
    *,
    instance_id: InstanceId = _INSTANCE_ID,
    kind: SubjectKind = SubjectKind.HUMAN,
) -> AuthenticatedActor:
    """Build one secret-free Task 8 fixture actor."""
    return AuthenticatedActor(
        instance_id=instance_id,
        subject_id=subject_id,
        subject_kind=kind,
        token_id=TokenId(f"tok_{subject_id.value.removeprefix('sub_') or 'actor'}"),
    )


def _metadata(
    suffix: str,
    *,
    actor: AuthenticatedActor | None = None,
    idempotency_key: str | None = None,
) -> _MutationMetadata:
    """Build valid common metadata for one Subject mutation."""
    return {
        "actor": _actor() if actor is None else actor,
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": _NOW + timedelta(minutes=1),
        "idempotency_key": idempotency_key,
    }


def _create(  # noqa: PLR0913 - explicit fixture controls keep calls readable.
    repository: SQLiteRepository,
    suffix: str,
    *,
    handle: str | None = None,
    kind: SubjectKind = SubjectKind.AGENT,
    actor: AuthenticatedActor | None = None,
    idempotency_key: str | None = None,
) -> SubjectResult:
    """Create one named Subject through the public repository façade."""
    selected_handle = handle or f"agent-{suffix}"
    return repository.create_subject(
        CreateSubjectMutation(
            **_metadata(
                f"create-{suffix}",
                actor=actor,
                idempotency_key=idempotency_key,
            ),
            subject_id=SubjectId(f"sub_{suffix}"),
            kind=kind,
            handle=selected_handle,
            display_name=f"Display {suffix}",
        )
    )


def test_create_list_paginate_and_restart_preserve_exact_subjects(
    tmp_path: Path,
) -> None:
    """Human and Agent Subjects survive stable actor-bound pagination."""
    repository = _repository(tmp_path)
    human = _create(repository, "human", kind=SubjectKind.HUMAN)
    agent = _create(repository, "agent", handle="aaa-agent")

    first = repository.list_subjects(ListSubjects(actor=_actor(), limit=2))
    assert tuple(item.handle for item in first.subjects) == (
        "aaa-agent",
        "agent-human",
    )
    assert first.next_cursor is not None
    restarted = SQLiteRepository(repository.database_path)
    second = restarted.list_subjects(
        ListSubjects(actor=_actor(), limit=2, cursor=first.next_cursor)
    )
    assert tuple(item.handle for item in second.subjects) == ("local-operator",)
    assert second.next_cursor is None
    assert agent.subject in first.subjects
    assert human.subject in first.subjects


def test_subject_resolution_uses_only_exact_id_or_handle(tmp_path: Path) -> None:
    """Display names never become identity operands and cross-Instance IDs fail."""
    repository = _repository(tmp_path)
    created = _create(repository, "worker", handle="build-agent")
    subject = created.subject
    updated = repository.update_subject(
        UpdateSubjectMutation(
            **_metadata("update-by-id"),
            subject=subject.id,
            expected_version=1,
            display_name="Human-readable worker",
        )
    )
    assert updated.subject.display_name == "Human-readable worker"

    with pytest.raises(SubjectNotFoundError):
        repository.update_subject(
            UpdateSubjectMutation(
                **_metadata("display-is-not-id"),
                subject="human-readable-worker",
                expected_version=2,
                display_name="No match",
            )
        )
    with pytest.raises(AuthenticationFailedError):
        repository.list_subjects(
            ListSubjects(actor=_actor(instance_id=InstanceId("ins_other")))
        )


def test_subject_updates_are_versioned_attributed_and_idempotent(
    tmp_path: Path,
) -> None:
    """Every existing-state change increments once and replay is stable."""
    repository = _repository(tmp_path)
    subject = _create(repository, "versioned").subject
    mutation = UpdateSubjectMutation(
        **_metadata("update", idempotency_key="stable-update"),
        subject=subject.handle,
        expected_version=1,
        display_name="Version two",
    )
    updated = repository.update_subject(mutation)
    replayed = repository.update_subject(mutation)
    assert replayed == updated
    assert updated.subject.version == 2
    assert updated.subject.created_by == _OWNER_ID

    with pytest.raises(IdentityVersionConflictError):
        repository.set_subject_enabled(
            SetSubjectEnabledMutation(
                **_metadata("stale"),
                subject=subject.id,
                expected_version=1,
                enabled=False,
            )
        )
    with pytest.raises(IdempotencyConflictError):
        repository.update_subject(
            mutation.model_copy(update={"display_name": "Different"})
        )


def test_enable_disable_and_admin_changes_cover_disabled_targets(
    tmp_path: Path,
) -> None:
    """Administrators can manage disabled targets without mutable identity fields."""
    repository = _repository(tmp_path)
    original = _create(repository, "managed", kind=SubjectKind.HUMAN).subject
    disabled = repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("disable"),
            subject=original.handle,
            expected_version=1,
            enabled=False,
        )
    ).subject
    promoted = repository.set_instance_admin(
        SetInstanceAdminMutation(
            **_metadata("promote-disabled"),
            subject=disabled.id,
            expected_version=2,
            is_instance_admin=True,
        )
    ).subject
    enabled = repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("enable"),
            subject=promoted.id,
            expected_version=3,
            enabled=True,
        )
    ).subject
    assert enabled.enabled is True
    assert enabled.is_instance_admin is True
    assert enabled.version == 4
    assert enabled.handle == original.handle
    assert enabled.kind is original.kind


def test_final_enabled_administrator_cannot_be_disabled_or_demoted(
    tmp_path: Path,
) -> None:
    """Both paths enforce the last-enabled-administrator invariant atomically."""
    repository = _repository(tmp_path)
    with pytest.raises(LastInstanceAdminError):
        repository.set_subject_enabled(
            SetSubjectEnabledMutation(
                **_metadata("disable-owner"),
                subject=_OWNER_ID,
                expected_version=1,
                enabled=False,
            )
        )
    with pytest.raises(LastInstanceAdminError):
        repository.set_instance_admin(
            SetInstanceAdminMutation(
                **_metadata("demote-owner"),
                subject="local-operator",
                expected_version=1,
                is_instance_admin=False,
            )
        )
    current = repository.list_subjects(ListSubjects(actor=_actor())).subjects[-1]
    assert current.id == _OWNER_ID
    assert current.enabled is True
    assert current.is_instance_admin is True
    assert current.version == 1


def test_concurrent_handle_creation_has_exactly_one_winner(tmp_path: Path) -> None:
    """Immediate transactions serialize an Instance-scoped uniqueness race."""
    repository = _repository(tmp_path)

    def create(suffix: str) -> str:
        """Return a stable outcome label for one racing creator."""
        try:
            _create(repository, suffix, handle="shared-agent")
        except SubjectHandleConflictError:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(create, ("race-a", "race-b")))
    assert sorted(outcomes) == ["conflict", "created"]
    subjects = repository.list_subjects(ListSubjects(actor=_actor())).subjects
    assert sum(subject.handle == "shared-agent" for subject in subjects) == 1


def test_concurrent_admin_removal_cannot_eliminate_all_admins(tmp_path: Path) -> None:
    """Competing administrators cannot each remove the other's authority."""
    repository = _repository(tmp_path)
    second = _create(
        repository,
        "admin-two",
        handle="second-admin",
        kind=SubjectKind.HUMAN,
    ).subject
    second = repository.set_instance_admin(
        SetInstanceAdminMutation(
            **_metadata("promote-second"),
            subject=second.id,
            expected_version=1,
            is_instance_admin=True,
        )
    ).subject
    _insert_active_token(repository, second.id)
    second_actor = _actor(second.id)

    def remove_as_owner() -> str:
        """Have the bootstrap owner demote the second administrator."""
        try:
            repository.set_instance_admin(
                SetInstanceAdminMutation(
                    **_metadata("owner-removes-second"),
                    subject=second.id,
                    expected_version=2,
                    is_instance_admin=False,
                )
            )
        except LastInstanceAdminError, PermissionDeniedError:
            return "rejected"
        return "changed"

    def remove_as_second() -> str:
        """Have the second administrator demote the bootstrap owner."""
        try:
            repository.set_instance_admin(
                SetInstanceAdminMutation(
                    **_metadata("second-removes-owner", actor=second_actor),
                    subject=_OWNER_ID,
                    expected_version=1,
                    is_instance_admin=False,
                )
            )
        except LastInstanceAdminError, PermissionDeniedError:
            return "rejected"
        return "changed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(remove_as_owner), executor.submit(remove_as_second))
        outcomes = tuple(future.result() for future in futures)
    assert sorted(outcomes) == ["changed", "rejected"]
    connection = sqlite3.connect(repository.database_path)
    try:
        count = connection.execute(
            "SELECT count(*) FROM subjects WHERE enabled = 1 AND is_instance_admin = 1"
        ).fetchone()
    finally:
        connection.close()
    assert count == (1,)


def test_subject_cursor_rejects_actor_reuse_and_tampering(tmp_path: Path) -> None:
    """A pagination cursor cannot cross actors or accept noncanonical bytes."""
    repository = _repository(tmp_path)
    second = _create(
        repository,
        "cursor-admin",
        kind=SubjectKind.HUMAN,
    ).subject
    second = repository.set_instance_admin(
        SetInstanceAdminMutation(
            **_metadata("cursor-promote"),
            subject=second.id,
            expected_version=1,
            is_instance_admin=True,
        )
    ).subject
    _insert_active_token(repository, second.id)
    page = repository.list_subjects(ListSubjects(actor=_actor(), limit=1))
    assert page.next_cursor is not None
    with pytest.raises(InvalidInputError):
        repository.list_subjects(
            ListSubjects(
                actor=_actor(second.id),
                limit=1,
                cursor=page.next_cursor,
            )
        )
    with pytest.raises(InvalidInputError):
        repository.list_subjects(
            ListSubjects(actor=_actor(), cursor=f"{page.next_cursor}x")
        )
