"""Strict context-free requests accepted by the Phase 1 Session boundary."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from workaholic.domain import DEFAULT_TASK_PRIORITY, TaskId

_ProjectKeyText = Annotated[str, Field(min_length=2, max_length=16)]
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
    idempotency_key: _IdempotencyKey | None = None


class StatusRequest(_SessionRequest):
    """Request status for the exact current Workspace context."""


class ProjectListRequest(_SessionRequest):
    """Request Projects authorized for the current local actor."""


class TaskCreateRequest(_SessionRequest):
    """Request one Task in the Project selected by Workspace context."""

    title: str
    objective: str | None = None
    priority: int = Field(default=DEFAULT_TASK_PRIORITY, ge=0, le=100)
    idempotency_key: _IdempotencyKey | None = None


class TaskListRequest(_SessionRequest):
    """Request one deterministic Task page in the selected Project."""

    cursor: _Cursor | None = None
    limit: int = Field(default=100, ge=1, le=500)


class TaskGetRequest(_SessionRequest):
    """Request one selected-Project Task by canonical UID or Human key."""

    task: _TaskSelector
