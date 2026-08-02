"""Transport-neutral cumulative Phase 3 Session conformance tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from tests.contract.phase_three import (
    PhaseThreeIdentifierFactory,
    PhaseThreeSessionFactory,
    phase_three_time,
)
from tests.contract.phase_two import (
    DeterministicClock,
    DeterministicIdentifierFactory,
)
from tests.contract.test_phase_two_session import (
    PhaseTwoSessionContract,
    _workspace,
    _write_profile_registry,
)
from tests.contract.test_phase_two_session import (
    _LocalSessionFactory as _PhaseTwoLocalSessionFactory,
)

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    Clock,
    TaskListView,
    TaskResultInput,
    TaskUpdatePatch,
)
from workaholic.composition import LocalCompositionFactories, create_local_session
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    CriterionOutcome,
    CriterionStatus,
    ResultReviewStatus,
    TaskEventType,
    TaskState,
)
from workaholic.persistence.sqlite import SQLiteLocalActorSelector, SQLiteRepository
from workaholic.session import (
    ProjectCreateRequest,
    TaskAddDependencyRequest,
    TaskApproveRequest,
    TaskBlockRequest,
    TaskCreateRequest,
    TaskDetailsRequest,
    TaskEventsRequest,
    TaskListByViewRequest,
    TaskRejectRequest,
    TaskSubmitRequest,
    TaskUnblockRequest,
    TaskUpdateRequest,
    UpRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.session import WorkaholicSession

pytestmark = pytest.mark.contract


@dataclass(slots=True)
class _LocalSessionFactory(_PhaseTwoLocalSessionFactory):
    """Extend the cumulative local factory with explicit lifecycle inputs."""

    def create_with_dependencies(
        self,
        root: Path,
        workspace: Path,
        *,
        clock: Clock,
        identifiers: PhaseThreeIdentifierFactory,
    ) -> WorkaholicSession:
        """Construct a LocalSession using exact deterministic dependencies.

        Args:
            root: Test-owned profile data parent.
            workspace: Existing exact Workspace directory.
            clock: Authoritative deterministic lifecycle clock.
            identifiers: Complete deterministic identity source.

        Returns:
            Production LocalSession isolated from operator configuration.

        """
        config_directory = root.parent / f".{root.name}-config"
        _write_profile_registry(
            config_directory,
            root=root,
            profiles=("local",),
            default_profile="local",
        )
        return create_local_session(
            cwd=workspace,
            environment={"WORKAHOLIC_CONFIG_DIR": str(config_directory)},
            factories=LocalCompositionFactories(
                repository=SQLiteRepository,
                identity=SQLiteLocalActorSelector,
                clock=lambda: clock,
                identifiers=lambda: identifiers,
            ),
        )


class PhaseThreeSessionContract(PhaseTwoSessionContract):
    """Reusable cumulative observable contract for a Phase 3 Session."""

    @pytest.fixture
    def session_factory(self) -> PhaseThreeSessionFactory:
        """Provide the Session factory under cumulative conformance."""
        message = "A concrete Phase 3 Session contract must provide its factory."
        raise NotImplementedError(message)

    def test_explicit_lifecycle_dependencies_are_exact_and_reproducible(
        self,
        session_factory: PhaseThreeSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Factory-supplied time and identities own every generated field."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create_with_dependencies(
            root,
            workspace,
            clock=DeterministicClock(current=phase_three_time()),
            identifiers=DeterministicIdentifierFactory("session"),
        )

        bootstrap = session.up(UpRequest(project_key="ACME"))
        task = session.create_task(TaskCreateRequest(title="Deterministic task"))

        assert str(bootstrap.instance.id) == "ins_session_1"
        assert str(bootstrap.project.id) == "prj_session_1"
        assert str(bootstrap.subject.id) == "sub_session_1"
        assert bootstrap.instance.created_at == phase_three_time()
        assert str(task.uid) == "tsk_session_1"
        assert task.created_at == phase_three_time(1)

    def test_lifecycle_requests_preserve_versions_replay_and_invalidity(
        self,
        session_factory: PhaseThreeSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Session transitions enforce exact versions without implicit retry."""
        session = _bootstrapped_session(session_factory, tmp_path)
        original = session.create_task(TaskCreateRequest(title="Lifecycle task"))
        update_request = TaskUpdateRequest(
            task=original.uid,
            expected_version=1,
            idempotency_key="update-once",
            patch=TaskUpdatePatch(priority=90),
        )
        first = session.update_task(update_request)
        replay = session.update_task(update_request)
        assert replay == first
        assert first.task.version == 2

        with pytest.raises(ApplicationError) as stale:
            session.update_task(
                TaskUpdateRequest(
                    task=original.uid,
                    expected_version=1,
                    patch=TaskUpdatePatch(priority=10),
                )
            )
        assert stale.value.code is ApplicationErrorCode.VERSION_CONFLICT

        blocked = session.block_task(
            TaskBlockRequest(
                task=original.uid,
                expected_version=2,
                reason="Waiting for input.",
            )
        )
        assert blocked.task.state is TaskState.BLOCKED
        assert blocked.task.version == 3
        with pytest.raises(ApplicationError) as invalid:
            session.block_task(
                TaskBlockRequest(
                    task=original.uid,
                    expected_version=3,
                    reason="Still waiting.",
                )
            )
        assert invalid.value.code is ApplicationErrorCode.INVALID_TRANSITION
        opened = session.unblock_task(
            TaskUnblockRequest(task=original.uid, expected_version=3)
        )
        assert opened.task.state is TaskState.OPEN
        assert opened.task.version == 4

    def test_dependencies_drive_ready_views_without_read_side_mutation(
        self,
        session_factory: PhaseThreeSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Completing a prerequisite changes only derived dependant readiness."""
        session = _bootstrapped_session(session_factory, tmp_path)
        prerequisite = session.create_task(
            TaskCreateRequest(title="Complete first", priority=50)
        )
        dependant = session.create_task(
            TaskCreateRequest(title="Complete second", priority=90)
        )
        linked = session.add_task_dependency(
            TaskAddDependencyRequest(
                task=dependant.uid,
                prerequisite=prerequisite.uid,
                expected_version=1,
            )
        ).task
        before = session.read_task_events(TaskEventsRequest(task=dependant.uid))
        ready_before = session.list_tasks_by_view(
            TaskListByViewRequest(view=TaskListView.READY)
        )
        assert ready_before.tasks == (prerequisite,)

        completed = session.submit_human_result(
            TaskSubmitRequest(task=prerequisite.uid, expected_version=1)
        )
        assert completed.task.state is TaskState.DONE
        ready_after = session.list_tasks_by_view(
            TaskListByViewRequest(view=TaskListView.READY)
        )
        assert ready_after.tasks == (linked,)
        assert ready_after.readiness[0].ready is True
        assert session.read_task_events(TaskEventsRequest(task=dependant.uid)) == before

    def test_result_review_rejection_restart_and_event_pagination(
        self,
        session_factory: PhaseThreeSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Human review keeps immutable Results and attributable ordered events."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)
        session.up(UpRequest(project_key="ACME"))
        task = session.create_task(
            TaskCreateRequest(
                title="Reviewed work",
                approval=ApprovalRequirement.HUMAN,
                acceptance=(
                    AcceptanceCriterion(
                        id="ac_verified",
                        text="The implementation is verified.",
                        required=True,
                    ),
                ),
            )
        )
        result_input = TaskResultInput(
            summary="Implemented and verified.",
            criteria=(
                CriterionOutcome(
                    criterion_id="ac_verified",
                    status=CriterionStatus.PASSED,
                    evidence="Contract suite passes.",
                ),
            ),
            artifacts=(),
            proposed_follow_ups=(),
        )
        submitted = session.submit_human_result(
            TaskSubmitRequest(
                task=task.uid,
                expected_version=1,
                comment="First candidate.",
                result=result_input,
            )
        )
        assert submitted.task.state is TaskState.REVIEW
        assert submitted.result.attempt_id is None
        assert submitted.result.review.status is ResultReviewStatus.PENDING

        rejected = session.reject_result(
            TaskRejectRequest(
                task=task.uid,
                expected_version=2,
                reason="Evidence needs clarification.",
            )
        )
        assert rejected.task.state is TaskState.OPEN
        assert rejected.result.id == submitted.result.id
        assert rejected.result.review.status is ResultReviewStatus.REJECTED
        assert rejected.task.current_result_id is None

        restarted = session_factory.create(root, workspace)
        resubmitted = restarted.submit_human_result(
            TaskSubmitRequest(
                task=task.uid,
                expected_version=3,
                comment="Clarified candidate.",
                result=result_input,
            )
        )
        approved = restarted.approve_result(
            TaskApproveRequest(
                task=task.uid,
                expected_version=4,
                comment="Accepted.",
            )
        )
        assert approved.task.state is TaskState.DONE
        assert approved.task.version == 5
        assert approved.result.id == resubmitted.result.id
        assert approved.result.review.status is ResultReviewStatus.APPROVED
        details = restarted.get_task_details(TaskDetailsRequest(task=task.uid))
        assert details.current_result == approved.result

        first_page = restarted.read_task_events(
            TaskEventsRequest(task=task.uid, limit=2)
        )
        second_page = restarted.read_task_events(
            TaskEventsRequest(
                task=task.uid,
                after=first_page.next_cursor,
                limit=10,
            )
        )
        events = (*first_page.events, *second_page.events)
        assert tuple(event.event_type for event in events) == (
            TaskEventType.TASK_CREATED,
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.REVIEW_REJECTED,
            TaskEventType.RESULT_SUBMITTED,
            TaskEventType.REVIEW_APPROVED,
            TaskEventType.TASK_COMPLETED,
        )
        assert tuple(event.cursor for event in events) == tuple(
            sorted(event.cursor for event in events)
        )
        assert all(event.attempt_id is None for event in events)
        assert all(
            event.actor_subject_id == approved.result.submitted_by for event in events
        )

    def test_phase_three_operations_recheck_project_authority(
        self,
        session_factory: PhaseThreeSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Explicit Project selection cannot cross Task ownership boundaries."""
        session = _bootstrapped_session(session_factory, tmp_path)
        session.create_project(ProjectCreateRequest(key="DOCS", name="Documentation"))
        acme_task = session.create_task(TaskCreateRequest(title="ACME task"))
        docs_task = session.create_task(
            TaskCreateRequest(title="DOCS task", project="DOCS")
        )

        operations = (
            lambda: session.update_task(
                TaskUpdateRequest(
                    task=acme_task.uid,
                    project="DOCS",
                    expected_version=1,
                    patch=TaskUpdatePatch(priority=80),
                )
            ),
            lambda: session.add_task_dependency(
                TaskAddDependencyRequest(
                    task=acme_task.uid,
                    prerequisite=docs_task.uid,
                    expected_version=1,
                )
            ),
            lambda: session.read_task_events(
                TaskEventsRequest(task=acme_task.uid, project="DOCS")
            ),
        )
        for operation in operations:
            with pytest.raises(ApplicationError) as captured:
                operation()
            assert captured.value.code in {
                ApplicationErrorCode.TASK_NOT_FOUND,
                ApplicationErrorCode.DEPENDENCY_CONFLICT,
            }
        assert (
            session.get_task_details(
                TaskDetailsRequest(task=acme_task.uid)
            ).task.version
            == 1
        )


class TestEmbeddedLocalPhaseThreeSession(PhaseThreeSessionContract):
    """Apply the cumulative Session contract to production LocalSession."""

    @pytest.fixture
    def session_factory(self) -> PhaseThreeSessionFactory:
        """Provide a deterministic production LocalSession Phase 3 factory."""
        return _LocalSessionFactory()


def _bootstrapped_session(
    factory: PhaseThreeSessionFactory,
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
