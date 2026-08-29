"""Independent-connection races for Project ownership safeguards."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypedDict

import pytest

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
    from multiprocessing.process import BaseProcess

pytestmark = pytest.mark.integration

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


class _ProcessBarrier(Protocol):
    """Minimal spawned-process barrier used at the race boundary."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait until both independently spawned workers are ready."""
        ...


class _ProcessQueue(Protocol):
    """Minimal process-safe channel for closed worker outcomes."""

    def put(self, obj: object) -> None:
        """Publish one serializable outcome."""
        ...

    def get(self, *, timeout: float | None = None) -> object:
        """Read one serializable outcome within a bounded interval."""
        ...

    def close(self) -> None:
        """Release queue resources owned by the parent."""
        ...

    def join_thread(self) -> None:
        """Flush the queue feeder before teardown."""
        ...


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
    database_path: str,
    subject_id: str,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Race one exact Owner revocation from an independent process."""
    repository = SQLiteRepository(Path(database_path))
    target = SubjectId(subject_id)
    barrier.wait(timeout=10)
    try:
        repository.revoke_project_grant(
            RevokeProjectGrantMutation(
                **_metadata(f"revoke-{target.value}"),
                subject=target,
                project=_PROJECT_ID,
                expected_version=1,
            )
        )
    except LastProjectOwnerError:
        queue.put(("rejected", "last_project_owner"))
    except Exception as error:  # noqa: BLE001 - surface child failure safely.
        queue.put(("unexpected", type(error).__name__))
    else:
        queue.put(("changed", target.value))


def _disable_second(
    database_path: str,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Race second-Owner disablement from an independent process."""
    repository = SQLiteRepository(Path(database_path))
    barrier.wait(timeout=10)
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
        queue.put(("rejected", "last_project_owner"))
    except Exception as error:  # noqa: BLE001 - surface child failure safely.
        queue.put(("unexpected", type(error).__name__))
    else:
        queue.put(("changed", _SECOND_ID.value))


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
    context = get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_revoke,
            args=(str(repository.database_path), str(subject), barrier, queue),
        )
        for subject in (_ROOT_ID, _SECOND_ID)
    )
    _run_spawned_processes(processes)
    outcomes = _read_process_outcomes(queue, count=2)
    assert sorted(status for status, _detail in outcomes) == ["changed", "rejected"]
    assert _enabled_owner_count(repository) == 1


def test_concurrent_disable_and_revoke_leave_an_enabled_owner(tmp_path: Path) -> None:
    """Subject disablement and grant revocation share one atomic invariant."""
    repository = _setup(tmp_path)
    context = get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = (
        context.Process(
            target=_revoke,
            args=(str(repository.database_path), str(_ROOT_ID), barrier, queue),
        ),
        context.Process(
            target=_disable_second,
            args=(str(repository.database_path), barrier, queue),
        ),
    )
    _run_spawned_processes(processes)
    outcomes = _read_process_outcomes(queue, count=2)
    assert sorted(status for status, _detail in outcomes) == ["changed", "rejected"]
    assert _enabled_owner_count(repository) == 1


def _run_spawned_processes(processes: tuple[BaseProcess, ...]) -> None:
    """Start, bound, and validate all test-owned race processes."""
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    hung = tuple(process for process in processes if process.is_alive())
    for process in hung:
        process.terminate()
        process.join(timeout=5)
    if hung:
        pytest.fail("Spawned authorization worker timed out.")
    exit_codes = tuple(process.exitcode for process in processes)
    if any(code != 0 for code in exit_codes):
        pytest.fail(f"Authorization workers exited unsuccessfully: {exit_codes!r}")


def _read_process_outcomes(
    queue: _ProcessQueue,
    *,
    count: int,
) -> tuple[tuple[str, str], ...]:
    """Read and validate the closed authorization worker wire shape."""
    outcomes: list[tuple[str, str]] = []
    try:
        for _index in range(count):
            candidate = queue.get(timeout=10)
            if (
                not isinstance(candidate, tuple)
                or len(candidate) != 2
                or not all(isinstance(value, str) for value in candidate)
            ):
                pytest.fail("Authorization worker returned a malformed outcome.")
            status, detail = candidate
            if status == "unexpected":
                pytest.fail(f"Authorization worker failed with {detail}.")
            outcomes.append((status, detail))
    finally:
        queue.close()
        queue.join_thread()
    return tuple(outcomes)
