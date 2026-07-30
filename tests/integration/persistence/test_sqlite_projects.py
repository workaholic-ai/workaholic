"""Integration tests for atomic idempotent SQLite Project creation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    IdempotencyConflictError,
    NotInitializedError,
    PermissionDeniedError,
    ProjectCreationMutation,
    ProjectKeyConflictError,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 30, 12, 15, 30, 654321, tzinfo=UTC)
_CANONICAL_NOW = "2026-07-30T12:15:30.654321Z"


def _repository(tmp_path: Path) -> SQLiteRepository:
    """Create one bootstrapped Phase 2 SQLite repository.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository with one enabled local Human administrator.

    """
    repository = SQLiteRepository(tmp_path / "local.db")
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW - timedelta(minutes=1),
            project_key="ACME",
            project_name="Acme",
        )
    )
    return repository


def _mutation(  # noqa: PLR0913
    suffix: str,
    *,
    instance_id: str = "ins_local",
    subject_id: str = "sub_local",
    project_key: str = "DOCS",
    project_name: str = "Documentation",
    idempotency_key: str | None = None,
    occurred_at: datetime = _NOW,
) -> ProjectCreationMutation:
    """Build one deterministic Project creation mutation.

    Args:
        suffix: Candidate Project and request identity suffix.
        instance_id: Selected initialized Instance identity.
        subject_id: Selected creator Subject identity.
        project_key: Immutable Project key.
        project_name: Human-readable Project name.
        idempotency_key: Optional caller retry key.
        occurred_at: Authoritative transaction timestamp.

    Returns:
        Validated semantic Project mutation.

    """
    return ProjectCreationMutation(
        project_id=ProjectId(f"prj_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        instance_id=InstanceId(instance_id),
        actor_subject_id=SubjectId(subject_id),
        occurred_at=occurred_at,
        project_key=project_key,
        project_name=project_name,
        idempotency_key=idempotency_key,
    )


def _counts(database_path: Path) -> tuple[int, int, int, int]:
    """Read Project, grant, event, and Project-idempotency counts.

    Args:
        database_path: Bootstrapped SQLite database path.

    Returns:
        Counts in stable assertion order.

    """
    with open_read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM projects),
                (SELECT count(*) FROM project_grants),
                (SELECT count(*) FROM task_events),
                (
                    SELECT count(*) FROM idempotency_records
                    WHERE operation = 'project.create'
                )
            """
        ).fetchone()
    assert row is not None
    assert all(type(value) is int for value in row)
    return cast("tuple[int, int, int, int]", row)


def test_create_project_commits_project_and_owner_grant_together(
    tmp_path: Path,
) -> None:
    """One mutation commits its namespace and creator authorization atomically."""
    repository = _repository(tmp_path)

    result = repository.create_project(_mutation("docs"))

    assert result.project.id == ProjectId("prj_docs")
    assert result.project.instance_id == InstanceId("ins_local")
    assert result.project.key == "DOCS"
    assert result.project.name == "Documentation"
    assert result.project.created_at == _NOW
    assert result.grant.subject_id == SubjectId("sub_local")
    assert result.grant.project_id == result.project.id
    assert result.grant.role.value == "owner"
    assert _counts(repository.database_path) == (2, 2, 0, 0)
    with open_read_connection(repository.database_path) as connection:
        project = connection.execute(
            """
            SELECT id, instance_id, key, name, next_task_number, created_at
            FROM projects
            WHERE id = 'prj_docs'
            """
        ).fetchone()
    assert project == (
        "prj_docs",
        "ins_local",
        "DOCS",
        "Documentation",
        1,
        _CANONICAL_NOW,
    )


def test_create_project_persists_normalized_unicode_name(tmp_path: Path) -> None:
    """Project names are normalized before they enter durable state."""
    repository = _repository(tmp_path)
    mutation = _mutation("docs", project_name="  Cafe\u0301 docs  ")

    result = repository.create_project(mutation)

    assert mutation.project_name == "Café docs"
    assert result.project.name == "Café docs"
    with open_read_connection(repository.database_path) as connection:
        row = connection.execute(
            "SELECT name FROM projects WHERE id = 'prj_docs'"
        ).fetchone()
    assert row == ("Café docs",)


def test_matching_idempotency_replay_returns_original_project_and_grant(
    tmp_path: Path,
) -> None:
    """A semantic retry replays the original IDs without writing again."""
    repository = _repository(tmp_path)
    first = repository.create_project(
        _mutation("first", idempotency_key="project-create-1")
    )

    replayed = repository.create_project(
        _mutation(
            "replay",
            idempotency_key="project-create-1",
            occurred_at=_NOW + timedelta(hours=1),
        )
    )

    assert replayed == first
    assert _counts(repository.database_path) == (2, 2, 0, 1)
    with open_read_connection(repository.database_path) as connection:
        record = connection.execute(
            """
            SELECT subject_scope, operation, caller_key, outcome_json, created_at
            FROM idempotency_records
            WHERE operation = 'project.create'
            """
        ).fetchone()
    assert record is not None
    assert record[0:3] == ("sub_local", "project.create", "project-create-1")
    assert json.loads(record[3]) == {
        "project": {
            "created_at": _CANONICAL_NOW,
            "id": "prj_first",
            "instance_id": "ins_local",
            "key": "DOCS",
            "name": "Documentation",
        },
        "subject_id": "sub_local",
    }
    assert record[4] == _CANONICAL_NOW


@pytest.mark.parametrize(
    ("project_key", "project_name"),
    [
        ("DOCS", "Different name"),
        ("OPS", "Documentation"),
    ],
)
def test_conflicting_idempotency_reuse_changes_nothing(
    project_key: str,
    project_name: str,
    tmp_path: Path,
) -> None:
    """One caller key cannot represent different semantic Project input."""
    repository = _repository(tmp_path)
    repository.create_project(_mutation("first", idempotency_key="project-create-1"))
    before = _counts(repository.database_path)

    with pytest.raises(IdempotencyConflictError) as captured:
        repository.create_project(
            _mutation(
                "conflict",
                project_key=project_key,
                project_name=project_name,
                idempotency_key="project-create-1",
            )
        )

    assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
    assert _counts(repository.database_path) == before


@pytest.mark.parametrize("tamper", ["shape", "noncanonical", "project", "subject"])
def test_tampered_idempotency_outcome_is_never_replayed(
    tamper: str,
    tmp_path: Path,
) -> None:
    """Replay validates exact canonical outcome shape and durable relationships."""
    repository = _repository(tmp_path)
    repository.create_project(_mutation("first", idempotency_key="project-create-1"))
    with open_write_transaction(repository.database_path) as connection:
        row = connection.execute(
            """
            SELECT outcome_json
            FROM idempotency_records
            WHERE operation = 'project.create'
            """
        ).fetchone()
        assert row is not None
        outcome = json.loads(row[0])
        if tamper == "shape":
            outcome = {"wrong": "shape"}
            encoded = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
        elif tamper == "noncanonical":
            encoded = json.dumps(outcome, sort_keys=True)
        elif tamper == "project":
            outcome["project"]["name"] = "Tampered"
            encoded = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
        else:
            outcome["subject_id"] = "sub_other"
            encoded = json.dumps(outcome, separators=(",", ":"), sort_keys=True)
        connection.execute(
            """
            UPDATE idempotency_records
            SET outcome_json = ?
            WHERE operation = 'project.create'
            """,
            (encoded,),
        )

    with pytest.raises(StorageUnavailableError):
        repository.create_project(
            _mutation("replay", idempotency_key="project-create-1")
        )


def test_idempotency_replay_requires_persisted_owner_grant(tmp_path: Path) -> None:
    """A replay cannot conceal a missing creator authorization edge."""
    repository = _repository(tmp_path)
    repository.create_project(_mutation("first", idempotency_key="project-create-1"))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            DELETE FROM project_grants
            WHERE project_id = 'prj_first'
            """
        )

    with pytest.raises(StorageUnavailableError):
        repository.create_project(
            _mutation("replay", idempotency_key="project-create-1")
        )


def test_instance_mismatch_is_not_initialized_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """Project creation must select the exact initialized Instance."""
    repository = _repository(tmp_path)

    with pytest.raises(NotInitializedError) as captured:
        repository.create_project(_mutation("wrong", instance_id="ins_other"))

    assert captured.value.code is ApplicationErrorCode.NOT_INITIALIZED
    assert _counts(repository.database_path) == (1, 1, 0, 0)


@pytest.mark.parametrize("authorization", ["missing", "disabled", "not-admin"])
def test_unauthorized_creator_cannot_create_project(
    authorization: str,
    tmp_path: Path,
) -> None:
    """Project creation requires the exact enabled Human Instance administrator."""
    repository = _repository(tmp_path)
    mutation = _mutation("forbidden")
    if authorization == "missing":
        mutation = _mutation("forbidden", subject_id="sub_other")
    else:
        with open_write_transaction(repository.database_path) as connection:
            if authorization == "disabled":
                connection.execute(
                    "UPDATE subjects SET enabled = 0 WHERE id = 'sub_local'"
                )
            else:
                connection.execute(
                    """
                    UPDATE subjects
                    SET is_instance_admin = 0
                    WHERE id = 'sub_local'
                    """
                )

    with pytest.raises(PermissionDeniedError) as captured:
        repository.create_project(mutation)

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED
    assert _counts(repository.database_path) == (1, 1, 0, 0)


@pytest.mark.parametrize(
    ("project_key", "project_name"),
    [
        ("ACME", "Acme"),
        ("ACME", "Renamed Acme"),
    ],
)
def test_existing_project_key_is_always_reserved(
    project_key: str,
    project_name: str,
    tmp_path: Path,
) -> None:
    """An existing key conflicts regardless of whether other input matches."""
    repository = _repository(tmp_path)

    with pytest.raises(ProjectKeyConflictError) as captured:
        repository.create_project(
            _mutation(
                "duplicate",
                project_key=project_key,
                project_name=project_name,
            )
        )

    assert captured.value.code is ApplicationErrorCode.PROJECT_KEY_CONFLICT
    assert _counts(repository.database_path) == (1, 1, 0, 0)


def test_failure_after_project_and_grant_insert_rolls_back_everything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A late failure consumes neither Project key nor idempotency key."""
    repository = _repository(tmp_path)

    def fail_record(*_arguments: object, **_keywords: object) -> None:
        """Simulate failure after Project and grant insertion."""
        message = "injected idempotency failure"
        raise RuntimeError(message)

    monkeypatch.setattr(
        "workaholic.persistence.sqlite._projects._record_idempotent_project",
        fail_record,
    )
    mutation = _mutation("failed", idempotency_key="retry-after-failure")

    with pytest.raises(RuntimeError, match="injected"):
        repository.create_project(mutation)

    assert _counts(repository.database_path) == (1, 1, 0, 0)
    monkeypatch.undo()

    result = repository.create_project(mutation)

    assert result.project.key == "DOCS"
    assert _counts(repository.database_path) == (2, 2, 0, 1)


def test_candidate_project_id_collision_rolls_back_without_consuming_key(
    tmp_path: Path,
) -> None:
    """A generated-ID collision is operational failure, not partial creation."""
    repository = _repository(tmp_path)
    mutation = ProjectCreationMutation(
        project_id=ProjectId("prj_acme"),
        request_id=RequestId("req_collision"),
        instance_id=InstanceId("ins_local"),
        actor_subject_id=SubjectId("sub_local"),
        occurred_at=_NOW,
        project_key="DOCS",
        project_name="Documentation",
        idempotency_key="collision",
    )

    with pytest.raises(StorageUnavailableError):
        repository.create_project(mutation)

    assert _counts(repository.database_path) == (1, 1, 0, 0)


def test_repository_runtime_validates_mutation_type(tmp_path: Path) -> None:
    """The adapter rejects bypasses of its validated mutation boundary."""
    repository = _repository(tmp_path)

    with pytest.raises(StorageUnavailableError):
        repository.create_project(cast("ProjectCreationMutation", object()))

    assert _counts(repository.database_path) == (1, 1, 0, 0)
