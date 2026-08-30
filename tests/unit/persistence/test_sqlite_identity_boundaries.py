"""Runtime boundary tests for Phase 5 SQLite identity adapters."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.unit.persistence.test_sqlite_tokens import (
    _OWNER_TOKEN_ID,
    _digest_for,
    _repository,
)
from workaholic.application import (
    AuthenticateToken,
    AuthenticationFailedError,
    AuthorizeActor,
    GetCurrentIdentity,
    PermissionDeniedError,
)
from workaholic.domain import InstanceId, Permission, ProjectId, SubjectId, TokenId
from workaholic.persistence.sqlite import StorageUnavailableError
from workaholic.persistence.sqlite._audit_events import authenticated_audit_actor
from workaholic.persistence.sqlite._authorization import (
    require_administrator_remains,
    resolve_project,
    resolve_subject,
)
from workaholic.persistence.sqlite._tokens import _load_token

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path


def test_identity_repository_methods_reject_unknown_runtime_commands(
    tmp_path: Path,
) -> None:
    """Every semantic SQLite adapter validates the command category at runtime."""
    repository, _clock = _repository(tmp_path)
    unknown = cast("Any", object())
    storage_operations: tuple[Callable[[Any], object], ...] = (
        repository.create_subject,
        repository.list_subjects,
        repository.update_subject,
        repository.set_subject_enabled,
        repository.set_instance_admin,
        repository.issue_pending_token,
        repository.activate_token,
        repository.list_tokens,
        repository.revoke_token,
        repository.recover_local,
        repository.read_audit_events,
        repository.assign_project_grant,
        repository.list_project_grants,
        repository.revoke_project_grant,
    )
    for operation in storage_operations:
        with pytest.raises(StorageUnavailableError):
            operation(unknown)

    for operation in (
        repository.authenticate_token,
        repository.get_current_identity,
    ):
        with pytest.raises(AuthenticationFailedError):
            operation(unknown)
    with pytest.raises(PermissionDeniedError):
        repository.authorize_actor(unknown)


def test_authorization_rejects_missing_scope_and_invalid_resolver_inputs(
    tmp_path: Path,
) -> None:
    """Authorization helpers fail closed on absent scope and untyped selectors."""
    repository, _clock = _repository(tmp_path)
    command = AuthorizeActor.model_construct(
        actor=cast("AuthenticateToken", object()),
        permission=Permission.VIEW_PROJECT,
        project_id=None,
        occurred_at=cast("datetime", object()),
        required_kind=None,
    )
    with pytest.raises(PermissionDeniedError):
        repository.authorize_actor(command)

    connection = sqlite3.connect(repository.database_path)
    try:
        with pytest.raises(StorageUnavailableError):
            resolve_subject(
                connection,
                instance_id="ins_local",
                selector=SubjectId("sub_owner"),
            )
        with pytest.raises(StorageUnavailableError):
            resolve_subject(
                connection,
                instance_id=InstanceId("ins_local"),
                selector=cast("SubjectId", object()),
            )
        with pytest.raises(StorageUnavailableError):
            resolve_project(
                connection,
                instance_id=cast("Any", "ins_local"),
                selector=ProjectId("prj_local"),
            )
        with pytest.raises(StorageUnavailableError):
            _load_token(
                connection,
                token_id=TokenId("tok_owner"),
                instance_id=cast("Any", "ins_local"),
            )
    finally:
        connection.close()


def test_identity_security_helpers_reject_malformed_projections(tmp_path: Path) -> None:
    """Audit attribution and administrator guards require exact domain values."""
    repository, _clock = _repository(tmp_path)
    with pytest.raises(StorageUnavailableError):
        authenticated_audit_actor(object())

    connection = sqlite3.connect(repository.database_path)
    try:
        with pytest.raises(StorageUnavailableError):
            require_administrator_remains(
                connection,
                subject=cast("Any", object()),
                enabled=True,
                is_instance_admin=True,
            )
    finally:
        connection.close()


def test_authentication_rejects_invalid_actor_and_instance_types(
    tmp_path: Path,
) -> None:
    """Authentication collapses malformed actor and expected Instance inputs."""
    repository, clock = _repository(tmp_path)
    with pytest.raises(AuthenticationFailedError):
        repository.get_current_identity(
            GetCurrentIdentity.model_construct(actor=object())
        )

    command = AuthenticateToken.model_construct(
        token_id=TokenId("tok_owner"),
        token_digest=_digest_for(_OWNER_TOKEN_ID, fill=1),
        expected_instance_id="ins_local",
        occurred_at=clock.now(),
    )
    with pytest.raises(AuthenticationFailedError):
        repository.authenticate_token(command)
