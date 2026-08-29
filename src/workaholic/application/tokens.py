"""Backend-neutral non-secret Token lifecycle application services."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from workaholic.application._identity_support import (
    dependency_request_id,
    dependency_time,
    invalid_input,
    invalid_result,
    require_callable,
)
from workaholic.application.commands import (
    ActivateTokenMutation,
    IssueTokenMutation,
    ListTokens,
    RecoverLocalMutation,
    RevokeTokenMutation,
)
from workaholic.application.results import (
    CurrentIdentityResult,
    TokenPage,
    TokenResult,
)
from workaholic.domain import RequestId, SubjectId, SubjectKind, TokenStatus

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.ports import (
        Clock,
        IdentityIdentifierFactory,
        TokenRepository,
    )
    from workaholic.domain import AuthenticatedActor, InstanceId, TokenId


class TokenApplication:
    """Orchestrate pending, activation, listing, revocation, and recovery."""

    def __init__(
        self,
        repository: TokenRepository,
        clock: Clock,
        identifiers: IdentityIdentifierFactory,
    ) -> None:
        """Initialize explicit non-secret Token-service dependencies.

        Args:
            repository: Semantic Token persistence boundary.
            clock: Authoritative lifecycle clock.
            identifiers: Request identity factory.

        Raises:
            TypeError: If a dependency lacks a required operation.

        """
        for method_name in (
            "issue_pending_token",
            "activate_token",
            "list_tokens",
            "revoke_token",
            "recover_local",
        ):
            require_callable(repository, method_name, "Token repository")
        require_callable(clock, "now", "clock")
        require_callable(identifiers, "new_request_id", "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def issue_pending(  # noqa: PLR0913 - credential handoff is explicit.
        self,
        *,
        actor: AuthenticatedActor,
        token_id: TokenId,
        subject: SubjectId | str,
        token_digest: str,
        expires_at: datetime,
        idempotency_key: str | None = None,
    ) -> TokenResult:
        """Persist one pending non-authenticating Token digest.

        Raw Token generation remains in the trusted Session/auth boundary. This
        method receives only its public ID and non-reversible digest.

        Args:
            actor: Authenticated Instance administrator.
            token_id: Public identity already bound to the raw credential.
            subject: Target Subject ID or immutable handle.
            token_digest: Lowercase SHA-256 of the complete canonical Token.
            expires_at: Exclusive Token expiry.
            idempotency_key: Optional caller replay key reserved for activation.

        Returns:
            Committed non-secret pending Token metadata.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Pending Token issue"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = IssueTokenMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                token_id=token_id,
                subject=subject,
                token_digest=token_digest,
                expires_at=expires_at,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.issue_pending_token(mutation)
        if not _matches_pending(result, mutation=mutation):
            raise invalid_result(operation)
        return cast("TokenResult", result)

    def activate(
        self,
        *,
        actor: AuthenticatedActor,
        token_id: TokenId,
        idempotency_key: str | None = None,
    ) -> TokenResult:
        """Activate one pending Token after its credential sink succeeds.

        Args:
            actor: Authenticated Instance administrator.
            token_id: Exact pending Token identity.
            idempotency_key: Optional caller replay key consumed on activation.

        Returns:
            Active non-secret Token metadata.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Token activation"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = ActivateTokenMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                token_id=token_id,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.activate_token(mutation)
        if (
            not isinstance(result, TokenResult)
            or result.token.id != mutation.token_id
            or result.token.status is not TokenStatus.ACTIVE
        ):
            raise invalid_result(operation)
        return result

    def list(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> TokenPage:
        """List one self- or administrator-visible Token metadata page.

        Args:
            actor: Authenticated requesting Subject.
            subject: Optional target Subject selector; null selects self.
            cursor: Optional opaque continuation cursor.
            limit: Positive page size capped by the command contract.

        Returns:
            Stable non-secret Token metadata page.

        Raises:
            ApplicationError: If input or output is invalid.

        """
        operation = "Token listing"
        try:
            command = ListTokens(
                actor=actor,
                subject=subject,
                cursor=cursor,
                limit=limit,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.list_tokens(command)
        if not isinstance(result, TokenPage) or (
            isinstance(command.subject, SubjectId)
            and any(token.subject_id != command.subject for token in result.tokens)
        ):
            raise invalid_result(operation)
        if command.subject is None and any(
            token.subject_id != actor.subject_id for token in result.tokens
        ):
            raise invalid_result(operation)
        return result

    def revoke(
        self,
        *,
        actor: AuthenticatedActor,
        token_id: TokenId,
        idempotency_key: str | None = None,
    ) -> TokenResult:
        """Monotonically revoke one visible Token.

        Args:
            actor: Authenticated self or Instance administrator.
            token_id: Exact public Token identity.
            idempotency_key: Optional caller replay key.

        Returns:
            Revoked non-secret Token metadata.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Token revocation"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = RevokeTokenMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                token_id=token_id,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.revoke_token(mutation)
        if (
            not isinstance(result, TokenResult)
            or result.token.id != mutation.token_id
            or result.token.status is not TokenStatus.REVOKED
        ):
            raise invalid_result(operation)
        return result

    def recover_local(
        self,
        *,
        instance_id: InstanceId,
        bootstrap_handle: str,
        token_id: TokenId,
        token_digest: str,
        expires_at: datetime,
    ) -> CurrentIdentityResult:
        """Execute the exact tokenless embedded bootstrap-Human recovery.

        Args:
            instance_id: Explicit confirmed local Instance identity.
            bootstrap_handle: Exact confirmed immutable bootstrap handle.
            token_id: Public replacement Token identity.
            token_digest: Non-reversible replacement Token digest.
            expires_at: Exclusive replacement Token expiry.

        Returns:
            Recovered Human identity with active non-secret Token metadata.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Local recovery"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = RecoverLocalMutation(
                instance_id=instance_id,
                bootstrap_handle=bootstrap_handle,
                token_id=token_id,
                token_digest=token_digest,
                request_id=request_id,
                occurred_at=occurred_at,
                expires_at=expires_at,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.recover_local(mutation)
        if (
            not isinstance(result, CurrentIdentityResult)
            or result.subject.instance_id != mutation.instance_id
            or result.subject.handle != mutation.bootstrap_handle
            or result.subject.kind is not SubjectKind.HUMAN
            or result.token.id != mutation.token_id
            or result.token.subject_id != result.subject.id
            or result.token.status is not TokenStatus.ACTIVE
        ):
            raise invalid_result(operation)
        return result

    def _mutation_metadata(self, operation: str) -> tuple[RequestId, datetime]:
        """Generate one request identity and authoritative mutation time.

        Args:
            operation: Safe operation label.

        Returns:
            Valid request identity and UTC timestamp.

        """
        return (
            dependency_request_id(self._identifiers, operation=operation),
            dependency_time(self._clock, operation=operation),
        )


def _matches_pending(value: object, *, mutation: IssueTokenMutation) -> bool:
    """Return whether pending output matches exact non-secret input.

    Args:
        value: Candidate repository result.
        mutation: Dispatched pending mutation.

    Returns:
        Whether the output is the expected pending projection.

    """
    if not isinstance(value, TokenResult):
        return False
    token = value.token
    return (
        token.id == mutation.token_id
        and token.status is TokenStatus.PENDING
        and token.created_by == mutation.actor.subject_id
        and token.created_at == mutation.occurred_at
        and token.expires_at == mutation.expires_at
        and token.activated_at is None
        and token.revoked_at is None
        and token.revoked_by is None
        and (
            token.subject_id == mutation.subject
            if isinstance(mutation.subject, SubjectId)
            else True
        )
    )
