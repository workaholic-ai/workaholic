"""Unit tests for backend-neutral Token application services."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ActivateTokenMutation,
    ApplicationError,
    ApplicationErrorCode,
    CurrentIdentityResult,
    IssueTokenMutation,
    ListTokens,
    RecoverLocalMutation,
    RevokeTokenMutation,
    TokenApplication,
    TokenNotFoundError,
    TokenPage,
    TokenResult,
)
from workaholic.domain import (
    AuditEventId,
    AuthenticatedActor,
    InstanceId,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
    TokenStatus,
    TokenSummary,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workaholic.application import Clock, IdentityIdentifierFactory, TokenRepository

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_EXPIRES = _NOW + timedelta(days=1)
_DIGEST = "a" * 64
_ACTOR = AuthenticatedActor(
    instance_id=InstanceId("ins_local"),
    subject_id=SubjectId("sub_admin"),
    subject_kind=SubjectKind.HUMAN,
    token_id=TokenId("tok_admin"),
)
_TARGET = SubjectId("sub_agent")
_TOKEN_ID = TokenId("tok_created")


def _token(
    status: TokenStatus,
    *,
    token_id: TokenId = _TOKEN_ID,
    subject_id: SubjectId = _TARGET,
) -> TokenSummary:
    """Build one valid Token lifecycle projection.

    Args:
        status: Requested lifecycle status.
        token_id: Public Token identity.
        subject_id: Token-owning Subject.

    Returns:
        Valid non-secret Token metadata.

    """
    active = status in (TokenStatus.ACTIVE, TokenStatus.EXPIRED)
    revoked = status is TokenStatus.REVOKED
    return TokenSummary(
        id=token_id,
        subject_id=subject_id,
        status=status,
        created_by=_ACTOR.subject_id,
        created_at=_NOW,
        activated_at=_NOW if active or revoked else None,
        expires_at=_EXPIRES,
        revoked_at=_NOW if revoked else None,
        revoked_by=_ACTOR.subject_id if revoked else None,
    )


def _recovered_identity() -> CurrentIdentityResult:
    """Build one valid recovered bootstrap-Human identity.

    Returns:
        Active identity result for the replacement Token.

    """
    subject_id = SubjectId("sub_local")
    return CurrentIdentityResult(
        subject=Subject(
            id=subject_id,
            instance_id=_ACTOR.instance_id,
            kind=SubjectKind.HUMAN,
            handle="local-operator",
            display_name="Local operator",
            enabled=True,
            is_instance_admin=True,
            version=1,
            created_by=subject_id,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        token=_token(TokenStatus.ACTIVE, subject_id=subject_id),
    )


class _Clock:
    """Deterministic Token-service clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic complete identity identifier factory."""

    def new_subject_id(self) -> SubjectId:
        """Return an unused Subject identity."""
        return SubjectId("sub_candidate")

    def new_token_id(self) -> TokenId:
        """Return an unused Token identity."""
        return TokenId("tok_candidate")

    def new_audit_event_id(self) -> AuditEventId:
        """Return an unused AuditEvent identity."""
        return AuditEventId("aev_candidate")

    def new_request_id(self) -> RequestId:
        """Return the request identity."""
        return RequestId("req_token")


class _Repository:
    """Strict recording Token repository fake."""

    def __init__(self) -> None:
        """Initialize valid outputs and empty recordings."""
        self.result: object = TokenResult(token=_token(TokenStatus.PENDING))
        self.page: object = TokenPage(
            tokens=(_token(TokenStatus.ACTIVE),),
            next_cursor=None,
        )
        self.recovery_result: object = _recovered_identity()
        self.calls: list[object] = []

    def issue_pending_token(self, mutation: IssueTokenMutation) -> object:
        """Record one pending issue mutation."""
        self.calls.append(mutation)
        return self.result

    def activate_token(self, mutation: ActivateTokenMutation) -> object:
        """Record one activation mutation."""
        self.calls.append(mutation)
        return self.result

    def list_tokens(self, command: ListTokens) -> object:
        """Record one list command."""
        self.calls.append(command)
        return self.page

    def revoke_token(self, mutation: RevokeTokenMutation) -> object:
        """Record one revocation mutation."""
        self.calls.append(mutation)
        return self.result

    def recover_local(self, mutation: RecoverLocalMutation) -> object:
        """Record one embedded recovery mutation."""
        self.calls.append(mutation)
        return self.recovery_result


def _application(repository: _Repository) -> TokenApplication:
    """Construct TokenApplication with explicitly cast fakes.

    Args:
        repository: Recording Token repository.

    Returns:
        Configured Token application.

    """
    return TokenApplication(
        repository=cast("TokenRepository", repository),
        clock=cast("Clock", _Clock()),
        identifiers=cast("IdentityIdentifierFactory", _Identifiers()),
    )


def test_issue_pending_constructs_digest_only_non_secret_mutation() -> None:
    """Pending issue owns request/time without accepting raw credential data."""
    repository = _Repository()
    application = _application(repository)

    result = application.issue_pending(
        actor=_ACTOR,
        token_id=_TOKEN_ID,
        subject=_TARGET,
        token_digest=_DIGEST,
        expires_at=_EXPIRES,
        idempotency_key="token-create-1",
    )

    expected = IssueTokenMutation(
        actor=_ACTOR,
        request_id=RequestId("req_token"),
        occurred_at=_NOW,
        idempotency_key="token-create-1",
        token_id=_TOKEN_ID,
        subject=_TARGET,
        token_digest=_DIGEST,
        expires_at=_EXPIRES,
    )
    assert result is repository.result
    assert repository.calls == [expected]
    assert _DIGEST not in repr(expected)
    assert "token_digest" not in expected.model_dump()


def test_activate_list_and_revoke_construct_exact_non_secret_operations() -> None:
    """Remaining authenticated Token operations preserve public identity only."""
    repository = _Repository()
    application = _application(repository)
    repository.result = TokenResult(token=_token(TokenStatus.ACTIVE))
    application.activate(
        actor=_ACTOR,
        token_id=_TOKEN_ID,
        idempotency_key="token-activate-1",
    )
    page = application.list(actor=_ACTOR, subject=_TARGET, limit=20)
    repository.result = TokenResult(token=_token(TokenStatus.REVOKED))
    application.revoke(actor=_ACTOR, token_id=_TOKEN_ID)

    assert page is repository.page
    assert repository.calls == [
        ActivateTokenMutation(
            actor=_ACTOR,
            request_id=RequestId("req_token"),
            occurred_at=_NOW,
            idempotency_key="token-activate-1",
            token_id=_TOKEN_ID,
        ),
        ListTokens(actor=_ACTOR, subject=_TARGET, limit=20),
        RevokeTokenMutation(
            actor=_ACTOR,
            request_id=RequestId("req_token"),
            occurred_at=_NOW,
            token_id=_TOKEN_ID,
        ),
    ]


def test_recover_local_builds_exact_tokenless_command_and_validates_identity() -> None:
    """Recovery remains explicit and returns no raw Token or digest."""
    repository = _Repository()
    application = _application(repository)

    result = application.recover_local(
        instance_id=_ACTOR.instance_id,
        bootstrap_handle="local-operator",
        token_id=_TOKEN_ID,
        token_digest=_DIGEST,
        expires_at=_EXPIRES,
    )

    expected = RecoverLocalMutation(
        instance_id=_ACTOR.instance_id,
        bootstrap_handle="local-operator",
        token_id=_TOKEN_ID,
        token_digest=_DIGEST,
        request_id=RequestId("req_token"),
        occurred_at=_NOW,
        expires_at=_EXPIRES,
    )
    assert result is repository.recovery_result
    assert repository.calls == [expected]
    assert _DIGEST not in repr(expected)
    assert "token_digest" not in expected.model_dump()


@pytest.mark.parametrize("operation", ["issue", "activate", "list", "revoke"])
def test_mismatched_token_outputs_fail_closed(operation: str) -> None:
    """Every Token result is checked against its lifecycle operation."""
    repository = _Repository()
    application = _application(repository)
    repository.result = TokenResult(
        token=_token(TokenStatus.PENDING, token_id=TokenId("tok_other"))
    )
    repository.page = TokenPage(
        tokens=(
            _token(
                TokenStatus.ACTIVE,
                subject_id=SubjectId("sub_other"),
            ),
        ),
        next_cursor=None,
    )
    with pytest.raises(ApplicationError) as captured:
        _invoke_token_operation(application, operation)
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def _invoke_token_operation(application: TokenApplication, operation: str) -> None:
    """Invoke one Token output-validation path.

    Args:
        application: Configured Token application.
        operation: Closed test operation label.

    """
    if operation == "issue":
        application.issue_pending(
            actor=_ACTOR,
            token_id=_TOKEN_ID,
            subject=_TARGET,
            token_digest=_DIGEST,
            expires_at=_EXPIRES,
        )
        return
    if operation == "activate":
        application.activate(actor=_ACTOR, token_id=_TOKEN_ID)
        return
    if operation == "list":
        application.list(actor=_ACTOR, subject=_TARGET)
        return
    if operation != "revoke":
        raise AssertionError
    application.revoke(actor=_ACTOR, token_id=_TOKEN_ID)


def test_invalid_input_repository_failure_and_constructor_guard_are_stable() -> None:
    """Input is rejected early while stable persistence failures pass through."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as invalid:
        _application(repository).issue_pending(
            actor=_ACTOR,
            token_id=_TOKEN_ID,
            subject=_TARGET,
            token_digest="b" * 63,
            expires_at=_EXPIRES,
        )
    assert invalid.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.calls == []

    class _Missing(_Repository):
        """Repository exposing one stable missing-token failure."""

        def revoke_token(self, _mutation: RevokeTokenMutation) -> object:
            """Raise the stable missing-token error."""
            raise TokenNotFoundError

    with pytest.raises(TokenNotFoundError):
        _application(_Missing()).revoke(actor=_ACTOR, token_id=_TOKEN_ID)
    with pytest.raises(TypeError, match="Identity"):
        TokenApplication(
            repository=cast("TokenRepository", object()),
            clock=cast("Clock", _Clock()),
            identifiers=cast("IdentityIdentifierFactory", _Identifiers()),
        )


def test_recovery_rejects_mismatched_repository_identity() -> None:
    """Recovery output must retain exact confirmed Instance, handle, and Token."""
    repository = _Repository()
    repository.recovery_result = CurrentIdentityResult(
        subject=replace(
            _recovered_identity().subject,
            instance_id=InstanceId("ins_other"),
        ),
        token=_recovered_identity().token,
    )
    with pytest.raises(ApplicationError) as captured:
        _application(repository).recover_local(
            instance_id=_ACTOR.instance_id,
            bootstrap_handle="local-operator",
            token_id=_TOKEN_ID,
            token_digest=_DIGEST,
            expires_at=_EXPIRES,
        )
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize("operation", ["activate", "list", "revoke", "recover"])
def test_token_operations_reject_invalid_runtime_input(operation: str) -> None:
    """Every Token entry point validates runtime values before persistence."""
    repository = _Repository()
    application = _application(repository)

    operation_calls: dict[str, Callable[[], object]] = {
        "activate": lambda: application.activate(
            actor=_ACTOR,
            token_id=cast("TokenId", "tok_invalid"),
        ),
        "list": lambda: application.list(actor=_ACTOR, limit=0),
        "revoke": lambda: application.revoke(
            actor=_ACTOR,
            token_id=cast("TokenId", "tok_invalid"),
        ),
        "recover": lambda: application.recover_local(
            instance_id=_ACTOR.instance_id,
            bootstrap_handle="local-operator",
            token_id=_TOKEN_ID,
            token_digest=_DIGEST[:4],
            expires_at=_EXPIRES,
        ),
    }
    with pytest.raises(ApplicationError) as captured:
        operation_calls[operation]()

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.calls == []


@pytest.mark.parametrize("operation", ["issue", "activate", "list", "revoke"])
def test_token_operations_reject_malformed_repository_result(operation: str) -> None:
    """Token operations fail closed when repositories return unknown values."""
    repository = _Repository()
    repository.result = object()
    repository.page = object()

    with pytest.raises(ApplicationError) as captured:
        _invoke_token_operation(_application(repository), operation)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_self_token_listing_rejects_another_subjects_tokens() -> None:
    """An implicit self-list cannot expose another Subject's Token metadata."""
    repository = _Repository()
    repository.page = TokenPage(
        tokens=(_token(TokenStatus.ACTIVE, subject_id=SubjectId("sub_other")),),
        next_cursor=None,
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).list(actor=_ACTOR)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    "result",
    [
        object(),
        CurrentIdentityResult.model_construct(
            subject=replace(_recovered_identity().subject, handle="other-operator"),
            token=_recovered_identity().token,
        ),
        CurrentIdentityResult.model_construct(
            subject=replace(
                _recovered_identity().subject,
                kind=SubjectKind.AGENT,
            ),
            token=_recovered_identity().token,
        ),
        CurrentIdentityResult.model_construct(
            subject=_recovered_identity().subject,
            token=replace(
                _recovered_identity().token,
                id=TokenId("tok_other"),
            ),
        ),
        CurrentIdentityResult.model_construct(
            subject=_recovered_identity().subject,
            token=replace(
                _recovered_identity().token,
                subject_id=SubjectId("sub_other"),
            ),
        ),
        CurrentIdentityResult.model_construct(
            subject=_recovered_identity().subject,
            token=replace(
                _recovered_identity().token,
                status=TokenStatus.PENDING,
                activated_at=None,
            ),
        ),
    ],
)
def test_recovery_rejects_every_mismatched_output(result: object) -> None:
    """Recovery validates every identity and Token lifecycle invariant."""
    repository = _Repository()
    repository.recovery_result = result

    with pytest.raises(ApplicationError) as captured:
        _application(repository).recover_local(
            instance_id=_ACTOR.instance_id,
            bootstrap_handle="local-operator",
            token_id=_TOKEN_ID,
            token_digest=_DIGEST,
            expires_at=_EXPIRES,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
