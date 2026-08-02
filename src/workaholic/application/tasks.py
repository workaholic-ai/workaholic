"""Application orchestration for attributable Task creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workaholic.application.commands import CreateTaskInput, TaskCreationMutation
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.domain import Task, TaskState

if TYPE_CHECKING:
    from workaholic.application.ports import (
        Clock,
        IdentifierFactory,
        TaskCreationRepository,
    )


class TaskApplication:
    """Construct validated Task mutations and delegate atomic persistence."""

    def __init__(
        self,
        repository: TaskCreationRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        """Initialize explicit application dependencies.

        Args:
            repository: Semantic Task persistence boundary.
            clock: Authoritative transaction clock.
            identifiers: Candidate opaque identifier factory.

        Raises:
            TypeError: If a dependency does not expose the required interface.

        """
        _require_callable(repository, "create_task", "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_task_id", "new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def create(self, command: CreateTaskInput) -> Task:
        """Create one initial Task with attributable mutation identities.

        Args:
            command: Validated user-intent command.

        Returns:
            The atomically committed Task.

        Raises:
            ApplicationError: If input or dependency output violates its contract.

        """
        candidate_command: object = command
        if not isinstance(candidate_command, CreateTaskInput):
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task creation input is invalid.",
            )
        try:
            mutation = TaskCreationMutation(
                task_id=self._identifiers.new_task_id(),
                event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                project_id=candidate_command.project_id,
                actor_subject_id=candidate_command.subject_id,
                occurred_at=self._clock.now(),
                title=candidate_command.title,
                objective=candidate_command.objective,
                priority=candidate_command.priority,
                available_at=candidate_command.available_at,
                approval=candidate_command.approval,
                acceptance=candidate_command.acceptance,
                context=candidate_command.context,
                idempotency_key=candidate_command.idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task creation dependencies returned invalid values.",
            ) from error
        result: object = self._repository.create_task(mutation)
        if not isinstance(result, Task) or not _is_matching_initial_task(
            result,
            mutation=mutation,
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task persistence returned an invalid result.",
            )
        return result


def _is_matching_initial_task(task: Task, *, mutation: TaskCreationMutation) -> bool:
    """Return whether a persisted Task matches creation or replay semantics.

    Generated identities and timestamps may differ on idempotent replay, so this
    check binds only semantic caller input and invariant initial Task state.

    Args:
        task: Candidate repository result.
        mutation: Validated creation mutation sent to persistence.

    Returns:
        Whether the result is a valid initial snapshot for the mutation.

    """
    return (
        task.project_id == mutation.project_id
        and task.created_by == mutation.actor_subject_id
        and task.title == mutation.title
        and task.objective == mutation.objective
        and task.priority == mutation.priority
        and task.available_at == mutation.available_at
        and task.approval is mutation.approval
        and task.acceptance == mutation.acceptance
        and task.context == mutation.context
        and task.state is TaskState.OPEN
        and task.version == 1
        and task.depends_on == ()
        and task.blocking_reason is None
        and task.current_result_id is None
        and task.created_at == task.updated_at
    )


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
        message = f"Task {label} must provide {member_name}()."
        raise TypeError(message)
