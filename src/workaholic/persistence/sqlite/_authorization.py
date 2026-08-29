"""Shared transaction-local identity lookup and authorization primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application import (
    LastInstanceAdminError,
    PermissionDeniedError,
    SubjectNotFoundError,
)
from workaholic.domain import (
    AuthenticatedActor,
    DomainPermissionError,
    DomainValidationError,
    InstanceId,
    Permission,
    Subject,
    SubjectId,
    require_permission,
)
from workaholic.persistence.sqlite._records import (
    SUBJECT_FIELDS,
    require_integer,
    subject_from_row,
)
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3


def require_instance_administrator(
    connection: sqlite3.Connection,
    actor: AuthenticatedActor,
) -> Subject:
    """Require the fixture actor to be a current enabled Instance administrator.

    Task 8 intentionally has no persisted Token lifecycle yet. This helper
    therefore revalidates all available non-secret actor fields against the
    current Subject row inside the caller's transaction. Task 9 extends this
    boundary with active-Token validation without changing Subject operations.

    Args:
        connection: Active schema-validated SQLite transaction.
        actor: Secret-free fixture actor selected by the application boundary.

    Returns:
        Current authoritative Subject projection.

    Raises:
        PermissionDeniedError: If actor identity, kind, state, or authority fails.
        StorageUnavailableError: If persisted identity state is malformed.

    """
    candidate_actor: object = actor
    if not isinstance(candidate_actor, AuthenticatedActor):
        raise PermissionDeniedError
    rows = connection.execute(
        f"""
        SELECT {", ".join(SUBJECT_FIELDS)}
        FROM subjects
        WHERE id = ? AND instance_id = ?
        LIMIT 2
        """,  # noqa: S608 - column names are a fixed module constant.
        (str(candidate_actor.subject_id), str(candidate_actor.instance_id)),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if not rows:
        raise PermissionDeniedError
    subject = subject_from_row(rows[0])
    if subject.kind is not candidate_actor.subject_kind:
        raise PermissionDeniedError
    try:
        require_permission(
            subject=subject,
            grant=None,
            permission=Permission.MANAGE_INSTANCE,
            target_instance_id=candidate_actor.instance_id,
        )
    except (DomainPermissionError, DomainValidationError) as error:
        # Domain policy intentionally carries more detail than a public
        # authorization failure. Collapse it at the persistence boundary.
        raise PermissionDeniedError from error
    return subject


def resolve_subject(
    connection: sqlite3.Connection,
    *,
    instance_id: object,
    selector: SubjectId | str,
) -> Subject:
    """Resolve one exact Subject ID or immutable handle in an Instance.

    Args:
        connection: Active schema-validated SQLite transaction.
        instance_id: Actor's exact Instance identity.
        selector: Typed Subject ID or validated immutable handle.

    Returns:
        The unique current Subject projection.

    Raises:
        SubjectNotFoundError: If the exact scoped selector has no match.
        StorageUnavailableError: If input or persisted state is malformed.

    """
    if not isinstance(instance_id, InstanceId) or not isinstance(
        selector,
        (SubjectId, str),
    ):
        raise StorageUnavailableError
    column = "id" if isinstance(selector, SubjectId) else "handle"
    rows = connection.execute(
        f"""
        SELECT {", ".join(SUBJECT_FIELDS)}
        FROM subjects
        WHERE instance_id = ? AND {column} = ?
        LIMIT 2
        """,  # noqa: S608 - selector column and field list are closed constants.
        (str(instance_id), str(selector)),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if not rows:
        raise SubjectNotFoundError
    return subject_from_row(rows[0])


def require_administrator_remains(
    connection: sqlite3.Connection,
    *,
    subject: Subject,
    enabled: bool,
    is_instance_admin: bool,
) -> None:
    """Guard a prospective Subject state from removing the final active admin.

    Args:
        connection: Active immediate write transaction.
        subject: Current target Subject.
        enabled: Prospective enabled state.
        is_instance_admin: Prospective administrator state.

    Raises:
        LastInstanceAdminError: If the prospective state has no enabled admin.
        StorageUnavailableError: If arguments or persisted counts are malformed.

    """
    candidate_subject: object = subject
    candidate_enabled: object = enabled
    candidate_admin: object = is_instance_admin
    if (
        not isinstance(candidate_subject, Subject)
        or type(candidate_enabled) is not bool
        or type(candidate_admin) is not bool
    ):
        raise StorageUnavailableError
    if not (subject.enabled and subject.is_instance_admin):
        return
    if enabled and is_instance_admin:
        return
    row = connection.execute(
        """
        SELECT count(*)
        FROM subjects
        WHERE instance_id = ? AND enabled = 1 AND is_instance_admin = 1
        """,
        (str(subject.instance_id),),
    ).fetchone()
    if row is None or len(row) != 1:
        raise StorageUnavailableError
    if require_integer(row[0]) == 1:
        raise LastInstanceAdminError
