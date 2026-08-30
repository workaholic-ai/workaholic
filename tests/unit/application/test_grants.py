"""Unit tests for backend-neutral ProjectGrant application services."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    AssignProjectGrantMutation,
    GrantApplication,
    LastProjectOwnerError,
    ListProjectGrants,
    ProjectGrantPage,
    ProjectGrantResult,
    RevokeProjectGrantMutation,
)
from workaholic.domain import (
    AuditEventId,
    AuthenticatedActor,
    InstanceId,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    SubjectId,
    SubjectKind,
    TokenId,
)

if TYPE_CHECKING:
    from workaholic.application import Clock, GrantRepository, IdentityIdentifierFactory

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_PROJECT_ID = ProjectId("prj_local")
_SUBJECT_ID = SubjectId("sub_agent")
_ACTOR = AuthenticatedActor(
    instance_id=InstanceId("ins_local"),
    subject_id=SubjectId("sub_owner"),
    subject_kind=SubjectKind.HUMAN,
    token_id=TokenId("tok_owner"),
)


def _grant(
    *,
    role: ProjectRole = ProjectRole.AGENT,
    version: int = 1,
) -> ProjectGrant:
    """Build one valid ProjectGrant fixture.

    Args:
        role: Cumulative Project role.
        version: Positive optimistic version.

    Returns:
        Valid grant projection.

    """
    return ProjectGrant(
        instance_id=_ACTOR.instance_id,
        subject_id=_SUBJECT_ID,
        project_id=_PROJECT_ID,
        role=role,
        version=version,
        granted_by=_ACTOR.subject_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _Clock:
    """Deterministic grant-service clock."""

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
        """Return an unused audit identity."""
        return AuditEventId("aev_candidate")

    def new_request_id(self) -> RequestId:
        """Return the request identity."""
        return RequestId("req_grant")


class _Repository:
    """Strict recording ProjectGrant repository fake."""

    def __init__(self) -> None:
        """Initialize valid outputs and empty recordings."""
        self.result: object = ProjectGrantResult(grant=_grant())
        self.page: object = ProjectGrantPage(grants=(_grant(),), next_cursor=None)
        self.calls: list[object] = []

    def assign_project_grant(self, mutation: AssignProjectGrantMutation) -> object:
        """Record one assignment mutation."""
        self.calls.append(mutation)
        return self.result

    def list_project_grants(self, command: ListProjectGrants) -> object:
        """Record one list query."""
        self.calls.append(command)
        return self.page

    def revoke_project_grant(self, mutation: RevokeProjectGrantMutation) -> object:
        """Record one revocation mutation."""
        self.calls.append(mutation)
        return self.result


def _application(repository: _Repository) -> GrantApplication:
    """Construct GrantApplication with explicitly cast strict fakes.

    Args:
        repository: Recording grant repository.

    Returns:
        Configured grant application.

    """
    return GrantApplication(
        repository=cast("GrantRepository", repository),
        clock=cast("Clock", _Clock()),
        identifiers=cast("IdentityIdentifierFactory", _Identifiers()),
    )


def test_assign_create_and_replace_build_exact_optimistic_mutations() -> None:
    """Grant assignment preserves create-versus-replace version semantics."""
    repository = _Repository()
    application = _application(repository)

    created = application.assign(
        actor=_ACTOR,
        subject=_SUBJECT_ID,
        project=_PROJECT_ID,
        role=ProjectRole.AGENT,
        idempotency_key="grant-create",
    )
    repository.result = ProjectGrantResult(
        grant=_grant(role=ProjectRole.OPERATOR, version=2)
    )
    replaced = application.assign(
        actor=_ACTOR,
        subject="agent",
        project="LOCAL",
        role=ProjectRole.OPERATOR,
        expected_version=1,
    )

    assert created.grant.version == 1
    assert replaced.grant.version == 2
    assert repository.calls == [
        AssignProjectGrantMutation(
            actor=_ACTOR,
            request_id=RequestId("req_grant"),
            occurred_at=_NOW,
            idempotency_key="grant-create",
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            role=ProjectRole.AGENT,
        ),
        AssignProjectGrantMutation(
            actor=_ACTOR,
            request_id=RequestId("req_grant"),
            occurred_at=_NOW,
            subject="agent",
            project="LOCAL",
            role=ProjectRole.OPERATOR,
            expected_version=1,
        ),
    ]


def test_list_and_revoke_preserve_scope_pagination_and_removed_snapshot() -> None:
    """Grant queries and revocation retain exact Project and version scope."""
    repository = _Repository()
    application = _application(repository)

    page = application.list(
        actor=_ACTOR,
        project=_PROJECT_ID,
        cursor="cursor-1",
        limit=10,
    )
    revoked = application.revoke(
        actor=_ACTOR,
        subject=_SUBJECT_ID,
        project=_PROJECT_ID,
        expected_version=1,
        idempotency_key="grant-revoke",
    )

    assert page is repository.page
    assert revoked is repository.result
    assert repository.calls == [
        ListProjectGrants(
            actor=_ACTOR,
            project=_PROJECT_ID,
            cursor="cursor-1",
            limit=10,
        ),
        RevokeProjectGrantMutation(
            actor=_ACTOR,
            request_id=RequestId("req_grant"),
            occurred_at=_NOW,
            idempotency_key="grant-revoke",
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            expected_version=1,
        ),
    ]


@pytest.mark.parametrize("operation", ["assign", "list", "revoke"])
def test_cross_scope_or_wrong_version_output_fails_closed(operation: str) -> None:
    """Every grant output is runtime-checked against command scope."""
    repository = _Repository()
    repository.result = ProjectGrantResult(
        grant=replace(_grant(), project_id=ProjectId("prj_other"))
    )
    repository.page = ProjectGrantPage(
        grants=(replace(_grant(), project_id=ProjectId("prj_other")),),
        next_cursor=None,
    )
    application = _application(repository)

    with pytest.raises(ApplicationError) as captured:
        _invoke_grant_operation(application, operation)
    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def _invoke_grant_operation(
    application: GrantApplication,
    operation: str,
) -> None:
    """Invoke one grant output-validation path.

    Args:
        application: Configured grant application.
        operation: Closed test operation label.

    """
    if operation == "assign":
        application.assign(
            actor=_ACTOR,
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            role=ProjectRole.AGENT,
        )
        return
    if operation == "list":
        application.list(actor=_ACTOR, project=_PROJECT_ID)
        return
    if operation != "revoke":
        raise AssertionError
    application.revoke(
        actor=_ACTOR,
        subject=_SUBJECT_ID,
        project=_PROJECT_ID,
        expected_version=1,
    )


def test_invalid_input_and_stable_repository_error_behavior() -> None:
    """Invalid versions stop early and invariant failures pass through."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as invalid:
        _application(repository).revoke(
            actor=_ACTOR,
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            expected_version=0,
        )
    assert invalid.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.calls == []

    class _LastOwner(_Repository):
        """Repository enforcing the final-Owner invariant."""

        def revoke_project_grant(
            self,
            _mutation: RevokeProjectGrantMutation,
        ) -> object:
            """Raise the stable invariant error."""
            raise LastProjectOwnerError

    with pytest.raises(LastProjectOwnerError):
        _application(_LastOwner()).revoke(
            actor=_ACTOR,
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            expected_version=1,
        )


def test_constructor_runtime_validates_repository_contract() -> None:
    """Composition rejects a repository missing semantic grant methods."""
    with pytest.raises(TypeError, match="Identity"):
        GrantApplication(
            repository=cast("GrantRepository", object()),
            clock=cast("Clock", _Clock()),
            identifiers=cast("IdentityIdentifierFactory", _Identifiers()),
        )


def test_assign_and_list_reject_invalid_runtime_input() -> None:
    """Grant commands validate roles and pagination before persistence."""
    repository = _Repository()
    application = _application(repository)

    with pytest.raises(ApplicationError) as invalid_assignment:
        application.assign(
            actor=_ACTOR,
            subject=_SUBJECT_ID,
            project=_PROJECT_ID,
            role=cast("ProjectRole", "superuser"),
        )
    with pytest.raises(ApplicationError) as invalid_listing:
        application.list(actor=_ACTOR, project=_PROJECT_ID, limit=0)

    assert invalid_assignment.value.code is ApplicationErrorCode.INVALID_INPUT
    assert invalid_listing.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.calls == []


@pytest.mark.parametrize("operation", ["assign", "list", "revoke"])
def test_grant_operations_reject_malformed_repository_result(operation: str) -> None:
    """All grant operations fail closed when adapters violate result types."""
    repository = _Repository()
    repository.result = object()
    repository.page = object()

    with pytest.raises(ApplicationError) as captured:
        _invoke_grant_operation(_application(repository), operation)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_grant_listing_rejects_cross_instance_output() -> None:
    """Grant pages cannot leak rows from another Instance."""
    repository = _Repository()
    repository.page = ProjectGrantPage(
        grants=(replace(_grant(), instance_id=InstanceId("ins_other")),),
        next_cursor=None,
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).list(actor=_ACTOR, project="LOCAL")

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
