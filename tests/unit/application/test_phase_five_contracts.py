"""Application-boundary contracts for Phase 5 identity and authorization."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from pydantic import ValidationError

from workaholic.application import (
    ActivateTokenMutation,
    AssignProjectGrantMutation,
    AuditEventPage,
    AuditEventResult,
    AuthenticateToken,
    AuthorizationRepository,
    AuthorizeActor,
    CreateSubjectMutation,
    CurrentIdentityResult,
    GetCurrentIdentity,
    GrantRepository,
    IdentityIdentifierFactory,
    IdentityRepository,
    IssueTokenMutation,
    ListProjectGrants,
    ListSubjects,
    ListTokens,
    ProjectGrantPage,
    ProjectGrantResult,
    ReadAuditEvents,
    RecoverLocalMutation,
    RevokeProjectGrantMutation,
    RevokeTokenMutation,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    SubjectPage,
    SubjectRepository,
    SubjectResult,
    TokenPage,
    TokenRepository,
    TokenResult,
    UpdateSubjectMutation,
)
from workaholic.domain import (
    AuditEventId,
    AuditEventType,
    AuthenticatedActor,
    InstanceId,
    Permission,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
    TokenStatus,
    TokenSummary,
)

_NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)
_INSTANCE_ID = InstanceId("ins_local")
_SUBJECT_ID = SubjectId("sub_operator")
_PROJECT_ID = ProjectId("prj_acme")
_TOKEN_ID = TokenId("tok_primary")
_DIGEST = "a" * 64


class _MutationMetadata(TypedDict):
    """Typed shared fields accepted by every identity mutation."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str


def _actor() -> AuthenticatedActor:
    """Build one complete secret-free authenticated actor.

    Returns:
        A valid Human actor context.

    """
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=_SUBJECT_ID,
        subject_kind=SubjectKind.HUMAN,
        token_id=_TOKEN_ID,
    )


def _mutation_metadata() -> _MutationMetadata:
    """Build shared authenticated mutation metadata.

    Returns:
        Valid actor, request, time, and idempotency fields.

    """
    return {
        "actor": _actor(),
        "request_id": RequestId("req_identity"),
        "occurred_at": _NOW,
        "idempotency_key": "identity-operation-1",
    }


def _subject(
    *,
    subject_id: SubjectId = _SUBJECT_ID,
    handle: str = "local-operator",
) -> Subject:
    """Build one complete Subject result fixture.

    Returns:
        A valid enabled Human administrator.

    """
    return Subject(
        id=subject_id,
        instance_id=_INSTANCE_ID,
        kind=SubjectKind.HUMAN,
        handle=handle,
        display_name=handle,
        enabled=True,
        is_instance_admin=True,
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
    """Build one complete ProjectGrant result fixture.

    Returns:
        A valid current Project grant.

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
    token_id: TokenId = _TOKEN_ID,
    subject_id: SubjectId = _SUBJECT_ID,
    created_at: datetime = _NOW,
    status: TokenStatus = TokenStatus.ACTIVE,
) -> TokenSummary:
    """Build one non-secret Token metadata result fixture.

    Returns:
        A valid active Token summary.

    """
    return TokenSummary(
        id=token_id,
        subject_id=subject_id,
        status=status,
        created_by=_SUBJECT_ID,
        created_at=created_at,
        activated_at=created_at,
        expires_at=created_at + timedelta(days=1),
        revoked_at=None,
        revoked_by=None,
    )


def test_authentication_command_contains_only_typed_digest_input() -> None:
    """Authentication rejects raw/free-form identities and redacts its digest."""
    command = AuthenticateToken(
        token_id=_TOKEN_ID,
        token_digest=_DIGEST,
        expected_instance_id=_INSTANCE_ID,
        occurred_at=_NOW,
    )

    assert _DIGEST not in repr(command)
    assert "token_digest" not in command.model_dump()
    with pytest.raises(ValidationError):
        AuthenticateToken(
            token_id="tok_primary",  # type: ignore[arg-type]  # noqa: S106
            token_digest=_DIGEST,
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW,
        )
    with pytest.raises(ValidationError):
        AuthenticateToken(
            token_id=_TOKEN_ID,
            token_digest="raw-token-secret",  # noqa: S106
            expected_instance_id=_INSTANCE_ID,
            occurred_at=_NOW,
        )


def test_actor_bound_queries_reject_actor_overrides_and_unknown_fields() -> None:
    """Identity queries accept one actor object and no caller Subject override."""
    actor = _actor()

    assert GetCurrentIdentity(actor=actor).actor is actor
    assert ListSubjects(actor=actor).limit == 100
    assert ListTokens(actor=actor, subject=None).subject is None
    assert ListProjectGrants(actor=actor, project="ACME").project == "ACME"
    assert ReadAuditEvents(actor=actor).after == 0
    with pytest.raises(ValidationError):
        ListSubjects(
            actor=actor,
            subject_id=SubjectId("sub_override"),  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ListSubjects(actor=actor, limit=501)
    with pytest.raises(ValidationError):
        ReadAuditEvents(actor=actor, after=-1)


@pytest.mark.parametrize(
    "command",
    [
        ListProjectGrants(actor=_actor(), project=ProjectId("prj_acme")),
        ListProjectGrants(actor=_actor(), project="ACME"),
        ListTokens(actor=_actor(), subject=SubjectId("sub_operator")),
        ListTokens(actor=_actor(), subject="local-operator"),
    ],
)
def test_identity_selectors_accept_only_exact_ids_or_canonical_names(
    command: object,
) -> None:
    """Typed IDs and canonical immutable names remain unambiguous."""
    assert command is not None


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (ListProjectGrants, "project", "prj bad"),
        (ListProjectGrants, "project", "acme"),
        (ListTokens, "subject", "sub bad"),
        (ListTokens, "subject", "Display Name"),
    ],
)
def test_identity_selectors_reject_malformed_or_display_name_values(
    model: type[ListProjectGrants] | type[ListTokens],
    field: str,
    value: object,
) -> None:
    """Malformed typed prefixes and display names cannot become lookup keys."""
    with pytest.raises(ValidationError):
        model.model_validate({"actor": _actor(), field: value})


def test_authorization_command_has_exact_instance_and_project_scopes() -> None:
    """Instance administration remains separate from Project authorization."""
    instance_command = AuthorizeActor(
        actor=_actor(),
        permission=Permission.MANAGE_INSTANCE,
        occurred_at=_NOW,
    )
    project_command = AuthorizeActor(
        actor=_actor(),
        permission=Permission.VIEW_PROJECT,
        project_id=_PROJECT_ID,
        occurred_at=_NOW,
    )

    assert instance_command.project_id is None
    assert project_command.project_id == _PROJECT_ID
    with pytest.raises(ValidationError):
        AuthorizeActor(
            actor=_actor(),
            permission=Permission.VIEW_PROJECT,
            occurred_at=_NOW,
        )
    with pytest.raises(ValidationError):
        AuthorizeActor(
            actor=_actor(),
            permission=Permission.MANAGE_INSTANCE,
            project_id=_PROJECT_ID,
            occurred_at=_NOW,
        )


def test_subject_mutations_are_closed_versioned_and_kind_explicit() -> None:
    """Subject commands expose no handle, kind, admin, or actor override paths."""
    created = CreateSubjectMutation(
        **_mutation_metadata(),
        subject_id=SubjectId("sub_agent"),
        kind=SubjectKind.AGENT,
        handle="build-agent",
        display_name=None,  # type: ignore[arg-type]
    )
    updated = UpdateSubjectMutation(
        **_mutation_metadata(),
        subject="build-agent",
        expected_version=1,
        display_name="  Build agent  ",
    )
    enabled = SetSubjectEnabledMutation(
        **_mutation_metadata(),
        subject=SubjectId("sub_agent"),
        expected_version=2,
        enabled=False,
    )
    administrator = SetInstanceAdminMutation(
        **_mutation_metadata(),
        subject="build-agent",
        expected_version=3,
        is_instance_admin=True,
    )

    assert created.display_name == "build-agent"
    assert updated.display_name == "Build agent"
    assert enabled.enabled is False
    assert administrator.is_instance_admin is True
    for mutation in (updated, enabled, administrator):
        assert "handle" not in type(mutation).model_fields
        assert "kind" not in type(mutation).model_fields
    with pytest.raises(ValidationError):
        UpdateSubjectMutation(
            **_mutation_metadata(),
            subject="build-agent",
            expected_version=0,
            display_name="Build agent",
        )
    with pytest.raises(ValidationError):
        CreateSubjectMutation(
            **_mutation_metadata(),
            subject_id=SubjectId("sub_agent"),
            kind="agent",  # type: ignore[arg-type]
            handle="build-agent",
            display_name="Build agent",
        )


def test_grant_commands_encode_create_replace_and_revoke_concurrency() -> None:
    """Grant creation is versionless while replacement and revoke bind versions."""
    created = AssignProjectGrantMutation(
        **_mutation_metadata(),
        subject="build-agent",
        project="ACME",
        role=ProjectRole.AGENT,
    )
    replaced = AssignProjectGrantMutation(
        **_mutation_metadata(),
        subject=SubjectId("sub_agent"),
        project=_PROJECT_ID,
        role=ProjectRole.OPERATOR,
        expected_version=1,
    )
    revoked = RevokeProjectGrantMutation(
        **_mutation_metadata(),
        subject="build-agent",
        project="ACME",
        expected_version=2,
    )

    assert created.expected_version is None
    assert replaced.expected_version == 1
    assert "role" not in type(revoked).model_fields
    with pytest.raises(ValidationError):
        RevokeProjectGrantMutation(
            **_mutation_metadata(),
            subject="build-agent",
            project="ACME",
            expected_version=True,
        )


def test_token_lifecycle_commands_exclude_digest_from_dump_and_repr() -> None:
    """Token digest crosses only the specialized persistence command boundary."""
    issued = IssueTokenMutation(
        **_mutation_metadata(),
        token_id=TokenId("tok_agent"),
        subject="build-agent",
        token_digest=_DIGEST,
        expires_at=_NOW + timedelta(days=1),
    )
    activated = ActivateTokenMutation(
        **_mutation_metadata(),
        token_id=issued.token_id,
    )
    revoked = RevokeTokenMutation(
        **_mutation_metadata(),
        token_id=issued.token_id,
    )

    assert _DIGEST not in repr(issued)
    assert "token_digest" not in issued.model_dump()
    assert activated.token_id == revoked.token_id
    with pytest.raises(ValidationError):
        IssueTokenMutation(
            **_mutation_metadata(),
            token_id=TokenId("tok_agent"),
            subject="build-agent",
            token_digest=_DIGEST,
            expires_at=_NOW,
        )


def test_recovery_is_tokenless_exact_and_redacts_replacement_digest() -> None:
    """Recovery has no actor field and accepts only the bootstrap handle."""
    recovered = RecoverLocalMutation(
        instance_id=_INSTANCE_ID,
        bootstrap_handle="local-operator",
        token_id=TokenId("tok_recovered"),
        token_digest=_DIGEST,
        request_id=RequestId("req_recovery"),
        occurred_at=_NOW,
        expires_at=_NOW + timedelta(days=30),
    )

    assert "actor" not in type(recovered).model_fields
    assert "token_digest" not in recovered.model_dump()
    assert _DIGEST not in repr(recovered)
    with pytest.raises(ValidationError):
        RecoverLocalMutation(
            instance_id=_INSTANCE_ID,
            bootstrap_handle="another-human",
            token_id=TokenId("tok_recovered"),
            token_digest=_DIGEST,
            request_id=RequestId("req_recovery"),
            occurred_at=_NOW,
            expires_at=_NOW + timedelta(days=30),
        )


def test_current_identity_and_mutation_results_never_expose_hashes() -> None:
    """Normal identity results contain only domain Subject and TokenSummary."""
    identity = CurrentIdentityResult(subject=_subject(), token=_token())
    subject_result = SubjectResult(subject=_subject())
    grant_result = ProjectGrantResult(grant=_grant())
    token_result = TokenResult(token=_token())

    assert identity.subject.id == identity.token.subject_id
    for result in (identity, subject_result, grant_result, token_result):
        serialized = result.model_dump(mode="json")
        assert "token_hash" not in repr(serialized)
        assert "raw_token" not in repr(serialized)
    with pytest.raises(ValidationError):
        TokenResult(token=_token(), token_hash=_DIGEST)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CurrentIdentityResult(
            subject=_subject(subject_id=SubjectId("sub_other"), handle="other-human"),
            token=_token(),
        )


def test_identity_pages_validate_scope_order_and_cursor_shape() -> None:
    """Pages reject duplicate, unordered, cross-scope, and malformed results."""
    first_subject = _subject(subject_id=SubjectId("sub_a"), handle="alpha")
    second_subject = _subject(subject_id=SubjectId("sub_b"), handle="bravo")
    subjects = SubjectPage(
        subjects=(first_subject, second_subject),
        next_cursor="v5.c3ViamVjdHM",
    )
    grants = ProjectGrantPage(grants=(_grant(),), next_cursor=None)
    tokens = TokenPage(
        tokens=(
            _token(token_id=TokenId("tok_a")),
            _token(
                token_id=TokenId("tok_b"),
                created_at=_NOW + timedelta(seconds=1),
            ),
        ),
        next_cursor=None,
    )

    assert len(subjects.subjects) == 2
    assert grants.grants[0].project_id == _PROJECT_ID
    assert len(tokens.tokens) == 2
    with pytest.raises(ValidationError):
        SubjectPage(subjects=(second_subject, first_subject), next_cursor=None)
    with pytest.raises(ValidationError):
        SubjectPage(subjects=(), next_cursor="v4.old")
    with pytest.raises(ValidationError):
        TokenPage(
            tokens=(
                _token(),
                _token(
                    token_id=TokenId("tok_other"),
                    subject_id=SubjectId("sub_other"),
                    created_at=_NOW + timedelta(seconds=1),
                ),
            ),
            next_cursor=None,
        )


def test_identity_pages_reject_every_scope_and_order_violation() -> None:
    """Identity pages enforce uniqueness, order, and one exact target scope."""
    first_subject = _subject(subject_id=SubjectId("sub_a"), handle="alpha")
    cross_instance_subject = replace(
        _subject(subject_id=SubjectId("sub_b"), handle="bravo"),
        instance_id=InstanceId("ins_other"),
    )
    duplicate_grant = _grant()
    cross_project_grant = replace(_grant(), project_id=ProjectId("prj_other"))
    first_token = _token(token_id=TokenId("tok_a"))
    earlier_token = _token(
        token_id=TokenId("tok_b"),
        created_at=_NOW - timedelta(seconds=1),
    )

    invalid_pages = (
        lambda: SubjectPage(
            subjects=(first_subject, first_subject),
            next_cursor=None,
        ),
        lambda: SubjectPage(
            subjects=(first_subject, cross_instance_subject),
            next_cursor=None,
        ),
        lambda: ProjectGrantPage(
            grants=(duplicate_grant, duplicate_grant),
            next_cursor=None,
        ),
        lambda: ProjectGrantPage(
            grants=(duplicate_grant, cross_project_grant),
            next_cursor=None,
        ),
        lambda: TokenPage(
            tokens=(first_token, earlier_token),
            next_cursor=None,
        ),
        lambda: TokenPage(
            tokens=(first_token, first_token),
            next_cursor=None,
        ),
    )
    for invalid_page in invalid_pages:
        with pytest.raises(ValidationError):
            invalid_page()

    assert ProjectGrantPage(grants=(), next_cursor=None).grants == ()


def test_audit_results_freeze_payload_and_require_ascending_instance_page() -> None:
    """Administrative audit results are closed, immutable, and cursor ordered."""
    event = AuditEventResult(
        id=AuditEventId("aev_created"),
        cursor=1,
        instance_id=_INSTANCE_ID,
        actor_subject_id=_SUBJECT_ID,
        actor_kind=SubjectKind.HUMAN,
        actor_token_id=_TOKEN_ID,
        request_id=RequestId("req_identity"),
        event_type=AuditEventType.SUBJECT_CREATED,
        occurred_at=_NOW,
        payload={
            "subject_id": "sub_agent",
            "handle": "build-agent",
            "kind": "agent",
            "version": 1,
        },
    )
    page = AuditEventPage(events=(event,), next_cursor=1)

    assert page.events[0].payload["handle"] == "build-agent"
    with pytest.raises(TypeError):
        page.events[0].payload["handle"] = "changed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        AuditEventPage(events=(event,), next_cursor=2)


def test_audit_page_rejects_invalid_cursor_order_and_instance_scope() -> None:
    """Audit pages bind ascending unique cursors to one Instance and final cursor."""
    first = AuditEventResult(
        id=AuditEventId("aev_first"),
        cursor=1,
        instance_id=_INSTANCE_ID,
        actor_subject_id=_SUBJECT_ID,
        actor_kind=SubjectKind.HUMAN,
        actor_token_id=_TOKEN_ID,
        request_id=RequestId("req_first"),
        event_type=AuditEventType.SUBJECT_CREATED,
        occurred_at=_NOW,
        payload={
            "subject_id": "sub_agent",
            "handle": "build-agent",
            "kind": "agent",
            "version": 1,
        },
    )
    second = first.model_copy(update={"id": AuditEventId("aev_second"), "cursor": 2})
    cross_instance = second.model_copy(update={"instance_id": InstanceId("ins_other")})

    assert AuditEventPage(events=(), next_cursor=0).events == ()
    for values in (
        {"events": (second, first), "next_cursor": 1},
        {"events": (first, first), "next_cursor": 1},
        {"events": (first, cross_instance), "next_cursor": 2},
        {"events": (first,), "next_cursor": True},
    ):
        with pytest.raises(ValidationError):
            AuditEventPage.model_validate(values)


def test_phase_five_ports_are_narrow_explicit_and_secret_free() -> None:
    """Identity repository protocols expose typed semantic methods only."""
    expected = {
        SubjectRepository: {
            "create_subject",
            "list_subjects",
            "update_subject",
            "set_subject_enabled",
            "set_instance_admin",
        },
        GrantRepository: {
            "assign_project_grant",
            "list_project_grants",
            "revoke_project_grant",
        },
        TokenRepository: {
            "issue_pending_token",
            "activate_token",
            "list_tokens",
            "revoke_token",
            "recover_local",
        },
        AuthorizationRepository: {"authorize_actor"},
    }

    for protocol, methods in expected.items():
        assert methods <= set(dir(protocol))
        for method in methods:
            signature = inspect.signature(getattr(protocol, method))
            assert "raw_token" not in signature.parameters
            assert "token_hash" not in signature.parameters
    assert expected[SubjectRepository] <= set(dir(IdentityRepository))
    assert expected[GrantRepository] <= set(dir(IdentityRepository))
    assert expected[TokenRepository] <= set(dir(IdentityRepository))
    assert {
        "new_subject_id",
        "new_token_id",
        "new_audit_event_id",
        "new_request_id",
    } <= set(dir(IdentityIdentifierFactory))


def test_results_do_not_accept_actor_or_secret_override_fields() -> None:
    """Closed Pydantic result models reject unknown authority and secret fields."""
    with pytest.raises(ValidationError):
        SubjectResult(subject=_subject(), actor=_actor())  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ProjectGrantResult(
            grant=_grant(),
            role=ProjectRole.VIEWER,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        TokenPage(tokens=(_token(),), next_cursor=None, digest=_DIGEST)  # type: ignore[call-arg]


def test_token_summary_status_validation_prevents_inconsistent_identity_result() -> (
    None
):
    """Current identity cannot carry pending, expired, or revoked Token metadata."""
    active = _token()
    expired = replace(
        active,
        status=TokenStatus.EXPIRED,
        expires_at=_NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValidationError):
        CurrentIdentityResult(subject=_subject(), token=expired)
