"""Application orchestration for optimistic Task dependency mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from workaholic.application.commands import (
    AddTaskDependencyInput,
    AddTaskDependencyMutation,
    GetTask,
    RemoveTaskDependencyInput,
    RemoveTaskDependencyMutation,
)
from workaholic.application.errors import ApplicationError, ApplicationErrorCode
from workaholic.application.results import TaskMutationResult
from workaholic.domain import Task, TaskEventType, TaskId, TaskState

if TYPE_CHECKING:
    from workaholic.application.ports import Clock, IdentifierFactory

type _DependencyInput = AddTaskDependencyInput | RemoveTaskDependencyInput
type _DependencyMutation = AddTaskDependencyMutation | RemoveTaskDependencyMutation


class _DependencyRepository(Protocol):
    """Minimal semantic persistence surface for dependency changes."""

    def get_task(self, command: GetTask) -> Task:
        """Resolve one authorized scoped Task."""
        ...

    def add_task_dependency(
        self,
        mutation: AddTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Persist one optimistic dependency addition."""
        ...

    def remove_task_dependency(
        self,
        mutation: RemoveTaskDependencyMutation,
    ) -> TaskMutationResult:
        """Persist one optimistic dependency removal."""
        ...


class TaskDependencyApplication:
    """Resolve selectors and construct attributable dependency mutations."""

    def __init__(
        self,
        repository: _DependencyRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
    ) -> None:
        """Initialize explicit dependency-operation collaborators.

        Args:
            repository: Semantic Task query and dependency mutation boundary.
            clock: Authoritative operation clock.
            identifiers: Opaque request and event identity factory.

        Raises:
            TypeError: If a collaborator lacks a required operation.

        """
        for method_name in (
            "get_task",
            "add_task_dependency",
            "remove_task_dependency",
        ):
            _require_callable(repository, method_name, "repository")
        _require_callable(clock, "now", "clock")
        for method_name in ("new_event_id", "new_request_id"):
            _require_callable(identifiers, method_name, "identifier factory")
        self._repository = repository
        self._clock = clock
        self._identifiers = identifiers

    def add(self, command: AddTaskDependencyInput) -> TaskMutationResult:
        """Add one same-Project prerequisite at an expected Task version.

        Args:
            command: Validated Human dependency-addition intent.

        Returns:
            Committed dependant Task and its attributable update event.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        candidate: object = command
        if type(candidate) is not AddTaskDependencyInput:
            operation = "addition"
            raise _invalid_input(operation)
        return self._execute(candidate, add=True)

    def remove(self, command: RemoveTaskDependencyInput) -> TaskMutationResult:
        """Remove one prerequisite at an expected dependant Task version.

        Args:
            command: Validated Human dependency-removal intent.

        Returns:
            Committed dependant Task and its attributable update event.

        Raises:
            ApplicationError: If input, dependencies, or output violate contracts.

        """
        candidate: object = command
        if type(candidate) is not RemoveTaskDependencyInput:
            operation = "removal"
            raise _invalid_input(operation)
        return self._execute(candidate, add=False)

    def _execute(
        self,
        command: _DependencyInput,
        *,
        add: bool,
    ) -> TaskMutationResult:
        """Build, dispatch, and validate one dependency mutation.

        Args:
            command: Validated dependency intent.
            add: Whether the edge is being added rather than removed.

        Returns:
            Validated semantic mutation result.

        Raises:
            ApplicationError: If generated values or persistence output are invalid.

        """
        operation = "addition" if add else "removal"
        task_uid = self._resolve_task_uid(command.task, command=command)
        prerequisite_uid = self._resolve_task_uid(
            command.prerequisite,
            command=command,
        )
        try:
            event_id = self._identifiers.new_event_id()
            request_id = self._identifiers.new_request_id()
            occurred_at = self._clock.now()
            if add:
                add_mutation = AddTaskDependencyMutation(
                    task_uid=task_uid,
                    prerequisite_uid=prerequisite_uid,
                    project_id=command.project_id,
                    actor_subject_id=command.subject_id,
                    event_id=event_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                    expected_version=command.expected_version,
                    idempotency_key=command.idempotency_key,
                )
                result: object = self._repository.add_task_dependency(add_mutation)
                mutation: _DependencyMutation = add_mutation
            else:
                remove_mutation = RemoveTaskDependencyMutation(
                    task_uid=task_uid,
                    prerequisite_uid=prerequisite_uid,
                    project_id=command.project_id,
                    actor_subject_id=command.subject_id,
                    event_id=event_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                    expected_version=command.expected_version,
                    idempotency_key=command.idempotency_key,
                )
                result = self._repository.remove_task_dependency(remove_mutation)
                mutation = remove_mutation
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                f"Task dependency {operation} dependencies returned invalid values.",
            ) from error
        if not isinstance(result, TaskMutationResult) or not _matches_result(
            result,
            mutation=mutation,
            add=add,
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                f"Task dependency {operation} persistence returned an invalid result.",
            )
        return result

    def _resolve_task_uid(
        self,
        selector: TaskId | str,
        *,
        command: _DependencyInput,
    ) -> TaskId:
        """Resolve one Human key without reading canonical Task identities.

        Args:
            selector: Canonical Task identity or stable Human key.
            command: Owning same-Project operation intent.

        Returns:
            Canonical Task identity.

        Raises:
            ApplicationError: If a lookup returns an inconsistent Task.

        """
        if isinstance(selector, TaskId):
            return selector
        result: object = self._repository.get_task(
            GetTask(
                project_id=command.project_id,
                subject_id=command.subject_id,
                task=selector,
            )
        )
        if (
            not isinstance(result, Task)
            or result.project_id != command.project_id
            or result.key != selector
        ):
            raise ApplicationError(
                ApplicationErrorCode.INTERNAL_ERROR,
                "Task dependency resolution returned an invalid result.",
            )
        return result.uid


def _matches_result(
    result: TaskMutationResult,
    *,
    mutation: _DependencyMutation,
    add: bool,
) -> bool:
    """Return whether persistence honored one dependency mutation contract.

    Args:
        result: Candidate semantic result.
        mutation: Dispatched dependency mutation.
        add: Whether an edge addition was requested.

    Returns:
        Whether identities, graph, version, attribution, and payload agree.

    """
    task = result.task
    event = result.events[0]
    contains = mutation.prerequisite_uid in task.depends_on
    expected_payload = {
        "dependency": "added" if add else "removed",
        "prerequisite_uid": str(mutation.prerequisite_uid),
        "version": task.version,
    }
    return (
        task.uid == mutation.task_uid
        and task.project_id == mutation.project_id
        and task.version == mutation.expected_version + 1
        and task.state in (TaskState.OPEN, TaskState.BLOCKED)
        and contains is add
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
        and dict(event.payload) == expected_payload
    )


def _invalid_input(operation: str) -> ApplicationError:
    """Build a safe invalid dependency-input error.

    Args:
        operation: Safe operation label.

    Returns:
        Stable application error.

    """
    return ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        f"Task dependency {operation} input is invalid.",
    )


def _require_callable(value: object, member_name: str, label: str) -> None:
    """Require one explicit collaborator method.

    Args:
        value: Candidate collaborator.
        member_name: Required callable member.
        label: Safe collaborator label.

    Raises:
        TypeError: If the callable is unavailable.

    """
    if not callable(getattr(value, member_name, None)):
        message = f"Task dependency {label} must provide {member_name}()."
        raise TypeError(message)
