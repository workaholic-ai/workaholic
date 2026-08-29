"""Backend-neutral Subject and Instance-administrator application services."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from workaholic.application._identity_support import (
    dependency_request_id,
    dependency_time,
    invalid_dependencies,
    invalid_input,
    invalid_result,
    require_callable,
)
from workaholic.application.commands import (
    CreateSubjectMutation,
    ListSubjects,
    SetInstanceAdminMutation,
    SetSubjectEnabledMutation,
    UpdateSubjectMutation,
)
from workaholic.application.results import SubjectPage, SubjectResult
from workaholic.domain import RequestId, SubjectId

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application.ports import (
        Clock,
        IdentityIdentifierFactory,
        SubjectRepository,
    )
    from workaholic.domain import AuthenticatedActor, SubjectKind


class SubjectApplication:
    """Construct and validate the complete versioned Subject lifecycle."""

    def __init__(
        self,
        repository: SubjectRepository,
        clock: Clock,
        identifiers: IdentityIdentifierFactory,
    ) -> None:
        """Initialize explicit Subject-service dependencies.

        Args:
            repository: Semantic Subject persistence boundary.
            clock: Authoritative mutation clock.
            identifiers: Subject and request identity factory.

        Raises:
            TypeError: If a dependency lacks a required operation.

        """
        for method_name in (
            "create_subject",
            "list_subjects",
            "update_subject",
            "set_subject_enabled",
            "set_instance_admin",
        ):
            require_callable(repository, method_name, "Subject repository")
        require_callable(clock, "now", "clock")
        for method_name in ("new_subject_id", "new_request_id"):
            require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def create(
        self,
        *,
        actor: AuthenticatedActor,
        kind: SubjectKind,
        handle: str,
        display_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> SubjectResult:
        """Create one enabled non-administrative Human or Agent Subject.

        Args:
            actor: Authenticated Instance administrator.
            kind: Immutable Human or Agent kind.
            handle: Immutable Instance-scoped handle.
            display_name: Optional mutable display name; null defaults to handle.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed non-secret Subject outcome.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Subject creation"
        try:
            subject_id = self._identifiers.new_subject_id()
        except Exception as error:
            raise invalid_dependencies(operation) from error
        if not isinstance(subject_id, SubjectId):
            raise invalid_dependencies(operation)
        metadata = self._mutation_metadata(operation)
        try:
            mutation = CreateSubjectMutation(
                actor=actor,
                request_id=metadata[0],
                occurred_at=metadata[1],
                idempotency_key=idempotency_key,
                subject_id=subject_id,
                kind=kind,
                handle=handle,
                display_name=handle if display_name is None else display_name,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.create_subject(mutation)
        if not _matches_created_subject(result, mutation=mutation):
            raise invalid_result(operation)
        return cast("SubjectResult", result)

    def list(
        self,
        *,
        actor: AuthenticatedActor,
        cursor: str | None = None,
        limit: int = 100,
    ) -> SubjectPage:
        """List one stable handle-ordered Subject page.

        Args:
            actor: Authenticated Instance administrator.
            cursor: Optional opaque continuation cursor.
            limit: Positive page size capped by the command contract.

        Returns:
            Current Subject page.

        Raises:
            ApplicationError: If input or output is invalid.

        """
        operation = "Subject listing"
        try:
            command = ListSubjects(actor=actor, cursor=cursor, limit=limit)
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.list_subjects(command)
        if not isinstance(result, SubjectPage) or any(
            subject.instance_id != actor.instance_id for subject in result.subjects
        ):
            raise invalid_result(operation)
        return result

    def update(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        expected_version: int,
        display_name: str,
        idempotency_key: str | None = None,
    ) -> SubjectResult:
        """Update one Subject display name at its exact version.

        Args:
            actor: Authenticated Instance administrator.
            subject: Exact Subject ID or immutable handle.
            expected_version: Exact current positive version.
            display_name: Replacement normalized display name.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed Subject outcome.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Subject update"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = UpdateSubjectMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                subject=subject,
                expected_version=expected_version,
                display_name=display_name,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.update_subject(mutation)
        if not _matches_existing_subject(
            result,
            selector=mutation.subject,
            expected_version=mutation.expected_version,
            display_name=mutation.display_name,
        ):
            raise invalid_result(operation)
        return cast("SubjectResult", result)

    def set_enabled(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        expected_version: int,
        enabled: bool,
        idempotency_key: str | None = None,
    ) -> SubjectResult:
        """Enable or disable one Subject at its exact version.

        Args:
            actor: Authenticated Instance administrator.
            subject: Exact Subject ID or immutable handle.
            expected_version: Exact current positive version.
            enabled: Requested enabled state.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed Subject outcome.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Subject enabled-state update"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = SetSubjectEnabledMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                subject=subject,
                expected_version=expected_version,
                enabled=enabled,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.set_subject_enabled(mutation)
        if not _matches_existing_subject(
            result,
            selector=mutation.subject,
            expected_version=mutation.expected_version,
            enabled=mutation.enabled,
        ):
            raise invalid_result(operation)
        return cast("SubjectResult", result)

    def set_instance_admin(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        expected_version: int,
        is_instance_admin: bool,
        idempotency_key: str | None = None,
    ) -> SubjectResult:
        """Grant or revoke Instance administration at an exact version.

        Args:
            actor: Authenticated Instance administrator.
            subject: Exact Subject ID or immutable handle.
            expected_version: Exact current positive version.
            is_instance_admin: Requested administrator state.
            idempotency_key: Optional caller replay key.

        Returns:
            Committed Subject outcome.

        Raises:
            ApplicationError: If input, dependencies, or output are invalid.

        """
        operation = "Instance administrator update"
        request_id, occurred_at = self._mutation_metadata(operation)
        try:
            mutation = SetInstanceAdminMutation(
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                idempotency_key=idempotency_key,
                subject=subject,
                expected_version=expected_version,
                is_instance_admin=is_instance_admin,
            )
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.set_instance_admin(mutation)
        if not _matches_existing_subject(
            result,
            selector=mutation.subject,
            expected_version=mutation.expected_version,
            is_instance_admin=mutation.is_instance_admin,
        ):
            raise invalid_result(operation)
        return cast("SubjectResult", result)

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


def _matches_created_subject(
    value: object,
    *,
    mutation: CreateSubjectMutation,
) -> bool:
    """Return whether a create result preserves semantic caller intent.

    Args:
        value: Candidate repository result.
        mutation: Dispatched validated creation mutation.

    Returns:
        Whether fresh or idempotently replayed output is consistent.

    """
    if not isinstance(value, SubjectResult):
        return False
    subject = value.subject
    return (
        subject.instance_id == mutation.actor.instance_id
        and subject.kind is mutation.kind
        and subject.handle == mutation.handle
        and subject.display_name == mutation.display_name
        and subject.enabled
        and not subject.is_instance_admin
        and subject.version == 1
        and subject.created_by == mutation.actor.subject_id
    )


def _matches_existing_subject(  # noqa: PLR0913 - closed validation contract.
    value: object,
    *,
    selector: SubjectId | str,
    expected_version: int,
    display_name: str | None = None,
    enabled: bool | None = None,
    is_instance_admin: bool | None = None,
) -> bool:
    """Return whether an optimistic Subject result matches its mutation.

    Args:
        value: Candidate repository result.
        selector: Requested Subject ID or handle.
        expected_version: Mutation's exact prior version.
        display_name: Optional requested display name.
        enabled: Optional requested enabled state.
        is_instance_admin: Optional requested administrator state.

    Returns:
        Whether the result is the exact expected next projection.

    """
    if not isinstance(value, SubjectResult):
        return False
    subject = value.subject
    return (
        subject.version == expected_version + 1
        and (
            subject.id == selector
            if isinstance(selector, SubjectId)
            else subject.handle == selector
        )
        and (display_name is None or subject.display_name == display_name)
        and (enabled is None or subject.enabled is enabled)
        and (
            is_instance_admin is None or subject.is_instance_admin is is_instance_admin
        )
    )
