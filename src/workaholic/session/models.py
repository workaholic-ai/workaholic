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
