"""Unit tests for complete Phase 3 LocalSession orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from tests.unit.session.fakes import UnavailablePhaseFourServices
from workaholic.application import (
    AddTaskDependencyInput,
    ApplicationError,
    ApplicationErrorCode,
    ApproveResultInput,
    BlockTaskInput,
    BootstrapLocalProjectInput,
    BootstrapResult,
    CancelTaskInput,
    CreateProjectInput,
    CreateTaskInput,
    DependencyCycleError,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    GetTaskDetails,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ListTasksByView,
    ProjectCreationResult,
    ReadTaskEvents,
    RejectResultInput,
    RemoveTaskDependencyInput,
    StatusResult,
    SubmitHumanResultInput,
    TaskDetails,
    TaskEventPage,
    TaskEventResult,
    TaskListView,
    TaskMutationResult,
    TaskPage,
    TaskResultInput,
    TaskSubmissionResult,
    TaskUpdatePatch,
    UnblockTaskInput,
    UpdateTaskInput,
)
from workaholic.domain import (
    ApprovalRequirement,
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskReadiness,
    TaskResult,
    TaskState,
    WorkspaceBinding,
)
from workaholic.session import (
    LocalIdentity,
    LocalRuntime,
    LocalSession,
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
    WorkspaceContextSelection,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_NOW = datetime(2026, 8, 1, 12, 0, 0, 123456, tzinfo=UTC)
_INSTANCE_ID = InstanceId("ins_local")
_SUBJECT_ID = SubjectId("sub_local")


def _project(key: str = "ACME") -> Project:
    """Build one authoritative local Project."""
    return Project(
        id=ProjectId(f"prj_{key.lower()}"),
        instance_id=_INSTANCE_ID,
        key=key,
        name=key,
        created_at=_NOW - timedelta(days=1),
    )


def _subject() -> Subject:
    """Build the runtime-selected Human Owner."""
    return Subject(
        id=_SUBJECT_ID,
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _status(project: Project | None = None) -> StatusResult:
    """Build one complete authorized status for a Project."""
    selected = _project() if project is None else project
    return StatusResult(
        instance=Instance(id=_INSTANCE_ID, created_at=_NOW - timedelta(days=1)),
        project=selected,
        subject=_subject(),
        grant=ProjectGrant(
            subject_id=_SUBJECT_ID,
            project_id=selected.id,
            role=ProjectRole.OWNER,
        ),
    )


def _task_for(  # noqa: PLR0913 - explicit fixture controls clarify invariants.
    project_id: ProjectId,
    selector: TaskId | str,
    *,
    version: int,
    state: TaskState = TaskState.OPEN,
    current_result_id: ResultId | None = None,
    blocking_reason: str | None = None,
) -> Task:
    """Build one returned Task matching a semantic input selector."""
    project_key = "BETA" if project_id == ProjectId("prj_beta") else "ACME"
    task_uid = selector if isinstance(selector, TaskId) else TaskId("tsk_target")
    task_key = selector if isinstance(selector, str) else f"{project_key}-1"
    return Task(
        uid=task_uid,
        project_id=project_id,
        number=int(task_key.rpartition("-")[2]),
        key=task_key,
        title="Target task",
        objective="Exercise Session orchestration.",
        state=state,
        priority=50,
        version=version,
        created_by=_SUBJECT_ID,
        created_at=_NOW - timedelta(hours=1),
        updated_at=_NOW,
        approval=(
            ApprovalRequirement.HUMAN
            if state is TaskState.REVIEW
            else ApprovalRequirement.NONE
        ),
        blocking_reason=blocking_reason,
        current_result_id=current_result_id,
    )


def _event(
    task: Task,
    event_type: TaskEventType,
    *,
    cursor: int = 1,
    request_id: RequestId | None = None,
) -> TaskEvent:
    """Build one attributable event matching a returned Task."""
    return TaskEvent(
        id=TaskEventId(f"evt_{event_type.value}_{cursor}"),
        cursor=cursor,
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=_SUBJECT_ID,
        request_id=(
            RequestId(f"req_{event_type.value}") if request_id is None else request_id
        ),
        event_type=event_type,
        occurred_at=_NOW,
        payload={},
    )


def _mutation_result(
    command: object,
    event_type: TaskEventType,
    *,
    state: TaskState = TaskState.OPEN,
    blocking_reason: str | None = None,
) -> TaskMutationResult:
    """Build one scope- and version-consistent mutation result."""
    assert isinstance(
        command,
        (
            UpdateTaskInput,
            BlockTaskInput,
            UnblockTaskInput,
            CancelTaskInput,
            AddTaskDependencyInput,
            RemoveTaskDependencyInput,
        ),
    )
    task = _task_for(
        command.project_id,
        command.task,
        version=command.expected_version + 1,
        state=state,
        blocking_reason=blocking_reason,
    )
    return TaskMutationResult(task=task, events=(_event(task, event_type),))


def _submission_result(
    command: SubmitHumanResultInput | ApproveResultInput | RejectResultInput,
) -> TaskSubmissionResult:
    """Build one operation-appropriate Human Result transition."""
    result_id = ResultId("res_current")
    reviewed = not isinstance(command, SubmitHumanResultInput)
    rejected = isinstance(command, RejectResultInput)
    review_status = (
        ResultReviewStatus.REJECTED
        if rejected
        else (
            ResultReviewStatus.APPROVED
            if isinstance(command, ApproveResultInput)
            else ResultReviewStatus.NOT_REQUIRED
        )
    )
    task = _task_for(
        command.project_id,
        command.task,
        version=command.expected_version + 1,
        state=TaskState.OPEN if rejected else TaskState.DONE,
        current_result_id=None if rejected else result_id,
    )
    result = TaskResult(
        id=result_id,
        task_uid=task.uid,
        submitted_by=_SUBJECT_ID,
        attempt_id=None,
        submitted_at=_NOW if not reviewed else _NOW - timedelta(minutes=1),
        comment=(
            command.comment if isinstance(command, SubmitHumanResultInput) else None
        ),
        summary=(
            command.result.summary
            if isinstance(command, SubmitHumanResultInput)
            else "Previously submitted."
        ),
        criteria=(),
        artifacts=(),
        proposed_follow_ups=(),
        review=ResultReview(
            status=review_status,
            reviewed_by=_SUBJECT_ID if reviewed else None,
            reviewed_at=_NOW if reviewed else None,
            comment=(
                command.comment if isinstance(command, ApproveResultInput) else None
            ),
            reason=(command.reason if isinstance(command, RejectResultInput) else None),
        ),
    )
    event_types = (
        (TaskEventType.REVIEW_REJECTED,)
        if rejected
        else (
            (TaskEventType.REVIEW_APPROVED, TaskEventType.TASK_COMPLETED)
            if reviewed
            else (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)
        )
    )
    return TaskSubmissionResult(
        task=task,
        result=result,
        events=tuple(
            _event(
                task,
                event_type,
                cursor=index,
                request_id=RequestId("req_result_transition"),
            )
            for index, event_type in enumerate(event_types, start=1)
        ),
    )


class _Context:
    """Return one discovered ACME Workspace binding and record reads."""

    def __init__(self) -> None:
        """Initialize an empty discovery recording."""
        self.calls = 0

    def discover(self) -> WorkspaceContextSelection:
        """Return one canonical same-Instance Workspace selection."""
        self.calls += 1
        return WorkspaceContextSelection(
            binding=WorkspaceBinding(
                context_version=1,
                profile="local",
                instance_id=_INSTANCE_ID,
                project_id=ProjectId("prj_acme"),
                project_key="ACME",
                workspace_root=".",
            ),
            context_source=Path("/repo/.workaholic.env"),
            workspace_root=Path("/repo"),
        )

    def write_current(self, _binding: WorkspaceBinding) -> Path:
        """Fail if a Phase 3 operation attempts a context write."""
        pytest.fail("Phase 3 operations must not write Workspace context")

    def bind(
        self,
        _directory: Path | None,
        _binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Fail if a Phase 3 operation attempts a Project binding."""
        assert type(replace) is bool
        pytest.fail("Phase 3 operations must not bind Workspace context")


class _Profiles:
    """Resolve only the trusted local embedded profile."""

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Return the sole trusted local profile."""
        assert explicit_profile is None
        assert discovered_profile == "local"
        return "local"


class _Identity:
    """Return one trusted Human identity and record selections."""

    def __init__(self) -> None:
        """Initialize an empty selection count."""
        self.calls = 0

    def select(self) -> LocalIdentity:
        """Return the runtime-owned local identity."""
        self.calls += 1
        return LocalIdentity(instance_id=_INSTANCE_ID, subject_id=_SUBJECT_ID)


class _Queries:
    """Record every query application command used by Phase 3 Sessions."""

    def __init__(self) -> None:
        """Initialize recordings and valid detail, view, and event outputs."""
        self.status_commands: list[GetLocalStatus] = []
        self.project_commands: list[GetProjectByKey] = []
        self.details_commands: list[GetTaskDetails] = []
        self.view_commands: list[ListTasksByView] = []
        self.event_commands: list[ReadTaskEvents] = []

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Return authorized status for the exact selected Project."""
        self.status_commands.append(command)
        project = (
            _project("BETA")
            if command.project_id == ProjectId("prj_beta")
            else _project()
        )
        return _status(project)

    def list_projects(self, _command: ListProjects) -> tuple[Project, ...]:
        """Return the unused complete Project inventory."""
        return (_project(), _project("BETA"))

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Return one exact explicit Project selection."""
        self.project_commands.append(command)
        return _project(command.project_key)

    def list_tasks(self, _command: ListTasks) -> TaskPage:
        """Fail if a Phase 3 operation uses the Phase 2 list path."""
        pytest.fail("Phase 3 views must use list_tasks_by_view")

    def list_tasks_for_instance(self, _command: ListInstanceTasks) -> TaskPage:
        """Fail if a Phase 3 operation uses the Phase 2 Instance list path."""
        pytest.fail("Phase 3 views must use list_tasks_by_view")

    def get_task(self, _command: GetTask) -> Task:
        """Fail because operation services own authoritative Task resolution."""
        pytest.fail("LocalSession must not duplicate application Task resolution")

    def get_task_details(self, command: GetTaskDetails) -> TaskDetails:
        """Return complete details matching the command selection."""
        self.details_commands.append(command)
        task = _task_for(command.project_id, command.task, version=1)
        return TaskDetails(
            task=task,
            readiness=_readiness(),
            prerequisites=(),
            current_result=None,
        )

    def list_tasks_by_view(self, command: ListTasksByView) -> TaskPage:
        """Return one view-bound page with aligned readiness."""
        self.view_commands.append(command)
        project_id = command.project_id or ProjectId("prj_acme")
        task = _task_for(project_id, "ACME-1", version=1)
        return TaskPage(
            tasks=(task,),
            readiness=(_readiness(),),
            next_cursor=None,
            view=command.view,
        )

    def read_task_events_after(self, command: ReadTaskEvents) -> TaskEventPage:
        """Return one flat attributable event page after the requested cursor."""
        self.event_commands.append(command)
        task = _task_for(command.project_id, command.task, version=1)
        cursor = command.after + 1
        event = _event(task, TaskEventType.TASK_CREATED, cursor=cursor)
        return TaskEventPage(
            events=(
                TaskEventResult(
                    id=event.id,
                    cursor=event.cursor,
                    task_uid=event.task_uid,
                    project_id=event.project_id,
                    actor_subject_id=event.actor_subject_id,
                    actor_kind=SubjectKind.HUMAN,
                    attempt_id=None,
                    request_id=event.request_id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                ),
            ),
            next_cursor=cursor,
        )


class _Lifecycle:
    """Record and satisfy each lifecycle application command."""

    def __init__(self) -> None:
        """Initialize command recordings and an optional failure."""
        self.commands: list[object] = []
        self.error: ApplicationError | None = None
        self.override: object | None = None

    def _result(
        self,
        command: object,
        event_type: TaskEventType,
        *,
        state: TaskState = TaskState.OPEN,
        blocking_reason: str | None = None,
    ) -> TaskMutationResult:
        """Record and return one configured lifecycle outcome."""
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        if self.override is not None:
            return cast("TaskMutationResult", self.override)
        return _mutation_result(
            command,
            event_type,
            state=state,
            blocking_reason=blocking_reason,
        )

    def update(self, command: UpdateTaskInput) -> TaskMutationResult:
        """Record one Task update."""
        return self._result(command, TaskEventType.TASK_UPDATED)

    def block(self, command: BlockTaskInput) -> TaskMutationResult:
        """Record one Task block."""
        return self._result(
            command,
            TaskEventType.TASK_BLOCKED,
            state=TaskState.BLOCKED,
            blocking_reason=command.reason,
        )

    def unblock(self, command: UnblockTaskInput) -> TaskMutationResult:
        """Record one Task unblock."""
        return self._result(command, TaskEventType.TASK_UNBLOCKED)

    def cancel(self, command: CancelTaskInput) -> TaskMutationResult:
        """Record one Task cancellation."""
        return self._result(
            command,
            TaskEventType.TASK_CANCELLED,
            state=TaskState.CANCELLED,
        )


class _Dependencies:
    """Record and satisfy dependency application commands."""

    def __init__(self) -> None:
        """Initialize recordings plus optional failure and malformed output."""
        self.commands: list[object] = []
        self.error: ApplicationError | None = None
        self.override: object | None = None

    def _result(self, command: object) -> TaskMutationResult:
        """Record and return or fail one dependency operation."""
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        if self.override is not None:
            return cast("TaskMutationResult", self.override)
        return _mutation_result(command, TaskEventType.TASK_UPDATED)

    def add(self, command: AddTaskDependencyInput) -> TaskMutationResult:
        """Record one dependency addition."""
        return self._result(command)

    def remove(self, command: RemoveTaskDependencyInput) -> TaskMutationResult:
        """Record one dependency removal."""
        return self._result(command)


class _Results:
    """Record and satisfy Human Result application commands."""

    def __init__(self) -> None:
        """Initialize command recordings."""
        self.commands: list[object] = []

    def _result(
        self,
        command: SubmitHumanResultInput | ApproveResultInput | RejectResultInput,
    ) -> TaskSubmissionResult:
        """Record and return one operation-appropriate Result transition."""
        self.commands.append(command)
        return _submission_result(command)

    def submit(self, command: SubmitHumanResultInput) -> TaskSubmissionResult:
        """Record one direct Human submission."""
        return self._result(command)

    def approve(self, command: ApproveResultInput) -> TaskSubmissionResult:
        """Record one Human Result approval."""
        return self._result(command)

    def reject(self, command: RejectResultInput) -> TaskSubmissionResult:
        """Record one Human Result rejection."""
        return self._result(command)


class _UnusedBootstrap:
    """Provide the unused established bootstrap capability."""

    def up(self, _command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Fail an unexpected bootstrap."""
        pytest.fail("Phase 3 test must not bootstrap")


class _UnusedProjects:
    """Provide the unused established Project capability."""

    def create(self, _command: CreateProjectInput) -> ProjectCreationResult:
        """Fail unexpected Project creation."""
        pytest.fail("Phase 3 test must not create Projects")


class _UnusedTasks:
    """Provide the unused established Task creation capability."""

    def create(self, _command: CreateTaskInput) -> Task:
        """Fail unexpected Task creation."""
        pytest.fail("Phase 3 test must not create Tasks")


@dataclass(frozen=True, slots=True)
class _Runtimes:
    """Return one exact pre-composed local runtime."""

    runtime: LocalRuntime

    def open(self, profile: str) -> LocalRuntime:
        """Return the sole runtime for its exact trusted name."""
        assert profile == "local"
        return self.runtime


@dataclass(slots=True)
class _Fixture:
    """Owned LocalSession and recording semantic dependencies."""

    session: LocalSession
    context: _Context
    identity: _Identity
    queries: _Queries
    lifecycle: _Lifecycle
    dependencies: _Dependencies
    results: _Results


def _fixture() -> _Fixture:
    """Compose one complete recording LocalSession fixture."""
    context = _Context()
    identity = _Identity()
    queries = _Queries()
    lifecycle = _Lifecycle()
    dependencies = _Dependencies()
    results = _Results()
    runtime = LocalRuntime(
        profile="local",
        identity=identity,
        bootstrap=_UnusedBootstrap(),
        projects=_UnusedProjects(),
        queries=queries,
        tasks=_UnusedTasks(),
        lifecycle=lifecycle,
        dependencies=dependencies,
        results=results,
        claims=UnavailablePhaseFourServices(),
        execution=UnavailablePhaseFourServices(),
    )
    return _Fixture(
        session=LocalSession(
            context=context,
            profiles=_Profiles(),
            runtimes=_Runtimes(runtime),
        ),
        context=context,
        identity=identity,
        queries=queries,
        lifecycle=lifecycle,
        dependencies=dependencies,
        results=results,
    )


def _readiness() -> TaskReadiness:
    """Build one valid ready projection."""
    return TaskReadiness(
        ready=True,
        running=False,
        scheduled=False,
        stale=False,
        awaiting_review=False,
        reasons=(),
    )


def test_every_phase_three_operation_derives_exact_scope_and_application_input() -> (
    None
):
    """Session forwards caller intent only after deriving Project and Human actor."""
    fixture = _fixture()
    session = fixture.session

    session.update_task(
        TaskUpdateRequest(
            task="ACME-1",
            expected_version=1,
            patch=TaskUpdatePatch(priority=80),
            idempotency_key="update-1",
        )
    )
    session.block_task(
        TaskBlockRequest(
            task="ACME-1",
            expected_version=1,
            reason="Waiting for input.",
        )
    )
    session.unblock_task(TaskUnblockRequest(task="ACME-1", expected_version=1))
    session.cancel_task(
        TaskCancelRequest(task="ACME-1", expected_version=1, reason="Obsolete.")
    )
    session.add_task_dependency(
        TaskAddDependencyRequest(
            task="ACME-1",
            prerequisite="ACME-2",
            expected_version=1,
        )
    )
    session.remove_task_dependency(
        TaskRemoveDependencyRequest(
            task="ACME-1",
            prerequisite="ACME-2",
            expected_version=1,
        )
    )
    session.submit_human_result(
        TaskSubmitRequest(
            task="ACME-1",
            expected_version=1,
            comment="Implemented manually.",
            result=TaskResultInput(summary="Implemented and verified."),
        )
    )
    session.approve_result(
        TaskApproveRequest(
            task="ACME-1",
            expected_version=1,
            comment="Verified.",
        )
    )
    session.reject_result(
        TaskRejectRequest(
            task="ACME-1",
            expected_version=1,
            reason="Evidence is incomplete.",
        )
    )
    details = session.get_task_details(TaskDetailsRequest(task="ACME-1"))
    page = session.list_tasks_by_view(
        TaskListByViewRequest(view=TaskListView.READY, project="ACME")
    )
    events = session.read_task_events(
        TaskEventsRequest(task="ACME-1", after=4, limit=25)
    )

    assert details.task.key == "ACME-1"
    assert page.view is TaskListView.READY
    assert events.next_cursor == 5
    assert len(fixture.lifecycle.commands) == 4
    assert len(fixture.dependencies.commands) == 2
    assert len(fixture.results.commands) == 3
    commands = (
        *fixture.lifecycle.commands,
        *fixture.dependencies.commands,
        *fixture.results.commands,
        *fixture.queries.details_commands,
        *fixture.queries.view_commands,
        *fixture.queries.event_commands,
    )
    for command in commands:
        assert isinstance(
            command,
            (
                UpdateTaskInput,
                BlockTaskInput,
                UnblockTaskInput,
                CancelTaskInput,
                AddTaskDependencyInput,
                RemoveTaskDependencyInput,
                SubmitHumanResultInput,
                ApproveResultInput,
                RejectResultInput,
                GetTaskDetails,
                ListTasksByView,
                ReadTaskEvents,
            ),
        )
        assert command.project_id == ProjectId("prj_acme")
        assert command.subject_id == _SUBJECT_ID
    assert fixture.lifecycle.commands[0] == UpdateTaskInput(
        project_id=ProjectId("prj_acme"),
        subject_id=_SUBJECT_ID,
        task="ACME-1",
        expected_version=1,
        idempotency_key="update-1",
        patch=TaskUpdatePatch(priority=80),
    )
    assert fixture.dependencies.commands[0] == AddTaskDependencyInput(
        project_id=ProjectId("prj_acme"),
        subject_id=_SUBJECT_ID,
        task="ACME-1",
        prerequisite="ACME-2",
        expected_version=1,
    )
    assert fixture.results.commands[0] == SubmitHumanResultInput(
        project_id=ProjectId("prj_acme"),
        subject_id=_SUBJECT_ID,
        task="ACME-1",
        expected_version=1,
        comment="Implemented manually.",
        result=TaskResultInput(summary="Implemented and verified."),
    )
    assert fixture.context.calls == 12
    assert fixture.identity.calls == 12
    assert len(fixture.queries.status_commands) == 12


def test_explicit_project_selection_and_wrong_prefix_refuse_cross_project_work() -> (
    None
):
    """Explicit same-Instance Projects work while mismatched Human keys fail."""
    fixture = _fixture()

    result = fixture.session.block_task(
        TaskBlockRequest(
            task="BETA-1",
            project="BETA",
            expected_version=2,
            reason="Waiting for input.",
        )
    )

    assert result.task.project_id == ProjectId("prj_beta")
    assert result.task.key == "BETA-1"
    assert fixture.queries.project_commands[-1].project_key == "BETA"
    with pytest.raises(ApplicationError) as captured:
        fixture.session.block_task(
            TaskBlockRequest(
                task="ACME-1",
                project="BETA",
                expected_version=2,
                reason="Cross-Project request.",
            )
        )
    assert captured.value.code is ApplicationErrorCode.TASK_NOT_FOUND
    assert len(fixture.lifecycle.commands) == 1


def test_runtime_request_validation_precedes_scope_and_service_access() -> None:
    """Direct callers cannot bypass any Phase 3 Session request type."""
    fixture = _fixture()
    session = fixture.session
    invocations: tuple[Callable[[], object], ...] = (
        lambda: session.update_task(cast("TaskUpdateRequest", object())),
        lambda: session.block_task(cast("TaskBlockRequest", object())),
        lambda: session.unblock_task(cast("TaskUnblockRequest", object())),
        lambda: session.cancel_task(cast("TaskCancelRequest", object())),
        lambda: session.add_task_dependency(cast("TaskAddDependencyRequest", object())),
        lambda: session.remove_task_dependency(
            cast("TaskRemoveDependencyRequest", object())
        ),
        lambda: session.submit_human_result(cast("TaskSubmitRequest", object())),
        lambda: session.approve_result(cast("TaskApproveRequest", object())),
        lambda: session.reject_result(cast("TaskRejectRequest", object())),
        lambda: session.get_task_details(cast("TaskDetailsRequest", object())),
        lambda: session.list_tasks_by_view(cast("TaskListByViewRequest", object())),
        lambda: session.read_task_events(cast("TaskEventsRequest", object())),
    )

    for invoke in invocations:
        with pytest.raises(ApplicationError) as captured:
            invoke()
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert fixture.context.calls == 0
    assert fixture.identity.calls == 0


def test_application_failures_propagate_and_invalid_outputs_are_redacted() -> None:
    """Typed semantic failures remain exact while malformed outputs fail closed."""
    fixture = _fixture()
    failure = DependencyCycleError()
    fixture.dependencies.error = failure

    with pytest.raises(DependencyCycleError) as captured:
        fixture.session.add_task_dependency(
            TaskAddDependencyRequest(
                task="ACME-1",
                prerequisite="ACME-2",
                expected_version=1,
            )
        )
    assert captured.value is failure

    fixture.dependencies.error = None
    fixture.dependencies.override = object()
    with pytest.raises(ApplicationError) as invalid_dependency:
        fixture.session.remove_task_dependency(
            TaskRemoveDependencyRequest(
                task="ACME-1",
                prerequisite="ACME-2",
                expected_version=1,
            )
        )
    assert invalid_dependency.value.code is ApplicationErrorCode.INTERNAL_ERROR

    fixture.lifecycle.override = object()
    with pytest.raises(ApplicationError) as invalid:
        fixture.session.block_task(
            TaskBlockRequest(
                task="ACME-1",
                expected_version=1,
                reason="Waiting for input.",
            )
        )
    assert invalid.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_malformed_constructed_values_map_to_safe_invalid_input() -> None:
    """Pydantic bypasses do not leak diagnostics or reach mutation services."""
    fixture = _fixture()
    malformed = TaskBlockRequest.model_construct(
        task="not-a-task",
        expected_version=1,
        reason=object(),
        project=None,
        idempotency_key=None,
    )

    with pytest.raises(ApplicationError) as captured:
        fixture.session.block_task(malformed)

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert fixture.lifecycle.commands == []
