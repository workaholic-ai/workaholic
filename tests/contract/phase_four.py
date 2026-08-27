"""Reusable typed builders for cumulative Phase 4 conformance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from tests.contract.phase_three import (
    PhaseThreeIdentifierFactory,
    PhaseThreeRepositoryFactory,
    PhaseThreeSessionFactory,
)

from workaholic.application import (
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    ReleaseClaimMutation,
    RenewClaimMutation,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskResultInput,
)
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    Project,
    RequestId,
    ResultId,
    SubjectId,
    Task,
    TaskEventId,
    TaskProgress,
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
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

PHASE_FOUR_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class PhaseFourTransactionFailurePoint(StrEnum):
    """Semantic Phase 4 write boundaries available for rollback tests."""

    CLAIM_EVENT = "claim_event"
    CLAIM_IDEMPOTENCY = "claim_idempotency"
    PROGRESS_EVENT = "progress_event"
    PROGRESS_IDEMPOTENCY = "progress_idempotency"
    AGENT_RESULT_EVENT = "agent_result_event"
    AGENT_RESULT_IDEMPOTENCY = "agent_result_idempotency"


class PhaseFourIdentifierFactory(PhaseThreeIdentifierFactory, Protocol):
    """Generate every deterministic identity required through Phase 4."""


class PhaseFourRepositoryFactory(PhaseThreeRepositoryFactory, Protocol):
    """Construct cumulative repositories with Phase 4 failure hooks."""

    def identifiers(self, namespace: str) -> PhaseFourIdentifierFactory:
        """Construct a complete deterministic Phase 4 identifier source.

        Args:
            namespace: Stable scenario-specific identity namespace.

        Returns:
            Independent deterministic identifier factory.

        """
        ...

    def inject_phase_four_failure(
        self,
        point: PhaseFourTransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Fail one semantic Phase 4 write inside its active transaction.

        Args:
            point: Stable boundary at which the adapter must fail.

        Returns:
            Context manager scoping the injected failure.

        """
        ...


class PhaseFourSessionFactory(PhaseThreeSessionFactory, Protocol):
    """Construct isolated Sessions with complete Phase 4 dependencies."""


def phase_four_time(offset: int = 0) -> datetime:
    """Return a deterministic Phase 4 UTC timestamp.

    Args:
        offset: Nonnegative second offset from the fixture epoch.

    Returns:
        UTC fixture timestamp advanced by ``offset`` seconds.

    Raises:
        ValueError: If ``offset`` is not a nonnegative integer.

    """
    if type(offset) is not int or offset < 0:
        message = "Conformance timestamp offset must be a nonnegative integer."
        raise ValueError(message)
    return PHASE_FOUR_NOW + timedelta(seconds=offset)


def human_claim_mutation(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    occurred_at: datetime = PHASE_FOUR_NOW,
    lease_seconds: int = 28_800,
    idempotency_key: str | None = None,
) -> ClaimTaskMutation:
    """Build one deterministic targeted Human Claim mutation.

    Args:
        task: Ready Task to claim.
        actor: Authorized Human owner.
        label: Stable request and event identity suffix.
        occurred_at: Authoritative operation time.
        lease_seconds: Human Lease duration in whole seconds.
        idempotency_key: Optional replay key.

    Returns:
        Validated Human Claim mutation.

    """
    return ClaimTaskMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        lease_duration_seconds=lease_seconds,
        task_claimed_event_id=TaskEventId(f"evt_{label}_claimed"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        idempotency_key=idempotency_key,
    )


def agent_claim_mutation(  # noqa: PLR0913 - explicit semantic fixture
    project: Project,
    actor: SubjectId,
    label: str,
    *,
    occurred_at: datetime = PHASE_FOUR_NOW,
    lease_seconds: int = 900,
    idempotency_key: str | None = None,
    attempt_id: AttemptId | None = None,
) -> ClaimNextTaskMutation:
    """Build one deterministic Project-scoped Agent pull mutation.

    Args:
        project: Project from which to pull the next ready Task.
        actor: Authorized local Agent Subject.
        label: Stable Attempt, request, and event suffix.
        occurred_at: Authoritative operation time.
        lease_seconds: Agent Lease duration in whole seconds.
        idempotency_key: Optional replay key.
        attempt_id: Optional exact Attempt identity override.

    Returns:
        Validated Agent Claim mutation.

    """
    return ClaimNextTaskMutation(
        project_id=project.id,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        attempt_id=AttemptId(f"atm_{label}") if attempt_id is None else attempt_id,
        lease_duration_seconds=lease_seconds,
        task_claimed_event_id=TaskEventId(f"evt_{label}_claimed"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        idempotency_key=idempotency_key,
    )


def renewal_mutation(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    attempt_id: AttemptId | None,
    occurred_at: datetime,
    lease_seconds: int,
    idempotency_key: str | None = None,
) -> RenewClaimMutation:
    """Build one deterministic Human renewal or Agent heartbeat mutation.

    Args:
        task: Owned Task.
        actor: Current owner Subject.
        label: Stable request and event suffix.
        attempt_id: Null Human token or exact Agent Attempt.
        occurred_at: Authoritative renewal time.
        lease_seconds: Owner-appropriate Lease duration.
        idempotency_key: Optional replay key.

    Returns:
        Validated renewal mutation.

    """
    return RenewClaimMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        lease_duration_seconds=lease_seconds,
        claim_renewed_event_id=TaskEventId(f"evt_{label}_renewed"),
        idempotency_key=idempotency_key,
    )


def release_mutation(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    attempt_id: AttemptId | None,
    occurred_at: datetime,
    idempotency_key: str | None = None,
) -> ReleaseClaimMutation:
    """Build one deterministic Human or Agent Claim release mutation.

    Args:
        task: Owned Task.
        actor: Current owner Subject.
        label: Stable request and event suffix.
        attempt_id: Null Human token or exact Agent Attempt.
        occurred_at: Authoritative release time.
        idempotency_key: Optional replay key.

    Returns:
        Validated Claim release mutation.

    """
    return ReleaseClaimMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        claim_released_event_id=TaskEventId(f"evt_{label}_released"),
        idempotency_key=idempotency_key,
    )


def progress_mutation(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    actor: SubjectId,
    attempt_id: AttemptId,
    label: str,
    progress: TaskProgress,
    *,
    occurred_at: datetime,
    idempotency_key: str | None = None,
) -> ReportTaskProgressMutation:
    """Build one deterministic structured Agent progress mutation.

    Args:
        task: Owned Task.
        actor: Current Agent Subject.
        attempt_id: Exact active Attempt owner token.
        label: Stable request and event suffix.
        progress: Bounded structured progress input.
        occurred_at: Authoritative report time.
        idempotency_key: Optional replay key.

    Returns:
        Validated progress mutation with aligned observation events.

    """
    observations = progress.observations or ()
    return ReportTaskProgressMutation(
        project_id=task.project_id,
        task_uid=task.uid,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        progress=progress,
        progress_reported_event_id=TaskEventId(f"evt_{label}_progress"),
        observation_event_ids=tuple(
            TaskEventId(f"evt_{label}_observation_{index}")
            for index, _observation in enumerate(observations)
        ),
        idempotency_key=idempotency_key,
    )


def agent_submit_mutation(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    actor: SubjectId,
    attempt_id: AttemptId,
    label: str,
    result: TaskResultInput,
    *,
    occurred_at: datetime,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> SubmitAgentResultMutation:
    """Build one deterministic Attempt-backed Result submission mutation.

    Args:
        task: Claimed Task snapshot.
        actor: Current Agent Subject.
        attempt_id: Exact active Attempt owner token.
        label: Stable Result, request, and event suffix.
        result: Closed structured Result input.
        occurred_at: Authoritative submission time.
        expected_version: Optional version override for race scenarios.
        idempotency_key: Optional replay key.

    Returns:
        Validated Agent Result submission mutation.

    """
    completion_event = (
        None
        if task.approval is ApprovalRequirement.HUMAN
        else TaskEventId(f"evt_{label}_completed")
    )
    return SubmitAgentResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        idempotency_key=idempotency_key,
        attempt_id=attempt_id,
        result_id=ResultId(f"res_{label}"),
        result_submitted_event_id=TaskEventId(f"evt_{label}_submitted"),
        task_completed_event_id=completion_event,
        result=result,
    )


def human_claim_request(
    task: Task,
    *,
    lease: timedelta = timedelta(hours=8),
    idempotency_key: str | None = None,
    project: str | None = None,
) -> HumanTaskClaimRequest:
    """Build one deterministic targeted Human Claim request.

    Args:
        task: Ready Task to claim.
        lease: Requested Human Lease duration.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Attempt-free Human Claim request.

    """
    return HumanTaskClaimRequest(
        task=task.uid,
        lease=lease,
        idempotency_key=idempotency_key,
        project=project,
    )


def agent_claim_request(
    *,
    lease: timedelta = timedelta(minutes=15),
    idempotency_key: str | None = None,
    project: str | None = None,
) -> AgentTaskClaimRequest:
    """Build one deterministic Project-scoped Agent pull request.

    Args:
        lease: Requested Agent Lease duration.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Agent Claim request.

    """
    return AgentTaskClaimRequest(
        lease=lease,
        idempotency_key=idempotency_key,
        project=project,
    )


def human_renewal_request(
    task: Task,
    *,
    lease: timedelta = timedelta(hours=8),
    idempotency_key: str | None = None,
    project: str | None = None,
) -> HumanClaimRenewRequest:
    """Build one deterministic Attempt-free Human Claim renewal request.

    Args:
        task: Human-owned Task.
        lease: Requested Human Lease duration.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Human renewal request.

    """
    return HumanClaimRenewRequest(
        task=task.uid,
        lease=lease,
        idempotency_key=idempotency_key,
        project=project,
    )


def agent_heartbeat_request(
    task: Task,
    attempt_id: AttemptId,
    *,
    lease: timedelta = timedelta(minutes=15),
    idempotency_key: str | None = None,
    project: str | None = None,
) -> AgentHeartbeatRequest:
    """Build one deterministic exact-Attempt heartbeat request.

    Args:
        task: Agent-owned Task.
        attempt_id: Exact active Attempt owner token.
        lease: Requested Agent Lease duration.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Agent heartbeat request.

    """
    return AgentHeartbeatRequest(
        task=task.uid,
        attempt=attempt_id,
        lease=lease,
        idempotency_key=idempotency_key,
        project=project,
    )


def human_release_request(
    task: Task,
    *,
    idempotency_key: str | None = None,
    project: str | None = None,
) -> HumanClaimReleaseRequest:
    """Build one deterministic Attempt-free Human Claim release request.

    Args:
        task: Human-owned Task.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Human Claim release request.

    """
    return HumanClaimReleaseRequest(
        task=task.uid,
        idempotency_key=idempotency_key,
        project=project,
    )


def agent_release_request(
    task: Task,
    attempt_id: AttemptId,
    *,
    idempotency_key: str | None = None,
    project: str | None = None,
) -> AgentReleaseRequest:
    """Build one deterministic exact-Attempt release request.

    Args:
        task: Agent-owned Task.
        attempt_id: Exact active Attempt owner token.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Agent release request.

    """
    return AgentReleaseRequest(
        task=task.uid,
        attempt=attempt_id,
        idempotency_key=idempotency_key,
        project=project,
    )


def agent_progress_request(
    task: Task,
    attempt_id: AttemptId,
    progress: TaskProgress,
    *,
    idempotency_key: str | None = None,
    project: str | None = None,
) -> AgentProgressRequest:
    """Build one deterministic structured Agent progress request.

    Args:
        task: Agent-owned Task.
        attempt_id: Exact active Attempt owner token.
        progress: Bounded structured progress.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Agent progress request.

    """
    return AgentProgressRequest(
        task=task.uid,
        attempt=attempt_id,
        progress=progress,
        idempotency_key=idempotency_key,
        project=project,
    )


def agent_submit_request(  # noqa: PLR0913 - explicit semantic fixture
    task: Task,
    attempt_id: AttemptId,
    result: TaskResultInput,
    *,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
    project: str | None = None,
) -> AgentSubmitRequest:
    """Build one deterministic optimistic Agent Result request.

    Args:
        task: Agent-owned Task snapshot.
        attempt_id: Exact active Attempt owner token.
        result: Closed structured Result input.
        expected_version: Optional exact version override.
        idempotency_key: Optional replay key.
        project: Optional explicit Project key.

    Returns:
        Validated Agent submission request.

    """
    return AgentSubmitRequest(
        task=task.uid,
        attempt=attempt_id,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        result=result,
        idempotency_key=idempotency_key,
        project=project,
    )
