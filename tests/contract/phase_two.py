"""Reusable typed builders for cumulative Phase 2 conformance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from tests.contract.phase_one import (
    PhaseOneRepositoryFactory,
    PhaseOneSessionFactory,
)

from workaholic.application import (
    Clock,
    ExecutionIdentifierFactory,
    IdentifierFactory,
    ProjectCreationMutation,
    TaskCreationMutation,
)
from workaholic.domain import (
    AttemptId,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import BootstrapResult, WorkaholicRepository
    from workaholic.domain import Project
    from workaholic.session import WorkaholicSession

PHASE_TWO_NOW = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)


class PhaseTwoRepositoryFactory(PhaseOneRepositoryFactory, Protocol):
    """Construct an isolated repository with deterministic test dependencies."""

    def create(self, root: Path) -> WorkaholicRepository:
        """Construct or reopen an exact-version repository.

        Args:
            root: Test-owned backend persistence root.

        Returns:
            Repository bound to that root without invoking an operation.

        """
        ...

    def clock(self, *, offset: int = 0) -> Clock:
        """Construct a deterministic authoritative clock.

        Args:
            offset: Nonnegative second offset from the Phase 2 fixture time.

        Returns:
            Deterministic clock suitable for application composition.

        """
        ...

    def identifiers(self, namespace: str) -> PhaseTwoIdentifierFactory:
        """Construct a deterministic identifier sequence.

        Args:
            namespace: Stable scenario-specific identifier namespace.

        Returns:
            Deterministic identifier factory suitable for application composition.

        """
        ...


class PhaseTwoIdentifierFactory(
    IdentifierFactory,
    ExecutionIdentifierFactory,
    Protocol,
):
    """Generate every deterministic identity required through Phase 4."""


class PhaseTwoSessionFactory(PhaseOneSessionFactory, Protocol):
    """Construct isolated cumulative Sessions and trusted profile registries."""

    def create(self, root: Path, workspace: Path) -> WorkaholicSession:
        """Construct a default-profile Session without invoking an operation.

        Args:
            root: Test-owned trusted data root.
            workspace: Existing exact Workspace directory.

        Returns:
            Session isolated from operator configuration and state.

        """
        ...

    def create_profiled(
        self,
        root: Path,
        workspace: Path,
        *,
        profiles: tuple[str, ...],
        default_profile: str,
        environment_profile: str | None = None,
    ) -> WorkaholicSession:
        """Construct a Session over an explicit isolated profile registry.

        Args:
            root: Test-owned parent for profile data directories.
            workspace: Existing exact Workspace directory.
            profiles: Ordered trusted embedded profile names.
            default_profile: Configured registry default.
            environment_profile: Optional trusted environment override.

        Returns:
            Profile-aware Session isolated from operator configuration and state.

        """
        ...


@dataclass(slots=True)
class DeterministicClock:
    """Thread-safe clock that advances one fixed step after each read."""

    current: datetime = PHASE_TWO_NOW
    step: timedelta = timedelta(seconds=1)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def now(self) -> datetime:
        """Return the next deterministic authoritative timestamp.

        Returns:
            Current timestamp before advancing by one configured step.

        """
        with self._lock:
            result = self.current
            self.current += self.step
            return result


@dataclass(slots=True)
class DeterministicIdentifierFactory:
    """Thread-safe deterministic prefixed identifier generator."""

    namespace: str
    _counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate the stable identifier namespace."""
        candidate: object = self.namespace
        if (
            not isinstance(candidate, str)
            or not candidate
            or not candidate.replace("_", "").isalnum()
        ):
            message = "Conformance identifier namespace must be alphanumeric."
            raise ValueError(message)

    def new_instance_id(self) -> InstanceId:
        """Return the next deterministic Instance identifier."""
        return InstanceId(self._next("ins"))

    def new_project_id(self) -> ProjectId:
        """Return the next deterministic Project identifier."""
        return ProjectId(self._next("prj"))

    def new_subject_id(self) -> SubjectId:
        """Return the next deterministic Subject identifier."""
        return SubjectId(self._next("sub"))

    def new_task_id(self) -> TaskId:
        """Return the next deterministic Task identifier."""
        return TaskId(self._next("tsk"))

    def new_result_id(self) -> ResultId:
        """Return the next deterministic Result identifier."""
        return ResultId(self._next("res"))

    def new_event_id(self) -> TaskEventId:
        """Return the next deterministic TaskEvent identifier."""
        return TaskEventId(self._next("evt"))

    def new_request_id(self) -> RequestId:
        """Return the next deterministic request identifier."""
        return RequestId(self._next("req"))

    def new_attempt_id(self) -> AttemptId:
        """Return the next deterministic Agent Attempt identifier."""
        return AttemptId(self._next("atm"))

    def _next(self, prefix: str) -> str:
        """Return the next text identifier for one fixed prefix.

        Args:
            prefix: Internal identifier-kind prefix without its underscore.

        Returns:
            Stable prefixed opaque identifier text.

        """
        with self._lock:
            number = self._counts.get(prefix, 0) + 1
            self._counts[prefix] = number
        return f"{prefix}_{self.namespace}_{number}"


def phase_two_time(offset: int = 0) -> datetime:
    """Return a deterministic Phase 2 UTC timestamp.

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
    return PHASE_TWO_NOW + timedelta(seconds=offset)


def project_mutation(  # noqa: PLR0913 - explicit semantic fixture
    bootstrap: BootstrapResult,
    label: str,
    *,
    key: str = "DOCS",
    name: str = "Documentation",
    idempotency_key: str | None = None,
    occurred_at: datetime = PHASE_TWO_NOW,
) -> ProjectCreationMutation:
    """Build one deterministic Project creation mutation.

    Args:
        bootstrap: Authoritative initialized Instance and actor.
        label: Candidate Project and request identifier suffix.
        key: Candidate immutable Project key.
        name: Candidate Human-readable Project name.
        idempotency_key: Optional caller replay key.
        occurred_at: Authoritative candidate timestamp.

    Returns:
        Validated semantic Project mutation.

    """
    return ProjectCreationMutation(
        project_id=ProjectId(f"prj_{label}"),
        request_id=RequestId(f"req_project_{label}"),
        instance_id=bootstrap.instance.id,
        actor_subject_id=bootstrap.subject.id,
        occurred_at=occurred_at,
        project_key=key,
        project_name=name,
        idempotency_key=idempotency_key,
    )


def project_task_mutation(  # noqa: PLR0913 - explicit semantic fixture
    project: Project,
    subject_id: SubjectId,
    label: str,
    *,
    title: str | None = None,
    idempotency_key: str | None = None,
    event_label: str | None = None,
    occurred_at: datetime = PHASE_TWO_NOW,
) -> TaskCreationMutation:
    """Build one deterministic Task mutation for any selected Project.

    Args:
        project: Authoritative selected Project.
        subject_id: Authorized actor Subject.
        label: Candidate Task and request identifier suffix.
        title: Optional Task title; defaults to a label-derived value.
        idempotency_key: Optional caller replay key.
        event_label: Optional independent TaskEvent identifier suffix.
        occurred_at: Authoritative candidate timestamp.

    Returns:
        Validated semantic Task mutation.

    """
    normalized_title = f"Task {label}" if title is None else title
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{label}"),
        event_id=TaskEventId(f"evt_{label if event_label is None else event_label}"),
        request_id=RequestId(f"req_task_{label}"),
        project_id=project.id,
        actor_subject_id=subject_id,
        occurred_at=occurred_at,
        title=normalized_title,
        objective=normalized_title,
        priority=50,
        idempotency_key=idempotency_key,
    )
