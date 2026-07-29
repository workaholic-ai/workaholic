"""Application orchestration for deterministic local bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application.commands import (
    BootstrapLocalProjectInput,
    BootstrapMutation,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import BootstrapResult

if TYPE_CHECKING:
    from workaholic.application.ports import (
        Clock,
        IdentifierFactory,
        PhaseOneRepository,
    )


class BootstrapApplication:
    """Construct validated bootstrap mutations and delegate atomic persistence."""

    def __init__(
        self,
        repository: PhaseOneRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        """Initialize explicit application dependencies.

        Args:
            repository: Semantic Phase 1 persistence boundary.
            clock: Authoritative transaction clock.
            identifiers: Candidate opaque identifier factory.

        Raises:
            TypeError: If a dependency does not expose the required interface.

        """
        _require_callable(repository, "bootstrap_local_project", "repository")
        _require_callable(clock, "now", "clock")
        for method_name in (
            "new_instance_id",
            "new_project_id",
            "new_subject_id",
            "new_request_id",
        ):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def up(self, command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Bootstrap or locate the single local Project.

        Args:
            command: Validated user-intent command.

        Returns:
            The committed local identity, authorization, and Workspace binding.

        Raises:
            ApplicationError: If input or dependency output violates its contract.

        """
        candidate_command: object = command
        if not isinstance(candidate_command, BootstrapLocalProjectInput):
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Bootstrap input is invalid.",
            )
        try:
            mutation = BootstrapMutation(
                instance_id=self._identifiers.new_instance_id(),
                project_id=self._identifiers.new_project_id(),
                subject_id=self._identifiers.new_subject_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                project_key=candidate_command.project_key,
                idempotency_key=candidate_command.idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Bootstrap dependencies returned invalid values.",
            ) from error
        result = self._repository.bootstrap_local_project(mutation)
        if not isinstance(result, BootstrapResult):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Bootstrap persistence returned an invalid result.",
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
        message = f"Bootstrap {label} must provide {member_name}()."
        raise TypeError(message)
