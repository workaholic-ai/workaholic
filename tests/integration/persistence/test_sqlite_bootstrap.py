"""Integration tests for atomic idempotent SQLite local bootstrap."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    IdempotencyConflictError,
    PermissionDeniedError,
    ProjectKeyConflictError,
)
from workaholic.domain import InstanceId, ProjectId, RequestId, SubjectId
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    initialize_empty_store,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from workaholic.application import BootstrapResult

_NOW = datetime(2026, 7, 30, 10, 30, 45, 123456, tzinfo=UTC)
_CANONICAL_NOW = "2026-07-30T10:30:45.123456Z"


def _mutation(
    suffix: str,
    *,
    project_key: str = "ACME",
    project_name: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime = _NOW,
) -> BootstrapMutation:
    """Build one valid bootstrap mutation with distinct candidate identities.

    Args:
        suffix: Opaque identifier suffix.
        project_key: Requested local Project key.
        project_name: Optional initial Project display name.
        idempotency_key: Optional caller retry key.
        occurred_at: Authoritative transaction timestamp.

    Returns:
        Validated semantic bootstrap mutation.

    """
    selected_name = project_key if project_name is None else project_name
    return BootstrapMutation(
        instance_id=InstanceId(f"ins_{suffix}"),
        project_id=ProjectId(f"prj_{suffix}"),
        subject_id=SubjectId(f"sub_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        project_key=project_key,
        project_name=selected_name,
        idempotency_key=idempotency_key,
    )


def _counts(database_path: Path) -> dict[str, int]:
    """Read physical bootstrap row counts.

    Args:
        database_path: Initialized test store.

    Returns:
        Counts for identity, authorization, event, and idempotency tables.

    """
    with open_read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM instances),
                (SELECT count(*) FROM subjects),
                (SELECT count(*) FROM projects),
                (SELECT count(*) FROM project_grants),
                (SELECT count(*) FROM tasks),
                (SELECT count(*) FROM task_events),
                (SELECT count(*) FROM idempotency_records)
            """
        ).fetchone()
    assert row is not None
    names = (
        "instances",
        "subjects",
        "projects",
        "project_grants",
        "tasks",
        "task_events",
        "idempotency_records",
    )
    return dict(zip(names, row, strict=True))


def _disable_subject(connection: sqlite3.Connection) -> object:
    """Disable the selected local Subject.

    Args:
        connection: Active test mutation transaction.

    Returns:
        SQLite cursor for the physical test mutation.

    """
    return connection.execute("UPDATE subjects SET enabled = 0 WHERE id = 'sub_first'")


def _delete_owner_grant(connection: sqlite3.Connection) -> object:
    """Remove the selected local Subject's Owner grant.

    Args:
        connection: Active test mutation transaction.

    Returns:
        SQLite cursor for the physical test mutation.

    """
    return connection.execute("DELETE FROM project_grants")


def _bootstrap_pair(
    pair: tuple[SQLiteRepository, BootstrapMutation],
) -> BootstrapResult:
    """Execute one repository/mutation pair for concurrency tests.

    Args:
        pair: Repository and candidate bootstrap mutation.

    Returns:
        Committed or replayed bootstrap result.

    """
    repository, mutation = pair
    return repository.bootstrap_local_project(mutation)


def test_first_bootstrap_creates_exact_local_identity_without_secrets_or_events(
    tmp_path: Path,
) -> None:
    """First bootstrap persists one attributable Owner graph and nothing extra."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    assert repository.database_path == database_path

    result = repository.bootstrap_local_project(_mutation("first"))

    assert str(result.instance.id) == "ins_first"
    assert result.instance.created_at == _NOW
    assert str(result.project.id) == "prj_first"
    assert result.project.key == "ACME"
    assert result.project.name == "ACME"
    assert result.project.created_at == _NOW
    assert str(result.subject.id) == "sub_first"
    assert result.subject.display_name == "Local operator"
    assert result.subject.enabled
    assert result.subject.is_instance_admin
    assert result.grant.subject_id == result.subject.id
    assert result.grant.project_id == result.project.id
    assert result.workspace.project_key == "ACME"
    assert _counts(database_path) == {
        "instances": 1,
        "subjects": 1,
        "projects": 1,
        "project_grants": 1,
        "tasks": 0,
        "task_events": 0,
        "idempotency_records": 0,
    }
    with open_read_connection(database_path) as connection:
        assert connection.execute("SELECT created_at FROM instances").fetchone() == (
            _CANONICAL_NOW,
        )
        assert connection.execute("SELECT created_at FROM projects").fetchone() == (
            _CANONICAL_NOW,
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert "tokens" in table_names
        assert not {"credentials", "secrets"}.intersection(table_names)
        assert connection.execute("SELECT count(*) FROM tokens").fetchone() == (0,)


def test_bootstrap_persists_and_reloads_normalized_project_name(
    tmp_path: Path,
) -> None:
    """The initial Project display name is durable and not derived on reads."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)

    result = repository.bootstrap_local_project(
        _mutation("first", project_name="  Acme Platform  ")
    )
    retried = repository.bootstrap_local_project(
        _mutation("retry", project_name="Acme Platform")
    )

    assert result.project.name == "Acme Platform"
    assert retried == result
    with open_read_connection(database_path) as connection:
        assert connection.execute("SELECT key, name FROM projects").fetchone() == (
            "ACME",
            "Acme Platform",
        )


def test_bootstrap_rejects_same_key_with_a_different_project_name(
    tmp_path: Path,
) -> None:
    """An initialized Project key cannot silently acquire another name."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first", project_name="Acme Platform"))

    with pytest.raises(ProjectKeyConflictError):
        repository.bootstrap_local_project(
            _mutation("retry", project_name="Different Name")
        )

    with open_read_connection(database_path) as connection:
        assert connection.execute("SELECT name FROM projects").fetchone() == (
            "Acme Platform",
        )


def test_retry_without_idempotency_returns_persisted_graph(tmp_path: Path) -> None:
    """Repeating the same Project key ignores new candidates and creates no rows."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    first = repository.bootstrap_local_project(_mutation("first"))

    retried = repository.bootstrap_local_project(
        _mutation(
            "retry",
            occurred_at=_NOW + timedelta(hours=1),
        )
    )

    assert retried == first
    assert _counts(database_path) == {
        "instances": 1,
        "subjects": 1,
        "projects": 1,
        "project_grants": 1,
        "tasks": 0,
        "task_events": 0,
        "idempotency_records": 0,
    }


def test_matching_idempotency_replay_returns_original_durable_outcome(
    tmp_path: Path,
) -> None:
    """A matching caller key replays the first outcome after candidate changes."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    first = repository.bootstrap_local_project(
        _mutation("first", idempotency_key="bootstrap-1")
    )

    replayed = repository.bootstrap_local_project(
        _mutation(
            "retry",
            idempotency_key="bootstrap-1",
            occurred_at=_NOW + timedelta(days=1),
        )
    )

    assert replayed == first
    with open_read_connection(database_path) as connection:
        record = connection.execute(
            """
            SELECT subject_scope, operation, caller_key, request_fingerprint,
                   outcome_json, created_at
            FROM idempotency_records
            """
        ).fetchone()
    assert record is not None
    assert record[0:3] == (
        "local-bootstrap",
        "bootstrap.local_project",
        "bootstrap-1",
    )
    assert isinstance(record[3], str)
    assert len(record[3]) == 64
    assert record[4] == (
        '{"instance_id":"ins_first","project_id":"prj_first","subject_id":"sub_first"}'
    )
    assert record[5] == _CANONICAL_NOW


def test_conflicting_idempotency_reuse_rolls_back_without_new_state(
    tmp_path: Path,
) -> None:
    """One caller key cannot be reused for another semantic Project request."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(
        _mutation("first", idempotency_key="bootstrap-1")
    )
    before = _counts(database_path)

    with pytest.raises(IdempotencyConflictError) as captured:
        repository.bootstrap_local_project(
            _mutation(
                "second",
                project_key="OTHER",
                idempotency_key="bootstrap-1",
            )
        )

    assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
    assert _counts(database_path) == before


def test_second_project_key_is_rejected_without_idempotency_record(
    tmp_path: Path,
) -> None:
    """The Phase 1 runtime never creates a second Project namespace."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first"))

    with pytest.raises(ProjectKeyConflictError) as captured:
        repository.bootstrap_local_project(
            _mutation(
                "second",
                project_key="OTHER",
                idempotency_key="other-bootstrap",
            )
        )

    assert captured.value.code is ApplicationErrorCode.PROJECT_KEY_CONFLICT
    assert _counts(database_path)["projects"] == 1
    assert _counts(database_path)["idempotency_records"] == 0


def test_restart_reads_the_same_persisted_bootstrap_result(tmp_path: Path) -> None:
    """A new repository instance returns durable identities after process restart."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    first_repository = SQLiteRepository(database_path)
    first = first_repository.bootstrap_local_project(_mutation("first"))

    restarted_repository = SQLiteRepository(database_path)
    restarted = restarted_repository.bootstrap_local_project(_mutation("restart"))

    assert restarted == first


def test_injected_insert_failure_rolls_back_the_complete_graph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failure after a partial insert exposes no Instance or related record."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)

    def fail_after_instance(
        connection: sqlite3.Connection,
        mutation: BootstrapMutation,
    ) -> None:
        """Insert one candidate Instance and then simulate repository failure."""
        connection.execute(
            "INSERT INTO instances (id, created_at) VALUES (?, ?)",
            (str(mutation.instance_id), _CANONICAL_NOW),
        )
        message = "injected repository failure"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._bootstrap._insert_bootstrap_graph",
        fail_after_instance,
    )

    with pytest.raises(RuntimeError, match="injected"):
        repository.bootstrap_local_project(_mutation("first"))

    assert all(count == 0 for count in _counts(database_path).values())


def test_partial_preexisting_state_is_never_adopted(tmp_path: Path) -> None:
    """Bootstrap refuses a partially populated identity graph."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    with open_write_transaction(database_path) as connection:
        connection.execute(
            "INSERT INTO instances (id, created_at) VALUES (?, ?)",
            ("ins_partial", _CANONICAL_NOW),
        )
    repository = SQLiteRepository(database_path)

    with pytest.raises(StorageUnavailableError):
        repository.bootstrap_local_project(_mutation("first"))

    assert _counts(database_path)["instances"] == 1
    assert _counts(database_path)["projects"] == 0


def test_multiple_persisted_projects_are_rejected_as_corrupt(tmp_path: Path) -> None:
    """The single-Project runtime never selects arbitrarily among two rows."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first"))
    with open_write_transaction(database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("prj_second", "ins_first", "OTHER", "Other", 1, _CANONICAL_NOW),
        )

    with pytest.raises(StorageUnavailableError):
        repository.bootstrap_local_project(_mutation("retry"))


@pytest.mark.parametrize("extra_state", ["instance", "subject"])
def test_ambiguous_identity_or_subject_state_is_never_selected(
    extra_state: str,
    tmp_path: Path,
) -> None:
    """Bootstrap rejects multiple Instances and multiple candidate Subjects."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first"))
    with open_write_transaction(database_path) as connection:
        if extra_state == "instance":
            connection.execute(
                "INSERT INTO instances (id, created_at) VALUES (?, ?)",
                ("ins_extra", _CANONICAL_NOW),
            )
        else:
            connection.execute(
                """
                INSERT INTO subjects (
                    id, instance_id, kind, handle, display_name, enabled,
                    is_instance_admin, version, created_by, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sub_extra",
                    "ins_first",
                    "human",
                    "extra-operator",
                    "Extra operator",
                    1,
                    1,
                    1,
                    "sub_extra",
                    _CANONICAL_NOW,
                    _CANONICAL_NOW,
                ),
            )

    expected_error = (
        StorageUnavailableError if extra_state == "instance" else PermissionDeniedError
    )
    with pytest.raises(expected_error):
        repository.bootstrap_local_project(_mutation("retry"))


@pytest.mark.parametrize(
    "outcome",
    [
        {
            "instance_id": "ins_wrong",
            "project_id": "prj_first",
            "subject_id": "sub_first",
        },
        {
            "instance_id": "ins_first",
            "project_id": "prj_wrong",
            "subject_id": "sub_first",
        },
        {"wrong": "shape"},
        {
            "instance_id": 7,
            "project_id": "prj_first",
            "subject_id": "sub_first",
        },
    ],
)
def test_tampered_idempotency_outcome_is_never_replayed(
    outcome: dict[str, object],
    tmp_path: Path,
) -> None:
    """Replay verifies exact JSON shape and authoritative persisted identities."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(
        _mutation("first", idempotency_key="bootstrap-1")
    )
    with open_write_transaction(database_path) as connection:
        connection.execute(
            "UPDATE idempotency_records SET outcome_json = ?",
            (json.dumps(outcome, separators=(",", ":"), sort_keys=True),),
        )

    with pytest.raises(StorageUnavailableError):
        repository.bootstrap_local_project(
            _mutation("replay", idempotency_key="bootstrap-1")
        )


def test_malformed_persisted_timestamp_is_redacted(tmp_path: Path) -> None:
    """A shape-valid but impossible timestamp maps to safe storage failure."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first"))
    with open_write_transaction(database_path) as connection:
        connection.execute(
            "UPDATE instances SET created_at = ?",
            ("9999-99-99T99:99:99.999999Z",),
        )

    with pytest.raises(StorageUnavailableError) as captured:
        repository.bootstrap_local_project(_mutation("retry"))

    assert "9999" not in captured.value.safe_message


@pytest.mark.parametrize(
    "revoke",
    [
        _disable_subject,
        _delete_owner_grant,
    ],
)
def test_disabled_or_non_owner_subject_cannot_be_selected(
    revoke: Callable[[sqlite3.Connection], object],
    tmp_path: Path,
) -> None:
    """Persisted authorization is revalidated on every bootstrap lookup."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    repository.bootstrap_local_project(_mutation("first"))
    with open_write_transaction(database_path) as connection:
        revoke(connection)

    with pytest.raises(PermissionDeniedError) as captured:
        repository.bootstrap_local_project(_mutation("retry"))

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED


def test_concurrent_matching_bootstrap_creates_one_complete_graph(
    tmp_path: Path,
) -> None:
    """Concurrent first uses serialize into one graph and one replay record."""
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repositories = [SQLiteRepository(database_path) for _ in range(4)]
    mutations = [
        _mutation(str(index), idempotency_key="concurrent-bootstrap")
        for index in range(4)
    ]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                _bootstrap_pair,
                zip(repositories, mutations, strict=True),
            )
        )

    assert len(set(results)) == 1
    assert _counts(database_path) == {
        "instances": 1,
        "subjects": 1,
        "projects": 1,
        "project_grants": 1,
        "tasks": 0,
        "task_events": 0,
        "idempotency_records": 1,
    }


def test_repository_runtime_validates_inputs_and_configuration(tmp_path: Path) -> None:
    """Repository boundaries reject ambiguous paths and unvalidated mutations."""
    with pytest.raises(TypeError):
        SQLiteRepository(Path("relative.db"))

    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    with pytest.raises(StorageUnavailableError):
        repository.bootstrap_local_project(
            object(),  # type: ignore[arg-type]
        )
