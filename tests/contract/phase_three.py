"""Reusable typed builders for cumulative Phase 3 conformance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, overload

from tests.contract.phase_two import (
    PhaseTwoIdentifierFactory,
    PhaseTwoRepositoryFactory,
    PhaseTwoSessionFactory,
)

from workaholic.application import (
    AddTaskDependencyMutation,
    ApproveResultMutation,
    Clock,
    RejectResultMutation,
    RemoveTaskDependencyMutation,
    SubmitHumanResultMutation,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskCreationMutation,
    TaskResultInput,
    TaskUnblockMutation,
    TaskUpdateMutation,
    TaskUpdatePatch,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    Project,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    Task,
    TaskEventId,
    TaskId,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from workaholic.application import WorkaholicRepository
    from workaholic.session import WorkaholicSession

PHASE_THREE_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class _ExistingValues(TypedDict):
    """Precisely typed shared existing-Task mutation constructor fields."""

    task_uid: TaskId
    project_id: ProjectId
    actor_subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime
    expected_version: int
    idempotency_key: str | None


class TransactionFailurePoint(StrEnum):
    """Semantic write boundaries exposed only by conformance factories."""

    RESULT_EVENT = "result_event"
    RESULT_IDEMPOTENCY = "result_idempotency"


class PhaseThreeIdentifierFactory(PhaseTwoIdentifierFactory, Protocol):
    """Generate every deterministic identity required through Phase 3."""


class PhaseThreeRepositoryFactory(PhaseTwoRepositoryFactory, Protocol):
    """Construct exact-version repositories with deterministic failure hooks."""

    def create(
        self,
        root: Path,
        *,
        clock: Clock | None = None,
    ) -> WorkaholicRepository:
        """Construct or reopen one independent repository connection.

        Args:
            root: Test-owned backend persistence root.
            clock: Optional authoritative clock for readiness queries.

        Returns:
            Exact-version repository bound only to ``root``.

        """
        ...

    def identifiers(self, namespace: str) -> PhaseThreeIdentifierFactory:
        """Construct a complete deterministic Phase 3 identifier source.

        Args:
            namespace: Stable scenario-specific identity namespace.

        Returns:
            Independent deterministic identifier factory.

        """
        ...

    def inject_transaction_failure(
        self,
        point: TransactionFailurePoint,
    ) -> AbstractContextManager[None]:
        """Fail one semantic write boundary inside its active transaction.

        Args:
            point: Stable boundary at which the concrete adapter must fail.

        Returns:
            Context manager scoping the injected failure.

        """
        ...


class PhaseThreeSessionFactory(PhaseTwoSessionFactory, Protocol):
    """Construct isolated Sessions with deterministic lifecycle dependencies."""

    def create_with_dependencies(
        self,
        root: Path,
        workspace: Path,
        *,
        clock: Clock,
        identifiers: PhaseThreeIdentifierFactory,
    ) -> WorkaholicSession:
        """Construct one Session using exact test-owned lifecycle dependencies.

        Args:
            root: Test-owned trusted data root.
            workspace: Existing exact Workspace directory.
            clock: Authoritative deterministic lifecycle clock.
            identifiers: Deterministic complete identity factory.

        Returns:
            Isolated Session with no operator configuration dependencies.

        """
        ...


def phase_three_time(offset: int = 0) -> datetime:
    """Return a deterministic Phase 3 UTC timestamp.

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
    return PHASE_THREE_NOW + timedelta(seconds=offset)


def phase_three_task_mutation(  # noqa: PLR0913 - explicit fixture contract
    project: Project,
    subject_id: SubjectId,
    label: str,
    *,
    title: str | None = None,
    priority: int = 50,
    available_at: datetime | None = None,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    acceptance: tuple[AcceptanceCriterion, ...] = (),
    occurred_at: datetime = PHASE_THREE_NOW,
    idempotency_key: str | None = None,
) -> TaskCreationMutation:
    """Build one complete attributable Phase 3 Task creation mutation.

    Args:
        project: Authoritative selected Project.
        subject_id: Authorized Human actor.
        label: Stable identity suffix.
        title: Optional desired-outcome title.
        priority: Task priority.
        available_at: Optional scheduling boundary.
        approval: Result approval policy.
        acceptance: Ordered Task acceptance definition.
        occurred_at: Authoritative creation timestamp.
        idempotency_key: Optional caller replay key.

    Returns:
        Validated Task creation mutation.

    """
    effective_title = f"Task {label}" if title is None else title
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{label}"),
        event_id=TaskEventId(f"evt_{label}_created"),
        request_id=RequestId(f"req_{label}_created"),
        project_id=project.id,
        actor_subject_id=subject_id,
        occurred_at=occurred_at,
        title=effective_title,
        objective=effective_title,
        priority=priority,
        available_at=available_at,
        approval=approval,
        acceptance=acceptance,
        idempotency_key=idempotency_key,
    )


def update_mutation(  # noqa: PLR0913 - explicit fixture controls
    task: Task,
    actor: SubjectId,
    label: str,
    patch: TaskUpdatePatch,
    *,
    expected_version: int | None = None,
    occurred_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> TaskUpdateMutation:
    """Build one deterministic optimistic Task update.

    Args:
        task: Target Task snapshot.
        actor: Authorized Human actor.
        label: Stable request and event suffix.
        patch: Closed nonempty definition patch.
        expected_version: Optional version override.
        occurred_at: Optional authoritative timestamp.
        idempotency_key: Optional replay key.

    Returns:
        Validated update mutation.

    """
    return TaskUpdateMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=actor,
        request_id=RequestId(f"req_{label}"),
        event_id=TaskEventId(f"evt_{label}"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        occurred_at=(
            phase_three_time(task.version) if occurred_at is None else occurred_at
        ),
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        idempotency_key=idempotency_key,
        patch=patch,
    )


def block_mutation(
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    expected_version: int | None = None,
) -> TaskBlockMutation:
    """Build one deterministic optimistic Task block mutation.

    Args:
        task: Target open Task snapshot.
        actor: Authorized Human actor.
        label: Stable request and event suffix.
        expected_version: Optional version override.

    Returns:
        Validated Task block mutation.

    """
    return TaskBlockMutation(
        **_existing_values(task, actor, label, expected_version=expected_version),
        event_id=TaskEventId(f"evt_{label}"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        reason="Waiting for an explicit prerequisite.",
    )


def unblock_mutation(
    task: Task,
    actor: SubjectId,
    label: str,
) -> TaskUnblockMutation:
    """Build one deterministic optimistic Task unblock mutation.

    Args:
        task: Target blocked Task snapshot.
        actor: Authorized Human actor.
        label: Stable request and event suffix.

    Returns:
        Validated Task unblock mutation.

    """
    return TaskUnblockMutation(
        **_existing_values(task, actor, label),
        event_id=TaskEventId(f"evt_{label}"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
    )


def cancel_mutation(
    task: Task,
    actor: SubjectId,
    label: str,
) -> TaskCancelMutation:
    """Build one deterministic optimistic Task cancellation mutation.

    Args:
        task: Target mutable Task snapshot.
        actor: Authorized Human actor.
        label: Stable request and event suffix.

    Returns:
        Validated Task cancellation mutation.

    """
    return TaskCancelMutation(
        **_existing_values(task, actor, label),
        event_id=TaskEventId(f"evt_{label}"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        reason="No longer required.",
    )


@overload
def dependency_mutation(
    task: Task,
    prerequisite: Task,
    actor: SubjectId,
    label: str,
    *,
    remove: Literal[False] = False,
) -> AddTaskDependencyMutation: ...


@overload
def dependency_mutation(
    task: Task,
    prerequisite: Task,
    actor: SubjectId,
    label: str,
    *,
    remove: Literal[True],
) -> RemoveTaskDependencyMutation: ...


def dependency_mutation(
    task: Task,
    prerequisite: Task,
    actor: SubjectId,
    label: str,
    *,
    remove: bool = False,
) -> AddTaskDependencyMutation | RemoveTaskDependencyMutation:
    """Build one deterministic dependency addition or removal.

    Args:
        task: Dependant Task snapshot.
        prerequisite: Same-Project prerequisite.
        actor: Authorized Human actor.
        label: Stable request and event suffix.
        remove: Whether to build removal instead of addition.

    Returns:
        Validated dependency mutation of the selected kind.

    """
    mutation_type = (
        RemoveTaskDependencyMutation if remove else AddTaskDependencyMutation
    )
    return mutation_type(
        **_existing_values(task, actor, label),
        event_id=TaskEventId(f"evt_{label}"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        prerequisite_uid=prerequisite.uid,
    )


def submit_mutation(  # noqa: PLR0913 - explicit Result fixture contract
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    result: TaskResultInput | None = None,
    comment: str | None = None,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> SubmitHumanResultMutation:
    """Build one deterministic Human Result submission without an Attempt.

    Args:
        task: Target open Task snapshot.
        actor: Authorized Human actor.
        label: Stable Result, request, and event suffix.
        result: Optional structured Result content.
        comment: Optional Human comment.
        expected_version: Optional optimistic version override.
        idempotency_key: Optional replay key.

    Returns:
        Validated Human submission mutation.

    """
    return SubmitHumanResultMutation(
        **_existing_values(
            task,
            actor,
            label,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        ),
        result_id=ResultId(f"res_{label}"),
        result_submitted_event_id=TaskEventId(f"evt_{label}_submitted"),
        claim_expired_event_id=TaskEventId(f"evt_{label}_expired"),
        task_completed_event_id=(
            TaskEventId(f"evt_{label}_completed")
            if task.approval is ApprovalRequirement.NONE
            else None
        ),
        comment=comment,
        result=TaskResultInput() if result is None else result,
    )


def approve_mutation(
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    comment: str | None = None,
    idempotency_key: str | None = None,
) -> ApproveResultMutation:
    """Build one deterministic Human Result approval mutation.

    Args:
        task: Target review Task snapshot.
        actor: Authorized Human reviewer.
        label: Stable request and event suffix.
        comment: Optional approval comment.
        idempotency_key: Optional replay key.

    Returns:
        Validated Human approval mutation.

    """
    return ApproveResultMutation(
        **_existing_values(
            task,
            actor,
            label,
            idempotency_key=idempotency_key,
        ),
        review_approved_event_id=TaskEventId(f"evt_{label}_approved"),
        task_completed_event_id=TaskEventId(f"evt_{label}_completed"),
        comment=comment,
    )


def reject_mutation(
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    reason: str = "The evidence is incomplete.",
) -> RejectResultMutation:
    """Build one deterministic Human Result rejection mutation.

    Args:
        task: Target review Task snapshot.
        actor: Authorized Human reviewer.
        label: Stable request and event suffix.
        reason: Required rejection reason.

    Returns:
        Validated Human rejection mutation.

    """
    return RejectResultMutation(
        **_existing_values(task, actor, label),
        review_rejected_event_id=TaskEventId(f"evt_{label}_rejected"),
        reason=reason,
    )


def _existing_values(
    task: Task,
    actor: SubjectId,
    label: str,
    *,
    expected_version: int | None = None,
    idempotency_key: str | None = None,
) -> _ExistingValues:
    """Return shared deterministic fields for one existing-Task mutation.

    Args:
        task: Target Task snapshot.
        actor: Authorized Human actor.
        label: Stable request suffix.
        expected_version: Optional optimistic version override.
        idempotency_key: Optional replay key.

    Returns:
        Precisely typed common mutation constructor values.

    """
    return {
        "task_uid": task.uid,
        "project_id": task.project_id,
        "actor_subject_id": actor,
        "request_id": RequestId(f"req_{label}"),
        "occurred_at": phase_three_time(task.version),
        "expected_version": (
            task.version if expected_version is None else expected_version
        ),
        "idempotency_key": idempotency_key,
    }
