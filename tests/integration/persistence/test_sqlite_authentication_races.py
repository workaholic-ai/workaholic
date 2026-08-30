"""Independent-connection races for SQLite Token authentication state."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Final, Protocol, TypedDict

import pytest

from workaholic.application import (
    ActivateTokenMutation,
    AuthenticateToken,
    AuthenticationFailedError,
    BootstrapMutation,
    CreateSubjectMutation,
    IssueTokenMutation,
    RevokeTokenMutation,
    SetSubjectEnabledMutation,
)
from workaholic.auth import generate_token, hash_token
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
    from multiprocessing.process import BaseProcess

_NOW: Final = datetime(2026, 8, 29, 14, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_OWNER_ID: Final = SubjectId("sub_owner")
_OWNER_TOKEN_ID: Final = TokenId("tok_owner")
_AGENT_ID: Final = SubjectId("sub_agent")


class _MutationMetadata(TypedDict):
    """Exact common identity mutation keyword shape."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None


class _ProcessBarrier(Protocol):
    """Minimal spawned-process barrier used at authentication races."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait until both independently spawned workers are ready."""
        ...


class _ProcessQueue(Protocol):
    """Minimal process-safe channel for closed worker outcomes."""

    def put(self, obj: object) -> None:
        """Publish one serializable outcome."""
        ...

    def get(self, *, timeout: float | None = None) -> object:
        """Read one outcome within a bounded interval."""
        ...

    def close(self) -> None:
        """Release parent-owned queue resources."""
        ...

    def join_thread(self) -> None:
        """Flush the queue feeder before teardown."""
        ...


def _actor() -> AuthenticatedActor:
    """Return the persisted bootstrap administrator actor."""
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=_OWNER_ID,
        subject_kind=SubjectKind.HUMAN,
        token_id=_OWNER_TOKEN_ID,
    )


def _metadata(suffix: str, *, at: datetime) -> _MutationMetadata:
    """Build explicit mutation metadata for a race operation."""
    return {
        "actor": _actor(),
        "request_id": RequestId(f"req_{suffix}"),
        "occurred_at": at,
        "idempotency_key": None,
    }


def _digest(token_id: TokenId, *, fill: int) -> str:
    """Generate one canonical runtime Token digest without a source secret."""
    raw = generate_token(token_id, random_bytes=lambda size: bytes([fill]) * size)
    return hash_token(raw)


def _setup(tmp_path: Path) -> SQLiteRepository:
    """Create an Instance with active administrator auth and one Agent."""
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
                _digest(_OWNER_TOKEN_ID, fill=1),
                str(_OWNER_ID),
                _serialize(_NOW),
                _serialize(_NOW),
                _serialize(_NOW + timedelta(days=30)),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    repository.create_subject(
        CreateSubjectMutation(
            **_metadata("create-agent", at=_NOW + timedelta(minutes=1)),
            subject_id=_AGENT_ID,
            kind=SubjectKind.AGENT,
            handle="build-agent",
            display_name="Build agent",
        )
    )
    return repository


def _serialize(value: datetime) -> str:
    """Serialize one test timestamp in canonical SQLite form."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _issue(
    repository: SQLiteRepository,
    token_id: TokenId,
    *,
    fill: int,
    at: datetime,
) -> str:
    """Issue one pending Agent Token and return its runtime digest."""
    digest = _digest(token_id, fill=fill)
    repository.issue_pending_token(
        IssueTokenMutation(
            **_metadata(f"issue-{fill}", at=at),
            token_id=token_id,
            subject=_AGENT_ID,
            token_digest=digest,
            expires_at=at + timedelta(hours=1),
        )
    )
    return digest


def _authenticate_outcome(
    repository: SQLiteRepository,
    command: AuthenticateToken,
    barrier: Barrier,
) -> str:
    """Authenticate after a shared barrier and return a closed outcome."""
    barrier.wait()
    try:
        repository.authenticate_token(command)
    except AuthenticationFailedError:
        return "failed"
    return "authenticated"


def test_authentication_races_revoke_activation_and_disablement(
    tmp_path: Path,
) -> None:
    """Every committed state change governs the immediately following lookup."""
    repository = _setup(tmp_path)
    token_id = TokenId("tok_racing")
    digest = _issue(
        repository,
        token_id,
        fill=2,
        at=_NOW + timedelta(minutes=2),
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("activate-racing", at=_NOW + timedelta(minutes=3)),
            token_id=token_id,
        )
    )
    command = AuthenticateToken(
        token_id=token_id,
        token_digest=digest,
        expected_instance_id=_INSTANCE_ID,
        occurred_at=_NOW + timedelta(minutes=4),
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        authentication = executor.submit(
            _authenticate_outcome,
            SQLiteRepository(repository.database_path),
            command,
            barrier,
        )
        revocation = executor.submit(
            _revoke_after_barrier,
            SQLiteRepository(repository.database_path),
            token_id,
            barrier,
        )
        assert authentication.result() in ("authenticated", "failed")
        revocation.result()
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(command)

    pending_id = TokenId("tok_pending-race")
    pending_digest = _issue(
        repository,
        pending_id,
        fill=3,
        at=_NOW + timedelta(minutes=5),
    )
    pending_command = AuthenticateToken(
        token_id=pending_id,
        token_digest=pending_digest,
        expected_instance_id=_INSTANCE_ID,
        occurred_at=_NOW + timedelta(minutes=7),
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        authentication = executor.submit(
            _authenticate_outcome,
            SQLiteRepository(repository.database_path),
            pending_command,
            barrier,
        )
        activation = executor.submit(
            _activate_after_barrier,
            SQLiteRepository(repository.database_path),
            pending_id,
            barrier,
        )
        assert authentication.result() in ("authenticated", "failed")
        activation.result()
    assert repository.authenticate_token(pending_command).token_id == pending_id

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        authentication = executor.submit(
            _authenticate_outcome,
            SQLiteRepository(repository.database_path),
            pending_command,
            barrier,
        )
        disablement = executor.submit(
            _disable_after_barrier,
            SQLiteRepository(repository.database_path),
            barrier,
        )
        assert authentication.result() in ("authenticated", "failed")
        disablement.result()
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(pending_command)


def test_spawned_process_authentication_observes_revoke_and_disable_commits(
    tmp_path: Path,
) -> None:
    """Real processes never authenticate after revoke or disable commits."""
    repository = _setup(tmp_path)
    token_id = TokenId("tok_process_revoke")
    digest = _issue(
        repository,
        token_id,
        fill=4,
        at=_NOW + timedelta(minutes=10),
    )
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("process-activate", at=_NOW + timedelta(minutes=11)),
            token_id=token_id,
        )
    )
    outcomes = _run_authentication_process_race(
        repository.database_path,
        token_id=token_id,
        digest=digest,
        operation="revoke",
    )
    assert "revoked" in {status for status, _detail in outcomes}
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(
            _authentication_command(token_id, digest, minute=12)
        )
    assert _event_count(repository, request_id="req_process-revoke") == 1

    disable_root = tmp_path / "disable"
    disable_root.mkdir()
    disabled_repository = _setup(disable_root)
    disabled_token_id = TokenId("tok_process_disable")
    disabled_digest = _issue(
        disabled_repository,
        disabled_token_id,
        fill=5,
        at=_NOW + timedelta(minutes=13),
    )
    disabled_repository.activate_token(
        ActivateTokenMutation(
            **_metadata("disable-activate", at=_NOW + timedelta(minutes=14)),
            token_id=disabled_token_id,
        )
    )
    outcomes = _run_authentication_process_race(
        disabled_repository.database_path,
        token_id=disabled_token_id,
        digest=disabled_digest,
        operation="disable",
    )
    assert "disabled" in {status for status, _detail in outcomes}
    with pytest.raises(AuthenticationFailedError):
        disabled_repository.authenticate_token(
            _authentication_command(disabled_token_id, disabled_digest, minute=15)
        )
    assert _event_count(disabled_repository, request_id="req_process-disable") == 1


def _authentication_command(
    token_id: TokenId,
    digest: str,
    *,
    minute: int,
) -> AuthenticateToken:
    """Build one exact process-race authentication command."""
    return AuthenticateToken(
        token_id=token_id,
        token_digest=digest,
        expected_instance_id=_INSTANCE_ID,
        occurred_at=_NOW + timedelta(minutes=minute),
    )


def _spawned_authenticate(
    database_path: str,
    token_id: str,
    digest: str,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Authenticate from one independently spawned Python process."""
    repository = SQLiteRepository(Path(database_path))
    barrier.wait(timeout=10)
    try:
        actor = repository.authenticate_token(
            _authentication_command(TokenId(token_id), digest, minute=20)
        )
    except AuthenticationFailedError:
        queue.put(("failed", "authentication_failed"))
    except Exception as error:  # noqa: BLE001 - surface child failure safely.
        queue.put(("unexpected", type(error).__name__))
    else:
        queue.put(("authenticated", str(actor.subject_id)))


def _spawned_auth_state_change(
    database_path: str,
    token_id: str,
    operation: str,
    barrier: _ProcessBarrier,
    queue: _ProcessQueue,
) -> None:
    """Commit one revoke or disable from an independent Python process."""
    repository = SQLiteRepository(Path(database_path))
    barrier.wait(timeout=10)
    try:
        if operation == "revoke":
            repository.revoke_token(
                RevokeTokenMutation(
                    **_metadata("process-revoke", at=_NOW + timedelta(minutes=20)),
                    token_id=TokenId(token_id),
                )
            )
        elif operation == "disable":
            repository.set_subject_enabled(
                SetSubjectEnabledMutation(
                    **_metadata("process-disable", at=_NOW + timedelta(minutes=20)),
                    subject=_AGENT_ID,
                    expected_version=1,
                    enabled=False,
                )
            )
        else:
            queue.put(("unexpected", "invalid_operation"))
            return
    except Exception as error:  # noqa: BLE001 - surface child failure safely.
        queue.put(("unexpected", type(error).__name__))
    else:
        queue.put(("revoked" if operation == "revoke" else "disabled", token_id))


def _run_authentication_process_race(
    database_path: Path,
    *,
    token_id: TokenId,
    digest: str,
    operation: str,
) -> tuple[tuple[str, str], ...]:
    """Run one authentication-versus-state-change race using spawn."""
    context = get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = (
        context.Process(
            target=_spawned_authenticate,
            args=(str(database_path), str(token_id), digest, barrier, queue),
        ),
        context.Process(
            target=_spawned_auth_state_change,
            args=(
                str(database_path),
                str(token_id),
                operation,
                barrier,
                queue,
            ),
        ),
    )
    _run_spawned_processes(processes)
    return _read_process_outcomes(queue, count=2)


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
        pytest.fail("Spawned authentication worker timed out.")
    exit_codes = tuple(process.exitcode for process in processes)
    if any(code != 0 for code in exit_codes):
        pytest.fail(f"Authentication workers exited unsuccessfully: {exit_codes!r}")


def _read_process_outcomes(
    queue: _ProcessQueue,
    *,
    count: int,
) -> tuple[tuple[str, str], ...]:
    """Read and validate the closed authentication worker wire shape."""
    outcomes: list[tuple[str, str]] = []
    try:
        for _index in range(count):
            candidate = queue.get(timeout=10)
            if (
                not isinstance(candidate, tuple)
                or len(candidate) != 2
                or not all(isinstance(value, str) for value in candidate)
            ):
                pytest.fail("Authentication worker returned a malformed outcome.")
            status, detail = candidate
            if status == "unexpected":
                pytest.fail(f"Authentication worker failed with {detail}.")
            outcomes.append((status, detail))
    finally:
        queue.close()
        queue.join_thread()
    return tuple(outcomes)


def _event_count(repository: SQLiteRepository, *, request_id: str) -> int:
    """Return the exact committed audit-event count for one request."""
    connection = sqlite3.connect(repository.database_path)
    try:
        row = connection.execute(
            "SELECT count(*) FROM audit_events WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or type(row[0]) is not int:
        raise AssertionError
    return row[0]


def _revoke_after_barrier(
    repository: SQLiteRepository,
    token_id: TokenId,
    barrier: Barrier,
) -> None:
    """Commit administrator revocation after synchronizing the race."""
    barrier.wait()
    repository.revoke_token(
        RevokeTokenMutation(
            **_metadata("race-revoke", at=_NOW + timedelta(minutes=4)),
            token_id=token_id,
        )
    )


def _activate_after_barrier(
    repository: SQLiteRepository,
    token_id: TokenId,
    barrier: Barrier,
) -> None:
    """Commit pending Token activation after synchronizing the race."""
    barrier.wait()
    repository.activate_token(
        ActivateTokenMutation(
            **_metadata("race-activate", at=_NOW + timedelta(minutes=6)),
            token_id=token_id,
        )
    )


def _disable_after_barrier(
    repository: SQLiteRepository,
    barrier: Barrier,
) -> None:
    """Commit Subject disablement after synchronizing the race."""
    barrier.wait()
    repository.set_subject_enabled(
        SetSubjectEnabledMutation(
            **_metadata("race-disable", at=_NOW + timedelta(minutes=8)),
            subject=_AGENT_ID,
            expected_version=1,
            enabled=False,
        )
    )
