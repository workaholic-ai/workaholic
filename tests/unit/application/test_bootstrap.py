"""Unit tests for local bootstrap application orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    BootstrapApplication,
    BootstrapLocalProjectInput,
    BootstrapMutation,
    BootstrapResult,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    TaskEventId,
    TaskId,
    WorkspaceBinding,
)

if TYPE_CHECKING:
    from workaholic.application import PhaseOneRepository

_NOW = datetime(2026, 7, 30, 10, 30, tzinfo=UTC)


def _result() -> BootstrapResult:
    """Build one consistent bootstrap application result.

    Returns:
        Valid local identity and authorization graph.

    """
    instance = Instance(id=InstanceId("ins_local"), created_at=_NOW)
    project = Project(
        id=ProjectId("prj_acme"),
        instance_id=instance.id,
        key="ACME",
        name="Acme",
        created_at=_NOW,
    )
    subject = Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )
    grant = ProjectGrant(
        subject_id=subject.id,
        project_id=project.id,
        role=ProjectRole.OWNER,
    )
    return BootstrapResult(
        instance=instance,
        project=project,
        subject=subject,
        grant=grant,
        workspace=WorkspaceBinding(
            context_version=1,
            profile="local",
            instance_id=instance.id,
            project_id=project.id,
            project_key=project.key,
            workspace_root=".",
        ),
    )


class _Clock:
    """Deterministic application-test clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic complete IdentifierFactory test implementation."""

    def new_instance_id(self) -> InstanceId:
        """Return the candidate Instance identity."""
        return InstanceId("ins_candidate")

    def new_project_id(self) -> ProjectId:
        """Return the candidate Project identity."""
        return ProjectId("prj_candidate")

    def new_subject_id(self) -> SubjectId:
        """Return the candidate Subject identity."""
        return SubjectId("sub_candidate")

    def new_task_id(self) -> TaskId:
        """Return an unused candidate Task identity."""
        return TaskId("tsk_candidate")

    def new_event_id(self) -> TaskEventId:
        """Return an unused candidate event identity."""
        return TaskEventId("evt_candidate")

    def new_request_id(self) -> RequestId:
        """Return the candidate request identity."""
        return RequestId("req_candidate")


class _RecordingRepository:
    """Minimal bootstrap repository spy."""

    def __init__(self, result: object) -> None:
        """Initialize the configured result.

        Args:
            result: Value returned by bootstrap.

        """
        self.result = result
        self.mutations: list[BootstrapMutation] = []

    def bootstrap_local_project(self, mutation: BootstrapMutation) -> object:
        """Record the mutation and return the configured value."""
        self.mutations.append(mutation)
        return self.result


def test_up_builds_one_authoritative_semantic_mutation() -> None:
    """Application orchestration allocates candidates once and delegates once."""
    expected = _result()
    recording = _RecordingRepository(expected)
    application = BootstrapApplication(
        repository=cast("PhaseOneRepository", recording),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    actual = application.up(
        BootstrapLocalProjectInput(
            project_key="ACME",
            idempotency_key="bootstrap-1",
        )
    )

    assert actual is expected
    assert recording.mutations == [
        BootstrapMutation(
            instance_id=InstanceId("ins_candidate"),
            project_id=ProjectId("prj_candidate"),
            subject_id=SubjectId("sub_candidate"),
            request_id=RequestId("req_candidate"),
            occurred_at=_NOW,
            project_key="ACME",
            idempotency_key="bootstrap-1",
        )
    ]


@pytest.mark.parametrize(
    ("repository", "clock", "identifiers"),
    [
        (object(), _Clock(), _Identifiers()),
        (_RecordingRepository(_result()), object(), _Identifiers()),
        (_RecordingRepository(_result()), _Clock(), object()),
    ],
)
def test_constructor_runtime_validates_dependencies(
    repository: object,
    clock: object,
    identifiers: object,
) -> None:
    """Missing dependency methods fail at composition time."""
    with pytest.raises(TypeError, match="Bootstrap"):
        BootstrapApplication(
            repository=cast("PhaseOneRepository", repository),
            clock=cast("_Clock", clock),
            identifiers=cast("_Identifiers", identifiers),
        )


def test_up_runtime_validates_command_type() -> None:
    """The use case rejects bypasses of the validated command boundary."""
    application = BootstrapApplication(
        repository=cast("PhaseOneRepository", _RecordingRepository(_result())),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.up(cast("BootstrapLocalProjectInput", object()))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_invalid_dependency_output_is_a_safe_internal_error() -> None:
    """Malformed clock or identifier output never leaks Pydantic details."""

    class _InvalidClock:
        """Clock that violates its timezone contract."""

        def now(self) -> datetime:
            """Return a deliberately naive timestamp."""
            return _NOW.replace(tzinfo=None)

    application = BootstrapApplication(
        repository=cast("PhaseOneRepository", _RecordingRepository(_result())),
        clock=_InvalidClock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.up(BootstrapLocalProjectInput(project_key="ACME"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert "timezone" not in captured.value.safe_message.lower()


def test_invalid_repository_result_is_a_safe_internal_error() -> None:
    """The application verifies persistence output at runtime."""
    application = BootstrapApplication(
        repository=cast("PhaseOneRepository", _RecordingRepository(object())),
        clock=_Clock(),
        identifiers=_Identifiers(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.up(BootstrapLocalProjectInput(project_key="ACME"))

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
