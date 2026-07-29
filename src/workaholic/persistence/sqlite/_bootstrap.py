"""Semantic Phase 1 repository operations backed by SQLite transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    BootstrapMutation,
    BootstrapResult,
    IdempotencyConflictError,
    PermissionDeniedError,
    ProjectKeyConflictError,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    WorkspaceBinding,
)
from workaholic.persistence.sqlite.connection import open_write_transaction
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

_BOOTSTRAP_OPERATION: Final = "bootstrap.local_project"
_BOOTSTRAP_SUBJECT_SCOPE: Final = "local-bootstrap"
_LOCAL_SUBJECT_DISPLAY_NAME: Final = "Local operator"
_BOOTSTRAP_OUTCOME_KEYS: Final = frozenset(("instance_id", "project_id", "subject_id"))
_CANONICAL_TIMESTAMP_LENGTH: Final = 27


def bootstrap_local_project(
    database_path: Path,
    mutation: BootstrapMutation,
) -> BootstrapResult:
    """Atomically bootstrap or locate the single local Project.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated candidate identities and semantic input.

    Returns:
        The committed local identity and Owner authorization graph.

    Raises:
        IdempotencyConflictError: If a caller key has different input.
        ProjectKeyConflictError: If another local Project key already exists.
        PermissionDeniedError: If the persisted local Subject is not Owner.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate_mutation: object = mutation
    if not isinstance(candidate_mutation, BootstrapMutation):
        raise StorageUnavailableError
    fingerprint = _bootstrap_fingerprint(candidate_mutation.project_key)
    try:
        with open_write_transaction(database_path) as connection:
            return _bootstrap_in_transaction(
                connection,
                mutation=candidate_mutation,
                request_fingerprint=fingerprint,
            )
    except (
        IdempotencyConflictError,
        PermissionDeniedError,
        ProjectKeyConflictError,
        StorageUnavailableError,
    ):
        raise
    except (TypeError, ValueError) as error:
        raise StorageUnavailableError from error


def _bootstrap_in_transaction(
    connection: sqlite3.Connection,
    *,
    mutation: BootstrapMutation,
    request_fingerprint: str,
) -> BootstrapResult:
    """Execute bootstrap semantics inside one caller-owned write transaction.

    Args:
        connection: Active validated write transaction.
        mutation: Validated bootstrap mutation.
        request_fingerprint: Canonical semantic input digest.

    Returns:
        The existing, replayed, or newly created bootstrap result.

    """
    replay = _read_idempotent_bootstrap(
        connection,
        idempotency_key=mutation.idempotency_key,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay

    project_rows = connection.execute(
        "SELECT id, key FROM projects ORDER BY id LIMIT 2"
    ).fetchall()
    if project_rows:
        if len(project_rows) != 1:
            raise StorageUnavailableError
        persisted_key = _require_text(project_rows[0][1])
        if persisted_key != mutation.project_key:
            raise ProjectKeyConflictError
        result = _load_bootstrap_graph(
            connection,
            expected_project_id=_require_text(project_rows[0][0]),
        )
    else:
        _require_empty_bootstrap_state(connection)
        _insert_bootstrap_graph(connection, mutation)
        result = _load_bootstrap_graph(
            connection,
            expected_project_id=str(mutation.project_id),
        )

    if mutation.idempotency_key is not None:
        _record_idempotent_bootstrap(
            connection,
            mutation=mutation,
            request_fingerprint=request_fingerprint,
            result=result,
        )
    return result


def _bootstrap_fingerprint(project_key: str) -> str:
    """Build a stable fingerprint from caller-controlled semantic input only.

    Args:
        project_key: Validated immutable Project key.

    Returns:
        Lowercase SHA-256 hexadecimal digest.

    """
    encoded = _canonical_json({"project_key": project_key}).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_idempotent_bootstrap(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str | None,
    request_fingerprint: str,
) -> BootstrapResult | None:
    """Return a recorded matching outcome or reject conflicting reuse.

    Args:
        connection: Active semantic write transaction.
        idempotency_key: Optional caller-supplied key.
        request_fingerprint: Canonical semantic request digest.

    Returns:
        The persisted outcome for a matching replay, or ``None``.

    Raises:
        IdempotencyConflictError: If the key was used for different input.
        StorageUnavailableError: If the durable outcome is malformed.

    """
    if idempotency_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (_BOOTSTRAP_SUBJECT_SCOPE, _BOOTSTRAP_OPERATION, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    if _require_text(row[0]) != request_fingerprint:
        raise IdempotencyConflictError
    outcome = _parse_bootstrap_outcome(_require_text(row[1]))
    result = _load_bootstrap_graph(
        connection,
        expected_project_id=outcome["project_id"],
    )
    if (
        str(result.instance.id) != outcome["instance_id"]
        or str(result.subject.id) != outcome["subject_id"]
    ):
        raise StorageUnavailableError
    return result


def _record_idempotent_bootstrap(
    connection: sqlite3.Connection,
    *,
    mutation: BootstrapMutation,
    request_fingerprint: str,
    result: BootstrapResult,
) -> None:
    """Persist one bootstrap replay record in the owning transaction.

    Args:
        connection: Active semantic write transaction.
        mutation: Bootstrap mutation with caller key and authoritative time.
        request_fingerprint: Canonical semantic request digest.
        result: Committed logical outcome to replay.

    """
    if mutation.idempotency_key is None:
        return
    outcome = _canonical_json(
        {
            "instance_id": str(result.instance.id),
            "project_id": str(result.project.id),
            "subject_id": str(result.subject.id),
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
            _BOOTSTRAP_SUBJECT_SCOPE,
            _BOOTSTRAP_OPERATION,
            mutation.idempotency_key,
            request_fingerprint,
            outcome,
            _serialize_timestamp(mutation.occurred_at),
        ),
    )


def _require_empty_bootstrap_state(connection: sqlite3.Connection) -> None:
    """Require every domain table to be empty before first bootstrap.

    Args:
        connection: Active initialization transaction.

    Raises:
        StorageUnavailableError: If partial or unrelated local state exists.

    """
    count = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM instances)
            + (SELECT count(*) FROM subjects)
            + (SELECT count(*) FROM projects)
            + (SELECT count(*) FROM project_grants)
            + (SELECT count(*) FROM tasks)
            + (SELECT count(*) FROM task_events)
            + (SELECT count(*) FROM idempotency_records)
        """
    ).fetchone()
    if count != (0,):
        raise StorageUnavailableError


def _insert_bootstrap_graph(
    connection: sqlite3.Connection,
    mutation: BootstrapMutation,
) -> None:
    """Insert the complete local identity graph inside one transaction.

    Args:
        connection: Active semantic write transaction.
        mutation: Validated candidate identities and authoritative time.

    """
    timestamp = _serialize_timestamp(mutation.occurred_at)
    connection.execute(
        "INSERT INTO instances (id, created_at) VALUES (?, ?)",
        (str(mutation.instance_id), timestamp),
    )
    connection.execute(
        """
        INSERT INTO subjects (
            id, kind, display_name, enabled, is_instance_admin
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(mutation.subject_id),
            SubjectKind.HUMAN.value,
            _LOCAL_SUBJECT_DISPLAY_NAME,
            1,
            1,
        ),
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, instance_id, key, next_task_number, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(mutation.project_id),
            str(mutation.instance_id),
            mutation.project_key,
            1,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO project_grants (subject_id, project_id, role)
        VALUES (?, ?, ?)
        """,
        (
            str(mutation.subject_id),
            str(mutation.project_id),
            ProjectRole.OWNER.value,
        ),
    )


def _load_bootstrap_graph(
    connection: sqlite3.Connection,
    *,
    expected_project_id: str,
) -> BootstrapResult:
    """Load and validate the single local identity and authorization graph.

    Args:
        connection: Active validated transaction.
        expected_project_id: Project identity required by replay or lookup.

    Returns:
        Fully validated application result.

    Raises:
        PermissionDeniedError: If no singular enabled Owner can be selected.
        StorageUnavailableError: If persisted identity state is malformed.

    """
    instance_rows = connection.execute(
        "SELECT id, created_at FROM instances ORDER BY id LIMIT 2"
    ).fetchall()
    project_rows = connection.execute(
        """
        SELECT id, instance_id, key, created_at
        FROM projects
        ORDER BY id
        LIMIT 2
        """
    ).fetchall()
    if len(instance_rows) != 1 or len(project_rows) != 1:
        raise StorageUnavailableError
    project_id = _require_text(project_rows[0][0])
    if project_id != expected_project_id:
        raise StorageUnavailableError

    grant_rows = connection.execute(
        """
        SELECT subject_id, project_id, role
        FROM project_grants
        WHERE project_id = ?
        ORDER BY subject_id
        LIMIT 2
        """,
        (project_id,),
    ).fetchall()
    if len(grant_rows) != 1:
        raise PermissionDeniedError
    subject_id = _require_text(grant_rows[0][0])
    subject_rows = connection.execute(
        """
        SELECT id, kind, display_name, enabled, is_instance_admin
        FROM subjects
        ORDER BY id
        LIMIT 2
        """
    ).fetchall()
    if len(subject_rows) != 1 or _require_text(subject_rows[0][0]) != subject_id:
        raise PermissionDeniedError
    if (
        _require_text(subject_rows[0][1]) != SubjectKind.HUMAN.value
        or _require_boolean(subject_rows[0][3]) is not True
        or _require_boolean(subject_rows[0][4]) is not True
        or _require_text(grant_rows[0][2]) != ProjectRole.OWNER.value
    ):
        raise PermissionDeniedError

    instance = Instance(
        id=InstanceId(_require_text(instance_rows[0][0])),
        created_at=_parse_timestamp(instance_rows[0][1]),
    )
    project = Project(
        id=ProjectId(project_id),
        instance_id=InstanceId(_require_text(project_rows[0][1])),
        key=_require_text(project_rows[0][2]),
        created_at=_parse_timestamp(project_rows[0][3]),
    )
    subject = Subject(
        id=SubjectId(subject_id),
        kind=SubjectKind(_require_text(subject_rows[0][1])),
        display_name=_require_text(subject_rows[0][2]),
        enabled=_require_boolean(subject_rows[0][3]),
        is_instance_admin=_require_boolean(subject_rows[0][4]),
    )
    grant = ProjectGrant(
        subject_id=SubjectId(_require_text(grant_rows[0][0])),
        project_id=ProjectId(_require_text(grant_rows[0][1])),
        role=ProjectRole(_require_text(grant_rows[0][2])),
    )
    return BootstrapResult(
        instance=instance,
        project=project,
        subject=subject,
        grant=grant,
        workspace=WorkspaceBinding(
            context_version=1,
            profile="local",
            instance_id=instance.id,
            project_id=project.id,
            project_key=project.key,
            workspace_root=".",
        ),
    )


def _parse_bootstrap_outcome(value: str) -> Mapping[str, str]:
    """Parse one exact canonical idempotency outcome object.

    Args:
        value: Persisted canonical JSON.

    Returns:
        Exact bootstrap identity mapping.

    Raises:
        StorageUnavailableError: If the outcome is malformed.

    """
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict) or set(decoded) != _BOOTSTRAP_OUTCOME_KEYS:
        raise StorageUnavailableError
    if not all(isinstance(item, str) for item in decoded.values()):
        raise StorageUnavailableError
    return cast("Mapping[str, str]", decoded)


def _canonical_json(value: Mapping[str, str]) -> str:
    """Serialize one string mapping deterministically.

    Args:
        value: Mapping to serialize.

    Returns:
        Canonical compact JSON with sorted keys.

    """
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _serialize_timestamp(value: datetime) -> str:
    """Serialize one authoritative UTC timestamp as canonical RFC 3339 text.

    Args:
        value: Timezone-aware UTC datetime.

    Returns:
        Fixed-width microsecond precision text ending in ``Z``.

    """
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    """Parse one canonical UTC timestamp from SQLite.

    Args:
        value: Persisted timestamp value.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        StorageUnavailableError: If the persisted timestamp is malformed.

    """
    text = _require_text(value)
    if (
        len(text) != _CANONICAL_TIMESTAMP_LENGTH
        or not text.endswith("Z")
        or text[10] != "T"
        or text[19] != "."
    ):
        raise StorageUnavailableError
    return datetime.fromisoformat(f"{text[:-1]}+00:00")


def _require_text(value: object) -> str:
    """Require one nonempty SQLite text value.

    Args:
        value: Driver value.

    Returns:
        Nonempty string.

    Raises:
        StorageUnavailableError: If persisted data has the wrong type.

    """
    if not isinstance(value, str) or not value:
        raise StorageUnavailableError
    return value


def _require_boolean(value: object) -> bool:
    """Deserialize one strict SQLite boolean integer.

    Args:
        value: Driver value.

    Returns:
        Corresponding Python boolean.

    Raises:
        StorageUnavailableError: If the value is not exactly zero or one.

    """
    if type(value) is not int or value not in (0, 1):
        raise StorageUnavailableError
    return bool(value)
