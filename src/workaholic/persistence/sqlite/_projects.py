"""Atomic authorized Project creation for the SQLite repository."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    IdempotencyConflictError,
    NotInitializedError,
    PermissionDeniedError,
    ProjectCreationMutation,
    ProjectCreationResult,
    ProjectKeyConflictError,
)
from workaholic.domain import (
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    SubjectId,
    SubjectKind,
)
from workaholic.persistence.sqlite._records import (
    PROJECT_FIELD_SET,
    canonical_json,
    parse_timestamp,
    project_from_mapping,
    project_from_row,
    project_to_mapping,
    require_boolean,
    require_integer,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

_CREATE_PROJECT_OPERATION: Final = "project.create"
_PROJECT_OUTCOME_KEYS: Final = frozenset(("project", "subject_id"))


def create_project(
    database_path: Path,
    mutation: ProjectCreationMutation,
) -> ProjectCreationResult:
    """Atomically create one Project and grant its creator Owner access.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated semantic Project creation input.

    Returns:
        The new or idempotently replayed Project and Owner grant.

    Raises:
        IdempotencyConflictError: If a caller key has different semantic input.
        NotInitializedError: If the selected Instance does not exist.
        PermissionDeniedError: If the creator is not an enabled Human admin.
        ProjectKeyConflictError: If the immutable key was previously reserved.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate_mutation: object = mutation
    if not isinstance(candidate_mutation, ProjectCreationMutation):
        raise StorageUnavailableError
    request_fingerprint = _project_fingerprint(candidate_mutation)
    try:
        with open_write_transaction(database_path) as connection:
            return _create_project_in_transaction(
                connection,
                mutation=candidate_mutation,
                request_fingerprint=request_fingerprint,
            )
    except (
        IdempotencyConflictError,
        NotInitializedError,
        PermissionDeniedError,
        ProjectKeyConflictError,
        StorageUnavailableError,
    ):
        raise
    except (TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _create_project_in_transaction(
    connection: sqlite3.Connection,
    *,
    mutation: ProjectCreationMutation,
    request_fingerprint: str,
) -> ProjectCreationResult:
    """Execute Project creation inside one caller-owned write transaction.

    Args:
        connection: Active validated write transaction.
        mutation: Validated Project creation mutation.
        request_fingerprint: Canonical semantic request digest.

    Returns:
        The new or idempotently replayed Project and Owner grant.

    """
    _require_authorized_creator(connection, mutation)
    replay = _read_idempotent_project(
        connection,
        mutation=mutation,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    _require_available_project_key(connection, mutation)

    project = Project(
        id=mutation.project_id,
        instance_id=mutation.instance_id,
        key=mutation.project_key,
        name=mutation.project_name,
        created_at=mutation.occurred_at,
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, instance_id, key, name, next_task_number, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(project.id),
            str(project.instance_id),
            project.key,
            project.name,
            1,
            serialize_timestamp(project.created_at),
        ),
    )
    grant = ProjectGrant(
        instance_id=mutation.instance_id,
        subject_id=mutation.actor_subject_id,
        project_id=project.id,
        role=ProjectRole.OWNER,
        version=1,
        granted_by=mutation.actor_subject_id,
        created_at=mutation.occurred_at,
        updated_at=mutation.occurred_at,
    )
    connection.execute(
        """
        INSERT INTO project_grants (
            instance_id, subject_id, project_id, role, version, granted_by,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(grant.instance_id),
            str(grant.subject_id),
            str(grant.project_id),
            grant.role.value,
            grant.version,
            str(grant.granted_by),
            serialize_timestamp(grant.created_at),
            serialize_timestamp(grant.updated_at),
        ),
    )
    result = ProjectCreationResult(project=project, grant=grant)
    _record_idempotent_project(
        connection,
        mutation=mutation,
        request_fingerprint=request_fingerprint,
        result=result,
    )
    return result


def _require_authorized_creator(
    connection: sqlite3.Connection,
    mutation: ProjectCreationMutation,
) -> None:
    """Require one matching Instance and one enabled Human administrator.

    Args:
        connection: Active validated write transaction.
        mutation: Project request carrying Instance and creator identities.

    Raises:
        NotInitializedError: If the selected Instance does not exist.
        PermissionDeniedError: If the selected creator is not authorized.
        StorageUnavailableError: If local singleton state is malformed.

    """
    instance_rows = connection.execute(
        "SELECT id FROM instances ORDER BY id LIMIT 2"
    ).fetchall()
    if not instance_rows:
        raise NotInitializedError
    if len(instance_rows) != 1:
        raise StorageUnavailableError
    if require_text(instance_rows[0][0]) != str(mutation.instance_id):
        raise NotInitializedError

    subject_rows = connection.execute(
        """
        SELECT id, kind, enabled, is_instance_admin
        FROM subjects
        WHERE id = ?
        LIMIT 2
        """,
        (str(mutation.actor_subject_id),),
    ).fetchall()
    if len(subject_rows) != 1:
        raise PermissionDeniedError
    if (
        require_text(subject_rows[0][0]) != str(mutation.actor_subject_id)
        or require_text(subject_rows[0][1]) != SubjectKind.HUMAN.value
        or require_boolean(subject_rows[0][2]) is not True
        or require_boolean(subject_rows[0][3]) is not True
    ):
        raise PermissionDeniedError


def _project_fingerprint(mutation: ProjectCreationMutation) -> str:
    """Hash only caller-controlled semantic Project input.

    Args:
        mutation: Validated mutation containing semantic and generated fields.

    Returns:
        Lowercase SHA-256 hexadecimal digest.

    """
    encoded = canonical_json(
        {
            "actor_subject_id": str(mutation.actor_subject_id),
            "instance_id": str(mutation.instance_id),
            "project_key": mutation.project_key,
            "project_name": mutation.project_name,
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_idempotent_project(
    connection: sqlite3.Connection,
    *,
    mutation: ProjectCreationMutation,
    request_fingerprint: str,
) -> ProjectCreationResult | None:
    """Return a recorded matching Project or reject conflicting key reuse.

    Args:
        connection: Active semantic write transaction.
        mutation: Validated Project creation mutation.
        request_fingerprint: Canonical semantic request digest.

    Returns:
        Original result for a matching replay, or ``None``.

    Raises:
        IdempotencyConflictError: If the key was used for different input.
        StorageUnavailableError: If the durable outcome is malformed.

    """
    if mutation.idempotency_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (
            str(mutation.actor_subject_id),
            _CREATE_PROJECT_OPERATION,
            mutation.idempotency_key,
        ),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    project, subject_id = _parse_project_outcome(require_text(row[1]))
    if (
        project.instance_id != mutation.instance_id
        or project.key != mutation.project_key
        or project.name != mutation.project_name
        or subject_id != mutation.actor_subject_id
    ):
        raise StorageUnavailableError
    return _load_project_result(
        connection,
        project_id=project.id,
        expected_project=project,
        subject_id=subject_id,
    )


def _require_available_project_key(
    connection: sqlite3.Connection,
    mutation: ProjectCreationMutation,
) -> None:
    """Reject any currently or historically persisted Project key.

    Args:
        connection: Active semantic write transaction.
        mutation: Project request carrying the immutable key.

    Raises:
        ProjectKeyConflictError: If the key is already persisted.
        StorageUnavailableError: If duplicate rows violate schema expectations.

    """
    rows = connection.execute(
        """
        SELECT id
        FROM projects
        WHERE instance_id = ? AND key = ?
        LIMIT 2
        """,
        (str(mutation.instance_id), mutation.project_key),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if rows:
        raise ProjectKeyConflictError


def _load_project_result(
    connection: sqlite3.Connection,
    *,
    project_id: ProjectId,
    expected_project: Project,
    subject_id: SubjectId,
) -> ProjectCreationResult:
    """Load and verify one durable Project-creation result.

    Args:
        connection: Active validated transaction.
        project_id: Exact Project identity recorded by idempotency.
        expected_project: Canonical Project snapshot recorded by idempotency.
        subject_id: Creator identity recorded by idempotency.

    Returns:
        Verified Project and Owner grant.

    Raises:
        StorageUnavailableError: If the durable graph is absent or inconsistent.

    """
    project_rows = connection.execute(
        """
        SELECT id, instance_id, key, name, created_at
        FROM projects
        WHERE id = ?
        LIMIT 2
        """,
        (str(project_id),),
    ).fetchall()
    grant_rows = connection.execute(
        """
        SELECT
            instance_id, subject_id, project_id, role, version, granted_by,
            created_at, updated_at
        FROM project_grants
        WHERE subject_id = ? AND project_id = ?
        LIMIT 2
        """,
        (str(subject_id), str(project_id)),
    ).fetchall()
    if len(project_rows) != 1 or len(grant_rows) != 1:
        raise StorageUnavailableError
    project = project_from_row(project_rows[0])
    if project != expected_project:
        raise StorageUnavailableError
    grant = ProjectGrant(
        instance_id=InstanceId(require_text(grant_rows[0][0])),
        subject_id=SubjectId(require_text(grant_rows[0][1])),
        project_id=ProjectId(require_text(grant_rows[0][2])),
        role=ProjectRole(require_text(grant_rows[0][3])),
        version=require_integer(grant_rows[0][4]),
        granted_by=SubjectId(require_text(grant_rows[0][5])),
        created_at=parse_timestamp(grant_rows[0][6]),
        updated_at=parse_timestamp(grant_rows[0][7]),
    )
    if (
        grant.instance_id != project.instance_id
        or grant.subject_id != subject_id
        or grant.project_id != project.id
    ):
        raise StorageUnavailableError
    return ProjectCreationResult(project=project, grant=grant)


def _record_idempotent_project(
    connection: sqlite3.Connection,
    *,
    mutation: ProjectCreationMutation,
    request_fingerprint: str,
    result: ProjectCreationResult,
) -> None:
    """Persist one Project replay result in the owning transaction.

    Args:
        connection: Active semantic write transaction.
        mutation: Project mutation containing the optional caller key.
        request_fingerprint: Canonical semantic request digest.
        result: Committed logical result to replay.

    """
    if mutation.idempotency_key is None:
        return
    outcome = canonical_json(
        {
            "project": project_to_mapping(result.project),
            "subject_id": str(result.grant.subject_id),
        }
    )
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(mutation.actor_subject_id),
            _CREATE_PROJECT_OPERATION,
            mutation.idempotency_key,
            request_fingerprint,
            outcome,
            serialize_timestamp(mutation.occurred_at),
        ),
    )


def _parse_project_outcome(value: str) -> tuple[Project, SubjectId]:
    """Parse one exact canonical Project-creation outcome.

    Args:
        value: Persisted canonical JSON.

    Returns:
        Original Project snapshot and creator identity.

    Raises:
        StorageUnavailableError: If the outcome is malformed or noncanonical.

    """
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict) or set(decoded) != _PROJECT_OUTCOME_KEYS:
        raise StorageUnavailableError
    project_data = decoded.get("project")
    subject_value = decoded.get("subject_id")
    if (
        not isinstance(project_data, dict)
        or set(project_data) != PROJECT_FIELD_SET
        or not isinstance(subject_value, str)
    ):
        raise StorageUnavailableError
    canonical_outcome = canonical_json(cast("Mapping[str, object]", decoded))
    if canonical_outcome != value:
        raise StorageUnavailableError
    return (
        project_from_mapping(cast("Mapping[str, object]", project_data)),
        SubjectId(subject_value),
    )
