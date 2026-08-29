"""Transactional SQLite persistence for cumulative ProjectGrant lifecycle."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    AssignProjectGrantMutation,
    GrantNotFoundError,
    IdempotencyConflictError,
    IdentityVersionConflictError,
    InvalidInputError,
    InvalidTransitionError,
    ListProjectGrants,
    PermissionDeniedError,
    ProjectGrantPage,
    ProjectGrantResult,
    RevokeProjectGrantMutation,
)
from workaholic.domain import AuditEventType, Project, ProjectGrant, Subject, SubjectId
from workaholic.persistence.sqlite._audit_events import (
    AuditEventDraft,
    append_audit_event,
    authenticated_audit_actor,
)
from workaholic.persistence.sqlite._authorization import (
    load_project_grant,
    require_grant_administrator,
    require_grant_change_preserves_owner,
    resolve_subject,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    PROJECT_GRANT_FIELDS,
    canonical_json,
    parse_json_object,
    project_grant_from_mapping,
    project_grant_from_row,
    project_grant_to_mapping,
    require_text,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.connection import (
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

_ASSIGN_OPERATION: Final = "project.grant.assign"
_REVOKE_OPERATION: Final = "project.grant.revoke"
_OUTCOME_KEYS: Final = frozenset(("grant",))
_CURSOR_PREFIX: Final = "v5."
_CURSOR_VERSION: Final = 5
_CURSOR_POSITION_LENGTH: Final = 2
_CURSOR_KEYS: Final = frozenset(
    ("after", "entity", "instance_id", "project_id", "subject_id", "v")
)


@dataclass(frozen=True, slots=True)
class _ReplayRequest:
    """Stable identity of one optional grant idempotency record."""

    actor_subject_id: SubjectId
    operation: str
    idempotency_key: str | None
    fingerprint: str


def assign_project_grant(
    database_path: Path,
    mutation: AssignProjectGrantMutation,
) -> ProjectGrantResult:
    """Create or replace one cumulative ProjectGrant atomically.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Authenticated create-or-replace grant mutation.

    Returns:
        Committed or idempotently replayed grant snapshot.

    Raises:
        IdentityVersionConflictError: If create/replace concurrency mismatches.
        InvalidTransitionError: If a fresh request assigns the current role.
        LastProjectOwnerError: If replacement removes the final enabled Owner.
        PermissionDeniedError: If actor or target state is unauthorized.

    """
    candidate: object = mutation
    if not isinstance(candidate, AssignProjectGrantMutation):
        raise StorageUnavailableError
    with open_write_transaction(database_path) as connection:
        actor, project = require_grant_administrator(
            connection,
            actor=candidate.actor,
            project=candidate.project,
            occurred_at=candidate.occurred_at,
        )
        target = resolve_subject(
            connection,
            instance_id=actor.instance_id,
            selector=candidate.subject,
        )
        if not target.enabled:
            raise PermissionDeniedError
        current = load_project_grant(
            connection,
            instance_id=actor.instance_id,
            project_id=project.id,
            subject_id=target.id,
        )
        request = _replay_request(
            mutation=candidate,
            actor=actor,
            project=project,
            target=target,
        )
        replay = _read_replay(connection, request)
        if replay is not None:
            return ProjectGrantResult(grant=replay)
        _require_assignment_version(candidate, current=current)
        if current is not None and current.role is candidate.role:
            raise InvalidTransitionError
        require_grant_change_preserves_owner(
            connection,
            current=current,
            project=project,
            subject=target,
            prospective_role=candidate.role,
        )
        grant = _build_assignment(
            mutation=candidate,
            actor=actor,
            project=project,
            target=target,
            current=current,
        )
        _persist_assignment(connection, grant=grant, current=current)
        append_audit_event(
            connection,
            AuditEventDraft(
                actor=authenticated_audit_actor(candidate.actor),
                request_id=candidate.request_id,
                event_type=AuditEventType.PROJECT_GRANT_ASSIGNED,
                occurred_at=candidate.occurred_at,
                payload={
                    "project_id": str(grant.project_id),
                    "role": grant.role.value,
                    "subject_id": str(grant.subject_id),
                    "version": grant.version,
                },
            ),
        )
        _record_replay(
            connection,
            request=request,
            grant=grant,
            occurred_at=candidate.occurred_at,
        )
        return ProjectGrantResult(grant=grant)


def list_project_grants(
    database_path: Path,
    command: ListProjectGrants,
    *,
    now: datetime,
) -> ProjectGrantPage:
    """List one stable Subject-handle-ordered page of Project grants.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Authenticated Project and pagination query.
        now: Authoritative transaction time for actor revalidation.

    Returns:
        Current grant page with a scope-bound opaque cursor.

    """
    candidate: object = command
    if not isinstance(candidate, ListProjectGrants):
        raise StorageUnavailableError
    with open_read_connection(database_path) as connection:
        actor, project = require_grant_administrator(
            connection,
            actor=candidate.actor,
            project=candidate.project,
            occurred_at=now,
        )
        after_handle, after_id = _decode_cursor(candidate, project=project)
        grant_columns = ", ".join(f"g.{field}" for field in PROJECT_GRANT_FIELDS)
        rows = connection.execute(
            f"""
            SELECT {grant_columns}, s.handle, s.id
            FROM project_grants AS g
            JOIN subjects AS s
              ON s.id = g.subject_id AND s.instance_id = g.instance_id
            WHERE g.instance_id = ? AND g.project_id = ?
              AND (s.handle > ? OR (s.handle = ? AND s.id > ?))
            ORDER BY s.handle ASC, s.id ASC
            LIMIT ?
            """,  # noqa: S608 - selected columns are a fixed module constant.
            (
                str(actor.instance_id),
                str(project.id),
                after_handle,
                after_handle,
                after_id,
                candidate.limit + 1,
            ),
        ).fetchall()
        page_rows = rows[: candidate.limit]
        grants = tuple(
            project_grant_from_row(row[: len(PROJECT_GRANT_FIELDS)])
            for row in page_rows
        )
        next_cursor = None
        if len(rows) > candidate.limit:
            last_row = page_rows[-1]
            next_cursor = _encode_cursor(
                command=candidate,
                project=project,
                handle=require_text(last_row[-2]),
                subject_id=SubjectId(require_text(last_row[-1])),
            )
        return ProjectGrantPage(grants=grants, next_cursor=next_cursor)


def revoke_project_grant(
    database_path: Path,
    mutation: RevokeProjectGrantMutation,
) -> ProjectGrantResult:
    """Revoke one exact current ProjectGrant atomically.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Authenticated optimistic grant revocation.

    Returns:
        Revoked grant snapshot or exact idempotent replay.

    Raises:
        GrantNotFoundError: If the scoped grant is absent.
        IdentityVersionConflictError: If the expected version is stale.
        LastProjectOwnerError: If revocation removes the final enabled Owner.

    """
    candidate: object = mutation
    if not isinstance(candidate, RevokeProjectGrantMutation):
        raise StorageUnavailableError
    with open_write_transaction(database_path) as connection:
        actor, project = require_grant_administrator(
            connection,
            actor=candidate.actor,
            project=candidate.project,
            occurred_at=candidate.occurred_at,
        )
        target = resolve_subject(
            connection,
            instance_id=actor.instance_id,
            selector=candidate.subject,
        )
        request = _revoke_replay_request(
            mutation=candidate,
            actor=actor,
            project=project,
            target=target,
        )
        replay = _read_replay(connection, request)
        if replay is not None:
            return ProjectGrantResult(grant=replay)
        current = load_project_grant(
            connection,
            instance_id=actor.instance_id,
            project_id=project.id,
            subject_id=target.id,
        )
        if current is None:
            raise GrantNotFoundError
        if current.version != candidate.expected_version:
            raise IdentityVersionConflictError
        require_grant_change_preserves_owner(
            connection,
            current=current,
            project=project,
            subject=target,
            prospective_role=None,
        )
        cursor = connection.execute(
            """
            DELETE FROM project_grants
            WHERE instance_id = ? AND project_id = ? AND subject_id = ?
              AND version = ?
            """,
            (
                str(current.instance_id),
                str(current.project_id),
                str(current.subject_id),
                current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise IdentityVersionConflictError
        append_audit_event(
            connection,
            AuditEventDraft(
                actor=authenticated_audit_actor(candidate.actor),
                request_id=candidate.request_id,
                event_type=AuditEventType.PROJECT_GRANT_REVOKED,
                occurred_at=candidate.occurred_at,
                payload={
                    "previous_role": current.role.value,
                    "previous_version": current.version,
                    "project_id": str(current.project_id),
                    "subject_id": str(current.subject_id),
                },
            ),
        )
        _record_replay(
            connection,
            request=request,
            grant=current,
            occurred_at=candidate.occurred_at,
        )
        return ProjectGrantResult(grant=current)


def _require_assignment_version(
    mutation: AssignProjectGrantMutation,
    *,
    current: ProjectGrant | None,
) -> None:
    """Enforce exact absent-create versus versioned-replacement semantics."""
    if current is None:
        if mutation.expected_version is not None:
            raise IdentityVersionConflictError
        return
    if mutation.expected_version != current.version:
        raise IdentityVersionConflictError


def _build_assignment(
    *,
    mutation: AssignProjectGrantMutation,
    actor: Subject,
    project: Project,
    target: Subject,
    current: ProjectGrant | None,
) -> ProjectGrant:
    """Build one attributed create or replacement ProjectGrant."""
    return ProjectGrant(
        instance_id=actor.instance_id,
        subject_id=target.id,
        project_id=project.id,
        role=mutation.role,
        version=1 if current is None else current.version + 1,
        granted_by=actor.id,
        created_at=mutation.occurred_at if current is None else current.created_at,
        updated_at=mutation.occurred_at,
    )


def _persist_assignment(
    connection: sqlite3.Connection,
    *,
    grant: ProjectGrant,
    current: ProjectGrant | None,
) -> None:
    """Insert or replace one versioned ProjectGrant row exactly once."""
    if current is None:
        connection.execute(
            f"""
            INSERT INTO project_grants ({", ".join(PROJECT_GRANT_FIELDS)})
            VALUES ({", ".join("?" for _ in PROJECT_GRANT_FIELDS)})
            """,  # noqa: S608 - columns and placeholders are fixed constants.
            _grant_values(grant),
        )
        return
    cursor = connection.execute(
        """
        UPDATE project_grants
        SET role = ?, version = ?, granted_by = ?, updated_at = ?
        WHERE instance_id = ? AND project_id = ? AND subject_id = ?
          AND version = ?
        """,
        (
            grant.role.value,
            grant.version,
            str(grant.granted_by),
            serialize_timestamp(grant.updated_at),
            str(grant.instance_id),
            str(grant.project_id),
            str(grant.subject_id),
            current.version,
        ),
    )
    if cursor.rowcount != 1:
        raise IdentityVersionConflictError


def _grant_values(grant: ProjectGrant) -> tuple[object, ...]:
    """Return one ProjectGrant in canonical SQL parameter order."""
    mapping = project_grant_to_mapping(grant)
    return tuple(mapping[field] for field in PROJECT_GRANT_FIELDS)


def _replay_request(
    *,
    mutation: AssignProjectGrantMutation,
    actor: Subject,
    project: Project,
    target: Subject,
) -> _ReplayRequest:
    """Build one canonical assignment idempotency identity."""
    return _ReplayRequest(
        actor_subject_id=actor.id,
        operation=_ASSIGN_OPERATION,
        idempotency_key=mutation.idempotency_key,
        fingerprint=_fingerprint(
            {
                "actor_subject_id": str(actor.id),
                "expected_version": mutation.expected_version,
                "instance_id": str(actor.instance_id),
                "project_id": str(project.id),
                "role": mutation.role.value,
                "subject_id": str(target.id),
            }
        ),
    )


def _revoke_replay_request(
    *,
    mutation: RevokeProjectGrantMutation,
    actor: Subject,
    project: Project,
    target: Subject,
) -> _ReplayRequest:
    """Build one canonical revocation idempotency identity."""
    return _ReplayRequest(
        actor_subject_id=actor.id,
        operation=_REVOKE_OPERATION,
        idempotency_key=mutation.idempotency_key,
        fingerprint=_fingerprint(
            {
                "actor_subject_id": str(actor.id),
                "expected_version": mutation.expected_version,
                "instance_id": str(actor.instance_id),
                "project_id": str(project.id),
                "subject_id": str(target.id),
            }
        ),
    )


def _fingerprint(value: Mapping[str, object]) -> str:
    """Return a deterministic SHA-256 fingerprint for semantic input."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_replay(
    connection: sqlite3.Connection,
    request: _ReplayRequest,
) -> ProjectGrant | None:
    """Return one persisted grant replay or reject conflicting key reuse."""
    if request.idempotency_key is None:
        return None
    row = connection.execute(
        """
        SELECT request_fingerprint, outcome_json
        FROM idempotency_records
        WHERE subject_scope = ? AND operation = ? AND caller_key = ?
        """,
        (
            str(request.actor_subject_id),
            request.operation,
            request.idempotency_key,
        ),
    ).fetchone()
    if row is None:
        return None
    if require_text(row[0]) != request.fingerprint:
        raise IdempotencyConflictError
    outcome = parse_json_object(
        row[1],
        maximum=IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    )
    if set(outcome) != _OUTCOME_KEYS or not isinstance(outcome["grant"], dict):
        raise StorageUnavailableError
    return project_grant_from_mapping(cast("dict[str, object]", outcome["grant"]))


def _record_replay(
    connection: sqlite3.Connection,
    *,
    request: _ReplayRequest,
    grant: ProjectGrant,
    occurred_at: datetime,
) -> None:
    """Persist one safe grant mutation outcome in the owning transaction."""
    if request.idempotency_key is None:
        return
    connection.execute(
        """
        INSERT INTO idempotency_records (
            subject_scope, operation, caller_key, request_fingerprint,
            outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(request.actor_subject_id),
            request.operation,
            request.idempotency_key,
            request.fingerprint,
            canonical_json({"grant": project_grant_to_mapping(grant)}),
            serialize_timestamp(occurred_at),
        ),
    )


def _encode_cursor(
    *,
    command: ListProjectGrants,
    project: Project,
    handle: str,
    subject_id: SubjectId,
) -> str:
    """Encode one canonical actor- and Project-bound grant cursor."""
    payload = canonical_json(
        {
            "after": [handle, str(subject_id)],
            "entity": "project-grants",
            "instance_id": str(command.actor.instance_id),
            "project_id": str(project.id),
            "subject_id": str(command.actor.subject_id),
            "v": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _decode_cursor(
    command: ListProjectGrants,
    *,
    project: Project,
) -> tuple[str, str]:
    """Decode one canonical grant cursor or return the initial position."""
    if command.cursor is None:
        return "", ""
    try:
        return _parse_cursor(command, project=project)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InvalidInputError from error


def _parse_cursor(
    command: ListProjectGrants,
    *,
    project: Project,
) -> tuple[str, str]:
    """Parse and verify one non-null grant cursor."""
    cursor = command.cursor
    if cursor is None or not cursor.startswith(_CURSOR_PREFIX):
        raise ValueError
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    if not encoded or "=" in encoded:
        raise ValueError
    padding = "=" * (-len(encoded) % 4)
    payload_bytes = base64.b64decode(
        f"{encoded}{padding}",
        altchars=b"-_",
        validate=True,
    )
    decoded: object = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(decoded, dict) or set(decoded) != _CURSOR_KEYS:
        raise ValueError
    payload = cast("dict[str, object]", decoded)
    after = payload["after"]
    if (
        payload["v"] != _CURSOR_VERSION
        or payload["entity"] != "project-grants"
        or payload["instance_id"] != str(command.actor.instance_id)
        or payload["project_id"] != str(project.id)
        or payload["subject_id"] != str(command.actor.subject_id)
        or not isinstance(after, list)
        or len(after) != _CURSOR_POSITION_LENGTH
        or not isinstance(after[0], str)
        or not isinstance(after[1], str)
        or not after[0]
    ):
        raise ValueError
    SubjectId(after[1])
    canonical = (
        base64.urlsafe_b64encode(canonical_json(payload).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    if cursor != f"{_CURSOR_PREFIX}{canonical}":
        raise ValueError
    return after[0], after[1]
