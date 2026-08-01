"""Application orchestration for optimistic Human Task lifecycle mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from workaholic.application.commands import (
    GetTask,
    TaskUpdateMutation,
    UpdateTaskInput,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import TaskMutationResult
from workaholic.domain import Task, TaskEventType, TaskId, TaskState

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, IdentifierFactory


class _LifecycleRepository(Protocol):
    """Minimal semantic persistence surface needed by Task field updates."""

    def get_task(self, command: GetTask) -> Task:
        """Resolve one authorized scoped Task."""
        ...

    def update_task_if_version(
        self,
        mutation: TaskUpdateMutation,
    ) -> TaskMutationResult:
        """Persist one optimistic Task definition update."""
        ...


class TaskLifecycleApplication:
    """Construct attributable optimistic mutations and validate their results."""

    def __init__(
        self,
        repository: _LifecycleRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        """Initialize explicit lifecycle dependencies.

        Args:
            repository: Semantic Task query and mutation boundary.
            clock: Authoritative transaction clock.
            identifiers: Opaque request and TaskEvent identity factory.

        Raises:
            TypeError: If a dependency lacks a required method.

        """
        for method_name in ("get_task", "update_task_if_version"):
            _require_callable(repository, method_name, "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def update(self, command: UpdateTaskInput) -> TaskMutationResult:
        """Update editable Task definition fields at an expected version.

        Args:
            command: Validated Human update intent.

        Returns:
            Committed Task snapshot and its single attributable update event.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        candidate: object = command
        if not isinstance(candidate, UpdateTaskInput):
            raise ApplicationError(
                ApplicationErrorCode.INVALID_INPUT,
                "Task update input is invalid.",
            )
        task_uid = self._resolve_task_uid(candidate)
        try:
            mutation = TaskUpdateMutation(
                task_uid=task_uid,
                project_id=candidate.project_id,
                actor_subject_id=candidate.subject_id,
                event_id=self._identifiers.new_event_id(),
                request_id=self._identifiers.new_request_id(),
                occurred_at=self._clock.now(),
                expected_version=candidate.expected_version,
                patch=candidate.patch,
                idempotency_key=candidate.idempotency_key,
            )
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task update dependencies returned invalid values.",
            ) from error
        result: object = self._repository.update_task_if_version(mutation)
        if not isinstance(result, TaskMutationResult) or not _matches_update(
            result,
            mutation=mutation,
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task update persistence returned an invalid result.",
            )
        return result

    def _resolve_task_uid(self, command: UpdateTaskInput) -> TaskId:
        """Resolve a Human key only when the caller did not supply a TaskId.

        Args:
            command: Validated scoped update intent.

        Returns:
            Canonical Task identity for the persistence mutation.

        Raises:
            ApplicationError: If persistence returns an inconsistent Task.

        """
        if isinstance(command.task, TaskId):
            return command.task
        result: object = self._repository.get_task(
            GetTask(
                project_id=command.project_id,
                subject_id=command.subject_id,
                task=command.task,
            )
        )
        if (
            not isinstance(result, Task)
            or result.project_id != command.project_id
            or result.key != command.task
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task update resolution returned an invalid result.",
            )
        return result.uid


def _matches_update(
    result: TaskMutationResult,
    *,
    mutation: TaskUpdateMutation,
) -> bool:
    """Return whether one result satisfies the optimistic update contract.

    Args:
        result: Candidate persistence result.
        mutation: Update mutation sent to persistence.

    Returns:
        Whether identities, version, patch, and event semantics match.

    """
    task = result.task
    event = result.events[0]
    patch_fields = mutation.patch.model_fields_set
    return (
        task.uid == mutation.task_uid
        and task.project_id == mutation.project_id
        and task.version == mutation.expected_version + 1
        and task.state in (TaskState.OPEN, TaskState.BLOCKED)
        and all(
            getattr(task, field_name) == getattr(mutation.patch, field_name)
            for field_name in patch_fields
        )
        and event.event_type is TaskEventType.TASK_UPDATED
        and event.actor_subject_id == mutation.actor_subject_id
        and event.occurred_at == task.updated_at
        and (
            mutation.idempotency_key is not None
            or (
                task.updated_at == mutation.occurred_at
                and event.id == mutation.event_id
                and event.request_id == mutation.request_id
            )
        )
        and dict(event.payload)
        == {
            "changes": tuple(sorted(patch_fields)),
            "version": task.version,
        }
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
        message = f"Task lifecycle {label} must provide {member_name}()."
        raise TypeError(message)
