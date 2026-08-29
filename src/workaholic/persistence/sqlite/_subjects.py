"""Transactional SQLite persistence for the complete Subject lifecycle."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    CreateSubjectMutation,
    IdempotencyConflictError,
    IdentityVersionConflictError,
    InvalidInputError,
    ListSubjects,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    SubjectHandleConflictError,
    SubjectPage,
    SubjectResult,
    UpdateSubjectMutation,
)
from workaholic.domain import Subject, SubjectId
from workaholic.persistence.sqlite._authorization import (
    require_administrator_remains,
    require_instance_administrator,
    resolve_subject,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    SUBJECT_FIELDS,
    canonical_json,
    parse_json_object,
    require_text,
    serialize_timestamp,
    subject_from_mapping,
    subject_from_row,
    subject_to_mapping,
)
from workaholic.persistence.sqlite.connection import (
    open_read_connection,
    open_write_transaction,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

_CREATE_OPERATION: Final = "subject.create"
_UPDATE_OPERATION: Final = "subject.update"
_ENABLE_OPERATION: Final = "subject.enable"
_DISABLE_OPERATION: Final = "subject.disable"
_ADMIN_GRANT_OPERATION: Final = "subject.admin.grant"
_ADMIN_REVOKE_OPERATION: Final = "subject.admin.revoke"
_OUTCOME_KEYS: Final = frozenset(("subject",))
_CURSOR_PREFIX: Final = "v5."
_CURSOR_VERSION: Final = 5
_CURSOR_POSITION_LENGTH: Final = 2
_CURSOR_KEYS: Final = frozenset(("after", "entity", "instance_id", "subject_id", "v"))


@dataclass(frozen=True, slots=True)
class _SubjectChange:
    """Closed prospective state used by the shared mutation transaction."""

    operation: str
    display_name: str
    enabled: bool
    is_instance_admin: bool


@dataclass(frozen=True, slots=True)
class _ReplayRequest:
    """Stable identity of one optional idempotency record."""

    actor_subject_id: SubjectId
    operation: str
    idempotency_key: str | None
    fingerprint: str


def create_subject(
    database_path: Path,
    mutation: CreateSubjectMutation,
) -> SubjectResult:
    """Create one enabled, non-administrative Subject atomically.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated authenticated Subject creation mutation.

    Returns:
        The committed or idempotently replayed Subject snapshot.

    Raises:
        SubjectHandleConflictError: If the immutable handle is already in use.
        IdempotencyConflictError: If a caller key names different semantics.
        PermissionDeniedError: If the actor is not a current administrator.
        StorageUnavailableError: If storage or persisted data is invalid.

    """
    candidate: object = mutation
    if not isinstance(candidate, CreateSubjectMutation):
        raise StorageUnavailableError
    fingerprint = _fingerprint(
        {
            "display_name": candidate.display_name,
            "handle": candidate.handle,
            "kind": candidate.kind.value,
            "subject_id": str(candidate.subject_id),
        }
    )
    with open_write_transaction(database_path) as connection:
        require_instance_administrator(
            connection,
            candidate.actor,
            occurred_at=candidate.occurred_at,
        )
        replay_request = _ReplayRequest(
            actor_subject_id=candidate.actor.subject_id,
            operation=_CREATE_OPERATION,
            idempotency_key=candidate.idempotency_key,
            fingerprint=fingerprint,
        )
        replay = _read_replay(connection, replay_request)
        if replay is not None:
            return SubjectResult(subject=replay)
        _require_available_handle(connection, mutation=candidate)
        _require_available_subject_id(connection, subject_id=candidate.subject_id)
        subject = Subject(
            id=candidate.subject_id,
            instance_id=candidate.actor.instance_id,
            kind=candidate.kind,
            handle=candidate.handle,
            display_name=candidate.display_name,
            enabled=True,
            is_instance_admin=False,
            version=1,
            created_by=candidate.actor.subject_id,
            created_at=candidate.occurred_at,
            updated_at=candidate.occurred_at,
        )
        _insert_subject(connection, subject)
        _record_replay(
            connection,
            request=replay_request,
            subject=subject,
            occurred_at=candidate.occurred_at,
        )
        return SubjectResult(subject=subject)


def list_subjects(
    database_path: Path,
    command: ListSubjects,
    *,
    now: datetime,
) -> SubjectPage:
    """List one stable handle-ordered page of Instance Subjects.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Authenticated actor-bound pagination query.
        now: Authoritative transaction time for actor revalidation.

    Returns:
        One page with a selection-bound opaque continuation cursor.

    Raises:
        InvalidInputError: If the cursor is malformed or belongs elsewhere.
        PermissionDeniedError: If the actor is not a current administrator.
        StorageUnavailableError: If storage or persisted data is invalid.

    """
    candidate: object = command
    if not isinstance(candidate, ListSubjects):
        raise StorageUnavailableError
    with open_read_connection(database_path) as connection:
        require_instance_administrator(
            connection,
            candidate.actor,
            occurred_at=now,
        )
        after_handle, after_id = _decode_cursor(candidate)
        rows = connection.execute(
            f"""
            SELECT {", ".join(SUBJECT_FIELDS)}
            FROM subjects
            WHERE instance_id = ?
              AND (handle > ? OR (handle = ? AND id > ?))
            ORDER BY handle ASC, id ASC
            LIMIT ?
            """,  # noqa: S608 - selected columns are a fixed module constant.
            (
                str(candidate.actor.instance_id),
                after_handle,
                after_handle,
                after_id,
                candidate.limit + 1,
            ),
        ).fetchall()
        page_rows = rows[: candidate.limit]
        subjects = tuple(subject_from_row(row) for row in page_rows)
        next_cursor = None
        if len(rows) > candidate.limit:
            last = subjects[-1]
            next_cursor = _encode_cursor(
                command=candidate,
                handle=last.handle,
                subject_id=last.id,
            )
        return SubjectPage(subjects=subjects, next_cursor=next_cursor)


def update_subject(
    database_path: Path,
    mutation: UpdateSubjectMutation,
) -> SubjectResult:
    """Update one Subject display name at its exact current version.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic Subject update.

    Returns:
        The committed or idempotently replayed Subject snapshot.

    """
    candidate: object = mutation
    if not isinstance(candidate, UpdateSubjectMutation):
        raise StorageUnavailableError
    return _mutate_existing(
        database_path,
        candidate,
        display_name=candidate.display_name,
    )


def set_subject_enabled(
    database_path: Path,
    mutation: SetSubjectEnabledMutation,
) -> SubjectResult:
    """Enable or disable one Subject at its exact current version.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic enabled-state mutation.

    Returns:
        The committed or idempotently replayed Subject snapshot.

    """
    candidate: object = mutation
    if not isinstance(candidate, SetSubjectEnabledMutation):
        raise StorageUnavailableError
    return _mutate_existing(database_path, candidate, enabled=candidate.enabled)


def set_instance_admin(
    database_path: Path,
    mutation: SetInstanceAdminMutation,
) -> SubjectResult:
    """Grant or revoke Instance administration at an exact Subject version.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic administrator mutation.

    Returns:
        The committed or idempotently replayed Subject snapshot.

    """
    candidate: object = mutation
    if not isinstance(candidate, SetInstanceAdminMutation):
        raise StorageUnavailableError
    return _mutate_existing(
        database_path,
        candidate,
        is_instance_admin=candidate.is_instance_admin,
    )


def _mutate_existing(
    database_path: Path,
    mutation: UpdateSubjectMutation
    | SetSubjectEnabledMutation
    | SetInstanceAdminMutation,
    *,
    display_name: str | None = None,
    enabled: bool | None = None,
    is_instance_admin: bool | None = None,
) -> SubjectResult:
    """Apply one closed Subject state change in an immediate transaction.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Validated optimistic existing-Subject mutation.
        display_name: Replacement display name when updating metadata.
        enabled: Replacement enabled state when changing availability.
        is_instance_admin: Replacement Instance administration state.

    Returns:
        The committed or idempotently replayed Subject snapshot.

    """
    with open_write_transaction(database_path) as connection:
        require_instance_administrator(
            connection,
            mutation.actor,
            occurred_at=mutation.occurred_at,
        )
        current = resolve_subject(
            connection,
            instance_id=mutation.actor.instance_id,
            selector=mutation.subject,
        )
        change = _build_change(
            current,
            display_name=display_name,
            enabled=enabled,
            is_instance_admin=is_instance_admin,
        )
        fingerprint = _fingerprint(
            {
                "display_name": change.display_name,
                "enabled": change.enabled,
                "expected_version": mutation.expected_version,
                "is_instance_admin": change.is_instance_admin,
                "subject_id": str(current.id),
            }
        )
        replay_request = _ReplayRequest(
            actor_subject_id=mutation.actor.subject_id,
            operation=change.operation,
            idempotency_key=mutation.idempotency_key,
            fingerprint=fingerprint,
        )
        replay = _read_replay(connection, replay_request)
        if replay is not None:
            return SubjectResult(subject=replay)
        if current.version != mutation.expected_version:
            raise IdentityVersionConflictError
        require_administrator_remains(
            connection,
            subject=current,
            enabled=change.enabled,
            is_instance_admin=change.is_instance_admin,
        )
        updated = replace(
            current,
            display_name=change.display_name,
            enabled=change.enabled,
            is_instance_admin=change.is_instance_admin,
            version=current.version + 1,
            updated_at=mutation.occurred_at,
        )
        cursor = connection.execute(
            """
            UPDATE subjects
            SET display_name = ?, enabled = ?, is_instance_admin = ?,
                version = ?, updated_at = ?
            WHERE id = ? AND instance_id = ? AND version = ?
            """,
            (
                updated.display_name,
                int(updated.enabled),
                int(updated.is_instance_admin),
                updated.version,
                serialize_timestamp(updated.updated_at),
                str(updated.id),
                str(updated.instance_id),
                current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise IdentityVersionConflictError
        _record_replay(
            connection,
            request=replay_request,
            subject=updated,
            occurred_at=mutation.occurred_at,
        )
        return SubjectResult(subject=updated)


def _build_change(
    current: Subject,
    *,
    display_name: str | None,
    enabled: bool | None,
    is_instance_admin: bool | None,
) -> _SubjectChange:
    """Build one exact mutation operation and prospective Subject state."""
    selected = sum(
        value is not None for value in (display_name, enabled, is_instance_admin)
    )
    if selected != 1:
        raise StorageUnavailableError
    if display_name is not None:
        return _SubjectChange(
            operation=_UPDATE_OPERATION,
            display_name=display_name,
            enabled=current.enabled,
            is_instance_admin=current.is_instance_admin,
        )
    if enabled is not None:
        return _SubjectChange(
            operation=_ENABLE_OPERATION if enabled else _DISABLE_OPERATION,
            display_name=current.display_name,
            enabled=enabled,
            is_instance_admin=current.is_instance_admin,
        )
    if is_instance_admin is None:
        raise StorageUnavailableError
    return _SubjectChange(
        operation=(
            _ADMIN_GRANT_OPERATION if is_instance_admin else _ADMIN_REVOKE_OPERATION
        ),
        display_name=current.display_name,
        enabled=current.enabled,
        is_instance_admin=is_instance_admin,
    )


def _require_available_handle(
    connection: sqlite3.Connection,
    *,
    mutation: CreateSubjectMutation,
) -> None:
    """Reject reuse of an immutable Instance-scoped Subject handle."""
    rows = connection.execute(
        """
        SELECT id FROM subjects
        WHERE instance_id = ? AND handle = ?
        LIMIT 2
        """,
        (str(mutation.actor.instance_id), mutation.handle),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if rows:
        raise SubjectHandleConflictError


def _require_available_subject_id(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
) -> None:
    """Reject an impossible generated Subject ID collision safely."""
    if (
        connection.execute(
            "SELECT 1 FROM subjects WHERE id = ? LIMIT 1",
            (str(subject_id),),
        ).fetchone()
        is not None
    ):
        raise StorageUnavailableError


def _insert_subject(connection: sqlite3.Connection, subject: Subject) -> None:
    """Insert one canonical Subject inside a caller-owned transaction."""
    values = subject_to_mapping(subject)
    connection.execute(
        f"""
        INSERT INTO subjects ({", ".join(SUBJECT_FIELDS)})
        VALUES ({", ".join("?" for _ in SUBJECT_FIELDS)})
        """,  # noqa: S608 - columns and placeholders are fixed constants.
        tuple(
            int(value) if isinstance(value, bool) else value
            for value in (values[field] for field in SUBJECT_FIELDS)
        ),
    )


def _fingerprint(value: Mapping[str, object]) -> str:
    """Return a deterministic SHA-256 fingerprint for semantic input."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_replay(
    connection: sqlite3.Connection,
    request: _ReplayRequest,
) -> Subject | None:
    """Return one exact persisted replay or reject conflicting key reuse."""
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
    if set(outcome) != _OUTCOME_KEYS or not isinstance(outcome["subject"], dict):
        raise StorageUnavailableError
    return subject_from_mapping(cast("dict[str, object]", outcome["subject"]))


def _record_replay(
    connection: sqlite3.Connection,
    *,
    request: _ReplayRequest,
    subject: Subject,
    occurred_at: datetime,
) -> None:
    """Persist one Subject mutation outcome in the owning transaction."""
    if request.idempotency_key is None:
        return
    candidate_occurred_at: object = occurred_at
    if not isinstance(candidate_occurred_at, datetime):
        raise StorageUnavailableError
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
            canonical_json({"subject": subject_to_mapping(subject)}),
            serialize_timestamp(occurred_at),
        ),
    )


def _encode_cursor(
    *,
    command: ListSubjects,
    handle: str,
    subject_id: SubjectId,
) -> str:
    """Encode one canonical actor- and Instance-bound Subject cursor."""
    payload = canonical_json(
        {
            "after": [handle, str(subject_id)],
            "entity": "subjects",
            "instance_id": str(command.actor.instance_id),
            "subject_id": str(command.actor.subject_id),
            "v": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _decode_cursor(command: ListSubjects) -> tuple[str, str]:
    """Decode one canonical cursor or return the initial ordering position."""
    if command.cursor is None:
        return "", ""
    try:
        return _parse_cursor(command)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise InvalidInputError from error


def _parse_cursor(command: ListSubjects) -> tuple[str, str]:
    """Parse and verify one non-null Subject cursor."""
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
        or payload["entity"] != "subjects"
        or payload["instance_id"] != str(command.actor.instance_id)
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
