"""Strict presentation-independent requests accepted by the Session boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime  # noqa: TC003
from pathlib import Path  # noqa: TC003
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workaholic.application import TaskListView, TaskResultInput, TaskUpdatePatch
from workaholic.domain import (
    ACCEPTANCE_CRITERIA_MAX_ITEMS,
    CONTEXT_REFERENCES_MAX_ITEMS,
    DEFAULT_TASK_PRIORITY,
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    TaskId,
    normalize_project_name,
    validate_profile_name,
    validate_project_key,
    validate_utc_timestamp,
)

_ProjectKeyText = Annotated[str, BeforeValidator(validate_project_key)]
_ProjectNameText = Annotated[str, BeforeValidator(normalize_project_name)]
_ProfileName = Annotated[str, BeforeValidator(validate_profile_name)]
_IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128)]
_Cursor = Annotated[str, Field(min_length=1, max_length=2_048)]
_TaskSelector = TaskId | Annotated[str, Field(min_length=1, max_length=256)]


class _SessionRequest(BaseModel):
    """Shared strictness policy for presentation-independent requests."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class UpRequest(_SessionRequest):
    """Request local bootstrap for one immutable Project key."""

    project_key: _ProjectKeyText
    project_name: _ProjectNameText | None = None
    profile: _ProfileName | None = None
    idempotency_key: _IdempotencyKey | None = None


class StatusRequest(_SessionRequest):
    """Request status for an optional profile and Project selection."""

    profile: _ProfileName | None = None
    project: _ProjectKeyText | None = None


class ContextRequest(_SessionRequest):
    """Request the effective profile, Project, actor, and Workspace context."""

    profile: _ProfileName | None = None
    project: _ProjectKeyText | None = None


class ProjectListRequest(_SessionRequest):
    """Request Projects authorized in one selected profile."""

    profile: _ProfileName | None = None


class ProjectCreateRequest(_SessionRequest):
    """Request one named Project in an initialized profile."""

    key: _ProjectKeyText
    name: _ProjectNameText
    profile: _ProfileName | None = None
    idempotency_key: _IdempotencyKey | None = None


class ProjectBindRequest(_SessionRequest):
    """Request a durable Workspace binding to an existing Project."""

    project: _ProjectKeyText
    path: Path | None = None
    profile: _ProfileName | None = None
    replace: bool = False


class TaskCreateRequest(_SessionRequest):
    """Request one Task in an explicit or discovered Project."""

    title: str
    objective: str | None = None
    priority: int = Field(default=DEFAULT_TASK_PRIORITY, ge=0, le=100)
    available_at: datetime | None = None
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    acceptance: tuple[AcceptanceCriterion, ...] = ()
    context: tuple[ContextReference, ...] = ()
    idempotency_key: _IdempotencyKey | None = None
    project: _ProjectKeyText | None = None

    @field_validator("available_at", mode="before")
    @classmethod
    def _validate_available_at(cls, value: object) -> datetime | None:
        """Validate optional UTC availability without coercion.

        Args:
            value: Candidate availability timestamp or ``None``.

        Returns:
            The validated UTC timestamp or ``None``.

        """
        if value is None:
            return None
        return validate_utc_timestamp(value, label="Task creation available_at")

    @field_validator("approval", mode="before")
    @classmethod
    def _validate_approval(cls, value: object) -> ApprovalRequirement:
        """Validate the exact Task approval requirement.

        Args:
            value: Candidate enum or serialized approval value.

        Returns:
            The typed approval requirement.

        Raises:
            ValueError: If the value is null or unsupported.

        """
        if isinstance(value, ApprovalRequirement):
            return value
        if not isinstance(value, str):
            message = "Task approval must be none or human."
            raise ValueError(message)  # noqa: TRY004 - Pydantic boundary.
        return ApprovalRequirement(value)

    @field_validator("acceptance", mode="before")
    @classmethod
    def _validate_acceptance(
        cls,
        value: object,
    ) -> tuple[AcceptanceCriterion, ...]:
        """Validate one closed ordered acceptance definition.

        Args:
            value: Candidate criterion collection.

        Returns:
            Immutable validated criteria.

        """
        values = _structured_values(
            value,
            label="Task acceptance",
            maximum=ACCEPTANCE_CRITERIA_MAX_ITEMS,
        )
        criteria: list[AcceptanceCriterion] = []
        for item in values:
            if isinstance(item, AcceptanceCriterion):
                criterion = item
            elif isinstance(item, Mapping) and set(item) == {
                "id",
                "text",
                "required",
            }:
                criterion = AcceptanceCriterion(
                    id=item["id"],
                    text=item["text"],
                    required=item["required"],
                )
            else:
                message = "Task acceptance entries must use the closed criterion shape."
                raise ValueError(message)
            criteria.append(criterion)
        if len({item.id for item in criteria}) != len(criteria):
            message = "Task acceptance criterion IDs must be unique."
            raise ValueError(message)
        return tuple(criteria)

    @field_validator("context", mode="before")
    @classmethod
    def _validate_context(
        cls,
        value: object,
    ) -> tuple[ContextReference, ...]:
        """Validate one closed ordered inert-context definition.

        Args:
            value: Candidate context-reference collection.

        Returns:
            Immutable validated context references.

        """
        values = _structured_values(
            value,
            label="Task context",
            maximum=CONTEXT_REFERENCES_MAX_ITEMS,
        )
        references: list[ContextReference] = []
        for item in values:
            if isinstance(item, ContextReference):
                reference = item
            elif (
                isinstance(item, Mapping)
                and "uri" in item
                and set(item) <= {"uri", "version"}
            ):
                reference = ContextReference(
                    uri=item["uri"],
                    version=item.get("version"),
                )
            else:
                message = "Task context entries must use the closed reference shape."
                raise ValueError(message)
            references.append(reference)
        if len({(item.uri, item.version) for item in references}) != len(references):
            message = "Task context references must be unique by URI and version."
            raise ValueError(message)
        return tuple(references)


class TaskListRequest(_SessionRequest):
    """Request one deterministic selected-Project or all-Projects Task page."""

    cursor: _Cursor | None = None
    limit: int = Field(default=100, ge=1, le=500)
    project: _ProjectKeyText | None = None
    all_projects: bool = False

    @model_validator(mode="after")
    def _validate_selection(self) -> TaskListRequest:
        """Reject simultaneous one-Project and all-Project selection.

        Returns:
            The validated Task-list request.

        Raises:
            ValueError: If both mutually exclusive selectors are supplied.

        """
        if self.project is not None and self.all_projects:
            message = "Project and all-project selection are mutually exclusive."
            raise ValueError(message)
        return self


class TaskGetRequest(_SessionRequest):
    """Request one explicit- or discovered-Project Task by UID or Human key."""

    task: _TaskSelector
    project: _ProjectKeyText | None = None


class _ExistingTaskRequest(_SessionRequest):
    """Shared optimistic intent for one existing selected-Project Task."""

    task: _TaskSelector
    expected_version: int = Field(ge=1)
    idempotency_key: _IdempotencyKey | None = None
    project: _ProjectKeyText | None = None


class TaskUpdateRequest(_ExistingTaskRequest):
    """Request an optimistic replacement of editable Task definition fields."""

    patch: TaskUpdatePatch

    @field_validator("patch", mode="before")
    @classmethod
    def _validate_patch(cls, value: object) -> TaskUpdatePatch:
        """Revalidate a closed nonempty Task patch at the Session boundary.

        Args:
            value: Candidate TaskUpdatePatch or its closed mapping form.

        Returns:
            Independently validated immutable application patch.

        """
        if isinstance(value, TaskUpdatePatch):
            value = value.model_dump(exclude_unset=True)
        return TaskUpdatePatch.model_validate(value)


class TaskBlockRequest(_ExistingTaskRequest):
    """Request an optimistic transition from open to blocked."""

    reason: str


class TaskUnblockRequest(_ExistingTaskRequest):
    """Request an optimistic transition from blocked to open."""


class TaskCancelRequest(_ExistingTaskRequest):
    """Request optimistic cancellation with an optional Human reason."""

    reason: str | None = None


class TaskAddDependencyRequest(_ExistingTaskRequest):
    """Request one same-Project prerequisite addition."""

    prerequisite: _TaskSelector


class TaskRemoveDependencyRequest(_ExistingTaskRequest):
    """Request one same-Project prerequisite removal."""

    prerequisite: _TaskSelector


class TaskSubmitRequest(_ExistingTaskRequest):
    """Request direct Human Result submission without an Agent Attempt."""

    comment: str | None = None
    result: TaskResultInput = Field(default_factory=TaskResultInput)

    @field_validator("result", mode="before")
    @classmethod
    def _validate_result(cls, value: object) -> TaskResultInput:
        """Revalidate caller-controlled Result content without identities.

        Args:
            value: Candidate TaskResultInput or its closed mapping form.

        Returns:
            Independently validated immutable Result content.

        """
        if isinstance(value, TaskResultInput):
            value = value.model_dump()
        return TaskResultInput.model_validate(value)


class TaskApproveRequest(_ExistingTaskRequest):
    """Request optimistic Human approval of the current pending Result."""

    comment: str | None = None


class TaskRejectRequest(_ExistingTaskRequest):
    """Request optimistic Human rejection of the current pending Result."""

    reason: str


class TaskDetailsRequest(_SessionRequest):
    """Request complete Task definition, readiness, dependencies, and Result."""

    task: _TaskSelector
    project: _ProjectKeyText | None = None


class TaskListByViewRequest(_SessionRequest):
    """Request one deterministic Project- or Instance-scoped Task view page."""

    view: TaskListView = TaskListView.ALL
    cursor: _Cursor | None = None
    limit: int = Field(default=100, ge=1, le=500)
    project: _ProjectKeyText | None = None
    all_projects: bool = False

    @field_validator("view", mode="before")
    @classmethod
    def _validate_view(cls, value: object) -> TaskListView:
        """Validate a typed or serialized closed Task view.

        Args:
            value: Candidate TaskListView or exact serialized value.

        Returns:
            Typed supported Task view.

        Raises:
            ValueError: If the value is not a supported string or enum.

        """
        if isinstance(value, TaskListView):
            return value
        if not isinstance(value, str):
            message = "Task list view must be a supported string."
            raise ValueError(message)  # noqa: TRY004 - Pydantic boundary.
        return TaskListView(value)

    @model_validator(mode="after")
    def _validate_selection(self) -> TaskListByViewRequest:
        """Reject simultaneous one-Project and all-Project selection.

        Returns:
            Validated mutually exclusive selection.

        Raises:
            ValueError: If both selection modes are requested.

        """
        if self.project is not None and self.all_projects:
            message = "Project and all-project selection are mutually exclusive."
            raise ValueError(message)
        return self


class TaskEventsRequest(_SessionRequest):
    """Request one bounded TaskEvent snapshot after an Instance cursor."""

    task: _TaskSelector
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)
    project: _ProjectKeyText | None = None


def _structured_values(
    value: object,
    *,
    label: str,
    maximum: int,
) -> tuple[object, ...]:
    """Copy and bound one ordered structured Session value.

    Args:
        value: Candidate ordered collection.
        label: Human-readable field label.
        maximum: Inclusive item-count bound.

    Returns:
        Immutable shallow copy for item validation.

    Raises:
        ValueError: If input is null, unordered, textual, or oversized.

    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{label} must be an ordered collection."
        raise ValueError(message)  # noqa: TRY004 - Pydantic boundary.
    copied = tuple(value)
    if len(copied) > maximum:
        message = f"{label} must not contain more than {maximum} items."
        raise ValueError(message)
    return copied
