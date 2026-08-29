"""Independent-connection races for Project ownership safeguards."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import TYPE_CHECKING, Final, TypedDict

from workaholic.application import (
    AssignProjectGrantMutation,
    BootstrapMutation,
    CreateSubjectMutation,
    LastProjectOwnerError,
    RevokeProjectGrantMutation,
    SetSubjectEnabledMutation,
)
from workaholic.domain import (
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

_NOW: Final = datetime(2026, 8, 29, 18, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_PROJECT_ID: Final = ProjectId("prj_local")
_ROOT_ID: Final = SubjectId("sub_root")
_SECOND_ID: Final = SubjectId("sub_second")


class _Metadata(TypedDict):
    """Exact common authenticated mutation fields."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: None


def _actor() -> AuthenticatedActor:
    """Return the active root administrator actor."""
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=_ROOT_ID,
        subject_kind=SubjectKind.HUMAN,
        token_id=TokenId("tok_root"),
    )


def _metadata(suffix: str) -> _Metadata:
    """Build authenticated mutation metadata for one racing operation."""
    return {
        "actor": _actor(),
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": _NOW + timedelta(minutes=2),
        "idempotency_key": None,
    }


def _setup(tmp_path: Path) -> SQLiteRepository:
    """Create a Project with two enabled Owners and active root auth."""
    repository = SQLiteRepository((tmp_path / "local.db").resolve())
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
            subject_id=_ROOT_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW,
            project_key="LOCAL",
            project_name="Local",
        )
    )
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
                "tok_root",
                str(_INSTANCE_ID),
                str(_ROOT_ID),
                hashlib.sha256(b"root-token-fixture").hexdigest(),
                str(_ROOT_ID),
                _serialize(_NOW),
                _serialize(_NOW),
                _serialize(_NOW + timedelta(days=1)),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    repository.create_subject(
        CreateSubjectMutation(
            **_metadata("create-second"),
            subject_id=_SECOND_ID,
            kind=SubjectKind.HUMAN,
            handle="second-owner",
            display_name="Second owner",
        )
    )
    repository.assign_project_grant(
        AssignProjectGrantMutation(
            **_metadata("grant-second"),
            subject=_SECOND_ID,
            project=_PROJECT_ID,
            role=ProjectRole.OWNER,
        )
    )
    return repository


def _serialize(value: datetime) -> str:
    """Serialize one test timestamp canonically."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _revoke(
    repository: SQLiteRepository,
    subject_id: SubjectId,
    barrier: Barrier,
) -> str:
    """Race one exact Owner revocation and return a closed outcome."""
    barrier.wait()
    try:
        repository.revoke_project_grant(
            RevokeProjectGrantMutation(
                **_metadata(f"revoke-{subject_id.value}"),
                subject=subject_id,
                project=_PROJECT_ID,
                expected_version=1,
            )
        )
    except LastProjectOwnerError:
        return "rejected"
    return "changed"


def _disable_second(repository: SQLiteRepository, barrier: Barrier) -> str:
    """Race disablement of the second Owner and return a closed outcome."""
    barrier.wait()
    try:
        repository.set_subject_enabled(
            SetSubjectEnabledMutation(
                **_metadata("disable-second"),
                subject=_SECOND_ID,
                expected_version=1,
                enabled=False,
            )
        )
    except LastProjectOwnerError:
        return "rejected"
    return "changed"


def _enabled_owner_count(repository: SQLiteRepository) -> int:
    """Read the physical enabled-Owner count while closing the connection."""
    connection = sqlite3.connect(repository.database_path)
    try:
        row = connection.execute(
            """
            SELECT count(*)
            FROM project_grants AS g
            JOIN subjects AS s
              ON s.id = g.subject_id AND s.instance_id = g.instance_id
            WHERE g.project_id = ? AND g.role = 'owner' AND s.enabled = 1
            """,
            (str(_PROJECT_ID),),
        ).fetchone()
    finally:
        connection.close()
    if row is None or type(row[0]) is not int:
        raise AssertionError
    return row[0]


def test_concurrent_owner_revocations_leave_exactly_one_enabled_owner(
    tmp_path: Path,
) -> None:
    """Serialized revocations cannot both remove the last Project Owner."""
    repository = _setup(tmp_path)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                _revoke,
                SQLiteRepository(repository.database_path),
                _ROOT_ID,
                barrier,
            ),
            executor.submit(
                _revoke,
                SQLiteRepository(repository.database_path),
                _SECOND_ID,
                barrier,
            ),
        )
        outcomes = tuple(future.result() for future in futures)
    assert sorted(outcomes) == ["changed", "rejected"]
    assert _enabled_owner_count(repository) == 1


def test_concurrent_disable_and_revoke_leave_an_enabled_owner(tmp_path: Path) -> None:
    """Subject disablement and grant revocation share one atomic invariant."""
    repository = _setup(tmp_path)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                _revoke,
                SQLiteRepository(repository.database_path),
                _ROOT_ID,
                barrier,
            ),
            executor.submit(
                _disable_second,
                SQLiteRepository(repository.database_path),
                barrier,
            ),
        )
        outcomes = tuple(future.result() for future in futures)
    assert sorted(outcomes) == ["changed", "rejected"]
    assert _enabled_owner_count(repository) == 1
