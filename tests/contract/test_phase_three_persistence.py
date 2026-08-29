"""Backend-neutral cumulative Phase 3 repository conformance tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import Barrier
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from tests.contract.phase_one import bootstrap_mutation
from tests.contract.phase_three import (
    PhaseThreeIdentifierFactory,
    PhaseThreeRepositoryFactory,
    TransactionFailurePoint,
    approve_mutation,
    block_mutation,
    cancel_mutation,
    dependency_mutation,
    phase_three_task_mutation,
    phase_three_time,
    reject_mutation,
    submit_mutation,
    unblock_mutation,
    update_mutation,
)
from tests.contract.phase_two import (
    DeterministicClock,
    DeterministicIdentifierFactory,
    phase_two_time,
)
from tests.contract.test_phase_two_persistence import PhaseTwoPersistenceContract

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetLocalStatus,
    GetTask,
    GetTaskDetails,
    IdempotencyConflictError,
    InvalidTransitionError,
    ListTasksByView,
    PermissionDeniedError,
    ReadTaskEvents,
    ResultInvalidError,
    TaskListView,
    TaskMutationResult,
    TaskResultInput,
    TaskUpdatePatch,
    UnsatisfiableDependencyError,
    VersionConflictError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    CriterionOutcome,
    CriterionStatus,
    ProposedFollowUp,
    ResultReviewStatus,
    SubjectId,
    TaskEventType,
    TaskState,
)
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from workaholic.application import (
        BootstrapResult,
        Clock,
        WorkaholicRepository,
    )

pytestmark = pytest.mark.contract

_ACCEPTANCE = (
    AcceptanceCriterion(
        id="ac_verified",
        text="The implementation is verified.",
        required=True,
    ),
)
_RESULT = TaskResultInput(
    summary="Implemented and verified.",
    criteria=(
        CriterionOutcome(
            criterion_id="ac_verified",
            status=CriterionStatus.PASSED,
            evidence="The conformance suite passes.",
        ),
    ),
    artifacts=(
        ArtifactReference(
            uri="workspace://repo/result.json",
            media_type="application/json",
            sha256="a" * 64,
        ),
    ),
    proposed_follow_ups=(ProposedFollowUp(title="Document the rollout"),),
)


@dataclass(frozen=True, slots=True)
class _SQLiteRepositoryFactory:
    """Adapt production SQLite to the backend-neutral Phase 3 factory."""

    def create(
        self,
        root: Path,
        *,
        clock: Clock | None = None,
    ) -> WorkaholicRepository:
        """Construct one independent exact-version SQLite repository.

        Args:
            root: Test-owned persistence root.
            clock: Optional authoritative readiness clock.

        Returns:
            Production repository bound below ``root``.

        """
        return cast(
            "WorkaholicRepository",
            SQLiteRepository(root / "local.db", clock=clock),
        )

    def clock(self, *, offset: int = 0) -> Clock:
        """Construct one deterministic advancing Phase 2-compatible clock."""
        return DeterministicClock(current=phase_two_time(offset))

    def identifiers(self, namespace: str) -> PhaseThreeIdentifierFactory:
        """Construct one complete deterministic identity sequence."""
        return DeterministicIdentifierFactory(namespace)

    def inject_transaction_failure(
        self,
        point: TransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Patch one SQLite Result write boundary for rollback conformance.

        Args:
            point: Semantic boundary selected by the shared suite.

        Returns:
            Scoped adapter-specific failure injection.

        """
        targets = {
            TransactionFailurePoint.RESULT_EVENT: (
                "workaholic.persistence.sqlite._task_results._insert_task_event"
            ),
            TransactionFailurePoint.RESULT_IDEMPOTENCY: (
                "workaholic.persistence.sqlite._task_results."
                "record_idempotent_result_outcome"
            ),
        }
        failure = RuntimeError(f"injected {point.value} failure")
        return cast(
            "AbstractContextManager[None]",
            patch(targets[point], side_effect=failure),
        )


class PhaseThreePersistenceContract(PhaseTwoPersistenceContract):
    """Reusable cumulative observable contract for a Phase 3 repository."""

    @pytest.fixture
    def repository_factory(self) -> PhaseThreeRepositoryFactory:
        """Provide the adapter factory under cumulative conformance."""
        message = "A concrete Phase 3 repository contract must provide its factory."
        raise NotImplementedError(message)

    def test_factory_provides_complete_ids_and_independent_connections(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Phase 3 dependencies are reproducible and connections share durability."""
        first_ids = repository_factory.identifiers("result")
        second_ids = repository_factory.identifiers("result")
        assert str(first_ids.new_result_id()) == "res_result_1"
        assert str(first_ids.new_event_id()) == "evt_result_1"
        assert str(first_ids.new_request_id()) == "req_result_1"
        assert str(second_ids.new_result_id()) == "res_result_1"

        root = tmp_path / "store"
        first, bootstrap = _bootstrapped(repository_factory, root, "independent")
        second = repository_factory.create(root)
        assert first is not second
        assert (
            second.get_local_status(
                GetLocalStatus(
                    instance_id=bootstrap.instance.id,
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                )
            ).schema_version
            == 5
        )

    def test_lifecycle_transitions_version_once_and_reject_invalid_states(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Update, block, unblock, and cancel enforce the closed state machine."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "lifecycle",
        )
        original = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "lifecycle",
            )
        )
        updated = repository.update_task_if_version(
            update_mutation(
                original,
                bootstrap.subject.id,
                "update",
                TaskUpdatePatch(priority=80),
            )
        )
        blocked = repository.block_task(
            block_mutation(updated.task, bootstrap.subject.id, "block")
        )
        with pytest.raises(InvalidTransitionError):
            repository.block_task(
                block_mutation(blocked.task, bootstrap.subject.id, "block_again")
            )
        with pytest.raises(InvalidTransitionError):
            repository.submit_human_result(
                submit_mutation(
                    blocked.task,
                    bootstrap.subject.id,
                    "submit_blocked",
                )
            )
        opened = repository.unblock_task(
            unblock_mutation(blocked.task, bootstrap.subject.id, "unblock")
        )
        cancelled = repository.cancel_task(
            cancel_mutation(opened.task, bootstrap.subject.id, "cancel")
        )

        assert tuple(
            result.task.version for result in (updated, blocked, opened, cancelled)
        ) == (2, 3, 4, 5)
        assert tuple(
            result.events[0].event_type
            for result in (updated, blocked, opened, cancelled)
        ) == (
            TaskEventType.TASK_UPDATED,
            TaskEventType.TASK_BLOCKED,
            TaskEventType.TASK_UNBLOCKED,
            TaskEventType.TASK_CANCELLED,
        )
        with pytest.raises(VersionConflictError):
            repository.update_task_if_version(
                update_mutation(
                    original,
                    bootstrap.subject.id,
                    "stale",
                    TaskUpdatePatch(priority=10),
                )
            )
        with pytest.raises(InvalidTransitionError):
            repository.cancel_task(
                cancel_mutation(cancelled.task, bootstrap.subject.id, "recancel")
            )
        with pytest.raises(InvalidTransitionError):
            repository.update_task_if_version(
                update_mutation(
                    cancelled.task,
                    bootstrap.subject.id,
                    "update_cancelled",
                    TaskUpdatePatch(priority=20),
                )
            )

    def test_exact_version_race_has_one_atomic_winner(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Independent connections cannot both commit one expected Task version."""
        root = tmp_path / "store"
        repository, bootstrap = _bootstrapped(repository_factory, root, "race")
        task = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "race",
            )
        )
        barrier = Barrier(2)

        def update(label: str) -> TaskMutationResult | ApplicationError:
            """Race one distinct update from the same authoritative snapshot."""
            connection = repository_factory.create(root)
            barrier.wait()
            try:
                return connection.update_task_if_version(
                    update_mutation(
                        task,
                        bootstrap.subject.id,
                        label,
                        TaskUpdatePatch(title=f"Winner {label}"),
                    )
                )
            except ApplicationError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(update, ("one", "two")))

        successes = tuple(
            result for result in outcomes if isinstance(result, TaskMutationResult)
        )
        failures = tuple(
            result for result in outcomes if isinstance(result, ApplicationError)
        )
        assert len(successes) == len(failures) == 1
        assert failures[0].code is ApplicationErrorCode.VERSION_CONFLICT
        persisted = repository_factory.create(root).get_task(
            GetTask(
                project_id=task.project_id,
                subject_id=bootstrap.subject.id,
                task=task.uid,
            )
        )
        assert persisted == successes[0].task
        assert persisted.version == 2

        replay_barrier = Barrier(2)

        def replay(label: str) -> TaskMutationResult | ApplicationError:
            """Race one equivalent idempotent update on independent connections."""
            connection = repository_factory.create(root)
            replay_barrier.wait()
            try:
                return connection.update_task_if_version(
                    update_mutation(
                        persisted,
                        bootstrap.subject.id,
                        label,
                        TaskUpdatePatch(priority=77),
                        idempotency_key="race-replay",
                    )
                )
            except ApplicationError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            replayed = tuple(executor.map(replay, ("replay_one", "replay_two")))
        assert all(isinstance(item, TaskMutationResult) for item in replayed)
        assert replayed[0] == replayed[1]
        assert cast("TaskMutationResult", replayed[0]).task.version == 3

    def test_dependency_cycle_and_cancelled_prerequisite_fail_closed(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Graph cycles and cancelled prerequisites never produce completion."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "dependencies",
        )
        first = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "first",
            )
        )
        second = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "second",
            )
        )
        dependant = repository.add_task_dependency(
            dependency_mutation(
                first,
                second,
                bootstrap.subject.id,
                "first_depends_second",
            )
        ).task
        with pytest.raises(ApplicationError) as duplicate:
            repository.add_task_dependency(
                dependency_mutation(
                    dependant,
                    second,
                    bootstrap.subject.id,
                    "duplicate_dependency",
                )
            )
        assert duplicate.value.code is ApplicationErrorCode.DEPENDENCY_CONFLICT
        detached = repository.remove_task_dependency(
            dependency_mutation(
                dependant,
                second,
                bootstrap.subject.id,
                "remove_dependency",
                remove=True,
            )
        ).task
        with pytest.raises(ApplicationError) as missing:
            repository.remove_task_dependency(
                dependency_mutation(
                    detached,
                    second,
                    bootstrap.subject.id,
                    "remove_missing_dependency",
                    remove=True,
                )
            )
        assert missing.value.code is ApplicationErrorCode.DEPENDENCY_CONFLICT
        dependant = repository.add_task_dependency(
            dependency_mutation(
                detached,
                second,
                bootstrap.subject.id,
                "restore_dependency",
            )
        ).task
        with pytest.raises(ApplicationError) as cycle:
            repository.add_task_dependency(
                dependency_mutation(
                    second,
                    dependant,
                    bootstrap.subject.id,
                    "second_depends_first",
                )
            )
        assert cycle.value.code is ApplicationErrorCode.DEPENDENCY_CYCLE

        cancelled = repository.cancel_task(
            cancel_mutation(second, bootstrap.subject.id, "cancel_prerequisite")
        ).task
        assert cancelled.state is TaskState.CANCELLED
        with pytest.raises(UnsatisfiableDependencyError):
            repository.submit_human_result(
                submit_mutation(
                    dependant,
                    bootstrap.subject.id,
                    "unsatisfiable",
                )
            )
        details = repository.get_task_details(
            GetTaskDetails(
                project_id=dependant.project_id,
                subject_id=bootstrap.subject.id,
                task=dependant.uid,
            )
        )
        assert details.task == dependant
        assert details.current_result is None

    def test_readiness_time_ordering_cursor_and_reads_do_not_mutate(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Derived views use authoritative time and stable cursor-bound ordering."""
        root = tmp_path / "store"
        clock = DeterministicClock(
            current=phase_three_time(10),
            step=timedelta(0),
        )
        repository, bootstrap = _bootstrapped(
            repository_factory,
            root,
            "views",
            clock=clock,
        )
        ready_high = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "ready_high",
                priority=90,
            )
        )
        ready_low = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "ready_low",
                priority=10,
            )
        )
        boundary_ready = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "boundary_ready",
                priority=50,
                available_at=phase_three_time(10),
            )
        )
        scheduled = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "scheduled",
                available_at=phase_three_time(11),
            )
        )
        first = repository.list_tasks_by_view(
            ListTasksByView(
                subject_id=bootstrap.subject.id,
                project_id=bootstrap.project.id,
                view=TaskListView.READY,
                limit=1,
            )
        )
        assert first.tasks == (ready_high,)
        assert first.next_cursor is not None
        final = repository.list_tasks_by_view(
            ListTasksByView(
                subject_id=bootstrap.subject.id,
                project_id=bootstrap.project.id,
                view=TaskListView.READY,
                cursor=first.next_cursor,
                limit=2,
            )
        )
        assert final.tasks == (boundary_ready, ready_low)
        scheduled_page = repository.list_tasks_by_view(
            ListTasksByView(
                subject_id=bootstrap.subject.id,
                project_id=bootstrap.project.id,
                view=TaskListView.SCHEDULED,
            )
        )
        assert scheduled_page.tasks == (scheduled,)

        before = repository.read_task_events_after(
            ReadTaskEvents(
                project_id=ready_high.project_id,
                subject_id=bootstrap.subject.id,
                task=ready_high.uid,
            )
        )
        repository.get_task_details(
            GetTaskDetails(
                project_id=ready_high.project_id,
                subject_id=bootstrap.subject.id,
                task=ready_high.uid,
            )
        )
        repeated = repository.read_task_events_after(
            ReadTaskEvents(
                project_id=ready_high.project_id,
                subject_id=bootstrap.subject.id,
                task=ready_high.uid,
            )
        )
        assert repeated == before

    def test_results_review_restart_rejection_and_multi_event_versioning(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Structured Human Results retain attribution through every disposition."""
        root = tmp_path / "store"
        repository, bootstrap = _bootstrapped(repository_factory, root, "results")
        reviewed_task = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "reviewed",
                approval=ApprovalRequirement.HUMAN,
                acceptance=_ACCEPTANCE,
            )
        )
        submitted = repository.submit_human_result(
            submit_mutation(
                reviewed_task,
                bootstrap.subject.id,
                "submitted",
                result=_RESULT,
                comment="Implemented manually.",
            )
        )
        assert submitted.task.state is TaskState.REVIEW
        assert submitted.task.version == 2
        assert submitted.result.attempt_id is None
        assert submitted.result.review.status is ResultReviewStatus.PENDING
        assert tuple(event.event_type for event in submitted.events) == (
            TaskEventType.RESULT_SUBMITTED,
        )

        reopened = repository_factory.create(root)
        rejected = reopened.reject_result(
            reject_mutation(
                submitted.task,
                bootstrap.subject.id,
                "rejected",
                reason="Provide clearer evidence.",
            )
        )
        assert rejected.task.state is TaskState.OPEN
        assert rejected.task.version == 3
        assert rejected.task.current_result_id is None
        assert rejected.result.review.status is ResultReviewStatus.REJECTED

        resubmitted = reopened.submit_human_result(
            submit_mutation(
                rejected.task,
                bootstrap.subject.id,
                "resubmitted",
                result=_RESULT,
            )
        )
        approved = repository_factory.create(root).approve_result(
            approve_mutation(
                resubmitted.task,
                bootstrap.subject.id,
                "approved",
                comment="Accepted.",
            )
        )
        assert approved.task.state is TaskState.DONE
        assert approved.task.version == 5
        assert approved.result.id == resubmitted.result.id
        assert approved.result.review.status is ResultReviewStatus.APPROVED
        assert tuple(event.event_type for event in approved.events) == (
            TaskEventType.REVIEW_APPROVED,
            TaskEventType.TASK_COMPLETED,
        )
        assert all(
            event.actor_subject_id == bootstrap.subject.id for event in approved.events
        )

        events = reopened.read_task_events_after(
            ReadTaskEvents(
                project_id=reviewed_task.project_id,
                subject_id=bootstrap.subject.id,
                task=reviewed_task.uid,
                limit=2,
            )
        )
        assert len(events.events) == 2
        final_events = reopened.read_task_events_after(
            ReadTaskEvents(
                project_id=reviewed_task.project_id,
                subject_id=bootstrap.subject.id,
                task=reviewed_task.uid,
                after=events.next_cursor,
                limit=10,
            )
        )
        event_types = tuple(
            event.event_type for event in (*events.events, *final_events.events)
        )
        assert event_types == (
            TaskEventType.TASK_CREATED,
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.REVIEW_REJECTED,
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.REVIEW_APPROVED,
            TaskEventType.TASK_COMPLETED,
        )

    def test_result_validation_idempotency_and_authorization_fail_closed(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Invalid content, replay conflicts, and unknown Humans never mutate."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "failures",
        )
        task = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "validated",
                acceptance=_ACCEPTANCE,
            )
        )
        with pytest.raises(InvalidTransitionError):
            repository.approve_result(
                approve_mutation(task, bootstrap.subject.id, "approve_open")
            )
        with pytest.raises(InvalidTransitionError):
            repository.reject_result(
                reject_mutation(task, bootstrap.subject.id, "reject_open")
            )
        with pytest.raises(ResultInvalidError):
            repository.submit_human_result(
                submit_mutation(task, bootstrap.subject.id, "invalid")
            )

        mutation = update_mutation(
            task,
            bootstrap.subject.id,
            "replay_one",
            TaskUpdatePatch(priority=80),
            idempotency_key="update-once",
        )
        first = repository.update_task_if_version(mutation)
        replay = repository.update_task_if_version(
            update_mutation(
                task,
                bootstrap.subject.id,
                "replay_two",
                TaskUpdatePatch(priority=80),
                idempotency_key="update-once",
            )
        )
        assert replay == first
        with pytest.raises(IdempotencyConflictError):
            repository.update_task_if_version(
                update_mutation(
                    task,
                    bootstrap.subject.id,
                    "replay_conflict",
                    TaskUpdatePatch(priority=10),
                    idempotency_key="update-once",
                )
            )

        unknown = SubjectId("sub_unknown")
        with pytest.raises(PermissionDeniedError):
            repository.update_task_if_version(
                update_mutation(
                    first.task,
                    unknown,
                    "forbidden",
                    TaskUpdatePatch(priority=20),
                )
            )
        persisted = repository.get_task(
            GetTask(
                project_id=task.project_id,
                subject_id=bootstrap.subject.id,
                task=task.uid,
            )
        )
        assert persisted == first.task

    @pytest.mark.parametrize("point", tuple(TransactionFailurePoint))
    def test_injected_result_failure_rolls_back_every_owned_record(
        self,
        repository_factory: PhaseThreeRepositoryFactory,
        tmp_path: Path,
        point: TransactionFailurePoint,
    ) -> None:
        """Failures after Result or Task writes restore state, events, and replay."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            f"rollback_{point.value}",
        )
        task = repository.create_task(
            phase_three_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                f"rollback_{point.value}",
            )
        )
        before_details = repository.get_task_details(
            GetTaskDetails(
                project_id=task.project_id,
                subject_id=bootstrap.subject.id,
                task=task.uid,
            )
        )
        before_events = repository.read_task_events_after(
            ReadTaskEvents(
                project_id=task.project_id,
                subject_id=bootstrap.subject.id,
                task=task.uid,
            )
        )
        mutation = submit_mutation(
            task,
            bootstrap.subject.id,
            f"submit_{point.value}",
            idempotency_key="rollback-submit",
        )

        with (
            repository_factory.inject_transaction_failure(point),
            pytest.raises(RuntimeError, match="injected"),
        ):
            repository.submit_human_result(mutation)

        assert (
            repository.get_task_details(
                GetTaskDetails(
                    project_id=task.project_id,
                    subject_id=bootstrap.subject.id,
                    task=task.uid,
                )
            )
            == before_details
        )
        assert (
            repository.read_task_events_after(
                ReadTaskEvents(
                    project_id=task.project_id,
                    subject_id=bootstrap.subject.id,
                    task=task.uid,
                )
            )
            == before_events
        )
        committed = repository.submit_human_result(mutation)
        assert committed.task.version == 2


class TestSQLitePhaseThreePersistence(PhaseThreePersistenceContract):
    """Apply the cumulative repository contract to production SQLite."""

    @pytest.fixture
    def repository_factory(self) -> PhaseThreeRepositoryFactory:
        """Provide the production SQLite Phase 3 factory."""
        return _SQLiteRepositoryFactory()


def _bootstrapped(
    factory: PhaseThreeRepositoryFactory,
    root: Path,
    label: str,
    *,
    clock: Clock | None = None,
) -> tuple[WorkaholicRepository, BootstrapResult]:
    """Create one repository with a committed ACME Human Owner graph.

    Args:
        factory: Adapter factory under cumulative conformance.
        root: Test-owned persistence root.
        label: Stable bootstrap identity suffix.
        clock: Optional deterministic readiness clock.

    Returns:
        Repository and authoritative bootstrap result.

    """
    repository = factory.create(root, clock=clock)
    bootstrap = repository.bootstrap_local_project(
        bootstrap_mutation(label, occurred_at=phase_three_time())
    )
    return repository, bootstrap
