"""Unit tests for explicit Phase 4 LocalSession orchestration."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.session.fakes import (
    UnavailablePhaseFourSession,
    UnavailablePhaseThreeServices,
)
from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapLocalProjectInput,
    BootstrapResult,
    CreateProjectInput,
    CreateTaskInput,
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
    StatusResult,
    TaskClaimResult,
    TaskDetails,
    TaskEventPage,
    TaskPage,
    TaskProgressResult,
    TaskResultInput,
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
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskId,
    TaskProgress,
    TaskState,
    WorkspaceBinding,
)
from workaholic.session import (
    AgentHeartbeatRequest,
    AgentProgressRequest,
    AgentReleaseRequest,
    AgentSubmitRequest,
    AgentTaskClaimRequest,
    HumanClaimReleaseRequest,
    HumanClaimRenewRequest,
    HumanTaskClaimRequest,
    LocalIdentity,
    LocalRuntime,
    LocalSession,
    WorkspaceContextSelection,
)

_NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
_INSTANCE_ID = InstanceId("ins_local")
_PROJECT_ID = ProjectId("prj_acme")
_SUBJECT_ID = SubjectId("sub_bootstrap")
_TASK_ID = TaskId("tsk_one")
_ATTEMPT_ID = AttemptId("atm_current")


class _Context:
    """Return one trusted Workspace binding without writes."""

    def discover(self) -> WorkspaceContextSelection:
        """Return the selected ACME Workspace context."""
        return WorkspaceContextSelection(
            binding=WorkspaceBinding(
                context_version=1,
                profile="local",
                instance_id=_INSTANCE_ID,
                project_id=_PROJECT_ID,
                project_key="ACME",
                workspace_root=".",
            ),
            context_source=Path("/workspace/.workaholic.env"),
            workspace_root=Path("/workspace"),
        )

    def write_current(self, _binding: WorkspaceBinding) -> Path:
        """Fail an unexpected context write."""
        pytest.fail("Phase 4 execution must not write Workspace context")

    def bind(
        self,
        _directory: Path | None,
        _binding: WorkspaceBinding,
        *,
        replace: bool,
    ) -> Path:
        """Fail an unexpected context binding."""
        assert type(replace) is bool
        pytest.fail("Phase 4 execution must not bind Workspace context")


class _Profiles:
    """Select the sole embedded profile."""

    def resolve(
        self,
        *,
        explicit_profile: str | None,
        discovered_profile: str | None,
    ) -> str:
        """Return local after checking Session-owned profile inputs."""
        assert explicit_profile is None
        assert discovered_profile == "local"
        return "local"


class _Identity:
    """Record selection of the sole initialized bootstrap Subject."""

    def __init__(self) -> None:
        """Initialize an empty selection count."""
        self.calls = 0

    def select(self) -> LocalIdentity:
        """Return the same trusted local identity on every request."""
        self.calls += 1
        return LocalIdentity(instance_id=_INSTANCE_ID, subject_id=_SUBJECT_ID)


class _Queries:
    """Provide only the authorization reads used by Phase 4 selection."""

    def __init__(self, project: Project) -> None:
        """Initialize one authorized Project and query log."""
        self.project = project
        self.calls: list[object] = []

    def get_project_by_key(self, command: GetProjectByKey) -> Project:
        """Resolve the explicit Project key."""
        self.calls.append(command)
        assert command.project_key == self.project.key
        return self.project

    def status(self, command: GetLocalStatus) -> StatusResult:
        """Return authorization for the selected bootstrap Subject."""
        self.calls.append(command)
        return _status(self.project)

    def list_projects(self, _command: ListProjects) -> tuple[Project, ...]:
        """Fail an unexpected Project-list query."""
        pytest.fail("Phase 4 execution must not list Projects")

    def list_tasks(self, _command: ListTasks) -> TaskPage:
        """Fail an unexpected Task-list query."""
        pytest.fail("Phase 4 execution must not list Tasks")

    def list_tasks_for_instance(self, _command: ListInstanceTasks) -> TaskPage:
        """Fail an unexpected Instance Task-list query."""
        pytest.fail("Phase 4 execution must not list Instance Tasks")

    def get_task(self, _command: GetTask) -> Task:
        """Fail an unexpected Session-level Task query."""
        pytest.fail("Phase 4 services own Task selector resolution")

    def get_task_details(self, _command: GetTaskDetails) -> TaskDetails:
        """Fail an unexpected Task-detail query."""
        pytest.fail("Phase 4 execution must not query Task details")

    def list_tasks_by_view(self, _command: ListTasksByView) -> TaskPage:
        """Fail an unexpected Task-view query."""
        pytest.fail("Phase 4 execution must not query Task views")

    def read_task_events_after(self, _command: ReadTaskEvents) -> TaskEventPage:
        """Fail an unexpected Task-event query."""
        pytest.fail("Phase 4 execution must not query Task events")


class _UnusedBootstrap:
    """Fail any unexpected bootstrap call."""

    def up(self, _command: BootstrapLocalProjectInput) -> BootstrapResult:
        """Fail unexpected bootstrap."""
        pytest.fail("Phase 4 execution must not bootstrap")


class _UnusedProjects:
    """Fail any unexpected Project creation call."""

    def create(self, _command: CreateProjectInput) -> ProjectCreationResult:
        """Fail unexpected Project creation."""
        pytest.fail("Phase 4 execution must not create a Project")


class _UnusedTasks:
    """Fail any unexpected Task creation call."""

    def create(self, _command: CreateTaskInput) -> Task:
        """Fail unexpected Task creation."""
        pytest.fail("Phase 4 execution must not create a Task")


class _Claims:
    """Record each explicit Human and Agent Claim service path."""

    def __init__(self, task: Task) -> None:
        """Initialize one authoritative Task and empty call log."""
        self.task = task
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failure: ApplicationError | None = None
        self.invalid_project = False

    def claim_task(self, **kwargs: object) -> TaskClaimResult:
        """Return one current Human Claim."""
        return self._result("claim_task", kwargs, attempt_id=None, released=False)

    def claim_next_task(self, **kwargs: object) -> TaskClaimResult:
        """Return one current Agent Claim and Attempt."""
        return self._result(
            "claim_next_task",
            kwargs,
            attempt_id=_ATTEMPT_ID,
            released=False,
        )

    def renew_claim(self, **kwargs: object) -> TaskClaimResult:
        """Return current ownership after shared renewal."""
        attempt_id = kwargs["attempt_id"]
        assert attempt_id is None or isinstance(attempt_id, AttemptId)
        return self._result(
            "renew_claim",
            kwargs,
            attempt_id=attempt_id,
            released=False,
        )

    def release_claim(self, **kwargs: object) -> TaskClaimResult:
        """Return no Claim and an optional terminal Agent Attempt."""
        attempt_id = kwargs["attempt_id"]
        assert attempt_id is None or isinstance(attempt_id, AttemptId)
        return self._result(
            "release_claim",
            kwargs,
            attempt_id=attempt_id,
            released=True,
        )

    def _result(
        self,
        operation: str,
        kwargs: dict[str, object],
        *,
        attempt_id: AttemptId | None,
        released: bool,
    ) -> TaskClaimResult:
        """Record one call and construct its owned result shape."""
        self.calls.append((operation, dict(kwargs)))
        if self.failure is not None:
            raise self.failure
        task = self.task
        if self.invalid_project:
            task = _task(project_id=ProjectId("prj_other"))
        attempt = _attempt(attempt_id, released=released)
        claim = None if released else _claim(attempt_id)
        return TaskClaimResult.model_construct(
            task=task,
            claim=claim,
            attempt=attempt,
            events=(),
        )


class _Execution:
    """Record Agent progress and submission service paths."""

    def __init__(self, task: Task) -> None:
        """Initialize one authoritative Task and empty call log."""
        self.task = task
        self.calls: list[tuple[str, dict[str, object]]] = []

    def report_progress(self, **kwargs: object) -> TaskProgressResult:
        """Return current ownership with a typed progress outcome."""
        self.calls.append(("report_progress", dict(kwargs)))
        return TaskProgressResult.model_construct(
            task=self.task,
            claim=_claim(_ATTEMPT_ID),
            attempt=_attempt(_ATTEMPT_ID, released=False),
            events=(),
        )

    def submit_result(self, **kwargs: object) -> TaskSubmissionResult:
        """Return attributable submitted Result metadata."""
        self.calls.append(("submit_result", dict(kwargs)))
        return TaskSubmissionResult.model_construct(
            task=self.task,
            result=SimpleNamespace(
                submitted_by=_SUBJECT_ID,
                attempt_id=_ATTEMPT_ID,
            ),
            attempt=_attempt(_ATTEMPT_ID, released=True),
            events=(),
        )


@dataclass(frozen=True, slots=True)
class _Runtimes:
    """Open one exact local runtime."""

    runtime: LocalRuntime

    def open(self, profile: str) -> LocalRuntime:
        """Return the runtime only for its configured profile."""
        assert profile == "local"
        return self.runtime


@dataclass(slots=True)
class _Fixture:
    """Owned LocalSession and recording Phase 4 dependencies."""

    session: LocalSession
    identity: _Identity
    queries: _Queries
    claims: _Claims
    execution: _Execution


def _fixture() -> _Fixture:
    """Compose one complete Phase 4 LocalSession fixture."""
    project = _project()
    task = _task()
    identity = _Identity()
    queries = _Queries(project)
    claims = _Claims(task)
    execution = _Execution(task)
    phase_three = UnavailablePhaseThreeServices()
    runtime = LocalRuntime(
        profile="local",
        identity=identity,
        bootstrap=_UnusedBootstrap(),
        projects=_UnusedProjects(),
        queries=queries,
        tasks=_UnusedTasks(),
        lifecycle=phase_three,
        dependencies=phase_three,
        results=phase_three,
        claims=claims,
        execution=execution,
    )
    return _Fixture(
        session=LocalSession(
            context=_Context(),
            profiles=_Profiles(),
            runtimes=_Runtimes(runtime),
        ),
        identity=identity,
        queries=queries,
        claims=claims,
        execution=execution,
    )


def _project() -> Project:
    """Build the sole selected Project."""
    return Project(
        id=_PROJECT_ID,
        instance_id=_INSTANCE_ID,
        key="ACME",
        name="ACME",
        created_at=_NOW,
    )


def _status(project: Project) -> StatusResult:
    """Build one complete authorized local status."""
    return StatusResult(
        profile="local",
        instance=Instance(id=_INSTANCE_ID, created_at=_NOW),
        project=project,
        subject=Subject(
            id=_SUBJECT_ID,
            kind=SubjectKind.HUMAN,
            display_name="Local operator",
            enabled=True,
            is_instance_admin=True,
        ),
        grant=ProjectGrant(
            subject_id=_SUBJECT_ID,
            project_id=project.id,
            role=ProjectRole.OWNER,
        ),
    )


def _task(*, project_id: ProjectId = _PROJECT_ID) -> Task:
    """Build one stable open Task."""
    return Task(
        uid=_TASK_ID,
        project_id=project_id,
        number=1,
        key="ACME-1",
        title="Implement Phase 4",
        objective="Implement the local execution boundary.",
        state=TaskState.OPEN,
        priority=50,
        version=3,
        created_by=_SUBJECT_ID,
        created_at=_NOW,
        updated_at=_NOW,
        approval=ApprovalRequirement.NONE,
    )


def _claim(attempt_id: AttemptId | None) -> TaskClaim:
    """Build one current Claim owned by the bootstrap Subject."""
    return TaskClaim(
        task_uid=_TASK_ID,
        task_key="ACME-1",
        subject_id=_SUBJECT_ID,
        attempt_id=attempt_id,
        claimed_at=_NOW,
        lease_expires_at=_NOW + timedelta(hours=1),
    )


def _attempt(
    attempt_id: AttemptId | None,
    *,
    released: bool,
) -> TaskAttempt | None:
    """Build an active or terminal Agent Attempt when requested."""
    if attempt_id is None:
        return None
    return TaskAttempt(
        id=attempt_id,
        task_uid=_TASK_ID,
        subject_id=_SUBJECT_ID,
        status=AttemptStatus.RELEASED if released else AttemptStatus.ACTIVE,
        lease_expires_at=_NOW + timedelta(hours=1),
        started_at=_NOW,
        ended_at=_NOW + timedelta(minutes=1) if released else None,
    )


def test_every_phase_four_method_routes_exact_path_and_trusted_scope() -> None:
    """Eight explicit Session paths preserve intent and bootstrap attribution."""
    fixture = _fixture()
    session = fixture.session
    lease = timedelta(minutes=20)
    progress = TaskProgress(message="Running tests.", percent_complete=75)
    result = TaskResultInput(summary="Implemented.")

    outcomes = (
        session.claim_task(
            HumanTaskClaimRequest(
                task="ACME-1",
                lease=lease,
                idempotency_key="human-claim",
            )
        ),
        session.claim_next_task(
            AgentTaskClaimRequest(
                lease=lease,
                project="ACME",
                idempotency_key="agent-claim",
            )
        ),
        session.renew_claim(HumanClaimRenewRequest(task="ACME-1", lease=lease)),
        session.heartbeat_attempt(
            AgentHeartbeatRequest(
                task="ACME-1",
                attempt=_ATTEMPT_ID,
                lease=lease,
            )
        ),
        session.release_claim(HumanClaimReleaseRequest(task="ACME-1")),
        session.release_attempt(
            AgentReleaseRequest(task="ACME-1", attempt=_ATTEMPT_ID)
        ),
        session.report_progress(
            AgentProgressRequest(
                task="ACME-1",
                attempt=_ATTEMPT_ID,
                progress=progress,
            )
        ),
        session.submit_agent_result(
            AgentSubmitRequest(
                task="ACME-1",
                attempt=_ATTEMPT_ID,
                expected_version=3,
                result=result,
            )
        ),
    )

    assert all(outcome.task.uid == _TASK_ID for outcome in outcomes)
    assert [operation for operation, _values in fixture.claims.calls] == [
        "claim_task",
        "claim_next_task",
        "renew_claim",
        "renew_claim",
        "release_claim",
        "release_claim",
    ]
    assert [operation for operation, _values in fixture.execution.calls] == [
        "report_progress",
        "submit_result",
    ]
    all_calls = (
        *(values for _operation, values in fixture.claims.calls),
        *(values for _operation, values in fixture.execution.calls),
    )
    assert all(values["project_id"] == _PROJECT_ID for values in all_calls)
    assert all(values["subject_id"] == _SUBJECT_ID for values in all_calls)
    assert fixture.identity.calls == 8
    assert fixture.claims.calls[0][1]["lease_duration"] == lease
    assert "attempt_id" not in fixture.claims.calls[0][1]
    assert fixture.claims.calls[2][1]["attempt_id"] is None
    assert fixture.claims.calls[3][1]["attempt_id"] == _ATTEMPT_ID
    assert fixture.execution.calls[0][1]["progress"] == progress
    assert fixture.execution.calls[1][1]["expected_version"] == 3
    assert fixture.execution.calls[1][1]["result"] == result
    assert any(isinstance(call, GetProjectByKey) for call in fixture.queries.calls)


@pytest.mark.parametrize(
    ("method_name", "wrong_request"),
    [
        ("claim_task", AgentTaskClaimRequest()),
        ("claim_next_task", HumanTaskClaimRequest(task="ACME-1")),
        ("renew_claim", HumanClaimReleaseRequest(task="ACME-1")),
        (
            "heartbeat_attempt",
            AgentReleaseRequest(task="ACME-1", attempt=_ATTEMPT_ID),
        ),
        ("release_claim", HumanClaimRenewRequest(task="ACME-1")),
        (
            "release_attempt",
            AgentHeartbeatRequest(task="ACME-1", attempt=_ATTEMPT_ID),
        ),
        (
            "report_progress",
            AgentReleaseRequest(task="ACME-1", attempt=_ATTEMPT_ID),
        ),
        (
            "submit_agent_result",
            AgentProgressRequest(
                task="ACME-1",
                attempt=_ATTEMPT_ID,
                progress=TaskProgress(message="No."),
            ),
        ),
    ],
)
def test_phase_four_methods_reject_ambiguous_request_types_before_resolution(
    method_name: str,
    wrong_request: object,
) -> None:
    """Command-path distinctions cannot be bypassed with sibling requests."""
    fixture = _fixture()
    operation = getattr(fixture.session, method_name)

    with pytest.raises(ApplicationError) as captured:
        operation(wrong_request)

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert fixture.identity.calls == 0
    assert fixture.claims.calls == []
    assert fixture.execution.calls == []


def test_application_errors_propagate_without_reclassification() -> None:
    """Stable execution failures pass through the Session unchanged."""
    fixture = _fixture()
    expected = ApplicationError(
        ApplicationErrorCode.TASK_LOCKED,
        "The Task is locked by another owner.",
    )
    fixture.claims.failure = expected

    with pytest.raises(ApplicationError) as captured:
        fixture.session.claim_task(HumanTaskClaimRequest(task="ACME-1"))

    assert captured.value is expected


def test_malformed_service_result_is_redacted_as_internal_error() -> None:
    """A cross-Project service result cannot escape the Session boundary."""
    fixture = _fixture()
    fixture.claims.invalid_project = True

    with pytest.raises(ApplicationError) as captured:
        fixture.session.claim_task(HumanTaskClaimRequest(task="ACME-1"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert "invalid result" in captured.value.safe_message


def test_phase_four_session_fake_preserves_production_method_signatures() -> None:
    """Focused test fakes cannot silently drift from the production Session."""
    methods = (
        "claim_task",
        "claim_next_task",
        "renew_claim",
        "heartbeat_attempt",
        "release_claim",
        "release_attempt",
        "report_progress",
        "submit_agent_result",
    )

    for method_name in methods:
        production = inspect.signature(getattr(LocalSession, method_name))
        fake = inspect.signature(getattr(UnavailablePhaseFourSession, method_name))
        assert tuple(production.parameters) == tuple(fake.parameters)
        assert production.return_annotation == fake.return_annotation
        assert (
            production.parameters["request"].annotation
            == fake.parameters["request"].annotation
        )


def test_phase_four_surface_has_no_network_or_identity_configuration_inputs() -> None:
    """Phase 4 Session methods expose only their transport-neutral requests."""
    forbidden = {"token", "credential", "capability", "agent_subject", "url"}

    for method_name in (
        "claim_task",
        "claim_next_task",
        "renew_claim",
        "heartbeat_attempt",
        "release_claim",
        "release_attempt",
        "report_progress",
        "submit_agent_result",
    ):
        signature = inspect.signature(getattr(LocalSession, method_name))
        assert set(signature.parameters) == {"self", "request"}
        assert forbidden.isdisjoint(signature.parameters)
