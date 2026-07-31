"""Strict Pydantic commands and semantic mutations for cumulative use cases."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime  # noqa: TC003

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workaholic.domain import (
    DEFAULT_TASK_PRIORITY,
    DomainValidationError,
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
    normalize_project_name,
    normalize_task_objective,
    normalize_task_title,
    validate_profile_name,
    validate_project_key,
    validate_task_key,
    validate_task_priority,
    validate_utc_timestamp,
)

_IDEMPOTENCY_KEY_MAX_LENGTH = 128
_CURSOR_MAX_LENGTH = 2_048
_TASK_SELECTOR_MAX_LENGTH = 256
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 500


class _CommandModel(BaseModel):
    """Shared strictness policy for application boundary models."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class BootstrapLocalProjectInput(_CommandModel):
    """Validated request to bootstrap or locate the initial local Project."""

    project_key: str
    project_name: str = ""
    idempotency_key: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_project_name_to_key(cls, value: object) -> object:
        """Default a missing initial Project name to its immutable key.

        Args:
            value: Candidate model input.

        Returns:
            A copied mapping with the Project name default, or the input.

        """
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        if copied.get("project_name") is None and "project_key" in copied:
            copied["project_name"] = copied["project_key"]
        return copied

    @field_validator("project_key", mode="before")
    @classmethod
    def _validate_project_key(cls, value: object) -> str:
        """Validate the immutable Project key.

        Args:
            value: Candidate Project key.

        Returns:
            The validated Project key.

        """
        return validate_project_key(value)

    @field_validator("project_name", mode="before")
    @classmethod
    def _normalize_project_name(cls, value: object) -> str:
        """Normalize and validate the initial Project display name.

        Args:
            value: Candidate Project display name.

        Returns:
            The normalized Project display name.

        """
        return normalize_project_name(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class GetLocalStatus(_CommandModel):
    """Read the selected local Instance, Project, and Subject status."""

    profile: str = "local"
    instance_id: InstanceId
    project_id: ProjectId
    subject_id: SubjectId

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile bound to status.

        Args:
            value: Candidate profile name.

        Returns:
            Validated trusted profile name.

        """
        return validate_profile_name(value)


class ListProjects(_CommandModel):
    """List Projects authorized for the selected local Subject."""

    instance_id: InstanceId
    subject_id: SubjectId


class GetProjectByKey(_CommandModel):
    """Read one authorized Project by its immutable key."""

    instance_id: InstanceId
    subject_id: SubjectId
    project_key: str

    @field_validator("project_key", mode="before")
    @classmethod
    def _validate_project_key(cls, value: object) -> str:
        """Validate the immutable Project selector.

        Args:
            value: Candidate Project key.

        Returns:
            The validated Project key.

        """
        return validate_project_key(value)


class CreateProjectInput(_CommandModel):
    """Validated request to create one Project in an initialized Instance."""

    instance_id: InstanceId
    subject_id: SubjectId
    project_key: str
    project_name: str
    idempotency_key: str | None = None

    @field_validator("project_key", mode="before")
    @classmethod
    def _validate_project_key(cls, value: object) -> str:
        """Validate the immutable Project key.

        Args:
            value: Candidate Project key.

        Returns:
            The validated Project key.

        """
        return validate_project_key(value)

    @field_validator("project_name", mode="before")
    @classmethod
    def _normalize_project_name(cls, value: object) -> str:
        """Normalize and validate the Project display name.

        Args:
            value: Candidate Project display name.

        Returns:
            The normalized Project display name.

        """
        return normalize_project_name(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class CreateTaskInput(_CommandModel):
    """Validated request to create one Task in the selected Project."""

    project_id: ProjectId
    subject_id: SubjectId
    title: str
    objective: str = ""
    priority: int = DEFAULT_TASK_PRIORITY
    idempotency_key: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_objective_to_title(cls, value: object) -> object:
        """Default a missing objective before strict field validation.

        Args:
            value: Candidate model input.

        Returns:
            A copied mapping with the objective default, or the unchanged input.

        """
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        if copied.get("objective") is None and "title" in copied:
            copied["objective"] = copied["title"]
        return copied

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str:
        """Normalize and validate the Task title.

        Args:
            value: Candidate Task title.

        Returns:
            The normalized title.

        """
        return normalize_task_title(value)

    @field_validator("objective", mode="before")
    @classmethod
    def _normalize_objective(cls, value: object) -> str:
        """Normalize and validate the Task objective.

        Args:
            value: Candidate Task objective.

        Returns:
            The normalized objective.

        """
        return normalize_task_objective(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _validate_priority(cls, value: object) -> int:
        """Validate Task priority without integer coercion.

        Args:
            value: Candidate priority.

        Returns:
            The validated priority.

        """
        return validate_task_priority(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class ListTasks(_CommandModel):
    """Read one deterministic page of Tasks for a selected Project."""

    profile: str = "local"
    project_id: ProjectId
    subject_id: SubjectId
    cursor: str | None = None
    limit: int = Field(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE)

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile bound into pagination.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)

    @field_validator("cursor", mode="before")
    @classmethod
    def _validate_cursor(cls, value: object) -> str | None:
        """Validate an optional opaque pagination cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Cursor",
            maximum=_CURSOR_MAX_LENGTH,
            optional=True,
        )


class ListInstanceTasks(_CommandModel):
    """Read one deterministic Task page across authorized Instance Projects."""

    profile: str = "local"
    instance_id: InstanceId
    subject_id: SubjectId
    cursor: str | None = None
    limit: int = Field(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE)

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile bound into pagination.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)

    @field_validator("cursor", mode="before")
    @classmethod
    def _validate_cursor(cls, value: object) -> str | None:
        """Validate an optional opaque pagination cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Cursor",
            maximum=_CURSOR_MAX_LENGTH,
            optional=True,
        )


class GetTask(_CommandModel):
    """Read one Task by canonical UID or stable human key."""

    project_id: ProjectId
    subject_id: SubjectId
    task: TaskId | str

    @field_validator("task", mode="before")
    @classmethod
    def _validate_task_selector(cls, value: object) -> TaskId | str:
        """Validate and disambiguate a Task UID or human key.

        Args:
            value: Candidate Task selector.

        Returns:
            A typed TaskId or validated human Task key.

        """
        if isinstance(value, TaskId):
            return value
        if not isinstance(value, str) or len(value) > _TASK_SELECTOR_MAX_LENGTH:
            message = "Task selector must be a supported Task UID or human key."
            raise ValueError(message)
        if value.startswith("tsk_"):
            return TaskId(value)
        _, separator, number_text = value.rpartition("-")
        if separator != "-" or not number_text.isascii() or not number_text.isdecimal():
            message = "Task selector must be a supported Task UID or human key."
            raise ValueError(message)
        return validate_task_key(value, task_number=int(number_text))


class BootstrapMutation(_CommandModel):
    """Fully validated semantic input for atomic local bootstrap."""

    instance_id: InstanceId
    project_id: ProjectId
    subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime
    project_key: str
    project_name: str = ""
    idempotency_key: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_project_name_to_key(cls, value: object) -> object:
        """Default a missing initial Project name to its immutable key.

        Args:
            value: Candidate model input.

        Returns:
            A copied mapping with the Project name default, or the input.

        """
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        if copied.get("project_name") is None and "project_key" in copied:
            copied["project_name"] = copied["project_key"]
        return copied

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Require an authoritative UTC bootstrap timestamp.

        Args:
            value: Candidate timestamp.

        Returns:
            The validated datetime.

        """
        return validate_utc_timestamp(value, label="Bootstrap occurred_at")

    @field_validator("project_key", mode="before")
    @classmethod
    def _validate_project_key(cls, value: object) -> str:
        """Validate the immutable Project key.

        Args:
            value: Candidate Project key.

        Returns:
            The validated Project key.

        """
        return validate_project_key(value)

    @field_validator("project_name", mode="before")
    @classmethod
    def _normalize_project_name(cls, value: object) -> str:
        """Normalize and validate the initial Project display name.

        Args:
            value: Candidate Project display name.

        Returns:
            The normalized Project display name.

        """
        return normalize_project_name(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class ProjectCreationMutation(_CommandModel):
    """Fully validated semantic input for atomic Project creation."""

    project_id: ProjectId
    request_id: RequestId
    instance_id: InstanceId
    actor_subject_id: SubjectId
    occurred_at: datetime
    project_key: str
    project_name: str
    idempotency_key: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Require an authoritative UTC Project-creation timestamp.

        Args:
            value: Candidate timestamp.

        Returns:
            The validated datetime.

        """
        return validate_utc_timestamp(value, label="Project creation occurred_at")

    @field_validator("project_key", mode="before")
    @classmethod
    def _validate_project_key(cls, value: object) -> str:
        """Validate the immutable Project key.

        Args:
            value: Candidate Project key.

        Returns:
            The validated Project key.

        """
        return validate_project_key(value)

    @field_validator("project_name", mode="before")
    @classmethod
    def _normalize_project_name(cls, value: object) -> str:
        """Normalize and validate the Project display name.

        Args:
            value: Candidate Project display name.

        Returns:
            The normalized Project display name.

        """
        return normalize_project_name(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class TaskCreationMutation(_CommandModel):
    """Fully validated semantic input for atomic Task creation."""

    task_id: TaskId
    event_id: TaskEventId
    request_id: RequestId
    project_id: ProjectId
    actor_subject_id: SubjectId
    occurred_at: datetime
    title: str
    objective: str
    priority: int
    idempotency_key: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Require an authoritative UTC Task-creation timestamp.

        Args:
            value: Candidate timestamp.

        Returns:
            The validated datetime.

        """
        return validate_utc_timestamp(value, label="Task creation occurred_at")

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str:
        """Normalize and validate the Task title.

        Args:
            value: Candidate Task title.

        Returns:
            The normalized title.

        """
        return normalize_task_title(value)

    @field_validator("objective", mode="before")
    @classmethod
    def _normalize_objective(cls, value: object) -> str:
        """Normalize and validate the Task objective.

        Args:
            value: Candidate Task objective.

        Returns:
            The normalized objective.

        """
        return normalize_task_objective(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _validate_priority(cls, value: object) -> int:
        """Validate Task priority without integer coercion.

        Args:
            value: Candidate priority.

        Returns:
            The validated priority.

        """
        return validate_task_priority(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional opaque caller key.

        Args:
            value: Candidate idempotency key.

        Returns:
            The validated key or ``None``.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


def _validate_opaque_token(
    value: object,
    *,
    label: str,
    maximum: int,
    optional: bool,
) -> str | None:
    """Validate a bounded opaque CLI token without interpreting it.

    Args:
        value: Candidate token.
        label: Human-readable field name.
        maximum: Inclusive maximum Unicode character count.
        optional: Whether ``None`` is accepted.

    Returns:
        The validated token or ``None``.

    Raises:
        ValueError: If the token is absent when required or malformed.

    """
    if value is None and optional:
        return None
    if not isinstance(value, str):
        message = f"{label} must be a string."
        raise DomainValidationError(message)
    if not value or value != value.strip() or len(value) > maximum:
        message = f"{label} must contain 1 through {maximum} trimmed characters."
        raise ValueError(message)
    if any(character.isspace() or not character.isprintable() for character in value):
        message = f"{label} must not contain whitespace or control characters."
        raise ValueError(message)
    return value
