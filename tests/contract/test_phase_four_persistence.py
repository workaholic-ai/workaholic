"""Backend-neutral cumulative Phase 4 repository conformance tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from tests.contract.phase_four import (
    PhaseFourIdentifierFactory,
    PhaseFourRepositoryFactory,
    PhaseFourTransactionFailurePoint,
    agent_claim_mutation,
    agent_submit_mutation,
    human_claim_mutation,
    phase_four_time,
    progress_mutation,
    release_mutation,
    renewal_mutation,
)
from tests.contract.phase_three import (
    phase_three_task_mutation,
    reject_mutation,
    update_mutation,
)
from tests.contract.phase_two import DeterministicClock, DeterministicIdentifierFactory
from tests.contract.test_phase_three_persistence import (
    PhaseThreePersistenceContract,
    _bootstrapped,
    _SQLiteRepositoryFactory,
)

from workaholic.application import (
    ClaimNextTaskMutation,
    GetTaskDetails,
    IdempotencyConflictError,
    LeaseLostError,
    NoTaskAvailableError,
    ReadTaskEvents,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskLockedError,
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

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from workaholic.application import (
        BootstrapResult,
        Clock,
        TaskClaimResult,
        TaskDetails,
        TaskEventPage,
        TaskProgressResult,
        TaskSubmissionResult,
        WorkaholicRepository,
    )
    from workaholic.domain import SubjectId, Task

pytestmark = pytest.mark.contract

_PROGRESS = TaskProgress(
    message="Running conformance checks.",
    percent_complete=70,
    observations=(
        ProgressObservation(
            kind=ObservationKind.RISK,
            text="A retry may be required.",
        ),
    ),
)
_RESULT = TaskResultInput(summary="Implemented and verified.")


@dataclass(frozen=True, slots=True)
class _SQLitePhaseFourRepositoryFactory(_SQLiteRepositoryFactory):
    """Adapt production SQLite to the backend-neutral Phase 4 factory."""

    def identifiers(self, namespace: str) -> PhaseFourIdentifierFactory:
        """Construct one complete deterministic Phase 4 identity sequence."""
        return DeterministicIdentifierFactory(namespace)

    def inject_phase_four_failure(
        self,
        point: PhaseFourTransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Patch one SQLite semantic write boundary for rollback conformance.

        Args:
            point: Semantic Phase 4 boundary selected by the shared suite.

        Returns:
            Scoped adapter-specific failure injection.

        """
        targets = {
            PhaseFourTransactionFailurePoint.CLAIM_EVENT: (
                "workaholic.persistence.sqlite._task_claims.insert_task_event"
            ),
            PhaseFourTransactionFailurePoint.CLAIM_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._task_claims._record_idempotent_claim"
            ),
            PhaseFourTransactionFailurePoint.PROGRESS_EVENT: (
                "workaholic.persistence.sqlite._task_execution.insert_task_event"
            ),
            PhaseFourTransactionFailurePoint.PROGRESS_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._task_execution."
                "_record_idempotent_progress"
            ),
            PhaseFourTransactionFailurePoint.AGENT_RESULT_EVENT: (
                "workaholic.persistence.sqlite._task_results._insert_task_event"
            ),
            PhaseFourTransactionFailurePoint.AGENT_RESULT_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._task_results."
                "record_idempotent_result_outcome"
            ),
        }
        return cast(
            "AbstractContextManager[None]",
            patch(
                targets[point],
                side_effect=RuntimeError(f"injected {point.value} failure"),
            ),
        )


class PhaseFourPersistenceContract(PhaseThreePersistenceContract):
    """Reusable cumulative observable contract for a Phase 4 repository."""

    @pytest.fixture
    def repository_factory(self) -> PhaseFourRepositoryFactory:
        """Provide the adapter factory under cumulative conformance."""
        message = "A concrete Phase 4 repository contract must provide its factory."
        raise NotImplementedError(message)

    def test_factory_provides_attempt_ids_and_shared_restart_state(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Phase 4 identities are reproducible and independent connections agree."""
        first_ids = repository_factory.identifiers("execution")
        second_ids = repository_factory.identifiers("execution")
        assert str(first_ids.new_attempt_id()) == "atm_execution_1"
        assert str(first_ids.new_result_id()) == "res_execution_1"
        assert str(second_ids.new_attempt_id()) == "atm_execution_1"

        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            tmp_path / "store",
            "restart",
        )
        claimed = repository.claim_next_task(
            agent_claim_mutation(bootstrap.project, bootstrap.subject.id, "restart")
        )
        reopened = repository_factory.create(
            tmp_path / "store",
            clock=DeterministicClock(
                current=phase_four_time(),
                step=timedelta(0),
            ),
        )
        details = _details(reopened, task, bootstrap.subject.id)
        assert details.claim == claimed.claim
        assert details.attempt == claimed.attempt
        assert details.readiness.running is True

    def test_agent_pull_order_and_no_task_outcome_are_deterministic(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Pull order is priority-descending and active Claims remove candidates."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "ordering",
        )
        low = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "ordering_low",
                priority=10,
            )
        )
        high = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "ordering_high",
                priority=90,
            )
        )

        first = repository.claim_next_task(
            agent_claim_mutation(bootstrap.project, bootstrap.subject.id, "order_one")
        )
        second = repository.claim_next_task(
            agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "order_two",
                occurred_at=phase_four_time(1),
            )
        )

        assert (first.task, second.task) == (high, low)
        assert first.task.version == second.task.version == 1
        with pytest.raises(NoTaskAvailableError):
            repository.claim_next_task(
                agent_claim_mutation(
                    bootstrap.project,
                    bootstrap.subject.id,
                    "order_none",
                    occurred_at=phase_four_time(2),
                )
            )

    def test_human_and_agent_owner_operations_preserve_task_version(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Claim, renew, heartbeat, progress, and release never version the Task."""
        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            tmp_path / "store",
            "owners",
        )
        human = repository.claim_task(
            human_claim_mutation(task, bootstrap.subject.id, "human_claim")
        )
        assert human.attempt is None
        renewed_human = repository.renew_claim(
            renewal_mutation(
                task,
                bootstrap.subject.id,
                "human_renew",
                attempt_id=None,
                occurred_at=phase_four_time(1),
                lease_seconds=28_800,
            )
        )
        assert renewed_human.claim is not None
        assert renewed_human.claim.lease_expires_at == phase_four_time(1) + timedelta(
            hours=8
        )
        repository.release_claim(
            release_mutation(
                task,
                bootstrap.subject.id,
                "human_release",
                attempt_id=None,
                occurred_at=phase_four_time(2),
            )
        )

        agent = repository.claim_next_task(
            agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "agent_claim",
                occurred_at=phase_four_time(3),
            )
        )
        assert agent.attempt is not None
        attempt_id = agent.attempt.id
        heartbeat = repository.renew_claim(
            renewal_mutation(
                task,
                bootstrap.subject.id,
                "heartbeat",
                attempt_id=attempt_id,
                occurred_at=phase_four_time(4),
                lease_seconds=900,
            )
        )
        progress = repository.report_task_progress(
            progress_mutation(
                task,
                bootstrap.subject.id,
                attempt_id,
                "progress",
                _PROGRESS,
                occurred_at=phase_four_time(5),
            )
        )
        released = repository.release_claim(
            release_mutation(
                task,
                bootstrap.subject.id,
                "agent_release",
                attempt_id=attempt_id,
                occurred_at=phase_four_time(6),
            )
        )

        assert heartbeat.task.version == progress.task.version == 1
        assert tuple(event.event_type for event in progress.events) == (
            TaskEventType.PROGRESS_REPORTED,
            TaskEventType.OBSERVATION_ADDED,
        )
        assert released.claim is None
        assert released.attempt is not None
        assert released.attempt.status is AttemptStatus.RELEASED
        assert released.task.version == 1

    def test_active_agent_lock_and_wrong_attempt_are_inert(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Non-owner Human mutation and stale Agent tokens cannot alter ownership."""
        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            tmp_path / "store",
            "lock",
        )
        claimed = repository.claim_next_task(
            agent_claim_mutation(bootstrap.project, bootstrap.subject.id, "lock")
        )
        before = _observable_snapshot(repository, task, bootstrap.subject.id)

        with pytest.raises(TaskLockedError):
            repository.update_task_if_version(
                update_mutation(
                    task,
                    bootstrap.subject.id,
                    "locked_update",
                    TaskUpdatePatch(priority=99),
                )
            )
        with pytest.raises(LeaseLostError):
            repository.report_task_progress(
                progress_mutation(
                    task,
                    bootstrap.subject.id,
                    AttemptId("atm_wrong"),
                    "wrong_attempt",
                    TaskProgress(message="Must not persist."),
                    occurred_at=phase_four_time(1),
                )
            )

        assert _observable_snapshot(repository, task, bootstrap.subject.id) == before
        assert claimed.task.version == 1

    def test_exact_expiry_is_read_only_then_reclaim_materializes_once(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Reads hide stale ownership; reclaim emits exact expiry and new Claim."""
        root = tmp_path / "store"
        clock = DeterministicClock(
            current=phase_four_time(1),
            step=timedelta(0),
        )
        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            root,
            "expiry",
            clock=clock,
        )
        original = repository.claim_next_task(
            agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "short",
                lease_seconds=1,
            )
        )
        assert original.attempt is not None
        before_events = _events(repository, task, bootstrap.subject.id)

        stale = _details(repository, task, bootstrap.subject.id)
        reopened_stale = _details(
            repository_factory.create(root, clock=clock),
            task,
            bootstrap.subject.id,
        )
        assert reopened_stale == stale
        assert stale.claim is None
        assert stale.attempt is None
        assert stale.readiness.ready is True
        assert stale.readiness.stale is True
        assert stale.readiness.reasons == (ReadinessReason.STALE_CLAIM,)
        assert _events(repository, task, bootstrap.subject.id) == before_events

        reclaimed = repository.claim_next_task(
            agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "reclaim",
                occurred_at=phase_four_time(2),
            )
        )
        assert tuple(event.event_type for event in reclaimed.events) == (
            TaskEventType.CLAIM_EXPIRED,
            TaskEventType.TASK_CLAIMED,
        )
        expiry = reclaimed.events[0]
        assert expiry.attempt_id == original.attempt.id
        assert expiry.payload == {"lease_expires_at": "2026-08-10T12:00:01.000000Z"}
        with pytest.raises(LeaseLostError):
            repository.release_claim(
                release_mutation(
                    task,
                    bootstrap.subject.id,
                    "stale_release",
                    attempt_id=original.attempt.id,
                    occurred_at=phase_four_time(3),
                )
            )

    @pytest.mark.parametrize(
        "approval",
        [ApprovalRequirement.NONE, ApprovalRequirement.HUMAN],
    )
    def test_agent_submission_ends_claim_versions_once_and_supports_review(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
        approval: ApprovalRequirement,
    ) -> None:
        """Agent submission always terminates its Attempt, including review entry."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            f"submit_{approval.value}",
        )
        task = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                f"submit_{approval.value}",
                approval=approval,
            )
        )
        claimed = repository.claim_next_task(
            agent_claim_mutation(bootstrap.project, bootstrap.subject.id, "submit")
        )
        assert claimed.attempt is not None

        submitted = repository.submit_agent_result(
            agent_submit_mutation(
                task,
                bootstrap.subject.id,
                claimed.attempt.id,
                "submit",
                _RESULT,
                occurred_at=phase_four_time(1),
            )
        )

        assert submitted.task.version == 2
        assert submitted.result.attempt_id == claimed.attempt.id
        assert submitted.attempt is not None
        assert submitted.attempt.status is AttemptStatus.SUBMITTED
        assert submitted.attempt.ended_at == phase_four_time(1)
        assert _details(repository, task, bootstrap.subject.id).claim is None
        if approval is ApprovalRequirement.NONE:
            assert submitted.task.state is TaskState.DONE
            assert tuple(event.event_type for event in submitted.events) == (
                TaskEventType.RESULT_SUBMITTED,
                TaskEventType.TASK_COMPLETED,
            )
        else:
            assert submitted.task.state is TaskState.REVIEW
            assert submitted.result.review.status is ResultReviewStatus.PENDING
            rejection = reject_mutation(
                submitted.task,
                bootstrap.subject.id,
                "reject_agent",
            ).model_copy(update={"occurred_at": phase_four_time(2)})
            rejected = repository.reject_result(rejection)
            assert rejected.task.state is TaskState.OPEN
            assert rejected.task.version == 3
            reclaimed = repository.claim_next_task(
                agent_claim_mutation(
                    bootstrap.project,
                    bootstrap.subject.id,
                    "after_reject",
                    occurred_at=phase_four_time(3),
                )
            )
            assert reclaimed.attempt is not None
            assert reclaimed.attempt.id != submitted.attempt.id

    def test_claim_progress_and_submission_replays_are_closed_and_conflicting(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Every Phase 4 replay returns one outcome and rejects changed semantics."""
        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            tmp_path / "store",
            "replay",
        )
        claim = agent_claim_mutation(
            bootstrap.project,
            bootstrap.subject.id,
            "claim_original",
            idempotency_key="claim-once",
        )
        claimed = repository.claim_next_task(claim)
        replayed_claim = repository.claim_next_task(
            agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "claim_replay",
                idempotency_key="claim-once",
            )
        )
        assert replayed_claim == claimed
        assert claimed.attempt is not None
        with pytest.raises(IdempotencyConflictError):
            repository.claim_next_task(
                agent_claim_mutation(
                    bootstrap.project,
                    bootstrap.subject.id,
                    "claim_conflict",
                    lease_seconds=901,
                    idempotency_key="claim-once",
                )
            )

        progress = progress_mutation(
            task,
            bootstrap.subject.id,
            claimed.attempt.id,
            "progress_original",
            _PROGRESS,
            occurred_at=phase_four_time(1),
            idempotency_key="progress-once",
        )
        first_progress = repository.report_task_progress(progress)
        replayed_progress = repository.report_task_progress(
            progress_mutation(
                task,
                bootstrap.subject.id,
                claimed.attempt.id,
                "progress_replay",
                _PROGRESS,
                occurred_at=phase_four_time(2),
                idempotency_key="progress-once",
            )
        )
        assert replayed_progress == first_progress

        submission = agent_submit_mutation(
            task,
            bootstrap.subject.id,
            claimed.attempt.id,
            "submit_original",
            _RESULT,
            occurred_at=phase_four_time(3),
            idempotency_key="submit-once",
        )
        first_submission = repository.submit_agent_result(submission)
        replayed_submission = repository.submit_agent_result(
            agent_submit_mutation(
                task,
                bootstrap.subject.id,
                claimed.attempt.id,
                "submit_replay",
                _RESULT,
                occurred_at=phase_four_time(4),
                idempotency_key="submit-once",
            )
        )
        assert replayed_submission == first_submission

    @pytest.mark.parametrize("point", tuple(PhaseFourTransactionFailurePoint))
    def test_injected_phase_four_failure_rolls_back_all_observable_state(
        self,
        repository_factory: PhaseFourRepositoryFactory,
        tmp_path: Path,
        point: PhaseFourTransactionFailurePoint,
    ) -> None:
        """Late Claim, progress, and Result failures consume no durable state."""
        repository, bootstrap, task = _repository_with_task(
            repository_factory,
            tmp_path / "store",
            f"rollback_{point.value}",
        )
        mutation: (
            ClaimNextTaskMutation
            | ReportTaskProgressMutation
            | SubmitAgentResultMutation
        )
        if point in {
            PhaseFourTransactionFailurePoint.CLAIM_EVENT,
            PhaseFourTransactionFailurePoint.CLAIM_IDEMPOTENCY,
        }:
            mutation = agent_claim_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                f"rollback_{point.value}",
                idempotency_key="rollback",
            )
        else:
            claimed = repository.claim_next_task(
                agent_claim_mutation(
                    bootstrap.project,
                    bootstrap.subject.id,
                    f"owner_{point.value}",
                )
            )
            assert claimed.attempt is not None
            if point in {
                PhaseFourTransactionFailurePoint.PROGRESS_EVENT,
                PhaseFourTransactionFailurePoint.PROGRESS_IDEMPOTENCY,
            }:
                progress = progress_mutation(
                    task,
                    bootstrap.subject.id,
                    claimed.attempt.id,
                    f"rollback_{point.value}",
                    _PROGRESS,
                    occurred_at=phase_four_time(1),
                    idempotency_key="rollback",
                )
                mutation = progress
            else:
                submission = agent_submit_mutation(
                    task,
                    bootstrap.subject.id,
                    claimed.attempt.id,
                    f"rollback_{point.value}",
                    _RESULT,
                    occurred_at=phase_four_time(1),
                    idempotency_key="rollback",
                )
                mutation = submission
        before = _observable_snapshot(repository, task, bootstrap.subject.id)

        with (
            repository_factory.inject_phase_four_failure(point),
            pytest.raises(RuntimeError, match="injected"),
        ):
            _run_phase_four_mutation(repository, mutation)

        assert _observable_snapshot(repository, task, bootstrap.subject.id) == before
        committed = _run_phase_four_mutation(repository, mutation)
        assert committed.task.uid == task.uid


class TestSQLitePhaseFourPersistence(PhaseFourPersistenceContract):
    """Apply the cumulative Phase 4 repository contract to SQLite."""

    @pytest.fixture
    def repository_factory(self) -> PhaseFourRepositoryFactory:
        """Provide the production SQLite Phase 4 factory."""
        return _SQLitePhaseFourRepositoryFactory()


def _repository_with_task(
    factory: PhaseFourRepositoryFactory,
    root: Path,
    label: str,
    *,
    clock: Clock | None = None,
) -> tuple[WorkaholicRepository, BootstrapResult, Task]:
    """Create one bootstrapped repository and ready Task.

    Args:
        factory: Adapter factory under conformance.
        root: Test-owned persistence root.
        label: Stable fixture identity suffix.
        clock: Optional deterministic query clock.

    Returns:
        Repository, bootstrap graph, and ready Task.

    """
    fixture_clock = clock or DeterministicClock(
        current=phase_four_time(),
        step=timedelta(0),
    )
    repository, bootstrap = _bootstrapped(
        factory,
        root,
        label,
        clock=fixture_clock,
    )
    task = repository.create_task(
        phase_three_task_mutation(
            bootstrap.project,
            bootstrap.subject.id,
            label,
        )
    )
    return repository, bootstrap, task


def _details(
    repository: WorkaholicRepository,
    task: Task,
    subject_id: SubjectId,
) -> TaskDetails:
    """Read one complete Task detail projection."""
    return repository.get_task_details(
        GetTaskDetails(
            project_id=task.project_id,
            subject_id=subject_id,
            task=task.uid,
        )
    )


def _events(
    repository: WorkaholicRepository,
    task: Task,
    subject_id: SubjectId,
) -> TaskEventPage:
    """Read every Task event through the backend-neutral query contract."""
    return repository.read_task_events_after(
        ReadTaskEvents(
            project_id=task.project_id,
            subject_id=subject_id,
            task=task.uid,
            limit=500,
        )
    )


def _observable_snapshot(
    repository: WorkaholicRepository,
    task: Task,
    subject_id: SubjectId,
) -> tuple[object, object]:
    """Capture all public Task detail and event state for rollback assertions."""
    return _details(repository, task, subject_id), _events(
        repository,
        task,
        subject_id,
    )


def _run_phase_four_mutation(
    repository: WorkaholicRepository,
    mutation: (
        ClaimNextTaskMutation | ReportTaskProgressMutation | SubmitAgentResultMutation
    ),
) -> TaskClaimResult | TaskProgressResult | TaskSubmissionResult:
    """Dispatch one typed Phase 4 mutation for parameterized rollback tests.

    Args:
        repository: Repository under conformance.
        mutation: Exact Claim, progress, or Agent Result mutation.

    Returns:
        Validated semantic operation result.

    """
    if isinstance(mutation, ClaimNextTaskMutation):
        return repository.claim_next_task(mutation)
    if isinstance(mutation, ReportTaskProgressMutation):
        return repository.report_task_progress(mutation)
    return repository.submit_agent_result(mutation)
