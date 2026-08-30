"""Unit tests for backend-neutral authentication application services."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AuthenticateToken,
    AuthenticationApplication,
    AuthenticationFailedError,
    AuthorizeActor,
    CurrentIdentityResult,
    GetCurrentIdentity,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    Permission,
    ProjectId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
    TokenStatus,
    TokenSummary,
)

if TYPE_CHECKING:
    from workaholic.application import (
        AuthenticationRepository,
        AuthorizationRepository,
        Clock,
    )

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_ACTOR = AuthenticatedActor(
    instance_id=InstanceId("ins_local"),
    subject_id=SubjectId("sub_agent"),
    subject_kind=SubjectKind.AGENT,
    token_id=TokenId("tok_agent"),
)


def _subject() -> Subject:
    """Build the enabled authenticated Subject projection.

    Returns:
        Valid Agent Subject matching the actor fixture.

    """
    return Subject(
        id=_ACTOR.subject_id,
        instance_id=_ACTOR.instance_id,
        kind=_ACTOR.subject_kind,
        handle="agent",
        display_name="Agent",
        enabled=True,
        is_instance_admin=False,
        version=1,
        created_by=SubjectId("sub_owner"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _identity() -> CurrentIdentityResult:
    """Build current active Token metadata for the actor fixture.

    Returns:
        Internally consistent identity result.

    """
    return CurrentIdentityResult(
        subject=_subject(),
        token=TokenSummary(
            id=_ACTOR.token_id,
            subject_id=_ACTOR.subject_id,
            status=TokenStatus.ACTIVE,
            created_by=SubjectId("sub_owner"),
            created_at=_NOW,
            activated_at=_NOW,
            expires_at=_NOW + timedelta(days=1),
            revoked_at=None,
            revoked_by=None,
        ),
    )


class _Clock:
    """Deterministic authentication test clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Repository:
    """Strict recording authentication and authorization repository."""

    def __init__(self) -> None:
        """Initialize valid outputs and empty command recordings."""
        self.actor_result: object = _ACTOR
        self.identity_result: object = _identity()
        self.subject_result: object = _subject()
        self.auth_commands: list[AuthenticateToken] = []
        self.identity_commands: list[GetCurrentIdentity] = []
        self.authorization_commands: list[AuthorizeActor] = []

    def authenticate_token(self, command: AuthenticateToken) -> object:
        """Record one authentication command and return configured output."""
        self.auth_commands.append(command)
        return self.actor_result

    def get_current_identity(self, command: GetCurrentIdentity) -> object:
        """Record one identity query and return configured output."""
        self.identity_commands.append(command)
        return self.identity_result

    def authorize_actor(self, command: AuthorizeActor) -> object:
        """Record one authorization command and return configured output."""
        self.authorization_commands.append(command)
        return self.subject_result


def _application(
    repository: _Repository,
    *,
    clock: object | None = None,
) -> AuthenticationApplication:
    """Construct the service with explicitly cast strict fakes.

    Args:
        repository: Recording combined repository fake.
        clock: Optional clock override.

    Returns:
        Configured authentication application.

    """
    return AuthenticationApplication(
        authentication_repository=cast("AuthenticationRepository", repository),
        authorization_repository=cast("AuthorizationRepository", repository),
        clock=cast("Clock", _Clock() if clock is None else clock),
    )


def test_authenticate_builds_digest_only_command_and_validates_actor() -> None:
    """Authentication owns time and never receives or returns a raw Token."""
    repository = _Repository()
    application = _application(repository)
    digest = "a" * 64

    result = application.authenticate(
        token_id=_ACTOR.token_id,
        token_digest=digest,
        expected_instance_id=_ACTOR.instance_id,
    )

    assert result is _ACTOR
    assert repository.auth_commands == [
        AuthenticateToken(
            token_id=_ACTOR.token_id,
            token_digest=digest,
            expected_instance_id=_ACTOR.instance_id,
            occurred_at=_NOW,
        )
    ]
    assert digest not in repr(repository.auth_commands[0])
    assert "token_digest" not in repository.auth_commands[0].model_dump()


def test_whoami_and_authorize_construct_exact_queries() -> None:
    """Identity and permission reads bind output to the authenticated actor."""
    repository = _Repository()
    application = _application(repository)

    identity = application.whoami(_ACTOR)
    subject = application.authorize(
        actor=_ACTOR,
        permission=Permission.EXECUTE_AGENT,
        project_id=ProjectId("prj_local"),
        required_kind=SubjectKind.AGENT,
    )

    assert identity is repository.identity_result
    assert subject is repository.subject_result
    assert repository.identity_commands == [GetCurrentIdentity(actor=_ACTOR)]
    assert repository.authorization_commands == [
        AuthorizeActor(
            actor=_ACTOR,
            permission=Permission.EXECUTE_AGENT,
            project_id=ProjectId("prj_local"),
            required_kind=SubjectKind.AGENT,
            occurred_at=_NOW,
        )
    ]


def test_repository_permission_failure_passes_through_unchanged() -> None:
    """Stable persistence authorization failures are not remapped."""

    class _Denied(_Repository):
        """Repository that denies authentication."""

        def authenticate_token(self, _command: AuthenticateToken) -> object:
            """Raise the stable authentication failure."""
            raise AuthenticationFailedError

    application = _application(_Denied())

    with pytest.raises(AuthenticationFailedError):
        application.authenticate(
            token_id=_ACTOR.token_id,
            token_digest="a" * 64,
            expected_instance_id=_ACTOR.instance_id,
        )


@pytest.mark.parametrize("operation", ["authenticate", "whoami", "authorize"])
def test_mismatched_repository_output_fails_closed(operation: str) -> None:
    """Every identity result is runtime-checked against its command scope."""
    repository = _Repository()
    other = replace(_ACTOR, token_id=TokenId("tok_other"))
    if operation == "authenticate":
        repository.actor_result = other
    elif operation == "whoami":
        repository.identity_result = CurrentIdentityResult(
            subject=_subject(),
            token=replace(_identity().token, id=TokenId("tok_other")),
        )
    else:
        repository.subject_result = replace(_subject(), enabled=False)
    application = _application(repository)

    with pytest.raises(ApplicationError) as captured:
        _invoke_identity_operation(application, operation)
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def _invoke_identity_operation(
    application: AuthenticationApplication,
    operation: str,
) -> None:
    """Invoke one identity output-validation path.

    Args:
        application: Configured identity application.
        operation: Closed test operation label.

    """
    if operation == "authenticate":
        application.authenticate(
            token_id=_ACTOR.token_id,
            token_digest="a" * 64,
            expected_instance_id=_ACTOR.instance_id,
        )
        return
    if operation == "whoami":
        application.whoami(_ACTOR)
        return
    if operation != "authorize":
        raise AssertionError
    application.authorize(
        actor=_ACTOR,
        permission=Permission.MANAGE_INSTANCE,
    )


def test_invalid_input_and_clock_fail_before_repository_io() -> None:
    """Malformed caller and dependency data never cross the repository port."""

    class _InvalidClock:
        """Clock violating the timezone contract."""

        def now(self) -> datetime:
            """Return a deliberately naive time."""
            return _NOW.replace(tzinfo=None)

    repository = _Repository()
    with pytest.raises(ApplicationError) as invalid_digest:
        _application(repository).authenticate(
            token_id=_ACTOR.token_id,
            token_digest="b" * 63,
            expected_instance_id=_ACTOR.instance_id,
        )
    with pytest.raises(ApplicationError) as invalid_clock:
        _application(repository, clock=_InvalidClock()).authenticate(
            token_id=_ACTOR.token_id,
            token_digest="a" * 64,
            expected_instance_id=_ACTOR.instance_id,
        )

    assert invalid_digest.value.code is ApplicationErrorCode.INVALID_INPUT
    assert invalid_clock.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.auth_commands == []


@pytest.mark.parametrize(
    ("authentication", "authorization", "clock"),
    [
        (object(), _Repository(), _Clock()),
        (_Repository(), object(), _Clock()),
        (_Repository(), _Repository(), object()),
    ],
)
def test_constructor_runtime_validates_dependencies(
    authentication: object,
    authorization: object,
    clock: object,
) -> None:
    """Composition fails immediately when a required operation is absent."""
    with pytest.raises(TypeError, match="Identity"):
        AuthenticationApplication(
            authentication_repository=cast(
                "AuthenticationRepository",
                authentication,
            ),
            authorization_repository=cast(
                "AuthorizationRepository",
                authorization,
            ),
            clock=cast("Clock", clock),
        )
