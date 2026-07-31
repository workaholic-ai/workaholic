"""Strict presentation-independent requests accepted by the Session boundary."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from workaholic.domain import (
    DEFAULT_TASK_PRIORITY,
    TaskId,
    normalize_project_name,
    validate_profile_name,
    validate_project_key,
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
    idempotency_key: _IdempotencyKey | None = None
    project: _ProjectKeyText | None = None


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
