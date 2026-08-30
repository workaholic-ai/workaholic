"""Backend-neutral authentication and authorization application services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application._identity_support import (
    dependency_time,
    invalid_input,
    invalid_result,
    require_callable,
)
from workaholic.application.commands import (
    AuthenticateToken,
    AuthorizeActor,
    GetCurrentIdentity,
)
from workaholic.application.results import CurrentIdentityResult
from workaholic.domain import AuthenticatedActor, Subject

if TYPE_CHECKING:
    from workaholic.application.ports import (
        AuthenticationRepository,
        AuthorizationRepository,
        Clock,
    )
    from workaholic.domain import (
        InstanceId,
        Permission,
        ProjectId,
        SubjectKind,
        TokenId,
    )


class AuthenticationApplication:
    """Authenticate credentials and revalidate secret-free identity context."""

    def __init__(
        self,
        authentication_repository: AuthenticationRepository,
        authorization_repository: AuthorizationRepository,
        clock: Clock,
    ) -> None:
        """Initialize explicit identity-read dependencies.

        Args:
            authentication_repository: Token authentication and identity port.
            authorization_repository: Fresh permission-check port.
            clock: Authoritative authentication and authorization clock.

        Raises:
            TypeError: If a dependency lacks a required operation.

        """
        require_callable(
            authentication_repository,
            "authenticate_token",
            "authentication repository",
        )
        require_callable(
            authentication_repository,
            "get_current_identity",
            "authentication repository",
        )
        require_callable(
            authorization_repository,
            "authorize_actor",
            "authorization repository",
        )
        require_callable(clock, "now", "clock")
        self._authentication_repository = authentication_repository
        self._authorization_repository = authorization_repository
        self._clock = clock

    def authenticate(
        self,
        *,
        token_id: TokenId,
        token_digest: str,
        expected_instance_id: InstanceId,
    ) -> AuthenticatedActor:
        """Authenticate one parsed non-secret Token digest.

        Args:
            token_id: Public identity parsed from the canonical Token.
            token_digest: Lowercase SHA-256 digest of the complete raw Token.
            expected_instance_id: Trusted selected profile Instance identity.

        Returns:
            Secret-free authenticated actor context.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Token authentication"
        occurred_at = dependency_time(self._clock, operation=operation)
        try:
            command = AuthenticateToken(
                token_id=token_id,
                token_digest=token_digest,
                expected_instance_id=expected_instance_id,
                occurred_at=occurred_at,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._authentication_repository.authenticate_token(command)
        if (
            not isinstance(result, AuthenticatedActor)
            or result.token_id != command.token_id
            or result.instance_id != command.expected_instance_id
        ):
            raise invalid_result(operation)
        return result

    def whoami(self, actor: AuthenticatedActor) -> CurrentIdentityResult:
        """Revalidate and return current non-secret identity metadata.

        Args:
            actor: Previously authenticated secret-free context.

        Returns:
            Current enabled Subject and active Token metadata.

        Raises:
            ApplicationError: If input or repository output is invalid.

        """
        operation = "Current identity"
        try:
            command = GetCurrentIdentity(actor=actor)
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._authentication_repository.get_current_identity(command)
        if (
            not isinstance(result, CurrentIdentityResult)
            or result.subject.id != actor.subject_id
            or result.subject.instance_id != actor.instance_id
            or result.subject.kind is not actor.subject_kind
            or result.token.id != actor.token_id
        ):
            raise invalid_result(operation)
        return result

    def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        permission: Permission,
        project_id: ProjectId | None = None,
        required_kind: SubjectKind | None = None,
    ) -> Subject:
        """Resolve one fresh permission projection for an application operation.

        Args:
            actor: Previously authenticated secret-free context.
            permission: Exact required Instance or cumulative Project permission.
            project_id: Project scope, absent only for Instance administration.
            required_kind: Optional exact Human or Agent constraint.

        Returns:
            Fresh current authorized Subject projection.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Authorization"
        occurred_at = dependency_time(self._clock, operation=operation)
        try:
            command = AuthorizeActor(
                actor=actor,
                permission=permission,
                project_id=project_id,
                required_kind=required_kind,
                occurred_at=occurred_at,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._authorization_repository.authorize_actor(command)
        if (
            not isinstance(result, Subject)
            or result.id != actor.subject_id
            or result.instance_id != actor.instance_id
            or result.kind is not actor.subject_kind
            or not result.enabled
        ):
            raise invalid_result(operation)
        return result
