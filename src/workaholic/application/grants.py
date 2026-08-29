"""Backend-neutral cumulative ProjectGrant application services."""

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
    AssignProjectGrantMutation,
    ListProjectGrants,
    RevokeProjectGrantMutation,
)
from workaholic.application.results import ProjectGrantPage, ProjectGrantResult
from workaholic.domain import ProjectId, RequestId, SubjectId

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.ports import (
        Clock,
        GrantRepository,
        IdentityIdentifierFactory,
    )
    from workaholic.domain import AuthenticatedActor, ProjectRole


class GrantApplication:
    """Construct and validate Project grant administration use cases."""

    def __init__(
        self,
        repository: GrantRepository,
        clock: Clock,
        identifiers: IdentityIdentifierFactory,
    ) -> None:
        """Initialize explicit ProjectGrant-service dependencies.

        Args:
            repository: Semantic ProjectGrant persistence boundary.
            clock: Authoritative mutation clock.
            identifiers: Request identity factory.

        Raises:
            TypeError: If a dependency lacks a required operation.

        """
        for method_name in (
            "assign_project_grant",
            "list_project_grants",
            "revoke_project_grant",
        ):
            require_callable(repository, method_name, "Grant repository")
        require_callable(clock, "now", "clock")
        require_callable(identifiers, "new_request_id", "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def assign(  # noqa: PLR0913 - explicit optimistic grant contract.
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        project: ProjectId | str,
        role: ProjectRole,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> ProjectGrantResult:
        """Create or replace one cumulative Project grant.

        Args:
            actor: Authenticated Project Owner.
            subject: Target Subject ID or immutable handle.
            project: Target Project ID or immutable key.
            role: Replacement cumulative Project role.
            expected_version: Null for create or exact current replace version.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed ProjectGrant outcome.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Project grant assignment"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = AssignProjectGrantMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                subject=subject,
                project=project,
                role=role,
                expected_version=expected_version,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.assign_project_grant(mutation)
        if not _matches_assignment(result, mutation=mutation):
            raise invalid_result(operation)
        return cast("ProjectGrantResult", result)

    def list(
        self,
        *,
        actor: AuthenticatedActor,
        project: ProjectId | str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ProjectGrantPage:
        """List one stable page of grants for exactly one Project.

        Args:
            actor: Authenticated Project Owner.
            project: Target Project ID or immutable key.
            cursor: Optional opaque continuation cursor.
            limit: Positive page size capped by the command contract.

        Returns:
            Current ProjectGrant page.

        Raises:
            ApplicationError: If input or output is invalid.

        """
        operation = "Project grant listing"
        try:
            command = ListProjectGrants(
                actor=actor,
                project=project,
                cursor=cursor,
                limit=limit,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.list_project_grants(command)
        if not isinstance(result, ProjectGrantPage) or any(
            grant.instance_id != actor.instance_id for grant in result.grants
        ):
            raise invalid_result(operation)
        if isinstance(command.project, ProjectId) and any(
            grant.project_id != command.project for grant in result.grants
        ):
            raise invalid_result(operation)
        return result

    def revoke(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        project: ProjectId | str,
        expected_version: int,
        idempotency_key: str | None = None,
    ) -> ProjectGrantResult:
        """Revoke one exact current Project grant.

        Args:
            actor: Authenticated Project Owner.
            subject: Target Subject ID or immutable handle.
            project: Target Project ID or immutable key.
            expected_version: Exact current positive grant version.
            idempotency_key: Optional caller replay key.

        Returns:
            Revoked final ProjectGrant snapshot.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Project grant revocation"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = RevokeProjectGrantMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                subject=subject,
                project=project,
                expected_version=expected_version,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.revoke_project_grant(mutation)
        if not _matches_revocation(result, mutation=mutation):
            raise invalid_result(operation)
        return cast("ProjectGrantResult", result)

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


def _matches_assignment(
    value: object,
    *,
    mutation: AssignProjectGrantMutation,
) -> bool:
    """Return whether assignment output matches exact semantic input.

    Args:
        value: Candidate repository result.
        mutation: Dispatched assignment mutation.

    Returns:
        Whether fresh or replayed output is consistent.

    """
    if not isinstance(value, ProjectGrantResult):
        return False
    grant = value.grant
    expected_result_version = (
        1 if mutation.expected_version is None else mutation.expected_version + 1
    )
    return (
        grant.instance_id == mutation.actor.instance_id
        and grant.role is mutation.role
        and grant.version == expected_result_version
        and grant.granted_by == mutation.actor.subject_id
        and (
            grant.subject_id == mutation.subject
            if isinstance(mutation.subject, SubjectId)
            else True
        )
        and (
            grant.project_id == mutation.project
            if isinstance(mutation.project, ProjectId)
            else True
        )
    )


def _matches_revocation(
    value: object,
    *,
    mutation: RevokeProjectGrantMutation,
) -> bool:
    """Return whether revocation output is the exact removed snapshot.

    Args:
        value: Candidate repository result.
        mutation: Dispatched revocation mutation.

    Returns:
        Whether the removed grant matches its optimistic selector.

    """
    if not isinstance(value, ProjectGrantResult):
        return False
    grant = value.grant
    return (
        grant.instance_id == mutation.actor.instance_id
        and grant.version == mutation.expected_version
        and (
            grant.subject_id == mutation.subject
            if isinstance(mutation.subject, SubjectId)
            else True
        )
        and (
            grant.project_id == mutation.project
            if isinstance(mutation.project, ProjectId)
            else True
        )
    )
