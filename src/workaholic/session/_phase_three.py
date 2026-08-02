"""Transport-neutral Phase 3 operations over one resolved local scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, Protocol

from workaholic.application import (
    AddTaskDependencyInput,
    ApplicationError,
    ApplicationErrorCode,
    ApproveResultInput,
    BlockTaskInput,
    CancelTaskInput,
    GetTaskDetails,
    ListTasksByView,
    ReadTaskEvents,
    RejectResultInput,
    RemoveTaskDependencyInput,
    SubmitHumanResultInput,
    TaskDetails,
    TaskEventPage,
    TaskMutationResult,
    TaskPage,
    TaskSubmissionResult,
    UnblockTaskInput,
    UpdateTaskInput,
)
from workaholic.domain import (
    InstanceId,
    Project,
    SubjectId,
    Task,
    TaskId,
    validate_profile_name,
)
from workaholic.session.models import (
    TaskAddDependencyRequest,
    TaskApproveRequest,
    TaskBlockRequest,
    TaskCancelRequest,
    TaskDetailsRequest,
    TaskEventsRequest,
    TaskListByViewRequest,
    TaskRejectRequest,
    TaskRemoveDependencyRequest,
    TaskSubmitRequest,
    TaskUnblockRequest,
    TaskUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class PhaseThreeQueryService(Protocol):
    """Application query capabilities consumed by Phase 3 Sessions."""

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Return complete Task details for one selected Project."""
        ...

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Return one selected readiness or lifecycle view."""
        ...

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Return one bounded attributable TaskEvent page."""
        ...


class PhaseThreeLifecycleService(Protocol):
    """Application lifecycle mutation capabilities consumed by Sessions."""

    def update(self, command: UpdateTaskInput) -> TaskMutationResult:
        """Update editable Task fields at an expected version."""
        ...

    def block(self, command: BlockTaskInput) -> TaskMutationResult:
        """Block one open Task at an expected version."""
        ...

    def unblock(self, command: UnblockTaskInput) -> TaskMutationResult:
        """Unblock one blocked Task at an expected version."""
        ...

    def cancel(self, command: CancelTaskInput) -> TaskMutationResult:
        """Cancel one mutable Task at an expected version."""
        ...


class PhaseThreeDependencyService(Protocol):
    """Application dependency mutation capabilities consumed by Sessions."""

    def add(self, command: AddTaskDependencyInput) -> TaskMutationResult:
        """Add one same-Project prerequisite."""
        ...

    def remove(self, command: RemoveTaskDependencyInput) -> TaskMutationResult:
        """Remove one same-Project prerequisite."""
        ...


class PhaseThreeResultService(Protocol):
    """Application Human Result capabilities consumed by Sessions."""

    def submit(self, command: SubmitHumanResultInput) -> TaskSubmissionResult:
        """Submit direct Human work without an Attempt."""
        ...

    def approve(self, command: ApproveResultInput) -> TaskSubmissionResult:
        """Approve the current pending Result."""
        ...

    def reject(self, command: RejectResultInput) -> TaskSubmissionResult:
        """Reject the current pending Result."""
        ...


@dataclass(frozen=True, slots=True)
class PhaseThreeScope:
    """One request's authoritative profile, actor, and optional Project scope."""

    profile: str
    instance_id: InstanceId
    subject_id: SubjectId
    project: Project | None
    queries: PhaseThreeQueryService
    lifecycle: PhaseThreeLifecycleService
    dependencies: PhaseThreeDependencyService
    results: PhaseThreeResultService

    def __post_init__(self) -> None:
        """Validate the complete trusted scope and capability surface."""
        try:
            profile = validate_profile_name(self.profile)
        except ValueError as error:
            message = "Phase 3 Session scope profile is invalid."
            raise TypeError(message) from error
        instance_value: object = self.instance_id
        subject_value: object = self.subject_id
        if not isinstance(instance_value, InstanceId) or not isinstance(
            subject_value,
            SubjectId,
        ):
            message = "Phase 3 Session scope requires typed local identities."
            raise TypeError(message)
        if self.project is not None and self.project.instance_id != self.instance_id:
            message = "Phase 3 Session Project must belong to its Instance."
            raise ValueError(message)
        for service, methods, label in (
            (
                self.queries,
                (
                    "get_task_details",
                    "list_tasks_by_view",
                    "read_task_events_after",
                ),
                "query service",
            ),
            (
                self.lifecycle,
                ("update", "block", "unblock", "cancel"),
                "lifecycle service",
            ),
            (
                self.dependencies,
                ("add", "remove"),
                "dependency service",
            ),
            (
                self.results,
                ("submit", "approve", "reject"),
                "Result service",
            ),
        ):
            for method_name in methods:
                _require_callable(service, method_name, label)
        object.__setattr__(self, "profile", profile)


@dataclass(frozen=True, slots=True)
class PhaseThreeProjectScope(PhaseThreeScope):
    """One Phase 3 scope with a required authorized Project."""

    project: Project


class PhaseThreeScopeResolver(Protocol):
    """Resolve one request into a trusted local Phase 3 scope."""

    def __call__(
        self,
        *,
        project: str | None,
        all_projects: bool,
    ) -> PhaseThreeScope:
        """Resolve profile, identity, authorization, and semantic services."""
        ...


class LocalTaskOperations:
    """Implement all Phase 3 Session operations over resolved application ports."""

    def __init__(self, resolve_scope: PhaseThreeScopeResolver) -> None:
        """Initialize one explicit trusted-scope resolver.

        Args:
            resolve_scope: Per-request profile, actor, and Project resolver.

        Raises:
            TypeError: If the resolver is not callable.

        """
        resolver_value: object = resolve_scope
        if not callable(resolver_value):
            message = "Phase 3 Session scope resolver must be callable."
            raise TypeError(message)
        self._resolve_scope = resolve_scope

    def update_task(self, request: TaskUpdateRequest) -> TaskMutationResult:
        """Update editable Task fields through one authorized scope."""
        candidate = _require_request(request, TaskUpdateRequest, "Task update")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: UpdateTaskInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                patch=candidate.patch,
            ),
            "Task update",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_mutation_result(
            scope.lifecycle.update(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task update",
        )

    def block_task(self, request: TaskBlockRequest) -> TaskMutationResult:
        """Block one Task through one authorized scope."""
        candidate = _require_request(request, TaskBlockRequest, "Task block")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: BlockTaskInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                reason=candidate.reason,
            ),
            "Task block",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_mutation_result(
            scope.lifecycle.block(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task block",
        )

    def unblock_task(self, request: TaskUnblockRequest) -> TaskMutationResult:
        """Unblock one Task through one authorized scope."""
        candidate = _require_request(request, TaskUnblockRequest, "Task unblock")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: UnblockTaskInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
            ),
            "Task unblock",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_mutation_result(
            scope.lifecycle.unblock(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task unblock",
        )

    def cancel_task(self, request: TaskCancelRequest) -> TaskMutationResult:
        """Cancel one Task through one authorized scope."""
        candidate = _require_request(request, TaskCancelRequest, "Task cancel")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: CancelTaskInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                reason=candidate.reason,
            ),
            "Task cancel",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_mutation_result(
            scope.lifecycle.cancel(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task cancel",
        )

    def add_task_dependency(
        self,
        request: TaskAddDependencyRequest,
    ) -> TaskMutationResult:
        """Add one same-Project prerequisite through an authorized scope."""
        candidate = _require_request(
            request,
            TaskAddDependencyRequest,
            "Task dependency addition",
        )
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: AddTaskDependencyInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                prerequisite=candidate.prerequisite,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
            ),
            "Task dependency addition",
        )
        _require_task_key_scope(command.task, scope.project)
        _require_task_key_scope(command.prerequisite, scope.project)
        return _require_mutation_result(
            scope.dependencies.add(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task dependency addition",
        )

    def remove_task_dependency(
        self,
        request: TaskRemoveDependencyRequest,
    ) -> TaskMutationResult:
        """Remove one same-Project prerequisite through an authorized scope."""
        candidate = _require_request(
            request,
            TaskRemoveDependencyRequest,
            "Task dependency removal",
        )
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: RemoveTaskDependencyInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                prerequisite=candidate.prerequisite,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
            ),
            "Task dependency removal",
        )
        _require_task_key_scope(command.task, scope.project)
        _require_task_key_scope(command.prerequisite, scope.project)
        return _require_mutation_result(
            scope.dependencies.remove(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task dependency removal",
        )

    def submit_human_result(
        self,
        request: TaskSubmitRequest,
    ) -> TaskSubmissionResult:
        """Submit direct Human work through an authorized scope."""
        candidate = _require_request(request, TaskSubmitRequest, "Task submission")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: SubmitHumanResultInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                comment=candidate.comment,
                result=candidate.result,
            ),
            "Task submission",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_submission_result(
            scope.results.submit(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task submission",
        )

    def approve_result(self, request: TaskApproveRequest) -> TaskSubmissionResult:
        """Approve one pending Result through an authorized scope."""
        candidate = _require_request(request, TaskApproveRequest, "Task approval")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: ApproveResultInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                comment=candidate.comment,
            ),
            "Task approval",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_submission_result(
            scope.results.approve(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task approval",
        )

    def reject_result(self, request: TaskRejectRequest) -> TaskSubmissionResult:
        """Reject one pending Result through an authorized scope."""
        candidate = _require_request(request, TaskRejectRequest, "Task rejection")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: RejectResultInput(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                expected_version=candidate.expected_version,
                idempotency_key=candidate.idempotency_key,
                reason=candidate.reason,
            ),
            "Task rejection",
        )
        _require_task_key_scope(command.task, scope.project)
        return _require_submission_result(
            scope.results.reject(command),
            scope=scope,
            selector=command.task,
            expected_version=command.expected_version,
            label="Task rejection",
        )

    def get_task_details(self, request: TaskDetailsRequest) -> TaskDetails:
        """Return complete Task details through an authorized scope."""
        candidate = _require_request(request, TaskDetailsRequest, "Task details")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: GetTaskDetails(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
            ),
            "Task details",
        )
        _require_task_key_scope(command.task, scope.project)
        result: object = scope.queries.get_task_details(command)
        if not isinstance(result, TaskDetails) or not _task_result_matches(
            result.task,
            scope=scope,
            selector=command.task,
        ):
            _raise_internal("Task details")
        return result

    def list_tasks_by_view(self, request: TaskListByViewRequest) -> TaskPage:
        """Return one Project- or Instance-scoped Task view."""
        candidate = _require_request(
            request,
            TaskListByViewRequest,
            "Task view",
        )
        scope = self._resolve_scope(
            project=candidate.project,
            all_projects=candidate.all_projects,
        )
        command = _build_input(
            lambda: ListTasksByView(
                profile=scope.profile,
                subject_id=scope.subject_id,
                project_id=None if scope.project is None else scope.project.id,
                instance_id=scope.instance_id if scope.project is None else None,
                view=candidate.view,
                cursor=candidate.cursor,
                limit=candidate.limit,
            ),
            "Task view",
        )
        result: object = scope.queries.list_tasks_by_view(command)
        if (
            not isinstance(result, TaskPage)
            or result.view is not command.view
            or len(result.readiness) != len(result.tasks)
            or (
                scope.project is not None
                and any(task.project_id != scope.project.id for task in result.tasks)
            )
        ):
            _raise_internal("Task view")
        return result

    def read_task_events(self, request: TaskEventsRequest) -> TaskEventPage:
        """Return one bounded attributable TaskEvent history page."""
        candidate = _require_request(request, TaskEventsRequest, "Task events")
        scope = self._project_scope(candidate.project)
        command = _build_input(
            lambda: ReadTaskEvents(
                project_id=scope.project.id,
                subject_id=scope.subject_id,
                task=candidate.task,
                after=candidate.after,
                limit=candidate.limit,
            ),
            "Task events",
        )
        _require_task_key_scope(command.task, scope.project)
        result: object = scope.queries.read_task_events_after(command)
        if (
            not isinstance(result, TaskEventPage)
            or len(result.events) > command.limit
            or (not result.events and result.next_cursor != command.after)
            or (
                result.events
                and (
                    result.events[0].cursor <= command.after
                    or any(
                        event.project_id != scope.project.id
                        or (
                            isinstance(command.task, TaskId)
                            and event.task_uid != command.task
                        )
                        for event in result.events
                    )
                )
            )
        ):
            _raise_internal("Task events")
        return result

    def _project_scope(self, project: str | None) -> PhaseThreeProjectScope:
        """Resolve and require one exact authorized Project scope."""
        scope = self._resolve_scope(project=project, all_projects=False)
        if scope.project is None:
            _raise_internal("Phase 3 Project scope")
        return PhaseThreeProjectScope(
            profile=scope.profile,
            instance_id=scope.instance_id,
            subject_id=scope.subject_id,
            project=scope.project,
            queries=scope.queries,
            lifecycle=scope.lifecycle,
            dependencies=scope.dependencies,
            results=scope.results,
        )


def _require_request[T](
    value: object,
    expected_type: type[T],
    label: str,
) -> T:
    """Require one exact runtime Session request type.

    Args:
        value: Candidate direct caller value.
        expected_type: Required concrete request model.
        label: Safe operation label.

    Returns:
        Narrowed request value.

    Raises:
        ApplicationError: If direct callers bypass validation.

    """
    if not isinstance(value, expected_type):
        _raise_invalid(label)
    return value


def _build_input[T](factory: Callable[[], T], label: str) -> T:
    """Build one application input and redact validation details.

    Args:
        factory: Side-effect-free validated input constructor.
        label: Safe operation label.

    Returns:
        Constructed application input.

    Raises:
        ApplicationError: If Session data violates the application contract.

    """
    try:
        return factory()
    except (TypeError, ValueError) as error:
        raise ApplicationError(
            ApplicationErrorCode.INVALID_INPUT,
            f"{label} Session request is invalid.",
        ) from error


def _require_task_key_scope(selector: TaskId | str, project: Project) -> None:
    """Refuse a Human Task key carrying another Project's namespace.

    Args:
        selector: Application-validated Task UID or stable Human key.
        project: Authoritative selected Project.

    Raises:
        ApplicationError: If a Human key names another Project namespace.

    """
    if isinstance(selector, str) and selector.rpartition("-")[0] != project.key:
        raise ApplicationError(
            ApplicationErrorCode.TASK_NOT_FOUND,
            "The requested Task was not found in the selected Project.",
        )


def _require_mutation_result(
    value: object,
    *,
    scope: PhaseThreeScope,
    selector: TaskId | str,
    expected_version: int,
    label: str,
) -> TaskMutationResult:
    """Require one scope-, actor-, and version-consistent mutation result."""
    if (
        not isinstance(value, TaskMutationResult)
        or not _task_result_matches(value.task, scope=scope, selector=selector)
        or value.task.version != expected_version + 1
        or any(event.actor_subject_id != scope.subject_id for event in value.events)
    ):
        _raise_internal(label)
    return value


def _require_submission_result(
    value: object,
    *,
    scope: PhaseThreeScope,
    selector: TaskId | str,
    expected_version: int,
    label: str,
) -> TaskSubmissionResult:
    """Require one scope-, actor-, and version-consistent Result transition."""
    if (
        not isinstance(value, TaskSubmissionResult)
        or not _task_result_matches(value.task, scope=scope, selector=selector)
        or value.task.version != expected_version + 1
        or any(event.actor_subject_id != scope.subject_id for event in value.events)
    ):
        _raise_internal(label)
    return value


def _task_result_matches(
    task: Task,
    *,
    scope: PhaseThreeScope,
    selector: TaskId | str,
) -> bool:
    """Return whether one Task exactly matches its selected request scope."""
    project = scope.project
    return (
        project is not None
        and task.project_id == project.id
        and task.key.rpartition("-")[0] == project.key
        and (
            task.uid == selector
            if isinstance(selector, TaskId)
            else task.key == selector
        )
    )


def _require_callable(value: object, member_name: str, label: str) -> None:
    """Require one explicit application capability."""
    if not callable(getattr(value, member_name, None)):
        message = f"Phase 3 Session {label} must provide {member_name}()."
        raise TypeError(message)


def _raise_invalid(label: str) -> Never:
    """Raise one stable invalid Session request error."""
    raise ApplicationError(
        ApplicationErrorCode.INVALID_INPUT,
        f"{label} Session request is invalid.",
    )


def _raise_internal(label: str) -> Never:
    """Raise one stable invalid application-result error."""
    raise ApplicationError(
        ApplicationErrorCode.INTERNAL_ERROR,
        f"{label} returned an invalid result.",
    )
