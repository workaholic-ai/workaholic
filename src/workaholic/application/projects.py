"""Application orchestration for authorized named Project creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application.commands import (
    CreateProjectInput,
    ProjectCreationMutation,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import ProjectCreationResult

if TYPE_CHECKING:
    from workaholic.application.ports import (
        Clock,
        IdentifierFactory,
        ProjectRepository,
    )


class ProjectApplication:
    """Construct validated Project mutations and delegate atomic persistence."""

    def __init__(
        self,
        repository: ProjectRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        """Initialize explicit Project-creation dependencies.

        Args:
            repository: Semantic Project persistence boundary.
            clock: Authoritative transaction clock.
            identifiers: Candidate opaque identifier factory.

        Raises:
            TypeError: If a dependency lacks a required method.

        """
        _require_callable(repository, "create_project", "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_project_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def create(self, command: CreateProjectInput) -> ProjectCreationResult:
        """Create one named Project owned by the selected local Human.

        Args:
            command: Validated Project-creation intent.

        Returns:
            The atomically committed Project and Owner grant.

        Raises:
            ApplicationError: If input or dependency output violates its contract.

        """
        candidate_command: object = command
        if not isinstance(candidate_command, CreateProjectInput):
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Project creation input is invalid.",
            )
        try:
            mutation = ProjectCreationMutation(
                project_id=self._identifiers.new_project_id(),
                request_id=self._identifiers.new_request_id(),
                instance_id=candidate_command.instance_id,
                actor_subject_id=candidate_command.subject_id,
                occurred_at=self._clock.now(),
                project_key=candidate_command.project_key,
                project_name=candidate_command.project_name,
                idempotency_key=candidate_command.idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Project creation dependencies returned invalid values.",
            ) from error
        result = self._repository.create_project(mutation)
        if not isinstance(result, ProjectCreationResult):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Project persistence returned an invalid result.",
            )
        return result


def _require_callable(value: object, member_name: str, label: str) -> None:
    """Require one explicit dependency method.

    Args:
        value: Candidate dependency.
        member_name: Required callable attribute.
        label: Safe Human-readable dependency name.

    Raises:
        TypeError: If the dependency does not expose the required method.

    """
    if not callable(getattr(value, member_name, None)):
        message = f"Project {label} must provide {member_name}()."
        raise TypeError(message)
