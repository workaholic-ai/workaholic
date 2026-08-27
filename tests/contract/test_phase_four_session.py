"""Transport-neutral cumulative Phase 4 Session conformance tests."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from tests.contract.phase_four import (
    PhaseFourSessionFactory,
    agent_claim_request,
    agent_heartbeat_request,
    agent_progress_request,
    agent_release_request,
    agent_submit_request,
    human_claim_request,
    human_release_request,
    human_renewal_request,
    phase_four_time,
)
from tests.contract.phase_two import DeterministicClock, DeterministicIdentifierFactory
from tests.contract.test_phase_three_session import (
    PhaseThreeSessionContract,
    _LocalSessionFactory,
)
from tests.contract.test_phase_two_session import _workspace

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    TaskResultInput,
    TaskUpdatePatch,
)
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
    ObservationKind,
    ProgressObservation,
    ReadinessReason,
    ResultReviewStatus,
    TaskEventType,
    TaskProgress,
    TaskState,
)
from workaholic.session import (
    AgentProgressRequest,
    AgentTaskClaimRequest,
    HumanTaskClaimRequest,
    ProjectCreateRequest,
    TaskCreateRequest,
    TaskDetailsRequest,
    TaskEventsRequest,
    TaskRejectRequest,
    TaskUpdateRequest,
    UpRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.session import WorkaholicSession

pytestmark = pytest.mark.contract

_PROGRESS = TaskProgress(
    message="Implementing the requested change.",
    percent_complete=75,
    observations=(
        ProgressObservation(
            kind=ObservationKind.NOTE,
            text="Use the repository contract as the durable boundary.",
        ),
    ),
)
_RESULT = TaskResultInput(summary="Implemented and verified.")


class PhaseFourSessionContract(PhaseThreeSessionContract):
    """Reusable cumulative observable contract for a Phase 4 Session."""

    @pytest.fixture
    def session_factory(self) -> PhaseFourSessionFactory:
        """Provide the Session factory under cumulative conformance."""
        message = "A concrete Phase 4 Session contract must provide its factory."
        raise NotImplementedError(message)

    def test_human_claim_renewal_and_release_use_no_attempt_token(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Human ownership remains targeted, long-lived, and Attempt-free."""
        session = _bootstrapped_phase_four_session(session_factory, tmp_path)
        task = session.create_task(TaskCreateRequest(title="Human-owned task"))

        claimed = session.claim_task(
            human_claim_request(
                task,
                idempotency_key="human-claim",
            )
        )
        renewed = session.renew_claim(
            human_renewal_request(
                task,
                lease=timedelta(hours=12),
                idempotency_key="human-renew",
            )
        )
        released = session.release_claim(
            human_release_request(
                task,
                idempotency_key="human-release",
            )
        )

        assert claimed.claim is not None
        assert claimed.claim.attempt_id is None
        assert claimed.attempt is renewed.attempt is released.attempt is None
        assert renewed.claim is not None
        assert renewed.claim.lease_expires_at > claimed.claim.lease_expires_at
        assert released.claim is None
        assert (
            claimed.task.version == renewed.task.version == released.task.version == 1
        )

    def test_agent_execution_replays_and_survives_session_restart(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Attempt ownership, progress, and submission persist across Sessions."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)
        bootstrap = session.up(UpRequest(project_key="ACME"))
        task = session.create_task(TaskCreateRequest(title="Agent-owned task"))
        claim_request = agent_claim_request(idempotency_key="agent-claim")

        claimed = session.claim_next_task(claim_request)
        replayed_claim = session.claim_next_task(claim_request)
        assert replayed_claim == claimed
        assert claimed.attempt is not None

        restarted = session_factory.create(root, workspace)
        heartbeat = restarted.heartbeat_attempt(
            agent_heartbeat_request(
                task,
                claimed.attempt.id,
                lease=timedelta(minutes=30),
                idempotency_key="agent-heartbeat",
            )
        )
        progress_request = agent_progress_request(
            task,
            claimed.attempt.id,
            _PROGRESS,
            idempotency_key="agent-progress",
        )
        progress = restarted.report_progress(progress_request)
        assert restarted.report_progress(progress_request) == progress
        submitted = restarted.submit_agent_result(
            agent_submit_request(
                task,
                claimed.attempt.id,
                _RESULT,
                idempotency_key="agent-submit",
            )
        )

        assert heartbeat.claim is not None
        assert heartbeat.claim.subject_id == bootstrap.subject.id
        assert progress.claim.attempt_id == claimed.attempt.id
        assert tuple(event.event_type for event in progress.events) == (
            TaskEventType.PROGRESS_REPORTED,
            TaskEventType.OBSERVATION_ADDED,
        )
        assert submitted.task.state is TaskState.DONE
        assert submitted.task.version == 2
        assert submitted.attempt is not None
        assert submitted.attempt.status is AttemptStatus.SUBMITTED
        assert submitted.result.attempt_id == claimed.attempt.id
        details = restarted.get_task_details(TaskDetailsRequest(task=task.uid))
        assert details.claim is None
        assert details.attempt is None
        assert details.current_result == submitted.result

    def test_agent_claim_locks_human_mutation_and_requires_exact_attempt(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """An active Agent Claim rejects Human writes and stale owner tokens."""
        session = _bootstrapped_phase_four_session(session_factory, tmp_path)
        task = session.create_task(TaskCreateRequest(title="Locked task"))
        claimed = session.claim_next_task(AgentTaskClaimRequest())
        assert claimed.attempt is not None

        with pytest.raises(ApplicationError) as locked:
            session.update_task(
                TaskUpdateRequest(
                    task=task.uid,
                    expected_version=task.version,
                    patch=TaskUpdatePatch(priority=90),
                )
            )
        assert locked.value.code is ApplicationErrorCode.TASK_LOCKED

        with pytest.raises(ApplicationError) as lost:
            session.report_progress(
                AgentProgressRequest(
                    task=task.uid,
                    attempt=AttemptId("atm_stale"),
                    progress=TaskProgress(message="Must not persist."),
                )
            )
        assert lost.value.code is ApplicationErrorCode.LEASE_LOST

        released = session.release_attempt(
            agent_release_request(task, claimed.attempt.id)
        )
        assert released.attempt is not None
        assert released.attempt.status is AttemptStatus.RELEASED
        updated = session.update_task(
            TaskUpdateRequest(
                task=task.uid,
                expected_version=task.version,
                patch=TaskUpdatePatch(priority=90),
            )
        )
        assert updated.task.version == 2

    def test_exact_lease_expiry_is_a_read_only_projection_until_reclaim(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Exact expiry hides ownership without emitting events before reclaim."""
        clock = DeterministicClock(current=phase_four_time(), step=timedelta(0))
        session = session_factory.create_with_dependencies(
            tmp_path / "data",
            _workspace(tmp_path, "workspace"),
            clock=clock,
            identifiers=DeterministicIdentifierFactory("expiry"),
        )
        session.up(UpRequest(project_key="ACME"))
        task = session.create_task(TaskCreateRequest(title="Expiring task"))
        original = session.claim_next_task(
            AgentTaskClaimRequest(lease=timedelta(seconds=1))
        )
        assert original.attempt is not None
        before = session.read_task_events(TaskEventsRequest(task=task.uid))

        clock.current = phase_four_time(1)
        stale = session.get_task_details(TaskDetailsRequest(task=task.uid))
        assert stale.claim is None
        assert stale.attempt is None
        assert stale.readiness.stale is True
        assert stale.readiness.reasons == (ReadinessReason.STALE_CLAIM,)
        assert session.read_task_events(TaskEventsRequest(task=task.uid)) == before

        clock.current = phase_four_time(2)
        reclaimed = session.claim_next_task(AgentTaskClaimRequest())
        assert reclaimed.attempt is not None
        assert reclaimed.attempt.id != original.attempt.id
        assert tuple(event.event_type for event in reclaimed.events) == (
            TaskEventType.CLAIM_EXPIRED,
            TaskEventType.TASK_CLAIMED,
        )

    def test_agent_review_rejection_opens_a_new_attempt(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Rejected Agent work preserves its Result and permits a fresh Attempt."""
        session = _bootstrapped_phase_four_session(session_factory, tmp_path)
        task = session.create_task(
            TaskCreateRequest(
                title="Reviewed Agent task",
                approval=ApprovalRequirement.HUMAN,
            )
        )
        claimed = session.claim_next_task(AgentTaskClaimRequest())
        assert claimed.attempt is not None
        submitted = session.submit_agent_result(
            agent_submit_request(task, claimed.attempt.id, _RESULT)
        )
        assert submitted.task.state is TaskState.REVIEW
        assert submitted.result.review.status is ResultReviewStatus.PENDING

        rejected = session.reject_result(
            TaskRejectRequest(
                task=task.uid,
                expected_version=submitted.task.version,
                reason="More evidence is required.",
            )
        )
        assert rejected.task.state is TaskState.OPEN
        assert rejected.result.review.status is ResultReviewStatus.REJECTED
        reclaimed = session.claim_next_task(AgentTaskClaimRequest())
        assert reclaimed.attempt is not None
        assert reclaimed.attempt.id != claimed.attempt.id

    def test_phase_four_operations_remain_within_the_selected_project(
        self,
        session_factory: PhaseFourSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Execution selection cannot cross Project ownership boundaries."""
        session = _bootstrapped_phase_four_session(session_factory, tmp_path)
        session.create_project(ProjectCreateRequest(key="DOCS", name="Documentation"))
        acme = session.create_task(TaskCreateRequest(title="ACME task"))
        docs = session.create_task(
            TaskCreateRequest(title="DOCS task", project="DOCS", priority=90)
        )

        claimed = session.claim_next_task(AgentTaskClaimRequest(project="DOCS"))
        assert claimed.task == docs
        with pytest.raises(ApplicationError) as captured:
            session.claim_task(HumanTaskClaimRequest(task=acme.uid, project="DOCS"))
        assert captured.value.code is ApplicationErrorCode.TASK_NOT_FOUND


class TestEmbeddedLocalPhaseFourSession(PhaseFourSessionContract):
    """Apply the cumulative Session contract to production LocalSession."""

    @pytest.fixture
    def session_factory(self) -> PhaseFourSessionFactory:
        """Provide a deterministic production LocalSession Phase 4 factory."""
        return _LocalSessionFactory()


def _bootstrapped_phase_four_session(
    factory: PhaseFourSessionFactory,
    tmp_path: Path,
) -> WorkaholicSession:
    """Create one deterministic Session with a bound ACME Workspace.

    Args:
        factory: Session factory under cumulative conformance.
        tmp_path: Pytest-owned isolated root.

    Returns:
        Initialized production Session.

    """
    session = factory.create(
        tmp_path / "data",
        _workspace(tmp_path, "workspace"),
    )
    session.up(UpRequest(project_key="ACME"))
    return session
