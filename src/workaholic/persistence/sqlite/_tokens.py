"""Transactional SQLite persistence for non-secret Token lifecycle metadata."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, cast

from workaholic.application import (
    ActivateTokenMutation,
    IdempotencyConflictError,
    InvalidInputError,
    InvalidTransitionError,
    IssueTokenMutation,
    ListTokens,
    PermissionDeniedError,
    RevokeTokenMutation,
    TokenNotFoundError,
    TokenPage,
    TokenResult,
)
from workaholic.domain import (
    AuditEventType,
    InstanceId,
    Subject,
    SubjectId,
    Token,
    TokenId,
    TokenStatus,
    TokenSummary,
)
from workaholic.persistence.sqlite._audit_events import (
    AuditEventDraft,
    append_audit_event,
    authenticated_audit_actor,
)
from workaholic.persistence.sqlite._authentication import require_authenticated_actor
from workaholic.persistence.sqlite._authorization import (
    require_instance_administrator,
    resolve_subject,
)
from workaholic.persistence.sqlite._records import (
    IDEMPOTENCY_OUTCOME_JSON_MAX_LENGTH,
    TOKEN_FIELDS,
    canonical_json,
    parse_json_object,
    parse_timestamp,
    require_text,
    serialize_timestamp,
    token_from_row,
    token_summary_from_mapping,
    token_summary_to_mapping,
    token_to_summary,
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

_ACTIVATE_OPERATION: Final = "token.activate"
_REVOKE_OPERATION: Final = "token.revoke"
_OUTCOME_KEYS: Final = frozenset(("token",))
_CURSOR_PREFIX: Final = "v5."
_CURSOR_VERSION: Final = 5
_CURSOR_POSITION_LENGTH: Final = 2
_CURSOR_KEYS: Final = frozenset(
    ("after", "entity", "instance_id", "subject_id", "target_subject_id", "v")
)


@dataclass(frozen=True, slots=True)
class _ReplayRequest:
    """Stable identity of one optional Token idempotency record."""

    actor_subject_id: SubjectId
    operation: str
    idempotency_key: str | None
    fingerprint: str


def issue_pending_token(
    database_path: Path,
    mutation: IssueTokenMutation,
) -> TokenResult:
    """Persist one non-authenticating Token digest for an enabled Subject.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Authenticated pending-Token metadata mutation.

    Returns:
        Non-secret pending Token metadata.

    Raises:
        PermissionDeniedError: If actor authority or target state is invalid.
        StorageUnavailableError: If identities collide or storage is malformed.

    """
    candidate: object = mutation
    if not isinstance(candidate, IssueTokenMutation):
        raise StorageUnavailableError
    # Provisioning idempotency belongs exclusively to successful activation.
    # Accepting a key here would reserve it before the credential sink exists.
    if candidate.idempotency_key is not None:
        raise InvalidInputError
    with open_write_transaction(database_path) as connection:
        require_instance_administrator(
            connection,
            candidate.actor,
            occurred_at=candidate.occurred_at,
        )
        target = resolve_subject(
            connection,
            instance_id=candidate.actor.instance_id,
            selector=candidate.subject,
        )
        if not target.enabled:
            raise PermissionDeniedError
        _require_available_token_identity(
            connection,
            token_id=candidate.token_id,
            token_digest=candidate.token_digest,
        )
        token = Token(
            id=candidate.token_id,
            instance_id=candidate.actor.instance_id,
            subject_id=target.id,
            token_hash=candidate.token_digest,
            created_by=candidate.actor.subject_id,
            created_at=candidate.occurred_at,
            activated_at=None,
            expires_at=candidate.expires_at,
            revoked_at=None,
            revoked_by=None,
        )
        _insert_token(connection, token)
        return TokenResult(
            token=token_to_summary(token, now=candidate.occurred_at),
        )


def activate_token(
    database_path: Path,
    mutation: ActivateTokenMutation,
) -> TokenResult:
    """Activate one pending Token after its external credential sink succeeds.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Authenticated activation mutation.

    Returns:
        Non-secret active Token metadata or an exact idempotent replay.

    """
    candidate: object = mutation
    if not isinstance(candidate, ActivateTokenMutation):
        raise StorageUnavailableError
    request = _ReplayRequest(
        actor_subject_id=candidate.actor.subject_id,
        operation=_ACTIVATE_OPERATION,
        idempotency_key=candidate.idempotency_key,
        fingerprint=_fingerprint(
            {
                "actor_subject_id": str(candidate.actor.subject_id),
                "instance_id": str(candidate.actor.instance_id),
                "token_id": str(candidate.token_id),
            }
        ),
    )
    with open_write_transaction(database_path) as connection:
        require_instance_administrator(
            connection,
            candidate.actor,
            occurred_at=candidate.occurred_at,
        )
        replay = _read_replay(connection, request)
        if replay is not None:
            return TokenResult(token=replay)
        token = _load_token(
            connection,
            token_id=candidate.token_id,
            instance_id=candidate.actor.instance_id,
        )
        target = resolve_subject(
            connection,
            instance_id=candidate.actor.instance_id,
            selector=token.subject_id,
        )
        if (
            not target.enabled
            or token.activated_at is not None
            or token.revoked_at is not None
            or candidate.occurred_at >= token.expires_at
        ):
            raise InvalidTransitionError
        active = replace(token, activated_at=candidate.occurred_at)
        cursor = connection.execute(
            """
            UPDATE tokens
            SET activated_at = ?
            WHERE id = ? AND instance_id = ?
              AND activated_at IS NULL AND revoked_at IS NULL
            """,
            (
                serialize_timestamp(candidate.occurred_at),
                str(active.id),
                str(active.instance_id),
            ),
        )
        if cursor.rowcount != 1:
            raise InvalidTransitionError
        summary = token_to_summary(active, now=candidate.occurred_at)
        append_audit_event(
            connection,
            AuditEventDraft(
                actor=authenticated_audit_actor(candidate.actor),
                request_id=candidate.request_id,
                event_type=AuditEventType.TOKEN_ISSUED,
                occurred_at=candidate.occurred_at,
                payload={
                    "token_id": str(active.id),
                    "subject_id": str(active.subject_id),
                    "expires_at": serialize_timestamp(active.expires_at),
                },
            ),
        )
        _record_replay(
            connection,
            request=request,
            summary=summary,
            occurred_at=candidate.occurred_at,
        )
        return TokenResult(token=summary)


def list_tokens(
    database_path: Path,
    command: ListTokens,
    *,
    now: datetime,
) -> TokenPage:
    """List one stable page of self- or administrator-visible Token metadata.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Authenticated target and pagination query.
        now: Authoritative transaction time for lifecycle projection.

    Returns:
        Creation-ordered non-secret Token metadata page.

    """
    candidate: object = command
    if not isinstance(candidate, ListTokens):
        raise StorageUnavailableError
    with open_read_connection(database_path) as connection:
        actor_subject, _actor_token = require_authenticated_actor(
            connection,
            candidate.actor,
            occurred_at=now,
        )
        target = _resolve_visible_target(
            connection,
            actor_subject=actor_subject,
            selector=candidate.subject,
        )
        after_created_at, after_id = _decode_cursor(candidate, target=target)
        rows = connection.execute(
            f"""
            SELECT {", ".join(TOKEN_FIELDS)}
            FROM tokens
            WHERE instance_id = ? AND subject_id = ?
              AND (
                  created_at > ?
                  OR (created_at = ? AND id > ?)
              )
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,  # noqa: S608 - selected columns are a fixed module constant.
            (
                str(candidate.actor.instance_id),
                str(target.id),
                after_created_at,
                after_created_at,
                after_id,
                candidate.limit + 1,
            ),
        ).fetchall()
        page_rows = rows[: candidate.limit]
        tokens = tuple(
            token_to_summary(token_from_row(row), now=now) for row in page_rows
        )
        next_cursor = None
        if len(rows) > candidate.limit:
            last = tokens[-1]
            next_cursor = _encode_cursor(
                command=candidate,
                target=target,
                created_at=last.created_at,
                token_id=last.id,
            )
        return TokenPage(tokens=tokens, next_cursor=next_cursor)


def revoke_token(
    database_path: Path,
    mutation: RevokeTokenMutation,
) -> TokenResult:
    """Monotonically revoke one self- or administrator-visible Token.

    Args:
        database_path: Absolute path to the validated SQLite store.
        mutation: Authenticated Token revocation mutation.

    Returns:
        Non-secret revoked Token metadata or an exact replay.

    """
    candidate: object = mutation
    if not isinstance(candidate, RevokeTokenMutation):
        raise StorageUnavailableError
    request = _ReplayRequest(
        actor_subject_id=candidate.actor.subject_id,
        operation=_REVOKE_OPERATION,
        idempotency_key=candidate.idempotency_key,
        fingerprint=_fingerprint(
            {
                "actor_subject_id": str(candidate.actor.subject_id),
                "instance_id": str(candidate.actor.instance_id),
                "token_id": str(candidate.token_id),
            }
        ),
    )
    with open_write_transaction(database_path) as connection:
        actor_subject, _actor_token = require_authenticated_actor(
            connection,
            candidate.actor,
            occurred_at=candidate.occurred_at,
        )
        replay = _read_replay(connection, request)
        if replay is not None:
            return TokenResult(token=replay)
        token = _load_token(
            connection,
            token_id=candidate.token_id,
            instance_id=candidate.actor.instance_id,
        )
        if token.subject_id != actor_subject.id and not actor_subject.is_instance_admin:
            raise TokenNotFoundError
        if token.revoked_at is None:
            if candidate.occurred_at < token.created_at or (
                token.activated_at is not None
                and candidate.occurred_at < token.activated_at
            ):
                raise StorageUnavailableError
            token = replace(
                token,
                revoked_at=candidate.occurred_at,
                revoked_by=actor_subject.id,
            )
            cursor = connection.execute(
                """
                UPDATE tokens
                SET revoked_at = ?, revoked_by = ?
                WHERE id = ? AND instance_id = ? AND revoked_at IS NULL
                """,
                (
                    serialize_timestamp(candidate.occurred_at),
                    str(actor_subject.id),
                    str(token.id),
                    str(token.instance_id),
                ),
            )
            if cursor.rowcount != 1:
                raise StorageUnavailableError
            append_audit_event(
                connection,
                AuditEventDraft(
                    actor=authenticated_audit_actor(candidate.actor),
                    request_id=candidate.request_id,
                    event_type=AuditEventType.TOKEN_REVOKED,
                    occurred_at=candidate.occurred_at,
                    payload={
                        "token_id": str(token.id),
                        "subject_id": str(token.subject_id),
                    },
                ),
            )
        summary = token_to_summary(token, now=candidate.occurred_at)
        if summary.status is not TokenStatus.REVOKED:
            raise StorageUnavailableError
        _record_replay(
            connection,
            request=request,
            summary=summary,
            occurred_at=candidate.occurred_at,
        )
        return TokenResult(token=summary)


def _resolve_visible_target(
    connection: sqlite3.Connection,
    *,
    actor_subject: Subject,
    selector: SubjectId | str | None,
) -> Subject:
    """Resolve one list target without exposing foreign Subjects to non-admins."""
    if selector is None or selector in (actor_subject.id, actor_subject.handle):
        return actor_subject
    if not actor_subject.is_instance_admin:
        raise PermissionDeniedError
    return resolve_subject(
        connection,
        instance_id=actor_subject.instance_id,
        selector=selector,
    )


def _require_available_token_identity(
    connection: sqlite3.Connection,
    *,
    token_id: TokenId,
    token_digest: str,
) -> None:
    """Reject generated Token-ID or digest collisions without disclosure."""
    row = connection.execute(
        "SELECT 1 FROM tokens WHERE id = ? OR token_hash = ? LIMIT 1",
        (str(token_id), token_digest),
    ).fetchone()
    if row is not None:
        raise StorageUnavailableError


def _insert_token(connection: sqlite3.Connection, token: Token) -> None:
    """Insert one canonical pending Token in a caller-owned transaction."""
    connection.execute(
        f"""
        INSERT INTO tokens ({", ".join(TOKEN_FIELDS)})
        VALUES ({", ".join("?" for _ in TOKEN_FIELDS)})
        """,  # noqa: S608 - columns and placeholders are fixed constants.
        (
            str(token.id),
            str(token.instance_id),
            str(token.subject_id),
            token.token_hash,
            str(token.created_by),
            serialize_timestamp(token.created_at),
            None,
            serialize_timestamp(token.expires_at),
            None,
            None,
        ),
    )


def _load_token(
    connection: sqlite3.Connection,
    *,
    token_id: TokenId,
    instance_id: InstanceId,
) -> Token:
    """Load one exact Instance-scoped public Token identity."""
    candidate_instance_id: object = instance_id
    if not isinstance(candidate_instance_id, InstanceId):
        raise StorageUnavailableError
    rows = connection.execute(
        f"""
        SELECT {", ".join(TOKEN_FIELDS)}
        FROM tokens
        WHERE id = ? AND instance_id = ?
        LIMIT 2
        """,  # noqa: S608 - selected columns are a fixed module constant.
        (str(token_id), str(instance_id)),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if not rows:
        raise TokenNotFoundError
    return token_from_row(rows[0])


def _fingerprint(value: Mapping[str, object]) -> str:
    """Return a deterministic SHA-256 fingerprint for semantic input."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_replay(
    connection: sqlite3.Connection,
    request: _ReplayRequest,
) -> TokenSummary | None:
    """Return one safe persisted Token summary or reject conflicting reuse."""
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
    if set(outcome) != _OUTCOME_KEYS or not isinstance(outcome["token"], dict):
        raise StorageUnavailableError
    return token_summary_from_mapping(cast("dict[str, object]", outcome["token"]))


def _record_replay(
    connection: sqlite3.Connection,
    *,
    request: _ReplayRequest,
    summary: TokenSummary,
    occurred_at: datetime,
) -> None:
    """Persist one safe Token mutation outcome in the owning transaction."""
    if request.idempotency_key is None:
        return
    if not isinstance(summary, TokenSummary):
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
            canonical_json({"token": token_summary_to_mapping(summary)}),
            serialize_timestamp(occurred_at),
        ),
    )


def _encode_cursor(
    *,
    command: ListTokens,
    target: Subject,
    created_at: datetime,
    token_id: TokenId,
) -> str:
    """Encode one canonical actor-, Instance-, and target-bound Token cursor."""
    payload = canonical_json(
        {
            "after": [serialize_timestamp(created_at), str(token_id)],
            "entity": "tokens",
            "instance_id": str(command.actor.instance_id),
            "subject_id": str(command.actor.subject_id),
            "target_subject_id": str(target.id),
            "v": _CURSOR_VERSION,
        }
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _decode_cursor(command: ListTokens, *, target: Subject) -> tuple[str, str]:
    """Decode one canonical Token cursor or return the initial position."""
    if command.cursor is None:
        return "", ""
    try:
        return _parse_cursor(command, target=target)
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        StorageUnavailableError,
    ) as error:
        raise InvalidInputError from error


def _parse_cursor(command: ListTokens, *, target: Subject) -> tuple[str, str]:
    """Parse and verify one non-null Token cursor."""
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
        or payload["entity"] != "tokens"
        or payload["instance_id"] != str(command.actor.instance_id)
        or payload["subject_id"] != str(command.actor.subject_id)
        or payload["target_subject_id"] != str(target.id)
        or not isinstance(after, list)
        or len(after) != _CURSOR_POSITION_LENGTH
        or not isinstance(after[0], str)
        or not isinstance(after[1], str)
        or not after[0]
    ):
        raise ValueError
    TokenId(after[1])
    if serialize_timestamp(parse_timestamp(after[0])) != after[0]:
        raise ValueError
    canonical = (
        base64.urlsafe_b64encode(canonical_json(payload).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    if cursor != f"{_CURSOR_PREFIX}{canonical}":
        raise ValueError
    return after[0], after[1]
