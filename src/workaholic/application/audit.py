"""Backend-neutral administrative audit-query application service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application._identity_support import (
    invalid_input,
    invalid_result,
    require_callable,
)
from workaholic.application.commands import ReadAuditEvents
from workaholic.application.results import AuditEventPage

if TYPE_CHECKING:
    from workaholic.application.ports import AuditRepository
    from workaholic.domain import AuthenticatedActor


class AuditApplication:
    """Read bounded append-only administrative audit history."""

    def __init__(self, repository: AuditRepository) -> None:
        """Initialize the explicit audit repository dependency.

        Args:
            repository: Authorized append-only audit query boundary.

        Raises:
            TypeError: If the dependency lacks the required operation.

        """
        require_callable(repository, "read_audit_events", "Audit repository")
        self._repository = repository

    def read(
        self,
        *,
        actor: AuthenticatedActor,
        after: int = 0,
        limit: int = 100,
    ) -> AuditEventPage:
        """Read one polling-safe ascending Instance audit page.

        Args:
            actor: Authenticated Instance administrator.
            after: Exclusive nonnegative audit cursor.
            limit: Positive page size capped by the command contract.

        Returns:
            Ascending audit page and greatest observed cursor.

        Raises:
            ApplicationError: If input or output is invalid.

        """
        operation = "Audit event read"
        try:
            command = ReadAuditEvents(actor=actor, after=after, limit=limit)
        except (TypeError, ValueError) as error:
            raise invalid_input(operation) from error
        result: object = self._repository.read_audit_events(command)
        if (
            not isinstance(result, AuditEventPage)
            or result.next_cursor < command.after
            or any(event.instance_id != actor.instance_id for event in result.events)
        ):
            raise invalid_result(operation)
        return result
