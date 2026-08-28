"""Unit tests for named Project creation application orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    CreateProjectInput,
    PermissionDeniedError,
    ProjectApplication,
    ProjectCreationMutation,
    ProjectCreationResult,
)
from workaholic.domain import (
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
)

if TYPE_CHECKING:
    from workaholic.application import (
        Clock,
        IdentifierFactory,
        ProjectRepository,
    )

_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _result() -> ProjectCreationResult:
    """Build one valid Project-creation result.

    Returns:
        Consistent Project and creator Owner grant.

    """
    project = Project(
        id=ProjectId("prj_docs"),
        instance_id=InstanceId("ins_local"),
        key="DOCS",
        name="Documentation",
        created_at=_NOW,
    )
    return ProjectCreationResult(
        project=project,
        grant=ProjectGrant(
            instance_id=project.instance_id,
            subject_id=SubjectId("sub_local"),
            project_id=project.id,
            role=ProjectRole.OWNER,
            version=1,
            granted_by=SubjectId("sub_local"),
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )


class _Clock:
    """Deterministic Project application-test clock."""

    def now(self) -> datetime:
        """Return the fixed authoritative timestamp."""
        return _NOW


class _Identifiers:
    """Deterministic complete IdentifierFactory test implementation."""

    def new_instance_id(self) -> InstanceId:
        """Return an unused candidate Instance identity."""
        return InstanceId("ins_candidate")

    def new_project_id(self) -> ProjectId:
        """Return the candidate Project identity."""
        return ProjectId("prj_candidate")

    def new_subject_id(self) -> SubjectId:
        """Return an unused candidate Subject identity."""
        return SubjectId("sub_candidate")

    def new_task_id(self) -> TaskId:
        """Return an unused candidate Task identity."""
        return TaskId("tsk_candidate")

    def new_event_id(self) -> TaskEventId:
        """Return an unused candidate TaskEvent identity."""
        return TaskEventId("evt_candidate")

    def new_request_id(self) -> RequestId:
        """Return the candidate request identity."""
        return RequestId("req_candidate")


class _RecordingRepository:
    """Minimal Project repository spy."""

    def __init__(self, result: object) -> None:
        """Initialize the configured result.

        Args:
            result: Value returned by create_project.

        """
        self.result = result
        self.mutations: list[ProjectCreationMutation] = []

    def create_project(self, mutation: ProjectCreationMutation) -> object:
        """Record one mutation and return the configured result."""
        self.mutations.append(mutation)
        return self.result


def _application(
    repository: _RecordingRepository,
    *,
    clock: object | None = None,
    identifiers: object | None = None,
) -> ProjectApplication:
    """Construct ProjectApplication with explicitly cast test doubles.

    Args:
        repository: Recording repository test double.
        clock: Optional clock override.
        identifiers: Optional identifier-factory override.

    Returns:
        Configured Project application.

    """
    return ProjectApplication(
        repository=cast("ProjectRepository", repository),
        clock=cast("Clock", _Clock() if clock is None else clock),
        identifiers=cast(
            "IdentifierFactory",
            _Identifiers() if identifiers is None else identifiers,
        ),
    )


def test_create_builds_one_normalized_project_mutation() -> None:
    """Application orchestration allocates candidates once and preserves intent."""
    expected = _result()
    recording = _RecordingRepository(expected)
    application = _application(recording)
    command = CreateProjectInput(
        instance_id=InstanceId("ins_local"),
        subject_id=SubjectId("sub_local"),
        project_key="DOCS",
        project_name="  Cafe\u0301 documentation  ",
        idempotency_key="project-create-1",
    )

    actual = application.create(command)

    assert actual is expected
    assert command.project_name == "Café documentation"
    assert recording.mutations == [
        ProjectCreationMutation(
            project_id=ProjectId("prj_candidate"),
            request_id=RequestId("req_candidate"),
            instance_id=InstanceId("ins_local"),
            actor_subject_id=SubjectId("sub_local"),
            occurred_at=_NOW,
            project_key="DOCS",
            project_name="Café documentation",
            idempotency_key="project-create-1",
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
    with pytest.raises(TypeError, match="Project"):
        ProjectApplication(
            repository=cast("ProjectRepository", repository),
            clock=cast("Clock", clock),
            identifiers=cast("IdentifierFactory", identifiers),
        )


def test_create_runtime_validates_command_type() -> None:
    """The use case rejects bypasses of the validated command boundary."""
    recording = _RecordingRepository(_result())
    application = _application(recording)

    with pytest.raises(ApplicationError) as captured:
        application.create(cast("CreateProjectInput", object()))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert recording.mutations == []


def test_invalid_clock_output_is_a_safe_internal_error() -> None:
    """A malformed timestamp never crosses the persistence boundary."""

    class _InvalidClock:
        """Clock that violates the timezone contract."""

        def now(self) -> datetime:
            """Return one deliberately naive timestamp."""
            return _NOW.replace(tzinfo=None)

    recording = _RecordingRepository(_result())
    application = _application(
        recording,
        clock=_InvalidClock(),
    )

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateProjectInput(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
                project_key="DOCS",
                project_name="Documentation",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert recording.mutations == []


@pytest.mark.parametrize("invalid_method", ["project", "request"])
def test_invalid_identifier_output_is_a_safe_internal_error(
    invalid_method: str,
) -> None:
    """Malformed generated identities never cross the persistence boundary."""

    class _InvalidIdentifiers(_Identifiers):
        """Identifier factory with one deliberately malformed output."""

        def new_project_id(self) -> ProjectId:
            """Return the configured Project identity candidate."""
            if invalid_method == "project":
                return cast("ProjectId", "not-a-project-id")
            return super().new_project_id()

        def new_request_id(self) -> RequestId:
            """Return the configured request identity candidate."""
            if invalid_method == "request":
                return cast("RequestId", "not-a-request-id")
            return super().new_request_id()

    recording = _RecordingRepository(_result())
    application = _application(recording, identifiers=_InvalidIdentifiers())

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateProjectInput(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
                project_key="DOCS",
                project_name="Documentation",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert recording.mutations == []


def test_invalid_repository_result_is_a_safe_internal_error() -> None:
    """The application verifies concrete ProjectCreationResult output."""
    application = _application(_RecordingRepository(object()))

    with pytest.raises(ApplicationError) as captured:
        application.create(
            CreateProjectInput(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
                project_key="DOCS",
                project_name="Documentation",
            )
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_repository_application_error_propagates_unchanged() -> None:
    """Stable semantic repository errors remain available to presentation."""

    class _DeniedRepository(_RecordingRepository):
        """Repository that rejects the selected creator."""

        def create_project(
            self,
            mutation: ProjectCreationMutation,
        ) -> ProjectCreationResult:
            """Reject one otherwise valid Project mutation."""
            self.mutations.append(mutation)
            raise PermissionDeniedError

    application = _application(_DeniedRepository(_result()))

    with pytest.raises(PermissionDeniedError) as captured:
        application.create(
            CreateProjectInput(
                instance_id=InstanceId("ins_local"),
                subject_id=SubjectId("sub_local"),
                project_key="DOCS",
                project_name="Documentation",
            )
        )

    assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED
