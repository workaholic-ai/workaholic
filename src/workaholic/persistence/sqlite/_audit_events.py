"""Append-only administrative audit persistence and bounded queries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from workaholic.application import AuditEventPage, AuditEventResult, ReadAuditEvents
from workaholic.domain import (
    AuditEvent,
    AuditEventId,
    AuditEventType,
    AuthenticatedActor,
    InstanceId,
    JsonValue,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
)
from workaholic.persistence.sqlite._authorization import (
    require_instance_administrator,
)
from workaholic.persistence.sqlite._event_records import (
    AUDIT_EVENT_FIELDS,
    audit_event_from_row,
)
from workaholic.persistence.sqlite._records import (
    canonical_json,
    require_integer,
    serialize_timestamp,
)
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

_MAX_SQLITE_INTEGER: Final = 9_223_372_036_854_775_807


@dataclass(frozen=True, slots=True)
class AuditActor:
    """Immutable event attribution snapshot at a transaction boundary."""

    instance_id: InstanceId
    subject_id: SubjectId
    kind: SubjectKind
    token_id: TokenId | None


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    """Complete cursor-free administrative event proposed by one mutation."""

    actor: AuditActor
    request_id: RequestId
    event_type: AuditEventType
    occurred_at: datetime
    payload: Mapping[str, JsonValue]


def append_audit_event(
    connection: sqlite3.Connection,
    draft: AuditEventDraft,
) -> AuditEvent:
    """Append one validated event inside a caller-owned write transaction.

    Args:
        connection: Active schema-validated SQLite write transaction.
        draft: Complete non-secret attribution and payload without a cursor.

    Returns:
        Persisted event with its allocated monotonic cursor.

    Raises:
        StorageUnavailableError: If input, payload, or cursor is malformed.

    """
    candidate: object = draft
    if not isinstance(candidate, AuditEventDraft):
        raise StorageUnavailableError
    event_id = _derive_event_id(candidate)
    try:
        proposed = AuditEvent(
            id=event_id,
            cursor=1,
            instance_id=candidate.actor.instance_id,
            actor_subject_id=candidate.actor.subject_id,
            actor_kind=candidate.actor.kind,
            actor_token_id=candidate.actor.token_id,
            request_id=candidate.request_id,
            event_type=candidate.event_type,
            occurred_at=candidate.occurred_at,
            payload=candidate.payload,
        )
    except (TypeError, ValueError) as error:
        raise StorageUnavailableError from error
    inserted = connection.execute(
        """
        INSERT INTO audit_events (
            id, instance_id, actor_subject_id, actor_kind, actor_token_id,
            request_id, event_type, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(proposed.id),
            str(proposed.instance_id),
            str(proposed.actor_subject_id),
            proposed.actor_kind.value,
            (None if proposed.actor_token_id is None else str(proposed.actor_token_id)),
            str(proposed.request_id),
            proposed.event_type.value,
            serialize_timestamp(proposed.occurred_at),
            canonical_json(proposed.payload),
        ),
    )
    return AuditEvent(
        id=proposed.id,
        cursor=require_integer(inserted.lastrowid),
        instance_id=proposed.instance_id,
        actor_subject_id=proposed.actor_subject_id,
        actor_kind=proposed.actor_kind,
        actor_token_id=proposed.actor_token_id,
        request_id=proposed.request_id,
        event_type=proposed.event_type,
        occurred_at=proposed.occurred_at,
        payload=proposed.payload,
    )


def read_audit_events(
    database_path: Path,
    command: ReadAuditEvents,
    *,
    now: datetime,
) -> AuditEventPage:
    """Read one bounded ascending administrator-authorized audit page.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Authenticated nonnegative cursor query.
        now: Authoritative transaction time for actor revalidation.

    Returns:
        Strictly ascending events and greatest observed cursor.

    Raises:
        PermissionDeniedError: If the actor is not a current administrator.
        StorageUnavailableError: If records violate their contracts.

    """
    candidate: object = command
    if not isinstance(candidate, ReadAuditEvents):
        raise StorageUnavailableError
    with open_read_connection(database_path) as connection:
        require_instance_administrator(
            connection,
            candidate.actor,
            occurred_at=now,
        )
        rows = (
            ()
            if candidate.after > _MAX_SQLITE_INTEGER
            else connection.execute(
                f"""
                SELECT {", ".join(AUDIT_EVENT_FIELDS)}
                FROM audit_events
                WHERE instance_id = ? AND cursor > ?
                ORDER BY cursor ASC
                LIMIT ?
                """,  # noqa: S608 - fields are a closed module constant.
                (
                    str(candidate.actor.instance_id),
                    candidate.after,
                    candidate.limit,
                ),
            ).fetchall()
        )
        events = tuple(_event_result(audit_event_from_row(row)) for row in rows)
        next_cursor = events[-1].cursor if events else candidate.after
        return AuditEventPage(events=events, next_cursor=next_cursor)


def authenticated_audit_actor(actor: object) -> AuditActor:
    """Build one exact audit snapshot from an authenticated actor context.

    Args:
        actor: Candidate ``AuthenticatedActor``.

    Returns:
        Immutable audit attribution with the authenticating Token.

    Raises:
        StorageUnavailableError: If the runtime value is not an actor.

    """
    if not isinstance(actor, AuthenticatedActor):
        raise StorageUnavailableError
    return AuditActor(
        instance_id=actor.instance_id,
        subject_id=actor.subject_id,
        kind=actor.subject_kind,
        token_id=actor.token_id,
    )


def _derive_event_id(draft: AuditEventDraft) -> AuditEventId:
    """Derive a collision-resistant stable ID from complete non-secret semantics."""
    seed = canonical_json(
        {
            "actor_kind": draft.actor.kind.value,
            "actor_subject_id": str(draft.actor.subject_id),
            "actor_token_id": (
                None if draft.actor.token_id is None else str(draft.actor.token_id)
            ),
            "event_type": draft.event_type.value,
            "instance_id": str(draft.actor.instance_id),
            "payload": dict(draft.payload),
            "request_id": str(draft.request_id),
        }
    ).encode("utf-8")
    return AuditEventId(f"aev_{hashlib.sha256(seed).hexdigest()}")


def _event_result(event: AuditEvent) -> AuditEventResult:
    """Convert one strict domain event into its flat application result."""
    return AuditEventResult(
        id=event.id,
        cursor=event.cursor,
        instance_id=event.instance_id,
        actor_subject_id=event.actor_subject_id,
        actor_kind=event.actor_kind,
        actor_token_id=event.actor_token_id,
        request_id=event.request_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )
