"""Reusable typed builders for Phase 1 repository and Session conformance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from workaholic.application import (
    BootstrapMutation,
    BootstrapResult,
    PhaseOneRepository,
    TaskCreationMutation,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.session import WorkaholicSession

PHASE_ONE_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class PhaseOneRepositoryFactory(Protocol):
    """Construct one repository over a test-owned persistence root."""

    def create(self, root: Path) -> PhaseOneRepository:
        """Construct or reopen a repository without mutating state.

        Args:
            root: Test-owned backend persistence root.

        Returns:
            Repository bound to that root.

        """
        ...


class PhaseOneSessionFactory(Protocol):
    """Construct one Session over test-owned data and Workspace roots."""

    def create(self, root: Path, workspace: Path) -> WorkaholicSession:
        """Construct or reopen a Session without invoking an operation.

        Args:
            root: Test-owned trusted local data root.
            workspace: Existing exact Workspace directory.

        Returns:
            Session bound to that data root and Workspace.

        """
        ...


def bootstrap_mutation(
    label: str,
    *,
    project_key: str = "ACME",
    idempotency_key: str | None = None,
    occurred_at: datetime = PHASE_ONE_NOW,
) -> BootstrapMutation:
    """Build one deterministic candidate bootstrap mutation.

    Args:
        label: Identifier suffix unique within the scenario.
        project_key: Candidate immutable Project key.
        idempotency_key: Optional caller replay key.
        occurred_at: Authoritative candidate timestamp.

    Returns:
        Validated bootstrap mutation.

    """
    return BootstrapMutation(
        instance_id=InstanceId(f"ins_{label}"),
        project_id=ProjectId(f"prj_{label}"),
        subject_id=SubjectId(f"sub_{label}"),
        request_id=RequestId(f"req_{label}"),
        occurred_at=occurred_at,
        project_key=project_key,
        idempotency_key=idempotency_key,
    )


def task_mutation(  # noqa: PLR0913
    bootstrap: BootstrapResult,
    label: str,
    *,
    title: str | None = None,
    objective: str | None = None,
    priority: int = 50,
    idempotency_key: str | None = None,
    event_label: str | None = None,
    occurred_at: datetime = PHASE_ONE_NOW,
) -> TaskCreationMutation:
    """Build one deterministic attributable Task mutation.

    Args:
        bootstrap: Authoritative bootstrap graph for Project and actor IDs.
        label: Candidate Task and request identifier suffix.
        title: Optional Task title; defaults to a label-derived value.
        objective: Optional objective; defaults to the normalized title.
        priority: Candidate Task priority.
        idempotency_key: Optional caller replay key.
        event_label: Optional independent TaskEvent identifier suffix.
        occurred_at: Authoritative candidate timestamp.

    Returns:
        Validated Task creation mutation.

    """
    normalized_title = f"Task {label}" if title is None else title
    normalized_objective = normalized_title if objective is None else objective
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{label}"),
        event_id=TaskEventId(f"evt_{label if event_label is None else event_label}"),
        request_id=RequestId(f"req_{label}"),
        project_id=bootstrap.project.id,
        actor_subject_id=bootstrap.subject.id,
        occurred_at=occurred_at,
        title=normalized_title,
        objective=normalized_objective,
        priority=priority,
        idempotency_key=idempotency_key,
    )


def later_timestamp(offset: int) -> datetime:
    """Return one deterministic later authoritative timestamp.

    Args:
        offset: Positive second offset from the Phase 1 fixture time.

    Returns:
        UTC fixture timestamp advanced by ``offset`` seconds.

    Raises:
        ValueError: If ``offset`` is not positive.

    """
    if type(offset) is not int or offset < 1:
        message = "Conformance timestamp offset must be a positive integer."
        raise ValueError(message)
    return PHASE_ONE_NOW + timedelta(seconds=offset)
