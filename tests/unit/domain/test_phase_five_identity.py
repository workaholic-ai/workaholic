"""Focused tests for Phase 5 identity, authorization, Token, and audit rules."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from workaholic.domain import (
    AuditEvent,
    AuditEventId,
    AuditEventType,
    AuthenticatedActor,
    DomainPermissionError,
    DomainValidationError,
    InstanceId,
    Permission,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    Token,
    TokenId,
    TokenStatus,
    TokenSummary,
    derive_token_status,
    project_role_implies,
    require_enabled_instance_administrator,
    require_enabled_project_owner,
    require_permission,
    require_subject_kind,
    validate_audit_event_payload,
    validate_json_value,
    validate_subject_handle,
    validate_uri_reference,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)
_INSTANCE_ID = InstanceId("ins_local")
_PROJECT_ID = ProjectId("prj_acme")
_SUBJECT_ID = SubjectId("sub_operator")


def _subject(
    *,
    subject_id: SubjectId = _SUBJECT_ID,
    handle: str = "local-operator",
    kind: SubjectKind = SubjectKind.HUMAN,
    enabled: bool = True,
    is_instance_admin: bool = True,
) -> Subject:
    """Build one complete valid Subject for policy tests.

    Returns:
        A validated Subject with stable Phase 5 metadata.

    """
    return Subject(
        id=subject_id,
        instance_id=_INSTANCE_ID,
        kind=kind,
        handle=handle,
        display_name="Local operator",
        enabled=enabled,
        is_instance_admin=is_instance_admin,
        version=1,
        created_by=_SUBJECT_ID,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _grant(
    *,
    subject_id: SubjectId = _SUBJECT_ID,
    role: ProjectRole = ProjectRole.OWNER,
) -> ProjectGrant:
    """Build one complete valid Project grant.

    Returns:
        A validated version-one grant.

    """
    return ProjectGrant(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        project_id=_PROJECT_ID,
        role=role,
        version=1,
        granted_by=_SUBJECT_ID,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _token(
    *,
    activated_at: datetime | None = _NOW,
    expires_at: datetime = _NOW + timedelta(days=1),
    revoked_at: datetime | None = None,
    revoked_by: SubjectId | None = None,
) -> Token:
    """Build one valid secret-free Token persistence entity.

    Returns:
        A validated Token record.

    """
    return Token(
        id=TokenId("tok_example"),
        instance_id=_INSTANCE_ID,
        subject_id=_SUBJECT_ID,
        token_hash="a" * 64,
        created_by=_SUBJECT_ID,
        created_at=_NOW,
        activated_at=activated_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
    )


@pytest.mark.parametrize(
    "handle",
    ["aa", "local-operator", "a0", "a" + "z" * 62],
)
def test_subject_handle_accepts_exact_ascii_boundaries(handle: str) -> None:
    """Handles accept only exact lowercase ASCII values at both length bounds."""
    assert validate_subject_handle(handle) == handle


@pytest.mark.parametrize(
    "handle",
    [
        "a",
        "a" * 64,
        "Local-operator",
        "local_operator",
        "local operator",
        "éclair",
        "a\n",
        "-agent",
        "1agent",
    ],
)
def test_subject_handle_rejects_aliasing_and_unsafe_values(handle: str) -> None:
    """Handles are rejected rather than trimmed, folded, or Unicode-normalized."""
    with pytest.raises(DomainValidationError, match="Subject handle"):
        validate_subject_handle(handle)


def test_subject_requires_complete_versioned_identity_metadata() -> None:
    """Subject validates Agent kind, version, printable name, and timestamp order."""
    agent = _subject(kind=SubjectKind.AGENT, handle="build-agent")

    assert agent.kind is SubjectKind.AGENT
    with pytest.raises(DomainValidationError, match="Subject version"):
        replace(agent, version=0)
    with pytest.raises(DomainValidationError, match="printable"):
        replace(agent, display_name="Build\nagent")
    with pytest.raises(DomainValidationError, match="must not precede"):
        replace(agent, updated_at=_NOW - timedelta(microseconds=1))


def test_project_grant_requires_version_attribution_and_ordered_timestamps() -> None:
    """Grant metadata cannot be omitted or represented by loose primitives."""
    grant = _grant(role=ProjectRole.AGENT)

    with pytest.raises(DomainValidationError, match="ProjectGrant version"):
        replace(grant, version=True)
    with pytest.raises(DomainValidationError, match="granted_by"):
        replace(grant, granted_by="sub_operator")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="must not precede"):
        replace(grant, updated_at=_NOW - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (ProjectRole.VIEWER, {Permission.VIEW_PROJECT}),
        (
            ProjectRole.AGENT,
            {Permission.VIEW_PROJECT, Permission.EXECUTE_AGENT},
        ),
        (
            ProjectRole.OPERATOR,
            {
                Permission.VIEW_PROJECT,
                Permission.EXECUTE_AGENT,
                Permission.OPERATE_PROJECT,
            },
        ),
        (
            ProjectRole.OWNER,
            {
                Permission.VIEW_PROJECT,
                Permission.EXECUTE_AGENT,
                Permission.OPERATE_PROJECT,
                Permission.MANAGE_PROJECT_GRANTS,
            },
        ),
    ],
)
def test_project_roles_have_exact_cumulative_permission_matrix(
    role: ProjectRole,
    allowed: set[Permission],
) -> None:
    """Each role implies all and only its documented cumulative permissions."""
    for permission in Permission:
        assert project_role_implies(role, permission) is (permission in allowed)


def test_permission_checks_keep_instance_admin_separate_from_project_roles() -> None:
    """Administrator authority and Project data authority never imply each other."""
    admin = _subject(is_instance_admin=True)
    viewer_grant = _grant(role=ProjectRole.VIEWER)

    require_permission(
        subject=admin,
        grant=None,
        permission=Permission.MANAGE_INSTANCE,
        target_instance_id=_INSTANCE_ID,
    )
    require_permission(
        subject=admin,
        grant=viewer_grant,
        permission=Permission.VIEW_PROJECT,
        target_instance_id=_INSTANCE_ID,
        target_project_id=_PROJECT_ID,
    )
    with pytest.raises(DomainPermissionError):
        require_permission(
            subject=admin,
            grant=None,
            permission=Permission.VIEW_PROJECT,
            target_instance_id=_INSTANCE_ID,
            target_project_id=_PROJECT_ID,
        )
    with pytest.raises(DomainPermissionError):
        require_permission(
            subject=_subject(is_instance_admin=False),
            grant=_grant(),
            permission=Permission.MANAGE_INSTANCE,
            target_instance_id=_INSTANCE_ID,
        )


def test_subject_kind_is_an_additional_execution_path_constraint() -> None:
    """A sufficient role does not let a Human impersonate an Agent path."""
    require_subject_kind(_subject(), SubjectKind.HUMAN)
    with pytest.raises(DomainPermissionError, match="operation path"):
        require_subject_kind(_subject(), SubjectKind.AGENT)


def test_token_status_uses_revoked_pending_expired_active_precedence() -> None:
    """Status is derived at explicit time with expiry as a half-open boundary."""
    assert derive_token_status(_token(), now=_NOW) is TokenStatus.ACTIVE
    assert (
        derive_token_status(_token(activated_at=None), now=_NOW) is TokenStatus.PENDING
    )
    assert (
        derive_token_status(_token(expires_at=_NOW + timedelta(seconds=1)), now=_NOW)
        is TokenStatus.ACTIVE
    )
    assert (
        derive_token_status(
            _token(expires_at=_NOW + timedelta(seconds=1)),
            now=_NOW + timedelta(seconds=1),
        )
        is TokenStatus.EXPIRED
    )
    assert (
        derive_token_status(
            _token(revoked_at=_NOW, revoked_by=_SUBJECT_ID),
            now=_NOW + timedelta(days=2),
        )
        is TokenStatus.REVOKED
    )


def test_token_and_summary_validate_secret_and_revocation_invariants() -> None:
    """Token records validate hashes while public summaries contain no hash field."""
    token = _token()
    summary = TokenSummary(
        id=token.id,
        subject_id=token.subject_id,
        status=TokenStatus.ACTIVE,
        created_by=token.created_by,
        created_at=token.created_at,
        activated_at=token.activated_at,
        expires_at=token.expires_at,
        revoked_at=None,
        revoked_by=None,
    )

    assert "token_hash" not in summary.__dataclass_fields__
    assert "a" * 64 not in repr(token)
    with pytest.raises(DomainValidationError, match="SHA-256"):
        replace(token, token_hash="not-a-digest")  # noqa: S106
    with pytest.raises(DomainValidationError, match="both revoked_at and revoked_by"):
        replace(token, revoked_at=_NOW)
    with pytest.raises(DomainValidationError, match="must be activated"):
        replace(summary, activated_at=None)


def test_authenticated_actor_contains_only_secret_free_typed_identity() -> None:
    """Authenticated actor context cannot carry a raw Token or role assertion."""
    actor = AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=_SUBJECT_ID,
        subject_kind=SubjectKind.AGENT,
        token_id=TokenId("tok_example"),
    )

    assert set(actor.__dataclass_fields__) == {
        "instance_id",
        "subject_id",
        "subject_kind",
        "token_id",
    }


def test_prospective_state_guards_require_enabled_admin_and_owner() -> None:
    """Last-administrator and last-Owner checks evaluate complete post-state."""
    owner = _subject()
    require_enabled_instance_administrator([owner], instance_id=_INSTANCE_ID)
    require_enabled_project_owner(
        [owner],
        [_grant()],
        instance_id=_INSTANCE_ID,
        project_id=_PROJECT_ID,
    )

    with pytest.raises(DomainValidationError, match="enabled administrator"):
        require_enabled_instance_administrator(
            [replace(owner, enabled=False)],
            instance_id=_INSTANCE_ID,
        )
    with pytest.raises(DomainValidationError, match="enabled Owner"):
        require_enabled_project_owner(
            [replace(owner, enabled=False)],
            [_grant()],
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
        )


def test_audit_event_validates_closed_payload_and_freezes_nested_values() -> None:
    """Audit payloads accept only the exact safe schema and become immutable."""
    event = AuditEvent(
        id=AuditEventId("aev_subject_update"),
        cursor=1,
        instance_id=_INSTANCE_ID,
        actor_subject_id=_SUBJECT_ID,
        actor_kind=SubjectKind.HUMAN,
        actor_token_id=TokenId("tok_example"),
        request_id=RequestId("req_update"),
        event_type=AuditEventType.SUBJECT_UPDATED,
        occurred_at=_NOW,
        payload={
            "subject_id": str(_SUBJECT_ID),
            "changed_fields": ("display_name",),
            "version": 2,
        },
    )

    assert event.payload["changed_fields"] == ("display_name",)
    with pytest.raises(TypeError):
        event.payload["version"] = 3  # type: ignore[index]
    with pytest.raises(DomainValidationError, match="exact closed fields"):
        validate_audit_event_payload(
            AuditEventType.TOKEN_REVOKED,
            {
                "token_id": "tok_example",
                "subject_id": "sub_operator",
                "token_hash": "a" * 64,
            },
        )
    with pytest.raises(DomainValidationError, match="changed_fields"):
        validate_audit_event_payload(
            AuditEventType.SUBJECT_UPDATED,
            {
                "subject_id": "sub_operator",
                "changed_fields": ["enabled"],
                "version": 2,
            },
        )


def test_every_audit_payload_schema_accepts_one_exact_example() -> None:
    """Every normative audit type has an executable closed payload contract."""
    payloads: Mapping[AuditEventType, Mapping[str, object]] = {
        AuditEventType.INSTANCE_BOOTSTRAPPED: {
            "instance_id": "ins_local",
            "subject_id": "sub_operator",
            "project_id": "prj_acme",
            "project_key": "ACME",
            "grant_role": "owner",
        },
        AuditEventType.PROJECT_CREATED: {
            "project_id": "prj_acme",
            "project_key": "ACME",
            "owner_subject_id": "sub_operator",
        },
        AuditEventType.SUBJECT_CREATED: {
            "subject_id": "sub_operator",
            "handle": "local-operator",
            "kind": "human",
            "version": 1,
        },
        AuditEventType.SUBJECT_UPDATED: {
            "subject_id": "sub_operator",
            "changed_fields": ["display_name"],
            "version": 2,
        },
        AuditEventType.SUBJECT_ENABLED: {"subject_id": "sub_operator", "version": 2},
        AuditEventType.SUBJECT_DISABLED: {"subject_id": "sub_operator", "version": 2},
        AuditEventType.INSTANCE_ADMIN_GRANTED: {
            "subject_id": "sub_operator",
            "version": 2,
        },
        AuditEventType.INSTANCE_ADMIN_REVOKED: {
            "subject_id": "sub_operator",
            "version": 2,
        },
        AuditEventType.PROJECT_GRANT_ASSIGNED: {
            "project_id": "prj_acme",
            "subject_id": "sub_operator",
            "role": "owner",
            "version": 1,
        },
        AuditEventType.PROJECT_GRANT_REVOKED: {
            "project_id": "prj_acme",
            "subject_id": "sub_operator",
            "previous_role": "owner",
            "previous_version": 1,
        },
        AuditEventType.TOKEN_ISSUED: {
            "token_id": "tok_example",
            "subject_id": "sub_operator",
            "expires_at": "2026-09-02T09:00:00Z",
        },
        AuditEventType.TOKEN_REVOKED: {
            "token_id": "tok_example",
            "subject_id": "sub_operator",
        },
    }

    assert set(payloads) == set(AuditEventType)
    for event_type, payload in payloads.items():
        validate_audit_event_payload(event_type, payload)


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        ("token_revoked", {}, "event_type"),
        (AuditEventType.TOKEN_REVOKED, [], "mapping"),
        (
            AuditEventType.TOKEN_REVOKED,
            {"token_id": 1, "subject_id": "sub_operator"},
            "token_id",
        ),
        (
            AuditEventType.TOKEN_REVOKED,
            {"token_id": "not-a-token", "subject_id": "sub_operator"},
            "token_id",
        ),
        (
            AuditEventType.PROJECT_CREATED,
            {
                "project_id": "prj_acme",
                "project_key": "lowercase",
                "owner_subject_id": "sub_operator",
            },
            "Project key",
        ),
        (
            AuditEventType.SUBJECT_CREATED,
            {
                "subject_id": "sub_operator",
                "handle": "Bad Handle",
                "kind": "human",
                "version": 1,
            },
            "Subject handle",
        ),
        (
            AuditEventType.SUBJECT_CREATED,
            {
                "subject_id": "sub_operator",
                "handle": "local-operator",
                "kind": 1,
                "version": 1,
            },
            "kind must be a string",
        ),
        (
            AuditEventType.SUBJECT_CREATED,
            {
                "subject_id": "sub_operator",
                "handle": "local-operator",
                "kind": "robot",
                "version": 1,
            },
            "kind is invalid",
        ),
        (
            AuditEventType.PROJECT_GRANT_ASSIGNED,
            {
                "project_id": "prj_acme",
                "subject_id": "sub_operator",
                "role": "administrator",
                "version": 1,
            },
            "role is invalid",
        ),
        (
            AuditEventType.SUBJECT_ENABLED,
            {"subject_id": "sub_operator", "version": 0},
            "version",
        ),
        (
            AuditEventType.TOKEN_ISSUED,
            {
                "token_id": "tok_example",
                "subject_id": "sub_operator",
                "expires_at": "tomorrow",
            },
            "expires_at",
        ),
    ],
)
def test_audit_payload_rejects_each_untrusted_field_boundary(
    event_type: object,
    payload: object,
    message: str,
) -> None:
    """Administrative audit parsing fails closed at every typed field boundary."""
    with pytest.raises(DomainValidationError, match=message):
        validate_audit_event_payload(event_type, payload)


@pytest.mark.parametrize(
    "value",
    [
        float("inf"),
        "x" * 65_537,
        {"": None},
        {"x" * 129: None},
        {str(index): None for index in range(257)},
        [None] * 1_025,
        object(),
    ],
)
def test_audit_json_rejects_nonfinite_and_bounded_values(value: object) -> None:
    """Audit JSON cannot smuggle unsupported or resource-exhausting values."""
    with pytest.raises(DomainValidationError):
        validate_json_value(value, label="payload")


def test_audit_json_rejects_excessive_recursive_depth() -> None:
    """Recursive audit JSON has a deterministic maximum nesting depth."""
    value: object = None
    for _index in range(18):
        value = [value]

    with pytest.raises(DomainValidationError, match="maximum JSON depth"):
        validate_json_value(value, label="payload")


@pytest.mark.parametrize("value", ["https://[invalid", "relative/path", "x:%ZZ"])
def test_uri_references_fail_closed_without_dereferencing(value: str) -> None:
    """Malformed external references are rejected as inert data."""
    with pytest.raises(DomainValidationError, match="URI"):
        validate_uri_reference(value, label="Artifact URI")


def test_authorization_rules_reject_malformed_and_cross_scope_inputs() -> None:
    """Permission checks reject malformed state before evaluating authority."""
    subject = _subject()
    grant = _grant()

    invalid_calls = [
        {"subject": object(), "permission": Permission.MANAGE_INSTANCE},
        {"subject": subject, "permission": "manage_instance"},
        {
            "subject": subject,
            "permission": Permission.MANAGE_INSTANCE,
            "target_instance_id": "ins_local",
        },
        {
            "subject": subject,
            "permission": Permission.VIEW_PROJECT,
            "target_project_id": None,
        },
    ]
    for overrides in invalid_calls:
        arguments: dict[str, object] = {
            "subject": subject,
            "grant": grant,
            "permission": Permission.MANAGE_INSTANCE,
            "target_instance_id": _INSTANCE_ID,
        }
        arguments.update(overrides)
        with pytest.raises(DomainValidationError):
            require_permission(**arguments)

    unsafe_subject = SimpleNamespace(
        id=_SUBJECT_ID,
        instance_id=_INSTANCE_ID,
        kind=SubjectKind.HUMAN,
        enabled=1,
        is_instance_admin=True,
    )
    with pytest.raises(DomainValidationError, match="booleans"):
        require_permission(
            subject=unsafe_subject,
            grant=grant,
            permission=Permission.MANAGE_INSTANCE,
            target_instance_id=_INSTANCE_ID,
        )
    with pytest.raises(DomainPermissionError):
        require_permission(
            subject=replace(subject, instance_id=InstanceId("ins_other")),
            grant=grant,
            permission=Permission.MANAGE_INSTANCE,
            target_instance_id=_INSTANCE_ID,
        )
    with pytest.raises(DomainPermissionError):
        require_permission(
            subject=subject,
            grant=replace(grant, project_id=ProjectId("prj_other")),
            permission=Permission.VIEW_PROJECT,
            target_instance_id=_INSTANCE_ID,
            target_project_id=_PROJECT_ID,
        )


def test_subject_kind_and_role_rules_reject_loose_runtime_values() -> None:
    """Role and Subject-kind policy inputs require their exact enum types."""
    with pytest.raises(DomainValidationError, match="Project role"):
        project_role_implies("owner", Permission.VIEW_PROJECT)
    with pytest.raises(DomainValidationError, match="Permission"):
        project_role_implies(ProjectRole.OWNER, "view_project")
    with pytest.raises(DomainValidationError, match="Subject value"):
        require_subject_kind(object(), SubjectKind.HUMAN)
    with pytest.raises(DomainValidationError, match="Required Subject kind"):
        require_subject_kind(_subject(), "human")
    with pytest.raises(DomainPermissionError):
        require_subject_kind(replace(_subject(), enabled=False), SubjectKind.HUMAN)


def test_token_status_rejects_malformed_projections_and_times() -> None:
    """Token status never trusts a loose projection or non-datetime clock value."""
    with pytest.raises(DomainValidationError, match="lifecycle value"):
        derive_token_status(object(), now=_NOW)
    with pytest.raises(DomainValidationError, match="must be a datetime"):
        derive_token_status(_token(), now="2026-09-01T09:00:00Z")


def test_identity_survival_guards_reject_malformed_collections() -> None:
    """Last-admin and last-Owner guards validate complete typed prospective sets."""
    with pytest.raises(DomainValidationError):
        require_enabled_instance_administrator(
            [_subject()],
            instance_id=cast("InstanceId", "ins_local"),
        )
    with pytest.raises(DomainValidationError):
        require_enabled_instance_administrator(
            cast("list[object]", None),
            instance_id=_INSTANCE_ID,
        )
    with pytest.raises(DomainValidationError):
        require_enabled_instance_administrator(
            [object()],
            instance_id=_INSTANCE_ID,
        )

    with pytest.raises(DomainValidationError, match="instance_id"):
        require_enabled_project_owner(
            [_subject()],
            [_grant()],
            instance_id=cast("InstanceId", "ins_local"),
            project_id=_PROJECT_ID,
        )
    with pytest.raises(DomainValidationError, match="project_id"):
        require_enabled_project_owner(
            [_subject()],
            [_grant()],
            instance_id=_INSTANCE_ID,
            project_id=cast("ProjectId", "prj_acme"),
        )
    with pytest.raises(DomainValidationError, match="iterable"):
        require_enabled_project_owner(
            cast("list[object]", None),
            [_grant()],
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
        )
    with pytest.raises(DomainValidationError, match="iterable"):
        require_enabled_project_owner(
            [_subject()],
            cast("list[object]", None),
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
        )
    with pytest.raises(DomainValidationError):
        require_enabled_project_owner(
            [object()],
            [_grant()],
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
        )
    with pytest.raises(DomainValidationError):
        require_enabled_project_owner(
            [_subject()],
            [object()],
            instance_id=_INSTANCE_ID,
            project_id=_PROJECT_ID,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": _NOW},
        {"activated_at": _NOW - timedelta(seconds=1)},
        {"activated_at": _NOW + timedelta(days=1)},
        {"revoked_at": _NOW - timedelta(seconds=1), "revoked_by": _SUBJECT_ID},
        {
            "activated_at": _NOW + timedelta(seconds=2),
            "revoked_at": _NOW + timedelta(seconds=1),
            "revoked_by": _SUBJECT_ID,
        },
        {"revoked_by": "sub_operator", "revoked_at": _NOW},
    ],
)
def test_token_entity_rejects_invalid_lifecycle_ordering(
    changes: dict[str, object],
) -> None:
    """Persisted Tokens enforce activation, expiry, and revocation ordering."""
    with pytest.raises(DomainValidationError):
        replace(_token(), **cast("Any", changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": _NOW},
        {"activated_at": _NOW - timedelta(seconds=1)},
        {"activated_at": _NOW + timedelta(days=1)},
        {"revoked_at": _NOW - timedelta(seconds=1), "revoked_by": _SUBJECT_ID},
        {
            "activated_at": _NOW + timedelta(seconds=2),
            "revoked_at": _NOW + timedelta(seconds=1),
            "revoked_by": _SUBJECT_ID,
        },
        {"status": TokenStatus.PENDING},
        {"status": TokenStatus.REVOKED},
    ],
)
def test_token_summary_rejects_inconsistent_public_lifecycle(
    changes: dict[str, object],
) -> None:
    """Public Token metadata cannot contradict its visible status."""
    token = _token()
    summary = TokenSummary(
        id=token.id,
        subject_id=token.subject_id,
        status=TokenStatus.ACTIVE,
        created_by=token.created_by,
        created_at=token.created_at,
        activated_at=token.activated_at,
        expires_at=token.expires_at,
        revoked_at=None,
        revoked_by=None,
    )
    with pytest.raises(DomainValidationError):
        replace(summary, **cast("Any", changes))


def test_domain_import_graph_remains_dependency_free() -> None:
    """Domain modules do not import infrastructure or hidden environment APIs."""
    domain_directory = Path(__file__).parents[3] / "src" / "workaholic" / "domain"
    forbidden_roots = {
        "keyring",
        "os",
        "pydantic",
        "sqlite3",
        "typer",
        "workaholic.application",
        "workaholic.auth",
        "workaholic.cli",
        "workaholic.persistence",
        "workaholic.session",
    }

    observed: set[str] = set()
    for source_path in domain_directory.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                observed.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                observed.add(node.module)

    assert not {
        imported
        for imported in observed
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for forbidden in forbidden_roots
        )
    }
