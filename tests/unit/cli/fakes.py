"""Explicit Phase 1 Session fakes shared by CLI unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from tests.unit.session.fakes import UnavailablePhaseFourSession

from workaholic.application import (
    BootstrapResult,
    ContextResult,
    ProjectCreationResult,
    StatusResult,
    TaskClaimResult,
    TaskDetails,
    TaskEventPage,
    TaskEventResult,
    TaskListView,
    TaskMutationResult,
    TaskPage,
    TaskProgressResult,
    TaskSubmissionResult,
)
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
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
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskReadiness,
    TaskResult,
    TaskState,
    WorkspaceBinding,
)

if TYPE_CHECKING:
    from workaholic.session import (
        AgentHeartbeatRequest,
        AgentProgressRequest,
        AgentReleaseRequest,
        AgentSubmitRequest,
        AgentTaskClaimRequest,
        ContextRequest,
        HumanClaimReleaseRequest,
        HumanClaimRenewRequest,
        HumanTaskClaimRequest,
        ProjectBindRequest,
        ProjectCreateRequest,
        ProjectListRequest,
        StatusRequest,
        TaskAddDependencyRequest,
        TaskApproveRequest,
        TaskBlockRequest,
        TaskCancelRequest,
        TaskCreateRequest,
        TaskDetailsRequest,
        TaskEventsRequest,
        TaskGetRequest,
        TaskListByViewRequest,
        TaskListRequest,
        TaskRejectRequest,
        TaskRemoveDependencyRequest,
        TaskSubmitRequest,
        TaskUnblockRequest,
        TaskUpdateRequest,
        UpRequest,
        WorkaholicSession,
    )

_NOW = datetime(2026, 7, 30, 12, 30, tzinfo=UTC)


def instance() -> Instance:
    """Build the deterministic CLI-test Instance.

    Returns:
        Validated Instance.

    """
    return Instance(id=InstanceId("ins_local"), created_at=_NOW)


def subject() -> Subject:
    """Build the deterministic CLI-test local Human.

    Returns:
        Validated enabled Human administrator.

    """
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def project(
    *,
    key: str = "ACME",
    name: str | None = None,
    identifier: str = "prj_acme",
) -> Project:
    """Build one deterministic CLI-test Project.

    Args:
        key: Immutable Project key.
        name: Optional Human-readable Project name.
        identifier: Canonical Project identifier text.

    Returns:
        Validated Project.

    """
    return Project(
        id=ProjectId(identifier),
        instance_id=instance().id,
        key=key,
        name=key if name is None else name,
        created_at=_NOW,
    )


def grant(selected_project: Project | None = None) -> ProjectGrant:
    """Build the deterministic Owner grant.

    Args:
        selected_project: Optional Project to authorize.

    Returns:
        Validated local Human Owner grant.

    """
    authorized_project = project() if selected_project is None else selected_project
    return ProjectGrant(
        subject_id=subject().id,
        project_id=authorized_project.id,
        role=ProjectRole.OWNER,
    )


def bootstrap_result() -> BootstrapResult:
    """Build one internally consistent local bootstrap result.

    Returns:
        Validated deterministic bootstrap result.

    """
    selected_project = project()
    return BootstrapResult(
        instance=instance(),
        project=selected_project,
        subject=subject(),
        grant=grant(selected_project),
        workspace=WorkspaceBinding(
            context_version=1,
            profile="local",
            instance_id=instance().id,
            project_id=selected_project.id,
            project_key=selected_project.key,
            workspace_root=".",
        ),
    )


def status_result() -> StatusResult:
    """Build one internally consistent local status result.

    Returns:
        Validated deterministic status result.

    """
    selected_project = project()
    return StatusResult(
        instance=instance(),
        project=selected_project,
        subject=subject(),
        grant=grant(selected_project),
    )


def context_result(
    *,
    selected_project: Project | None = None,
    profile: str = "local",
    workspace_root: Path | None = Path("/work/acme"),
) -> ContextResult:
    """Build one internally consistent effective-context result.

    Args:
        selected_project: Optional Project selected by the result.
        profile: Trusted profile name.
        workspace_root: Optional absolute Workspace root.

    Returns:
        Validated deterministic effective-context result.

    """
    effective_project = project() if selected_project is None else selected_project
    context_source = (
        None if workspace_root is None else workspace_root / ".workaholic.env"
    )
    return ContextResult(
        profile=profile,
        instance=instance(),
        project=effective_project,
        subject=subject(),
        grant=grant(effective_project),
        workspace_root=workspace_root,
        context_source=context_source,
    )


def project_creation_result() -> ProjectCreationResult:
    """Build one internally consistent Project-creation result.

    Returns:
        Validated deterministic Project and Owner grant.

    """
    created_project = project(
        key="DOCS",
        name="Documentation",
        identifier="prj_docs",
    )
    return ProjectCreationResult(
        project=created_project,
        grant=grant(created_project),
    )


def task() -> Task:
    """Build one deterministic CLI-test Task.

    Returns:
        Validated initial Task.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=project().id,
        number=1,
        key="ACME-1",
        title="First persistent task",
        objective="First persistent task",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=subject().id,
        created_at=_NOW,
        updated_at=_NOW,
    )


def task_submission_result(
    status: ResultReviewStatus = ResultReviewStatus.NOT_REQUIRED,
    *,
    agent: bool = False,
) -> TaskSubmissionResult:
    """Build one internally consistent Human or Agent Result transition.

    Args:
        status: Review disposition represented by the returned transition.
        agent: Whether the result is submitted through an Agent Attempt.

    Returns:
        Validated deterministic submission or review result.

    """
    if agent and status not in (
        ResultReviewStatus.NOT_REQUIRED,
        ResultReviewStatus.PENDING,
    ):
        message = "The CLI fake creates Agent submission outcomes only."
        raise ValueError(message)
    first_task = task()
    attempt_id = AttemptId("atm_cli") if agent else None
    result_id = ResultId("res_agent" if agent else "res_manual")
    event_types: tuple[TaskEventType, ...]
    if status is ResultReviewStatus.NOT_REQUIRED:
        state = TaskState.DONE
        version = 2
        approval = ApprovalRequirement.NONE
        current_result_id = result_id
        review = ResultReview(status=status)
        event_types = (
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.TASK_COMPLETED,
        )
        first_cursor = 2
    elif status is ResultReviewStatus.PENDING:
        state = TaskState.REVIEW
        version = 2
        approval = ApprovalRequirement.HUMAN
        current_result_id = result_id
        review = ResultReview(status=status)
        event_types = (TaskEventType.RESULT_SUBMITTED,)
        first_cursor = 2
    elif status is ResultReviewStatus.APPROVED:
        state = TaskState.DONE
        version = 3
        approval = ApprovalRequirement.HUMAN
        current_result_id = result_id
        review = ResultReview(
            status=status,
            reviewed_by=subject().id,
            reviewed_at=_NOW,
            comment="Looks good.",
        )
        event_types = (
            TaskEventType.REVIEW_APPROVED,
            TaskEventType.TASK_COMPLETED,
        )
        first_cursor = 3
    else:
        state = TaskState.OPEN
        version = 3
        approval = ApprovalRequirement.HUMAN
        current_result_id = None
        review = ResultReview(
            status=status,
            reviewed_by=subject().id,
            reviewed_at=_NOW,
            reason="Please address the missing evidence.",
        )
        event_types = (TaskEventType.REVIEW_REJECTED,)
        first_cursor = 3
    transitioned_task = replace(
        first_task,
        state=state,
        version=version,
        approval=approval,
        current_result_id=current_result_id,
        updated_at=_NOW,
    )
    result = TaskResult(
        id=result_id,
        task_uid=first_task.uid,
        submitted_by=subject().id,
        attempt_id=attempt_id,
        submitted_at=_NOW,
        comment=None,
        summary=None,
        criteria=(),
        artifacts=(),
        proposed_follow_ups=(),
        review=review,
    )
    request_id = RequestId(f"req_{status.value}")
    events = tuple(
        TaskEvent(
            id=TaskEventId(f"evt_{status.value}_{offset}"),
            cursor=first_cursor + offset,
            task_uid=first_task.uid,
            project_id=first_task.project_id,
            actor_subject_id=subject().id,
            attempt_id=attempt_id,
            request_id=request_id,
            event_type=event_type,
            occurred_at=_NOW,
            payload={},
        )
        for offset, event_type in enumerate(event_types)
    )
    return TaskSubmissionResult(
        task=transitioned_task,
        result=result,
        events=events,
        attempt=(
            None
            if attempt_id is None
            else TaskAttempt(
                id=attempt_id,
                task_uid=first_task.uid,
                subject_id=subject().id,
                status=AttemptStatus.SUBMITTED,
                lease_expires_at=_NOW + timedelta(minutes=15),
                started_at=_NOW - timedelta(minutes=1),
                ended_at=_NOW,
            )
        ),
    )


def task_event_result(
    *,
    cursor: int = 1,
    event_type: TaskEventType = TaskEventType.TASK_CREATED,
) -> TaskEventResult:
    """Build one attributable Human TaskEvent history record.

    Args:
        cursor: Positive Instance event cursor.
        event_type: Semantic event kind.

    Returns:
        Validated flattened event result.

    """
    first_task = task()
    return TaskEventResult(
        id=TaskEventId(f"evt_history_{cursor}"),
        cursor=cursor,
        task_uid=first_task.uid,
        project_id=first_task.project_id,
        actor_subject_id=subject().id,
        actor_kind=SubjectKind.HUMAN,
        attempt_id=None,
        request_id=RequestId(f"req_history_{cursor}"),
        event_type=event_type,
        occurred_at=_NOW,
        payload={"version": cursor},
    )


def task_event_page(
    *events: TaskEventResult,
    next_cursor: int | None = None,
) -> TaskEventPage:
    """Build one validated event page with an inferred resumable cursor.

    Args:
        events: Ordered event records in the page.
        next_cursor: Explicit cursor for an empty page or expected final cursor.

    Returns:
        Validated event snapshot page.

    """
    effective_cursor = (
        events[-1].cursor if next_cursor is None and events else (next_cursor or 0)
    )
    return TaskEventPage(events=events, next_cursor=effective_cursor)


def task_claim_result(
    *,
    agent: bool,
    event_type: TaskEventType = TaskEventType.TASK_CLAIMED,
    released: bool = False,
) -> TaskClaimResult:
    """Build one internally consistent CLI-test Claim operation result.

    Args:
        agent: Whether the owner path carries an Agent Attempt.
        event_type: Claim lifecycle event represented by the result.
        released: Whether the current Claim is absent after explicit release.

    Returns:
        Validated deterministic Claim result.

    """
    first_task = task()
    attempt_id = AttemptId("atm_cli") if agent else None
    lease_expires_at = _NOW + timedelta(minutes=15)
    occurred_at = _NOW + timedelta(minutes=1) if released else _NOW
    current_claim = None
    if not released:
        current_claim = TaskClaim(
            task_uid=first_task.uid,
            task_key=first_task.key,
            subject_id=subject().id,
            attempt_id=attempt_id,
            claimed_at=_NOW,
            lease_expires_at=lease_expires_at,
        )
    attempt = None
    if attempt_id is not None:
        attempt = TaskAttempt(
            id=attempt_id,
            task_uid=first_task.uid,
            subject_id=subject().id,
            status=AttemptStatus.RELEASED if released else AttemptStatus.ACTIVE,
            lease_expires_at=lease_expires_at,
            started_at=_NOW,
            ended_at=occurred_at if released else None,
        )
    event = TaskEvent(
        id=TaskEventId(f"evt_{event_type.value}_{'agent' if agent else 'human'}"),
        cursor=2,
        task_uid=first_task.uid,
        project_id=first_task.project_id,
        actor_subject_id=subject().id,
        attempt_id=attempt_id,
        request_id=RequestId(f"req_{event_type.value}"),
        event_type=event_type,
        occurred_at=occurred_at,
        payload={"lease_expires_at": lease_expires_at.isoformat()},
    )
    return TaskClaimResult(
        task=first_task,
        claim=current_claim,
        attempt=attempt,
        events=(event,),
    )


def task_progress_result() -> TaskProgressResult:
    """Build one ordered structured Agent progress result.

    Returns:
        Validated deterministic active ownership and progress events.

    """
    first_task = task()
    attempt_id = AttemptId("atm_cli")
    lease_expires_at = _NOW + timedelta(minutes=15)
    claim = TaskClaim(
        task_uid=first_task.uid,
        task_key=first_task.key,
        subject_id=subject().id,
        attempt_id=attempt_id,
        claimed_at=_NOW - timedelta(minutes=1),
        lease_expires_at=lease_expires_at,
    )
    attempt = TaskAttempt(
        id=attempt_id,
        task_uid=first_task.uid,
        subject_id=subject().id,
        status=AttemptStatus.ACTIVE,
        lease_expires_at=lease_expires_at,
        started_at=_NOW - timedelta(minutes=1),
        ended_at=None,
    )
    request_id = RequestId("req_progress")
    events = (
        TaskEvent(
            id=TaskEventId("evt_progress"),
            cursor=2,
            task_uid=first_task.uid,
            project_id=first_task.project_id,
            actor_subject_id=subject().id,
            attempt_id=attempt_id,
            request_id=request_id,
            event_type=TaskEventType.PROGRESS_REPORTED,
            occurred_at=_NOW,
            payload={"message": "Running tests.", "percent_complete": 70},
        ),
        TaskEvent(
            id=TaskEventId("evt_observation"),
            cursor=3,
            task_uid=first_task.uid,
            project_id=first_task.project_id,
            actor_subject_id=subject().id,
            attempt_id=attempt_id,
            request_id=request_id,
            event_type=TaskEventType.OBSERVATION_ADDED,
            occurred_at=_NOW,
            payload={"kind": "risk", "text": "A retry may be needed."},
        ),
    )
    return TaskProgressResult(
        task=first_task,
        claim=claim,
        attempt=attempt,
        events=events,
    )


class RecordingSession(UnavailablePhaseFourSession):
    """Configurable explicit fake for the cumulative Session boundary."""

    def __init__(self) -> None:  # noqa: PLR0915 - cumulative Session fake
        """Initialize deterministic results, failures, and call logs."""
        first_task = task()
        self.up_result = bootstrap_result()
        self.status_result = status_result()
        self.context_result = context_result()
        self.projects_result: tuple[Project, ...] = (project(),)
        self.project_creation_result = project_creation_result()
        self.project_binding_result = context_result()
        self.create_task_result = first_task
        self.task_page_result = TaskPage(tasks=(first_task,), next_cursor=None)
        self.get_task_result = first_task
        ready = TaskReadiness(
            ready=True,
            running=False,
            scheduled=False,
            stale=False,
            awaiting_review=False,
            reasons=(),
        )
        mutation_task = Task(
            uid=first_task.uid,
            project_id=first_task.project_id,
            number=first_task.number,
            key=first_task.key,
            title=first_task.title,
            objective=first_task.objective,
            state=first_task.state,
            priority=first_task.priority,
            version=2,
            created_by=first_task.created_by,
            created_at=first_task.created_at,
            updated_at=_NOW,
        )
        mutation_event = TaskEvent(
            id=TaskEventId("evt_updated"),
            cursor=2,
            task_uid=mutation_task.uid,
            project_id=mutation_task.project_id,
            actor_subject_id=subject().id,
            request_id=RequestId("req_updated"),
            event_type=TaskEventType.TASK_UPDATED,
            occurred_at=_NOW,
            payload={},
        )
        self.task_mutation_result = TaskMutationResult(
            task=mutation_task,
            events=(mutation_event,),
        )
        self.task_details_result = TaskDetails(
            task=first_task,
            readiness=ready,
            prerequisites=(),
            current_result=None,
        )
        self.task_view_page_result = TaskPage(
            tasks=(first_task,),
            readiness=(ready,),
            next_cursor=None,
            view=TaskListView.ALL,
        )
        self.task_submit_result = task_submission_result()
        self.task_approve_result = task_submission_result(ResultReviewStatus.APPROVED)
        self.task_reject_result = task_submission_result(ResultReviewStatus.REJECTED)
        self.task_event_page_result = task_event_page(task_event_result())
        self.task_event_page_results: list[TaskEventPage] = []
        self.human_claim_result = task_claim_result(agent=False)
        self.agent_claim_result = task_claim_result(agent=True)
        self.human_renew_result = task_claim_result(
            agent=False,
            event_type=TaskEventType.CLAIM_RENEWED,
        )
        self.agent_heartbeat_result = task_claim_result(
            agent=True,
            event_type=TaskEventType.CLAIM_RENEWED,
        )
        self.human_release_result = task_claim_result(
            agent=False,
            event_type=TaskEventType.CLAIM_RELEASED,
            released=True,
        )
        self.agent_release_result = task_claim_result(
            agent=True,
            event_type=TaskEventType.CLAIM_RELEASED,
            released=True,
        )
        self.task_progress_result = task_progress_result()
        self.agent_submit_result = task_submission_result(agent=True)
        self.failures: dict[str, Exception] = {}
        self.up_requests: list[UpRequest] = []
        self.status_requests: list[StatusRequest] = []
        self.context_requests: list[ContextRequest] = []
        self.project_list_requests: list[ProjectListRequest] = []
        self.project_create_requests: list[ProjectCreateRequest] = []
        self.project_bind_requests: list[ProjectBindRequest] = []
        self.task_create_requests: list[TaskCreateRequest] = []
        self.task_list_requests: list[TaskListRequest] = []
        self.task_get_requests: list[TaskGetRequest] = []
        self.task_update_requests: list[TaskUpdateRequest] = []
        self.task_block_requests: list[TaskBlockRequest] = []
        self.task_unblock_requests: list[TaskUnblockRequest] = []
        self.task_cancel_requests: list[TaskCancelRequest] = []
        self.task_add_dependency_requests: list[TaskAddDependencyRequest] = []
        self.task_remove_dependency_requests: list[TaskRemoveDependencyRequest] = []
        self.task_details_requests: list[TaskDetailsRequest] = []
        self.task_view_requests: list[TaskListByViewRequest] = []
        self.task_submit_requests: list[TaskSubmitRequest] = []
        self.task_approve_requests: list[TaskApproveRequest] = []
        self.task_reject_requests: list[TaskRejectRequest] = []
        self.task_event_requests: list[TaskEventsRequest] = []
        self.human_claim_requests: list[HumanTaskClaimRequest] = []
        self.agent_claim_requests: list[AgentTaskClaimRequest] = []
        self.human_renew_requests: list[HumanClaimRenewRequest] = []
        self.agent_heartbeat_requests: list[AgentHeartbeatRequest] = []
        self.human_release_requests: list[HumanClaimReleaseRequest] = []
        self.agent_release_requests: list[AgentReleaseRequest] = []
        self.agent_progress_requests: list[AgentProgressRequest] = []
        self.agent_submit_requests: list[AgentSubmitRequest] = []

    def up(self, request: UpRequest) -> BootstrapResult:
        """Record and answer one bootstrap request."""
        self.up_requests.append(request)
        self._raise_failure("up")
        return self.up_result

    def status(self, request: StatusRequest) -> StatusResult:
        """Record and answer one status request."""
        self.status_requests.append(request)
        self._raise_failure("status")
        return self.status_result

    def context(self, request: ContextRequest) -> ContextResult:
        """Record and answer one effective-context request."""
        self.context_requests.append(request)
        self._raise_failure("context")
        return self.context_result

    def list_projects(
        self,
        request: ProjectListRequest,
    ) -> tuple[Project, ...]:
        """Record and answer one Project-list request."""
        self.project_list_requests.append(request)
        self._raise_failure("list_projects")
        return self.projects_result

    def create_project(
        self,
        request: ProjectCreateRequest,
    ) -> ProjectCreationResult:
        """Record and answer one Project-create request."""
        self.project_create_requests.append(request)
        self._raise_failure("create_project")
        return self.project_creation_result

    def bind_project(self, request: ProjectBindRequest) -> ContextResult:
        """Record and answer one Project-bind request."""
        self.project_bind_requests.append(request)
        self._raise_failure("bind_project")
        return self.project_binding_result

    def create_task(self, request: TaskCreateRequest) -> Task:
        """Record and answer one Task-create request."""
        self.task_create_requests.append(request)
        self._raise_failure("create_task")
        return self.create_task_result

    def list_tasks(self, request: TaskListRequest) -> TaskPage:
        """Record and answer one Task-list request."""
        self.task_list_requests.append(request)
        self._raise_failure("list_tasks")
        return self.task_page_result

    def get_task(self, request: TaskGetRequest) -> Task:
        """Record and answer one Task-get request."""
        self.task_get_requests.append(request)
        self._raise_failure("get_task")
        return self.get_task_result

    def update_task(self, request: TaskUpdateRequest) -> TaskMutationResult:
        """Record and answer one Task-definition update."""
        self.task_update_requests.append(request)
        self._raise_failure("update_task")
        return self.task_mutation_result

    def block_task(self, request: TaskBlockRequest) -> TaskMutationResult:
        """Record and answer one Task block."""
        self.task_block_requests.append(request)
        self._raise_failure("block_task")
        return self.task_mutation_result

    def unblock_task(self, request: TaskUnblockRequest) -> TaskMutationResult:
        """Record and answer one Task unblock."""
        self.task_unblock_requests.append(request)
        self._raise_failure("unblock_task")
        return self.task_mutation_result

    def cancel_task(self, request: TaskCancelRequest) -> TaskMutationResult:
        """Record and answer one Task cancellation."""
        self.task_cancel_requests.append(request)
        self._raise_failure("cancel_task")
        return self.task_mutation_result

    def add_task_dependency(
        self,
        request: TaskAddDependencyRequest,
    ) -> TaskMutationResult:
        """Record and answer one dependency addition."""
        self.task_add_dependency_requests.append(request)
        self._raise_failure("add_task_dependency")
        return self.task_mutation_result

    def remove_task_dependency(
        self,
        request: TaskRemoveDependencyRequest,
    ) -> TaskMutationResult:
        """Record and answer one dependency removal."""
        self.task_remove_dependency_requests.append(request)
        self._raise_failure("remove_task_dependency")
        return self.task_mutation_result

    def submit_human_result(
        self,
        request: TaskSubmitRequest,
    ) -> TaskSubmissionResult:
        """Record and answer one direct Human Result submission."""
        self.task_submit_requests.append(request)
        self._raise_failure("submit_human_result")
        return self.task_submit_result

    def approve_result(
        self,
        request: TaskApproveRequest,
    ) -> TaskSubmissionResult:
        """Record and answer one Human Result approval."""
        self.task_approve_requests.append(request)
        self._raise_failure("approve_result")
        return self.task_approve_result

    def reject_result(
        self,
        request: TaskRejectRequest,
    ) -> TaskSubmissionResult:
        """Record and answer one Human Result rejection."""
        self.task_reject_requests.append(request)
        self._raise_failure("reject_result")
        return self.task_reject_result

    def get_task_details(self, request: TaskDetailsRequest) -> TaskDetails:
        """Record and answer one complete Task-details query."""
        self.task_details_requests.append(request)
        self._raise_failure("get_task_details")
        return self.task_details_result

    def list_tasks_by_view(self, request: TaskListByViewRequest) -> TaskPage:
        """Record and answer one view-bound Task page query."""
        self.task_view_requests.append(request)
        self._raise_failure("list_tasks_by_view")
        return self.task_view_page_result

    def read_task_events(self, request: TaskEventsRequest) -> TaskEventPage:
        """Record and answer one TaskEvent snapshot query."""
        self.task_event_requests.append(request)
        self._raise_failure("read_task_events")
        if self.task_event_page_results:
            return self.task_event_page_results.pop(0)
        return self.task_event_page_result

    def claim_task(self, request: HumanTaskClaimRequest) -> TaskClaimResult:
        """Record and answer one targeted Human Claim."""
        self.human_claim_requests.append(request)
        self._raise_failure("claim_task")
        return self.human_claim_result

    def claim_next_task(self, request: AgentTaskClaimRequest) -> TaskClaimResult:
        """Record and answer one Agent Task pull."""
        self.agent_claim_requests.append(request)
        self._raise_failure("claim_next_task")
        return self.agent_claim_result

    def renew_claim(self, request: HumanClaimRenewRequest) -> TaskClaimResult:
        """Record and answer one Human Claim renewal."""
        self.human_renew_requests.append(request)
        self._raise_failure("renew_claim")
        return self.human_renew_result

    def heartbeat_attempt(self, request: AgentHeartbeatRequest) -> TaskClaimResult:
        """Record and answer one Agent Attempt heartbeat."""
        self.agent_heartbeat_requests.append(request)
        self._raise_failure("heartbeat_attempt")
        return self.agent_heartbeat_result

    def release_claim(self, request: HumanClaimReleaseRequest) -> TaskClaimResult:
        """Record and answer one Human Claim release."""
        self.human_release_requests.append(request)
        self._raise_failure("release_claim")
        return self.human_release_result

    def release_attempt(self, request: AgentReleaseRequest) -> TaskClaimResult:
        """Record and answer one Agent Attempt release."""
        self.agent_release_requests.append(request)
        self._raise_failure("release_attempt")
        return self.agent_release_result

    def report_progress(self, request: AgentProgressRequest) -> TaskProgressResult:
        """Record and answer one Agent progress report."""
        self.agent_progress_requests.append(request)
        self._raise_failure("report_progress")
        return self.task_progress_result

    def submit_agent_result(
        self,
        request: AgentSubmitRequest,
    ) -> TaskSubmissionResult:
        """Record and answer one Attempt-backed Agent submission."""
        self.agent_submit_requests.append(request)
        self._raise_failure("submit_agent_result")
        return self.agent_submit_result

    def _raise_failure(self, operation: str) -> None:
        """Raise the configured failure for one operation, if present.

        Args:
            operation: Stable fake-operation name.

        Raises:
            Exception: Configured safe or unexpected failure.

        """
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure


class SessionProviderSpy:
    """Callable provider that records command-scoped Session acquisition."""

    def __init__(self, session: RecordingSession) -> None:
        """Initialize the provider with one explicit fake Session.

        Args:
            session: Session returned for every test invocation.

        """
        self.session = session
        self.call_count = 0

    def __call__(self) -> WorkaholicSession:
        """Return the configured Session and record acquisition."""
        self.call_count += 1
        return self.session
