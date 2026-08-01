"""Strict result models returned by cumulative application operations."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from workaholic.application.commands import TaskListView
from workaholic.domain import (
    DomainValidationError,
    Instance,
    Project,
    ProjectGrant,
    ProjectRole,
    ResultReviewStatus,
    Subject,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventType,
    TaskReadiness,
    TaskResult,
    TaskState,
    WorkspaceBinding,
    ready_task_ordering_key,
    validate_profile_name,
)

_CURSOR_MAX_LENGTH = 2_048


class _ResultModel(BaseModel):
    """Shared strictness policy for application result models."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class BootstrapResult(_ResultModel):
    """Persisted entities and safe binding produced by local bootstrap."""

    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace: WorkspaceBinding

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity bootstrap relationships.

        Returns:
            The internally consistent bootstrap result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        if (
            self.workspace.instance_id != self.instance.id
            or self.workspace.project_id != self.project.id
            or self.workspace.project_key != self.project.key
        ):
            message = "Bootstrap workspace does not match its Instance and Project."
            raise ValueError(message)
        return self


class StatusResult(_ResultModel):
    """Current embedded local status for one authorized Project."""

    mode: Literal["embedded"] = "embedded"
    profile: str = "local"
    schema_version: Literal[3] = 3
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile represented by status.

        Args:
            value: Candidate profile name.

        Returns:
            Validated trusted profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity status relationships.

        Returns:
            The internally consistent status result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        return self


class ProjectCreationResult(_ResultModel):
    """Committed Project and creator Owner grant."""

    project: Project
    grant: ProjectGrant

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate the committed Project and grant relationship.

        Returns:
            The internally consistent Project creation result.

        Raises:
            ValueError: If the grant does not own the created Project.

        """
        if (
            self.grant.project_id != self.project.id
            or self.grant.role is not ProjectRole.OWNER
        ):
            message = "Project creation grant must own the created Project."
            raise ValueError(message)
        return self


class ContextResult(_ResultModel):
    """One effective embedded profile, identity, and safe Workspace selection."""

    mode: Literal["embedded"] = "embedded"
    profile: str
    schema_version: Literal[3] = 3
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace_root: Path | None
    context_source: Path | None

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the selected trusted profile name.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate identity relationships and safe path disclosure.

        Returns:
            The internally consistent effective context.

        Raises:
            ValueError: If identities or optional context paths disagree.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        paths = (self.workspace_root, self.context_source)
        if (paths[0] is None) != (paths[1] is None):
            message = "Context paths must both be present or both be null."
            raise ValueError(message)
        if self.workspace_root is None or self.context_source is None:
            return self
        if (
            not self.workspace_root.is_absolute()
            or not self.context_source.is_absolute()
        ):
            message = "Context paths must be absolute."
            raise ValueError(message)
        if self.context_source.name != ".workaholic.env":
            message = "Context source must identify .workaholic.env."
            raise ValueError(message)
        if not self.workspace_root.is_relative_to(self.context_source.parent):
            message = "Workspace root must remain within its context directory."
            raise ValueError(message)
        return self


class TaskDetails(_ResultModel):
    """Complete Task, readiness, prerequisites, and selected Result details."""

    task: Task
    readiness: TaskReadiness
    prerequisites: tuple[Task, ...]
    current_result: TaskResult | None

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskDetails:
        """Validate detail relationships and deterministic prerequisite order.

        Returns:
            The internally consistent Task details.

        Raises:
            ValueError: If Task, prerequisite, or Result identities disagree.

        """
        prerequisite_ids = tuple(item.uid for item in self.prerequisites)
        if prerequisite_ids != tuple(
            item.uid for item in sorted(self.prerequisites, key=lambda item: item.key)
        ):
            message = "Task details prerequisites must be ordered by stable Task key."
            raise ValueError(message)
        if set(prerequisite_ids) != set(self.task.depends_on):
            message = "Task details prerequisites must exactly match Task depends_on."
            raise ValueError(message)
        if any(item.project_id != self.task.project_id for item in self.prerequisites):
            message = "Task details prerequisites must belong to the Task Project."
            raise ValueError(message)
        if self.task.current_result_id is None:
            if self.current_result is not None:
                message = "Task details must not select a Result absent from the Task."
                raise ValueError(message)
        elif (
            self.current_result is None
            or self.current_result.id != self.task.current_result_id
            or self.current_result.task_uid != self.task.uid
        ):
            message = "Task details current Result must match the Task selection."
            raise ValueError(message)
        return self


class TaskMutationResult(_ResultModel):
    """Committed Task and its single attributable mutation event."""

    task: Task
    events: tuple[TaskEvent, ...]

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskMutationResult:
        """Require exactly one event matching the committed Task.

        Returns:
            The internally consistent mutation result.

        Raises:
            ValueError: If event count or identities are inconsistent.

        """
        if len(self.events) != 1:
            message = "Task mutation result must contain exactly one TaskEvent."
            raise ValueError(message)
        _validate_event_batch(self.task, self.events)
        return self


class TaskSubmissionResult(_ResultModel):
    """Committed Task, retained Result, and ordered submission/review events."""

    task: Task
    result: TaskResult
    events: tuple[TaskEvent, ...]

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskSubmissionResult:
        """Validate Task, Result, review, and exact event-sequence consistency.

        Returns:
            The internally consistent submission or review result.

        Raises:
            ValueError: If identities, state, or events disagree.

        """
        if self.result.task_uid != self.task.uid:
            message = "Task submission Result must belong to the returned Task."
            raise ValueError(message)
        _validate_event_batch(self.task, self.events)
        status = self.result.review.status
        expected: tuple[TaskEventType, ...]
        if status is ResultReviewStatus.NOT_REQUIRED:
            expected = (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)
            consistent = (
                self.task.state is TaskState.DONE
                and self.task.current_result_id == self.result.id
            )
        elif status is ResultReviewStatus.PENDING:
            expected = (TaskEventType.RESULT_SUBMITTED,)
            consistent = (
                self.task.state is TaskState.REVIEW
                and self.task.current_result_id == self.result.id
            )
        elif status is ResultReviewStatus.APPROVED:
            expected = (TaskEventType.REVIEW_APPROVED, TaskEventType.TASK_COMPLETED)
            consistent = (
                self.task.state is TaskState.DONE
                and self.task.current_result_id == self.result.id
            )
        else:
            expected = (TaskEventType.REVIEW_REJECTED,)
            consistent = (
                self.task.state is TaskState.OPEN
                and self.task.current_result_id is None
            )
        if not consistent:
            message = "Task submission state must match the Result review disposition."
            raise ValueError(message)
        if tuple(event.event_type for event in self.events) != expected:
            message = "Task submission events must match the Result review disposition."
            raise ValueError(message)
        first = self.events[0]
        if status in (
            ResultReviewStatus.NOT_REQUIRED,
            ResultReviewStatus.PENDING,
        ):
            attribution_matches = (
                self.result.submitted_by == first.actor_subject_id
                and self.result.submitted_at == first.occurred_at
            )
        else:
            attribution_matches = (
                self.result.review.reviewed_by == first.actor_subject_id
                and self.result.review.reviewed_at == first.occurred_at
            )
        if not attribution_matches or self.task.updated_at != first.occurred_at:
            message = (
                "Task submission attribution and timestamps must match its events."
            )
            raise ValueError(message)
        return self


class TaskEventPage(_ResultModel):
    """One polling-safe, strictly ascending TaskEvent snapshot."""

    events: tuple[TaskEvent, ...]
    next_cursor: int

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> int:
        """Validate a nonnegative Instance event cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated nonnegative integer.

        Raises:
            DomainValidationError: If the value is negative or not an integer.

        """
        if type(value) is not int or value < 0:
            message = "TaskEvent next_cursor must be a nonnegative integer."
            raise DomainValidationError(message)
        return value

    @model_validator(mode="after")
    def _validate_event_order(self) -> TaskEventPage:
        """Require one scoped ascending event batch ending at next_cursor.

        Returns:
            The validated TaskEvent page.

        Raises:
            ValueError: If cursors or Task/Project identities disagree.

        """
        if not self.events:
            return self
        cursors = tuple(event.cursor for event in self.events)
        if tuple(sorted(cursors)) != cursors or len(set(cursors)) != len(cursors):
            message = "TaskEvent page events must have strictly ascending cursors."
            raise ValueError(message)
        first = self.events[0]
        if any(
            event.task_uid != first.task_uid or event.project_id != first.project_id
            for event in self.events
        ):
            message = "TaskEvent page events must share one Task and Project scope."
            raise ValueError(message)
        if self.next_cursor != cursors[-1]:
            message = "TaskEvent page next_cursor must equal the last event cursor."
            raise ValueError(message)
        return self


class TaskPage(_ResultModel):
    """One deterministic Project- or Instance-scoped Task page with readiness."""

    tasks: tuple[Task, ...]
    readiness: tuple[TaskReadiness, ...] = ()
    next_cursor: str | None
    view: TaskListView = TaskListView.ALL

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned opaque cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or ``None``.

        Raises:
            ValueError: If the cursor is malformed.

        """
        if value is None:
            return None
        if not isinstance(value, str):
            message = "Next cursor must be a string or null."
            raise DomainValidationError(message)
        if (
            not value
            or value != value.strip()
            or len(value) > _CURSOR_MAX_LENGTH
            or any(
                character.isspace() or not character.isprintable()
                for character in value
            )
        ):
            message = (
                "Next cursor must contain 1 through 2048 characters without "
                "whitespace or control characters."
            )
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _validate_task_order(self) -> Self:
        """Require strict Project-key and task-number ordering.

        Returns:
            The validated deterministic Task page.

        Raises:
            ValueError: If Tasks are not strictly ascending.

        """
        if self.readiness and len(self.readiness) != len(self.tasks):
            message = "Task page readiness must align one-for-one with Tasks."
            raise ValueError(message)
        previous_position: tuple[object, ...] | None = None
        for task in self.tasks:
            project_key, separator, _number = task.key.rpartition("-")
            if separator != "-":
                message = "Task page contains an invalid stable Task key."
                raise ValueError(message)
            position = (
                ready_task_ordering_key(task, project_key=project_key)
                if self.view is TaskListView.READY
                else (project_key, task.number)
            )
            if previous_position is not None and position <= previous_position:
                message = (
                    "Task page must be ordered by Project key and task number "
                    "ascending."
                )
                raise ValueError(message)
            previous_position = position
        return self


def _validate_event_batch(task: Task, events: tuple[TaskEvent, ...]) -> None:
    """Validate one attributable ordered event batch against a Task.

    Args:
        task: Committed Task returned by an operation.
        events: Events appended by that same semantic operation.

    Raises:
        ValueError: If identities, attribution, time, request, or order disagree.

    """
    if not events:
        message = "Task operation result must contain at least one TaskEvent."
        raise ValueError(message)
    first = events[0]
    cursors = tuple(event.cursor for event in events)
    if tuple(sorted(cursors)) != cursors or len(set(cursors)) != len(cursors):
        message = "Task operation events must have strictly ascending cursors."
        raise ValueError(message)
    if any(
        event.task_uid != task.uid
        or event.project_id != task.project_id
        or event.actor_subject_id != first.actor_subject_id
        or event.request_id != first.request_id
        or event.occurred_at != first.occurred_at
        for event in events
    ):
        message = "Task operation events must share Task and attribution identities."
        raise ValueError(message)


def _validate_identity_consistency(
    *,
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
) -> None:
    """Validate the embedded bootstrap-Human and Owner relationship.

    Args:
        instance: Selected local Instance.
        project: Selected local Project.
        subject: Selected local Human.
        grant: Subject's ProjectGrant.

    Raises:
        ValueError: If any entity or authorization relationship is inconsistent.

    """
    if project.instance_id != instance.id:
        message = "Project does not belong to the selected Instance."
        raise ValueError(message)
    subject_kind: object = subject.kind
    if (
        subject_kind is not SubjectKind.HUMAN
        or not subject.enabled
        or not subject.is_instance_admin
    ):
        message = "Embedded context requires an enabled Human administrator."
        raise ValueError(message)
    if (
        grant.subject_id != subject.id
        or grant.project_id != project.id
        or grant.role is not ProjectRole.OWNER
    ):
        message = "Embedded context requires the selected Human to own the Project."
        raise ValueError(message)
