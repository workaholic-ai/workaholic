"""Digest authentication and fresh actor revalidation for SQLite transactions."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from workaholic.application import (
    AuthenticateToken,
    AuthenticationFailedError,
    CurrentIdentityResult,
    GetCurrentIdentity,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    Subject,
    Token,
    TokenStatus,
)
from workaholic.persistence.sqlite._records import (
    SUBJECT_FIELDS,
    TOKEN_FIELDS,
    subject_from_row,
    token_from_row,
    token_to_summary,
)
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path


def authenticate_token(
    database_path: Path,
    command: AuthenticateToken,
) -> AuthenticatedActor:
    """Authenticate one canonical digest at an explicit transaction time.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Parsed Token identity, digest, Instance, and authoritative time.

    Returns:
        Secret-free authenticated actor context.

    Raises:
        AuthenticationFailedError: If any credential or identity check fails.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate: object = command
    if not isinstance(candidate, AuthenticateToken):
        raise AuthenticationFailedError
    with open_read_connection(database_path) as connection:
        token, subject = _load_token_and_subject(
            connection,
            token_id=str(candidate.token_id),
        )
        if not hmac.compare_digest(token.token_hash, candidate.token_digest):
            raise AuthenticationFailedError
        _require_authenticating_state(
            token=token,
            subject=subject,
            expected_instance_id=candidate.expected_instance_id,
            occurred_at=candidate.occurred_at,
        )
        return AuthenticatedActor(
            instance_id=subject.instance_id,
            subject_id=subject.id,
            subject_kind=subject.kind,
            token_id=token.id,
        )


def require_authenticated_actor(
    connection: sqlite3.Connection,
    actor: AuthenticatedActor,
    *,
    occurred_at: datetime,
) -> tuple[Subject, Token]:
    """Revalidate one actor inside the caller's transaction.

    Args:
        connection: Active schema-validated read or write transaction.
        actor: Previously authenticated secret-free actor context.
        occurred_at: Authoritative time for the current operation.

    Returns:
        Current Subject and active Token projections.

    Raises:
        AuthenticationFailedError: If Token or Subject is no longer valid.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate_actor: object = actor
    if not isinstance(candidate_actor, AuthenticatedActor):
        raise AuthenticationFailedError
    token, subject = _load_token_and_subject(
        connection,
        token_id=str(candidate_actor.token_id),
    )
    if (
        token.instance_id != candidate_actor.instance_id
        or token.subject_id != candidate_actor.subject_id
        or subject.kind is not candidate_actor.subject_kind
    ):
        raise AuthenticationFailedError
    _require_authenticating_state(
        token=token,
        subject=subject,
        expected_instance_id=candidate_actor.instance_id,
        occurred_at=occurred_at,
    )
    return subject, token


def get_current_identity(
    database_path: Path,
    command: GetCurrentIdentity,
    *,
    now: datetime,
) -> CurrentIdentityResult:
    """Revalidate and return current non-secret identity metadata.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Previously authenticated actor query.
        now: Authoritative transaction time.

    Returns:
        Enabled Subject and active Token metadata.

    Raises:
        AuthenticationFailedError: If the actor is no longer authenticating.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate: object = command
    if not isinstance(candidate, GetCurrentIdentity):
        raise AuthenticationFailedError
    with open_read_connection(database_path) as connection:
        subject, token = require_authenticated_actor(
            connection,
            candidate.actor,
            occurred_at=now,
        )
        return CurrentIdentityResult(
            subject=subject,
            token=token_to_summary(token, now=now),
        )


def _load_token_and_subject(
    connection: sqlite3.Connection,
    *,
    token_id: str,
) -> tuple[Token, Subject]:
    """Load one Token and owning Subject through one indexed joined lookup."""
    token_columns = ", ".join(f"t.{field}" for field in TOKEN_FIELDS)
    subject_columns = ", ".join(f"s.{field}" for field in SUBJECT_FIELDS)
    rows = connection.execute(
        f"""
        SELECT {token_columns}, {subject_columns}
        FROM tokens AS t
        JOIN subjects AS s
          ON s.id = t.subject_id AND s.instance_id = t.instance_id
        WHERE t.id = ?
        LIMIT 2
        """,  # noqa: S608 - selected columns are fixed module constants.
        (token_id,),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if not rows:
        raise AuthenticationFailedError
    token = token_from_row(rows[0][: len(TOKEN_FIELDS)])
    subject = subject_from_row(rows[0][len(TOKEN_FIELDS) :])
    if token.instance_id != subject.instance_id or token.subject_id != subject.id:
        raise StorageUnavailableError
    return token, subject


def _require_authenticating_state(
    *,
    token: Token,
    subject: Subject,
    expected_instance_id: object,
    occurred_at: datetime,
) -> None:
    """Collapse every invalid credential state to one safe public failure."""
    if not isinstance(expected_instance_id, InstanceId):
        raise AuthenticationFailedError
    if (
        token.instance_id != expected_instance_id
        or subject.instance_id != expected_instance_id
        or not subject.enabled
        or token_to_summary(token, now=occurred_at).status is not TokenStatus.ACTIVE
    ):
        raise AuthenticationFailedError
