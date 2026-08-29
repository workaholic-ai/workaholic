"""Shared transaction-local identity lookup and authorization primitives."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from workaholic.application import (
    AuthorizeActor,
    LastInstanceAdminError,
    LastProjectOwnerError,
    PermissionDeniedError,
    ProjectNotFoundError,
    SubjectNotFoundError,
)
from workaholic.domain import (
    AuthenticatedActor,
    DomainPermissionError,
    DomainValidationError,
    InstanceId,
    Permission,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    Subject,
    SubjectId,
    SubjectKind,
    require_enabled_project_owner,
    require_permission,
    require_subject_kind,
)
from workaholic.persistence.sqlite._authentication import require_authenticated_actor
from workaholic.persistence.sqlite._records import (
    PROJECT_FIELDS,
    PROJECT_GRANT_FIELDS,
    SUBJECT_FIELDS,
    project_from_row,
    project_grant_from_row,
    require_integer,
    subject_from_row,
)
from workaholic.persistence.sqlite.connection import open_read_connection
from workaholic.persistence.sqlite.errors import StorageUnavailableError

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuthorizedProject:
    """Fresh Subject, Project, and grant authorized in one transaction."""

    subject: Subject
    project: Project
    grant: ProjectGrant


@dataclass(frozen=True, slots=True)
class ProjectPermissionRequest:
    """Explicit transaction-local cumulative authorization request."""

    actor: AuthenticatedActor
    project: ProjectId | str
    permission: Permission
    occurred_at: datetime
    required_kind: SubjectKind | None = None


def authorize_actor(database_path: Path, command: AuthorizeActor) -> Subject:
    """Resolve one fresh authorization projection in a read transaction.

    Args:
        database_path: Absolute path to the validated SQLite store.
        command: Actor, permission, scope, kind, and authoritative time.

    Returns:
        Current authorized Subject projection.

    Raises:
        PermissionDeniedError: If current authorization does not imply permission.
        StorageUnavailableError: If persisted state is malformed.

    """
    candidate: object = command
    if not isinstance(candidate, AuthorizeActor):
        raise PermissionDeniedError
    with open_read_connection(database_path) as connection:
        if candidate.permission is Permission.MANAGE_INSTANCE:
            subject = require_instance_administrator(
                connection,
                candidate.actor,
                occurred_at=candidate.occurred_at,
            )
            _require_kind(subject, candidate.required_kind)
            return subject
        if candidate.project_id is None:
            raise PermissionDeniedError
        authorized = require_project_permission(
            connection,
            ProjectPermissionRequest(
                actor=candidate.actor,
                project=candidate.project_id,
                permission=candidate.permission,
                occurred_at=candidate.occurred_at,
                required_kind=candidate.required_kind,
            ),
        )
        return authorized.subject


def require_project_permission(
    connection: sqlite3.Connection,
    request: ProjectPermissionRequest,
) -> AuthorizedProject:
    """Require one current cumulative Project permission in a transaction.

    Args:
        connection: Active schema-validated SQLite transaction.
        request: Actor, Project, permission, time, and optional kind constraint.

    Returns:
        Fresh current Subject, Project, and ProjectGrant projections.

    Raises:
        PermissionDeniedError: If no current grant implies the permission.
        ProjectNotFoundError: If the exact scoped Project does not exist.
        StorageUnavailableError: If persisted state is malformed.

    """
    subject, _token = require_authenticated_actor(
        connection,
        request.actor,
        occurred_at=request.occurred_at,
    )
    selected_project = resolve_project(
        connection,
        instance_id=request.actor.instance_id,
        selector=request.project,
    )
    grant = load_project_grant(
        connection,
        instance_id=request.actor.instance_id,
        project_id=selected_project.id,
        subject_id=subject.id,
    )
    if grant is None:
        raise PermissionDeniedError
    try:
        require_permission(
            subject=subject,
            grant=grant,
            permission=request.permission,
            target_instance_id=request.actor.instance_id,
            target_project_id=selected_project.id,
        )
        _require_kind(subject, request.required_kind)
    except (DomainPermissionError, DomainValidationError) as error:
        raise PermissionDeniedError from error
    return AuthorizedProject(
        subject=subject,
        project=selected_project,
        grant=grant,
    )


def require_grant_administrator(
    connection: sqlite3.Connection,
    *,
    actor: AuthenticatedActor,
    project: ProjectId | str,
    occurred_at: datetime,
) -> tuple[Subject, Project]:
    """Require Instance administration or Owner on one exact Project.

    Args:
        connection: Active schema-validated SQLite transaction.
        actor: Previously authenticated actor context.
        project: Exact Project ID or immutable key.
        occurred_at: Authoritative Token validation time.

    Returns:
        Fresh actor Subject and target Project.

    Raises:
        PermissionDeniedError: If neither administration route is current.
        ProjectNotFoundError: If the scoped Project is absent.

    """
    subject, _token = require_authenticated_actor(
        connection,
        actor,
        occurred_at=occurred_at,
    )
    selected_project = resolve_project(
        connection,
        instance_id=actor.instance_id,
        selector=project,
    )
    if subject.is_instance_admin:
        return subject, selected_project
    grant = load_project_grant(
        connection,
        instance_id=actor.instance_id,
        project_id=selected_project.id,
        subject_id=subject.id,
    )
    try:
        require_permission(
            subject=subject,
            grant=grant,
            permission=Permission.MANAGE_PROJECT_GRANTS,
            target_instance_id=actor.instance_id,
            target_project_id=selected_project.id,
        )
    except (DomainPermissionError, DomainValidationError) as error:
        raise PermissionDeniedError from error
    return subject, selected_project


def require_instance_administrator(
    connection: sqlite3.Connection,
    actor: AuthenticatedActor,
    *,
    occurred_at: datetime,
) -> Subject:
    """Require a freshly authenticated enabled Instance administrator.

    Args:
        connection: Active schema-validated SQLite transaction.
        actor: Previously authenticated secret-free actor context.
        occurred_at: Authoritative time for fresh Token validation.

    Returns:
        Current authoritative Subject projection.

    Raises:
        PermissionDeniedError: If actor identity, kind, state, or authority fails.
        StorageUnavailableError: If persisted identity state is malformed.

    """
    subject, _token = require_authenticated_actor(
        connection,
        actor,
        occurred_at=occurred_at,
    )
    try:
        require_permission(
            subject=subject,
            grant=None,
            permission=Permission.MANAGE_INSTANCE,
            target_instance_id=actor.instance_id,
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


def resolve_project(
    connection: sqlite3.Connection,
    *,
    instance_id: InstanceId,
    selector: ProjectId | str,
) -> Project:
    """Resolve one exact Project ID or immutable key in an Instance.

    Args:
        connection: Active schema-validated SQLite transaction.
        instance_id: Exact actor Instance scope.
        selector: Typed Project ID or validated immutable key.

    Returns:
        Unique current Project projection.

    Raises:
        ProjectNotFoundError: If the scoped selector has no match.
        StorageUnavailableError: If input or persisted state is malformed.

    """
    candidate_instance: object = instance_id
    candidate_selector: object = selector
    if not isinstance(candidate_instance, InstanceId) or not isinstance(
        candidate_selector,
        (ProjectId, str),
    ):
        raise StorageUnavailableError
    column = "id" if isinstance(candidate_selector, ProjectId) else "key"
    rows = connection.execute(
        f"""
        SELECT {", ".join(PROJECT_FIELDS)}
        FROM projects
        WHERE instance_id = ? AND {column} = ?
        LIMIT 2
        """,  # noqa: S608 - selector column and fields are closed constants.
        (str(candidate_instance), str(candidate_selector)),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    if not rows:
        raise ProjectNotFoundError
    return project_from_row(rows[0])


def load_project_grant(
    connection: sqlite3.Connection,
    *,
    instance_id: InstanceId,
    project_id: ProjectId,
    subject_id: SubjectId,
) -> ProjectGrant | None:
    """Load one exact current ProjectGrant or return absence.

    Args:
        connection: Active schema-validated SQLite transaction.
        instance_id: Exact Instance scope.
        project_id: Exact governed Project.
        subject_id: Exact granted Subject.

    Returns:
        Current grant or ``None`` when absent.

    Raises:
        StorageUnavailableError: If duplicate or malformed state exists.

    """
    rows = connection.execute(
        f"""
        SELECT {", ".join(PROJECT_GRANT_FIELDS)}
        FROM project_grants
        WHERE instance_id = ? AND project_id = ? AND subject_id = ?
        LIMIT 2
        """,  # noqa: S608 - selected columns are a fixed module constant.
        (str(instance_id), str(project_id), str(subject_id)),
    ).fetchall()
    if len(rows) > 1:
        raise StorageUnavailableError
    return None if not rows else project_grant_from_row(rows[0])


def require_grant_change_preserves_owner(
    connection: sqlite3.Connection,
    *,
    current: ProjectGrant | None,
    project: Project,
    subject: Subject,
    prospective_role: ProjectRole | None,
) -> None:
    """Require a prospective grant assignment or removal to retain an Owner.

    Args:
        connection: Active immediate write transaction.
        current: Current target grant, or null for creation.
        project: Governed Project.
        subject: Target Subject.
        prospective_role: Replacement role, or null for revocation.

    Raises:
        LastProjectOwnerError: If the prospective graph has no enabled Owner.
        StorageUnavailableError: If persisted graph state is malformed.

    """
    subjects = _load_instance_subjects(connection, instance_id=project.instance_id)
    grants = list(_load_project_grants(connection, project=project))
    if current is not None:
        grants = [
            grant
            for grant in grants
            if not (
                grant.subject_id == current.subject_id
                and grant.project_id == current.project_id
            )
        ]
    if prospective_role is not None:
        version = 1 if current is None else current.version + 1
        grants.append(
            ProjectGrant(
                instance_id=project.instance_id,
                subject_id=subject.id,
                project_id=project.id,
                role=prospective_role,
                version=version,
                granted_by=subject.id,
                created_at=project.created_at,
                updated_at=project.created_at,
            )
        )
    _require_owner_policy(
        subjects=subjects,
        grants=tuple(grants),
        project=project,
    )


def require_subject_change_preserves_owners(
    connection: sqlite3.Connection,
    *,
    subject: Subject,
    enabled: bool,
) -> None:
    """Require Subject disablement to retain an enabled Owner per Project.

    Args:
        connection: Active immediate write transaction.
        subject: Current target Subject.
        enabled: Prospective enabled state.

    Raises:
        LastProjectOwnerError: If any affected Project would lose its last Owner.
        StorageUnavailableError: If persisted graph state is malformed.

    """
    if enabled or not subject.enabled:
        return
    project_rows = connection.execute(
        """
        SELECT p.id, p.instance_id, p.key, p.name, p.created_at
        FROM projects AS p
        JOIN project_grants AS g
          ON g.project_id = p.id AND g.instance_id = p.instance_id
        WHERE g.instance_id = ? AND g.subject_id = ? AND g.role = 'owner'
        ORDER BY p.id
        """,
        (str(subject.instance_id), str(subject.id)),
    ).fetchall()
    if not project_rows:
        return
    subjects = tuple(
        replace(item, enabled=False) if item.id == subject.id else item
        for item in _load_instance_subjects(
            connection,
            instance_id=subject.instance_id,
        )
    )
    for row in project_rows:
        project = project_from_row(row)
        _require_owner_policy(
            subjects=subjects,
            grants=_load_project_grants(connection, project=project),
            project=project,
        )


def _load_instance_subjects(
    connection: sqlite3.Connection,
    *,
    instance_id: InstanceId,
) -> tuple[Subject, ...]:
    """Load the complete validated Subject set for one Instance guard."""
    rows = connection.execute(
        f"""
        SELECT {", ".join(SUBJECT_FIELDS)}
        FROM subjects
        WHERE instance_id = ?
        ORDER BY id
        """,  # noqa: S608 - selected columns are a fixed module constant.
        (str(instance_id),),
    ).fetchall()
    return tuple(subject_from_row(row) for row in rows)


def _load_project_grants(
    connection: sqlite3.Connection,
    *,
    project: Project,
) -> tuple[ProjectGrant, ...]:
    """Load the complete validated grant set for one Project guard."""
    rows = connection.execute(
        f"""
        SELECT {", ".join(PROJECT_GRANT_FIELDS)}
        FROM project_grants
        WHERE instance_id = ? AND project_id = ?
        ORDER BY subject_id
        """,  # noqa: S608 - selected columns are a fixed module constant.
        (str(project.instance_id), str(project.id)),
    ).fetchall()
    return tuple(project_grant_from_row(row) for row in rows)


def _require_owner_policy(
    *,
    subjects: tuple[Subject, ...],
    grants: tuple[ProjectGrant, ...],
    project: Project,
) -> None:
    """Map the pure enabled-Owner invariant to its public application error."""
    try:
        require_enabled_project_owner(
            subjects,
            grants,
            instance_id=project.instance_id,
            project_id=project.id,
        )
    except DomainValidationError as error:
        raise LastProjectOwnerError from error


def _require_kind(subject: Subject, required_kind: SubjectKind | None) -> None:
    """Apply one optional pure Subject-kind constraint safely."""
    if required_kind is None:
        return
    try:
        require_subject_kind(subject, required_kind)
    except (DomainPermissionError, DomainValidationError) as error:
        raise PermissionDeniedError from error


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
