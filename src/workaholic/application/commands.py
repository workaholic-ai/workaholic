"""Strict Pydantic commands and semantic mutations for cumulative use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from workaholic.domain import (
    ACCEPTANCE_CRITERIA_MAX_ITEMS,
    ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
    CONTEXT_REFERENCES_MAX_ITEMS,
    DEFAULT_TASK_PRIORITY,
    RESULT_COLLECTION_MAX_ITEMS,
    RESULT_TEXT_MAX_LENGTH,
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    AttemptId,
    AuthenticatedActor,
    ContextReference,
    CriterionOutcome,
    CriterionStatus,
    DomainValidationError,
    InstanceId,
    Permission,
    ProjectId,
    ProjectRole,
    ProposedFollowUp,
    RequestId,
    ResultId,
    SubjectId,
    SubjectKind,
    TaskEventId,
    TaskId,
    TaskProgress,
    TokenId,
    normalize_bounded_printable_text,
    normalize_project_name,
    normalize_task_objective,
    normalize_task_title,
    resolve_lease_duration,
    validate_lowercase_sha256,
    validate_positive_integer,
    validate_profile_name,
    validate_project_key,
    validate_subject_handle,
    validate_task_key,
    validate_task_priority,
    validate_utc_timestamp,
)

_IDEMPOTENCY_KEY_MAX_LENGTH = 128
_CURSOR_MAX_LENGTH = 2_048
_TASK_SELECTOR_MAX_LENGTH = 256
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 500
_SUBJECT_DISPLAY_NAME_MAX_LENGTH = 200


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
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)

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
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)


class GetProjectByKey(_CommandModel):
    """Read one authorized Project by its immutable key."""

    instance_id: InstanceId
    subject_id: SubjectId
    project_key: str
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)

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
    available_at: datetime | None = None
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    acceptance: tuple[AcceptanceCriterion, ...] = ()
    context: tuple[ContextReference, ...] = ()
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

    @field_validator("available_at", mode="before")
    @classmethod
    def _validate_available_at(cls, value: object) -> datetime | None:
        """Validate optional UTC availability without datetime coercion.

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
            value: Candidate approval enum or serialized value.

        Returns:
            The typed approval requirement.

        """
        return _validate_required_approval(value)

    @field_validator("acceptance", mode="before")
    @classmethod
    def _validate_acceptance(
        cls,
        value: object,
    ) -> tuple[AcceptanceCriterion, ...]:
        """Validate the complete ordered acceptance definition.

        Args:
            value: Candidate acceptance-criterion collection.

        Returns:
            The immutable validated criterion collection.

        """
        return cast(
            "tuple[AcceptanceCriterion, ...]",
            _validate_acceptance_collection(value, optional=False),
        )

    @field_validator("context", mode="before")
    @classmethod
    def _validate_context(
        cls,
        value: object,
    ) -> tuple[ContextReference, ...]:
        """Validate the complete ordered inert context definition.

        Args:
            value: Candidate context-reference collection.

        Returns:
            The immutable validated reference collection.

        """
        return cast(
            "tuple[ContextReference, ...]",
            _validate_context_collection(value, optional=False),
        )

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
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)
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
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)
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
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)

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
    available_at: datetime | None = None
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    acceptance: tuple[AcceptanceCriterion, ...] = ()
    context: tuple[ContextReference, ...] = ()
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

    @field_validator("available_at", mode="before")
    @classmethod
    def _validate_available_at(cls, value: object) -> datetime | None:
        """Validate optional persisted availability without coercion.

        Args:
            value: Candidate UTC availability timestamp or ``None``.

        Returns:
            The validated UTC timestamp or ``None``.

        """
        if value is None:
            return None
        return validate_utc_timestamp(value, label="Task creation available_at")

    @field_validator("approval", mode="before")
    @classmethod
    def _validate_approval(cls, value: object) -> ApprovalRequirement:
        """Validate the exact persisted approval requirement.

        Args:
            value: Candidate approval enum or serialized value.

        Returns:
            The typed approval requirement.

        """
        return _validate_required_approval(value)

    @field_validator("acceptance", mode="before")
    @classmethod
    def _validate_acceptance(
        cls,
        value: object,
    ) -> tuple[AcceptanceCriterion, ...]:
        """Validate the complete ordered acceptance definition.

        Args:
            value: Candidate acceptance-criterion collection.

        Returns:
            The immutable validated criterion collection.

        """
        return cast(
            "tuple[AcceptanceCriterion, ...]",
            _validate_acceptance_collection(value, optional=False),
        )

    @field_validator("context", mode="before")
    @classmethod
    def _validate_context(
        cls,
        value: object,
    ) -> tuple[ContextReference, ...]:
        """Validate the complete ordered inert context definition.

        Args:
            value: Candidate context-reference collection.

        Returns:
            The immutable validated reference collection.

        """
        return cast(
            "tuple[ContextReference, ...]",
            _validate_context_collection(value, optional=False),
        )

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


class TaskListView(StrEnum):
    """Persisted or derived Task collection views available in Phase 3."""

    ALL = "all"
    READY = "ready"
    SCHEDULED = "scheduled"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskUpdatePatch(_CommandModel):
    """Closed nonempty patch containing only editable Task definition fields."""

    title: str | None = None
    objective: str | None = None
    priority: int | None = None
    available_at: datetime | None = None
    approval: ApprovalRequirement | None = None
    acceptance: tuple[AcceptanceCriterion, ...] | None = None
    context: tuple[ContextReference, ...] | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> str | None:
        """Normalize an optional Task title patch.

        Args:
            value: Candidate title or omitted default.

        Returns:
            The normalized title or ``None``.

        """
        return None if value is None else normalize_task_title(value)

    @field_validator("objective", mode="before")
    @classmethod
    def _validate_objective(cls, value: object) -> str | None:
        """Normalize an optional Task objective patch.

        Args:
            value: Candidate objective or omitted default.

        Returns:
            The normalized objective or ``None``.

        """
        return None if value is None else normalize_task_objective(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _validate_priority(cls, value: object) -> int | None:
        """Validate an optional Task priority patch.

        Args:
            value: Candidate priority or omitted default.

        Returns:
            The validated priority or ``None``.

        """
        return None if value is None else validate_task_priority(value)

    @field_validator("available_at", mode="before")
    @classmethod
    def _validate_available_at(cls, value: object) -> datetime | None:
        """Validate an optional UTC availability value or explicit clear.

        Args:
            value: Candidate timestamp or ``None``.

        Returns:
            The validated timestamp or ``None``.

        """
        if value is None:
            return None
        return validate_utc_timestamp(value, label="Task update available_at")

    @field_validator("approval", mode="before")
    @classmethod
    def _validate_approval(cls, value: object) -> ApprovalRequirement | None:
        """Validate an optional Task approval patch.

        Args:
            value: Candidate approval value or omitted default.

        Returns:
            The typed approval requirement or ``None``.

        """
        return _validate_optional_approval(value)

    @field_validator("acceptance", mode="before")
    @classmethod
    def _validate_acceptance(
        cls,
        value: object,
    ) -> tuple[AcceptanceCriterion, ...] | None:
        """Validate an optional complete acceptance replacement.

        Args:
            value: Candidate structured collection or omitted default.

        Returns:
            The immutable acceptance collection or ``None``.

        """
        return _validate_acceptance_collection(value, optional=True)

    @field_validator("context", mode="before")
    @classmethod
    def _validate_context(
        cls,
        value: object,
    ) -> tuple[ContextReference, ...] | None:
        """Validate an optional complete context replacement.

        Args:
            value: Candidate structured collection or omitted default.

        Returns:
            The immutable context collection or ``None``.

        """
        return _validate_context_collection(value, optional=True)

    @model_validator(mode="after")
    def _validate_nonempty_patch(self) -> TaskUpdatePatch:
        """Reject empty patches and nulls outside availability clearing.

        Returns:
            The validated nonempty patch.

        Raises:
            ValueError: If no field was supplied or a nonnullable field is null.

        """
        supplied = self.model_fields_set
        if not supplied:
            message = "Task update patch must contain at least one editable field."
            raise ValueError(message)
        invalid_nulls = {
            name
            for name in supplied
            if name != "available_at" and getattr(self, name) is None
        }
        if invalid_nulls:
            message = "Only Task update available_at may be explicitly null."
            raise ValueError(message)
        return self


class TaskResultInput(_CommandModel):
    """Closed caller-controlled structured Result content without identities."""

    summary: str | None = None
    criteria: tuple[CriterionOutcome, ...] = ()
    artifacts: tuple[ArtifactReference, ...] = ()
    proposed_follow_ups: tuple[ProposedFollowUp, ...] = ()

    @field_validator("summary", mode="before")
    @classmethod
    def _validate_summary(cls, value: object) -> str | None:
        """Normalize an optional Result summary.

        Args:
            value: Candidate summary or ``None``.

        Returns:
            The normalized summary or ``None``.

        """
        return _validate_optional_result_text(value, label="Result summary")

    @field_validator("criteria", mode="before")
    @classmethod
    def _validate_criteria(cls, value: object) -> tuple[CriterionOutcome, ...]:
        """Validate the ordered criterion-outcome collection.

        Args:
            value: Candidate structured collection.

        Returns:
            Immutable criterion outcomes.

        """
        return _validate_criterion_outcomes(value)

    @field_validator("artifacts", mode="before")
    @classmethod
    def _validate_artifacts(cls, value: object) -> tuple[ArtifactReference, ...]:
        """Validate the ordered artifact-reference collection.

        Args:
            value: Candidate structured collection.

        Returns:
            Immutable artifact references.

        """
        return _validate_artifact_references(value)

    @field_validator("proposed_follow_ups", mode="before")
    @classmethod
    def _validate_follow_ups(cls, value: object) -> tuple[ProposedFollowUp, ...]:
        """Validate the ordered inert follow-up collection.

        Args:
            value: Candidate structured collection.

        Returns:
            Immutable proposed follow-ups.

        """
        return _validate_proposed_follow_ups(value)


class _VersionedTaskInput(_CommandModel):
    """Shared validated Human intent for one existing-Task mutation."""

    project_id: ProjectId
    subject_id: SubjectId
    task: TaskId | str
    expected_version: int
    idempotency_key: str | None = None

    @field_validator("task", mode="before")
    @classmethod
    def _validate_task(cls, value: object) -> TaskId | str:
        """Validate a canonical or Human Task selector.

        Args:
            value: Candidate Task selector.

        Returns:
            A typed canonical identity or validated Human key.

        """
        return _validate_task_selector(value)

    @field_validator("expected_version", mode="before")
    @classmethod
    def _validate_expected_version(cls, value: object) -> int:
        """Require an explicit positive optimistic version.

        Args:
            value: Candidate version.

        Returns:
            The validated positive integer.

        """
        return _validate_positive_version(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional mutation replay key.

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


class UpdateTaskInput(_VersionedTaskInput):
    """Human intent to conditionally update Task definition fields."""

    patch: TaskUpdatePatch


class BlockTaskInput(_VersionedTaskInput):
    """Human intent to move an open Task to blocked."""

    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str:
        """Normalize the required blocking reason.

        Args:
            value: Candidate reason.

        Returns:
            The normalized bounded reason.

        """
        return _validate_required_reason(value, label="Task blocking reason")


class UnblockTaskInput(_VersionedTaskInput):
    """Human intent to return a blocked Task to open."""


class CancelTaskInput(_VersionedTaskInput):
    """Human intent to cancel a mutable Task with an optional reason."""

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str | None:
        """Normalize an optional cancellation reason.

        Args:
            value: Candidate reason or ``None``.

        Returns:
            The normalized reason or ``None``.

        """
        return _validate_optional_reason(value, label="Task cancellation reason")


class AddTaskDependencyInput(_VersionedTaskInput):
    """Human intent to add one Task prerequisite."""

    prerequisite: TaskId | str

    @field_validator("prerequisite", mode="before")
    @classmethod
    def _validate_prerequisite(cls, value: object) -> TaskId | str:
        """Validate the prerequisite Task selector.

        Args:
            value: Candidate prerequisite selector.

        Returns:
            A typed canonical identity or validated Human key.

        """
        return _validate_task_selector(value)


class RemoveTaskDependencyInput(AddTaskDependencyInput):
    """Human intent to remove one existing Task prerequisite."""


class SubmitHumanResultInput(_VersionedTaskInput):
    """Human intent to submit manual work without an Agent Attempt."""

    comment: str | None = None
    result: TaskResultInput = Field(default_factory=TaskResultInput)

    @field_validator("comment", mode="before")
    @classmethod
    def _validate_comment(cls, value: object) -> str | None:
        """Normalize an optional Human submission comment.

        Args:
            value: Candidate comment or ``None``.

        Returns:
            The normalized comment or ``None``.

        """
        return _validate_optional_result_text(value, label="Result comment")


class ApproveResultInput(_VersionedTaskInput):
    """Human intent to approve the Task's current pending Result."""

    comment: str | None = None

    @field_validator("comment", mode="before")
    @classmethod
    def _validate_comment(cls, value: object) -> str | None:
        """Normalize an optional approval comment.

        Args:
            value: Candidate comment or ``None``.

        Returns:
            The normalized comment or ``None``.

        """
        return _validate_optional_result_text(value, label="Review comment")


class RejectResultInput(_VersionedTaskInput):
    """Human intent to reject the Task's current pending Result."""

    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str:
        """Normalize the required rejection reason.

        Args:
            value: Candidate reason.

        Returns:
            The normalized bounded reason.

        """
        return _validate_required_reason(value, label="Review rejection reason")


class GetTaskDetails(_CommandModel):
    """Read complete Phase 3 Task details by scoped selector."""

    project_id: ProjectId
    subject_id: SubjectId
    task: TaskId | str
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)

    @field_validator("task", mode="before")
    @classmethod
    def _validate_task(cls, value: object) -> TaskId | str:
        """Validate the detail-query Task selector.

        Args:
            value: Candidate Task selector.

        Returns:
            A typed canonical identity or validated Human key.

        """
        return _validate_task_selector(value)


class ListTasksByView(_CommandModel):
    """Read one view-bound page in either Project or Instance scope."""

    profile: str = "local"
    subject_id: SubjectId
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)
    project_id: ProjectId | None = None
    instance_id: InstanceId | None = None
    view: TaskListView = TaskListView.ALL
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

    @field_validator("view", mode="before")
    @classmethod
    def _validate_view(cls, value: object) -> TaskListView:
        """Validate the exact Task list view.

        Args:
            value: Candidate view enum or string.

        Returns:
            The typed view.

        Raises:
            ValueError: If the view is unsupported.

        """
        if isinstance(value, TaskListView):
            return value
        if not isinstance(value, str):
            message = "Task list view must be a supported string."
            raise ValueError(message)  # noqa: TRY004 - Pydantic wraps ValueError.
        return TaskListView(value)

    @field_validator("cursor", mode="before")
    @classmethod
    def _validate_cursor(cls, value: object) -> str | None:
        """Validate an optional view-bound opaque cursor.

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

    @model_validator(mode="after")
    def _validate_scope(self) -> ListTasksByView:
        """Require exactly one Project or Instance selection scope.

        Returns:
            The validated view query.

        Raises:
            ValueError: If both or neither scopes are present.

        """
        if (self.project_id is None) == (self.instance_id is None):
            message = "Task view query requires exactly one Project or Instance scope."
            raise ValueError(message)
        return self


class ReadTaskEvents(_CommandModel):
    """Read one bounded TaskEvent snapshot after an Instance cursor."""

    project_id: ProjectId
    subject_id: SubjectId
    task: TaskId | str
    actor: AuthenticatedActor | None = Field(default=None, exclude=True, repr=False)
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE)

    @field_validator("task", mode="before")
    @classmethod
    def _validate_task(cls, value: object) -> TaskId | str:
        """Validate the event-query Task selector.

        Args:
            value: Candidate Task selector.

        Returns:
            A typed canonical identity or validated Human key.

        """
        return _validate_task_selector(value)


class _ExistingTaskMutation(_CommandModel):
    """Shared repository input for an attributable optimistic mutation."""

    task_uid: TaskId
    project_id: ProjectId
    actor_subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime
    expected_version: int
    idempotency_key: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Require one authoritative UTC mutation timestamp.

        Args:
            value: Candidate timestamp.

        Returns:
            The validated UTC datetime.

        """
        return validate_utc_timestamp(value, label="Task mutation occurred_at")

    @field_validator("expected_version", mode="before")
    @classmethod
    def _validate_expected_version(cls, value: object) -> int:
        """Require the exact positive optimistic precondition.

        Args:
            value: Candidate expected version.

        Returns:
            The validated positive version.

        """
        return _validate_positive_version(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional mutation replay key.

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


class _ClaimGuardedTaskMutation(_ExistingTaskMutation):
    """Human Task mutation carrying a candidate lazy-expiry event identity."""

    claim_expired_event_id: TaskEventId

    @model_validator(mode="after")
    def _validate_all_event_ids_are_distinct(self) -> _ClaimGuardedTaskMutation:
        """Prevent requested and conditional expiry events from sharing an ID.

        Returns:
            The mutation with pairwise-distinct candidate event identities.

        Raises:
            ValueError: If any event identity is reused within the mutation.

        """
        event_ids = tuple(
            value
            for name in type(self).model_fields
            if name.endswith("event_id")
            and isinstance((value := getattr(self, name)), TaskEventId)
        )
        _validate_distinct_event_ids(
            event_ids[0],
            *event_ids[1:],
            label="Task mutation",
        )
        return self


class TaskUpdateMutation(_ClaimGuardedTaskMutation):
    """Atomically apply one Task definition patch at an expected version."""

    event_id: TaskEventId
    patch: TaskUpdatePatch


class TaskBlockMutation(_ClaimGuardedTaskMutation):
    """Atomically block one open Task at an expected version."""

    event_id: TaskEventId
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str:
        """Normalize the required blocking reason.

        Args:
            value: Candidate reason.

        Returns:
            The normalized bounded reason.

        """
        return _validate_required_reason(value, label="Task blocking reason")


class TaskUnblockMutation(_ClaimGuardedTaskMutation):
    """Atomically unblock one Task at an expected version."""

    event_id: TaskEventId


class TaskCancelMutation(_ClaimGuardedTaskMutation):
    """Atomically cancel one mutable Task at an expected version."""

    event_id: TaskEventId
    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str | None:
        """Normalize an optional cancellation reason.

        Args:
            value: Candidate reason or ``None``.

        Returns:
            The normalized reason or ``None``.

        """
        return _validate_optional_reason(value, label="Task cancellation reason")


class AddTaskDependencyMutation(_ClaimGuardedTaskMutation):
    """Atomically add one same-Project prerequisite edge."""

    event_id: TaskEventId
    prerequisite_uid: TaskId


class RemoveTaskDependencyMutation(AddTaskDependencyMutation):
    """Atomically remove one existing prerequisite edge."""


class SubmitHumanResultMutation(_ClaimGuardedTaskMutation):
    """Atomically submit a Human Result without an Agent Attempt."""

    result_id: ResultId
    result_submitted_event_id: TaskEventId
    task_completed_event_id: TaskEventId | None = None
    comment: str | None = None
    result: TaskResultInput = Field(default_factory=TaskResultInput)

    @field_validator("comment", mode="before")
    @classmethod
    def _validate_comment(cls, value: object) -> str | None:
        """Normalize an optional Human submission comment.

        Args:
            value: Candidate comment or ``None``.

        Returns:
            The normalized comment or ``None``.

        """
        return _validate_optional_result_text(value, label="Result comment")


class ApproveResultMutation(_ExistingTaskMutation):
    """Atomically approve and complete the current pending Result."""

    review_approved_event_id: TaskEventId
    task_completed_event_id: TaskEventId
    comment: str | None = None

    @field_validator("comment", mode="before")
    @classmethod
    def _validate_comment(cls, value: object) -> str | None:
        """Normalize an optional approval comment.

        Args:
            value: Candidate comment or ``None``.

        Returns:
            The normalized comment or ``None``.

        """
        return _validate_optional_result_text(value, label="Review comment")

    @model_validator(mode="after")
    def _validate_distinct_event_ids(self) -> ApproveResultMutation:
        """Require separate review and completion event identities.

        Returns:
            The validated approval mutation.

        Raises:
            ValueError: If one event identity is reused.

        """
        if self.review_approved_event_id == self.task_completed_event_id:
            message = "Approval event identities must be distinct."
            raise ValueError(message)
        return self


class RejectResultMutation(_ExistingTaskMutation):
    """Atomically reject and deselect the current pending Result."""

    review_rejected_event_id: TaskEventId
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str:
        """Normalize the required rejection reason.

        Args:
            value: Candidate reason.

        Returns:
            The normalized bounded reason.

        """
        return _validate_required_reason(value, label="Review rejection reason")


class _ClaimOperationMutation(_CommandModel):
    """Shared trusted attribution for one Phase 4 Claim operation."""

    project_id: ProjectId
    actor_subject_id: SubjectId
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Require one authoritative UTC operation timestamp.

        Args:
            value: Candidate timestamp.

        Returns:
            The validated UTC datetime.

        """
        return validate_utc_timestamp(value, label="Claim operation occurred_at")

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional Claim-operation replay key.

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


class ClaimTaskMutation(_ClaimOperationMutation):
    """Atomically acquire one explicit ready Task for a Human owner."""

    task_uid: TaskId
    lease_duration_seconds: int
    task_claimed_event_id: TaskEventId
    claim_expired_event_id: TaskEventId

    @model_validator(mode="after")
    def _validate_claim_contract(self) -> ClaimTaskMutation:
        """Validate Human Lease bounds and distinct candidate events.

        Returns:
            The validated targeted Human Claim mutation.

        Raises:
            ValueError: If Lease seconds or event identities are invalid.

        """
        _validate_lease_duration_seconds(
            self.lease_duration_seconds,
            attempt_id=None,
        )
        _validate_distinct_event_ids(
            self.task_claimed_event_id,
            self.claim_expired_event_id,
            label="Claim acquisition",
        )
        return self


class ClaimNextTaskMutation(_ClaimOperationMutation):
    """Atomically pull the highest-ranked ready Task for one Agent Attempt."""

    attempt_id: AttemptId
    lease_duration_seconds: int
    task_claimed_event_id: TaskEventId
    claim_expired_event_id: TaskEventId

    @model_validator(mode="after")
    def _validate_claim_contract(self) -> ClaimNextTaskMutation:
        """Validate Agent Lease bounds and distinct candidate events.

        Returns:
            The validated Agent pull mutation.

        Raises:
            ValueError: If Lease seconds or event identities are invalid.

        """
        _validate_lease_duration_seconds(
            self.lease_duration_seconds,
            attempt_id=self.attempt_id,
        )
        _validate_distinct_event_ids(
            self.task_claimed_event_id,
            self.claim_expired_event_id,
            label="Claim acquisition",
        )
        return self


class RenewClaimMutation(_ClaimOperationMutation):
    """Atomically renew a Human Claim or heartbeat an Agent Attempt."""

    task_uid: TaskId
    attempt_id: AttemptId | None
    lease_duration_seconds: int
    claim_renewed_event_id: TaskEventId

    @model_validator(mode="after")
    def _validate_lease_contract(self) -> RenewClaimMutation:
        """Validate the duration against the nullable-Attempt owner path.

        Returns:
            The validated renewal mutation.

        """
        _validate_lease_duration_seconds(
            self.lease_duration_seconds,
            attempt_id=self.attempt_id,
        )
        return self


class ReleaseClaimMutation(_ClaimOperationMutation):
    """Atomically release the exact current Human or Agent Claim."""

    task_uid: TaskId
    attempt_id: AttemptId | None
    claim_released_event_id: TaskEventId


class ReportTaskProgressMutation(_ClaimOperationMutation):
    """Atomically append bounded progress for one current Agent Attempt."""

    task_uid: TaskId
    attempt_id: AttemptId
    progress: TaskProgress
    progress_reported_event_id: TaskEventId
    observation_event_ids: tuple[TaskEventId, ...] = ()

    @model_validator(mode="after")
    def _validate_progress_events(self) -> ReportTaskProgressMutation:
        """Bind one event identity to every ordered progress observation.

        Returns:
            The validated progress mutation.

        Raises:
            ValueError: If event count or identity uniqueness is invalid.

        """
        observation_count = len(self.progress.observations or ())
        if len(self.observation_event_ids) != observation_count:
            message = (
                "Progress observation event identities must align one-for-one "
                "with observations."
            )
            raise ValueError(message)
        _validate_distinct_event_ids(
            self.progress_reported_event_id,
            *self.observation_event_ids,
            label="Progress",
        )
        return self


class SubmitAgentResultMutation(_ExistingTaskMutation):
    """Atomically submit structured Result data for one current Agent Attempt."""

    attempt_id: AttemptId
    result_id: ResultId
    result_submitted_event_id: TaskEventId
    task_completed_event_id: TaskEventId | None = None
    result: TaskResultInput

    @model_validator(mode="after")
    def _validate_distinct_event_ids(self) -> SubmitAgentResultMutation:
        """Require distinct submitted and optional completion events.

        Returns:
            The validated Agent submission mutation.

        Raises:
            ValueError: If one event identity is reused.

        """
        _validate_distinct_event_ids(
            self.result_submitted_event_id,
            self.task_completed_event_id,
            label="Agent Result submission",
        )
        return self


class AuthenticateToken(_CommandModel):
    """Authenticate one parsed Token digest for an expected Instance."""

    token_id: TokenId
    token_digest: str = Field(repr=False, exclude=True)
    expected_instance_id: InstanceId
    occurred_at: datetime

    @field_validator("token_digest", mode="before")
    @classmethod
    def _validate_token_digest(cls, value: object) -> str:
        """Validate a canonical Token digest without accepting raw material.

        Args:
            value: Candidate lowercase SHA-256 digest.

        Returns:
            The validated digest.

        """
        return validate_lowercase_sha256(value, label="Token digest")

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Validate the explicit authentication transaction time.

        Args:
            value: Candidate authoritative time.

        Returns:
            The validated UTC timestamp.

        """
        return validate_utc_timestamp(value, label="Authentication occurred_at")


class GetCurrentIdentity(_CommandModel):
    """Read non-secret metadata for one authenticated identity."""

    actor: AuthenticatedActor


class AuthorizeActor(_CommandModel):
    """Request a fresh authorization projection for one operation."""

    actor: AuthenticatedActor
    permission: Permission
    project_id: ProjectId | None = None
    required_kind: SubjectKind | None = None
    occurred_at: datetime

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Validate the explicit authorization transaction time.

        Args:
            value: Candidate authoritative time.

        Returns:
            The validated UTC timestamp.

        """
        return validate_utc_timestamp(value, label="Authorization occurred_at")

    @model_validator(mode="after")
    def _validate_scope(self) -> AuthorizeActor:
        """Require a Project exactly for Project-scoped permissions.

        Returns:
            The internally consistent authorization command.

        Raises:
            ValueError: If Instance and Project permission scopes are mixed.

        """
        instance_permission = self.permission is Permission.MANAGE_INSTANCE
        if instance_permission == (self.project_id is not None):
            message = (
                "Instance permission requires no Project; Project permission "
                "requires one Project."
            )
            raise ValueError(message)
        return self


class _IdentityPageCommand(_CommandModel):
    """Shared actor-bound cursor contract for identity metadata lists."""

    actor: AuthenticatedActor
    cursor: str | None = None
    limit: int = Field(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE)

    @field_validator("cursor", mode="before")
    @classmethod
    def _validate_cursor(cls, value: object) -> str | None:
        """Validate one optional bounded opaque continuation cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or null.

        """
        return _validate_opaque_token(
            value,
            label="Identity cursor",
            maximum=_CURSOR_MAX_LENGTH,
            optional=True,
        )


class ListSubjects(_IdentityPageCommand):
    """List Instance Subjects as an authenticated administrator."""


class ListProjectGrants(_IdentityPageCommand):
    """List Project grants visible to an authorized administrator."""

    project: ProjectId | str

    @field_validator("project", mode="before")
    @classmethod
    def _validate_project(cls, value: object) -> ProjectId | str:
        """Validate an exact typed ID or canonical Project key.

        Args:
            value: Candidate Project selector.

        Returns:
            A typed ID or validated key.

        """
        return _validate_project_selector(value)


class ListTokens(_IdentityPageCommand):
    """List self or administrator-visible non-secret Token metadata."""

    subject: SubjectId | str | None = None

    @field_validator("subject", mode="before")
    @classmethod
    def _validate_subject(cls, value: object) -> SubjectId | str | None:
        """Validate an optional exact Subject ID or handle.

        Args:
            value: Candidate Subject selector or null for self.

        Returns:
            A typed ID, validated handle, or null.

        """
        if value is None:
            return None
        return _validate_subject_selector(value)


class ReadAuditEvents(_CommandModel):
    """Read one bounded ascending page of administrative AuditEvents."""

    actor: AuthenticatedActor
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE)


class _IdentityMutation(_CommandModel):
    """Shared authenticated metadata for administrative mutations."""

    actor: AuthenticatedActor
    request_id: RequestId
    occurred_at: datetime
    idempotency_key: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _validate_occurred_at(cls, value: object) -> datetime:
        """Validate an explicit authoritative mutation timestamp.

        Args:
            value: Candidate UTC time.

        Returns:
            The validated timestamp.

        """
        return validate_utc_timestamp(value, label="Identity mutation occurred_at")

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _validate_idempotency_key(cls, value: object) -> str | None:
        """Validate an optional bounded caller idempotency key.

        Args:
            value: Candidate key.

        Returns:
            The validated key or null.

        """
        return _validate_opaque_token(
            value,
            label="Idempotency key",
            maximum=_IDEMPOTENCY_KEY_MAX_LENGTH,
            optional=True,
        )


class CreateSubjectMutation(_IdentityMutation):
    """Create one enabled non-administrative Human or Agent Subject."""

    subject_id: SubjectId
    kind: SubjectKind
    handle: str
    display_name: str

    @model_validator(mode="before")
    @classmethod
    def _default_display_name(cls, value: object) -> object:
        """Default an omitted display name to the immutable handle.

        Args:
            value: Candidate model input.

        Returns:
            A copied mapping with the explicit display-name default.

        """
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        if copied.get("display_name") is None and "handle" in copied:
            copied["display_name"] = copied["handle"]
        return copied

    @field_validator("handle", mode="before")
    @classmethod
    def _validate_handle(cls, value: object) -> str:
        """Validate the immutable exact Subject handle.

        Args:
            value: Candidate handle.

        Returns:
            The validated handle.

        """
        return validate_subject_handle(value)

    @field_validator("display_name", mode="before")
    @classmethod
    def _validate_display_name(cls, value: object) -> str:
        """Normalize one mutable printable Subject display name.

        Args:
            value: Candidate display name.

        Returns:
            The normalized display name.

        """
        return normalize_bounded_printable_text(
            value,
            label="Subject display_name",
            maximum=_SUBJECT_DISPLAY_NAME_MAX_LENGTH,
        )


class _ExistingSubjectMutation(_IdentityMutation):
    """Shared optimistic contract for one existing Subject mutation."""

    subject: SubjectId | str
    expected_version: int

    @field_validator("subject", mode="before")
    @classmethod
    def _validate_subject(cls, value: object) -> SubjectId | str:
        """Validate an exact Subject ID or immutable handle.

        Args:
            value: Candidate Subject selector.

        Returns:
            A typed ID or validated handle.

        """
        return _validate_subject_selector(value)

    @field_validator("expected_version", mode="before")
    @classmethod
    def _validate_expected_version(cls, value: object) -> int:
        """Validate strict positive optimistic identity version.

        Args:
            value: Candidate version.

        Returns:
            The validated positive integer.

        """
        return validate_positive_integer(value, label="Expected identity version")


class UpdateSubjectMutation(_ExistingSubjectMutation):
    """Update only one Subject's mutable display name."""

    display_name: str

    @field_validator("display_name", mode="before")
    @classmethod
    def _validate_display_name(cls, value: object) -> str:
        """Normalize one mutable printable Subject display name.

        Args:
            value: Candidate display name.

        Returns:
            The normalized display name.

        """
        return normalize_bounded_printable_text(
            value,
            label="Subject display_name",
            maximum=_SUBJECT_DISPLAY_NAME_MAX_LENGTH,
        )


class SetSubjectEnabledMutation(_ExistingSubjectMutation):
    """Enable or disable one existing Subject at its exact version."""

    enabled: bool


class SetInstanceAdminMutation(_ExistingSubjectMutation):
    """Grant or revoke Instance-administrator status at an exact version."""

    is_instance_admin: bool


class _ProjectGrantMutation(_IdentityMutation):
    """Shared exact Subject and Project selectors for grant mutations."""

    subject: SubjectId | str
    project: ProjectId | str

    @field_validator("subject", mode="before")
    @classmethod
    def _validate_subject(cls, value: object) -> SubjectId | str:
        """Validate the exact granted Subject selector.

        Args:
            value: Candidate Subject ID or handle.

        Returns:
            The validated selector.

        """
        return _validate_subject_selector(value)

    @field_validator("project", mode="before")
    @classmethod
    def _validate_project(cls, value: object) -> ProjectId | str:
        """Validate the exact governed Project selector.

        Args:
            value: Candidate Project ID or key.

        Returns:
            The validated selector.

        """
        return _validate_project_selector(value)


class AssignProjectGrantMutation(_ProjectGrantMutation):
    """Create or replace one cumulative Project grant."""

    role: ProjectRole
    expected_version: int | None = None

    @field_validator("expected_version", mode="before")
    @classmethod
    def _validate_expected_version(cls, value: object) -> int | None:
        """Validate optional create-versus-replace concurrency input.

        Args:
            value: Candidate current version or null for absent-grant creation.

        Returns:
            The validated version or null.

        """
        if value is None:
            return None
        return validate_positive_integer(value, label="Expected grant version")


class RevokeProjectGrantMutation(_ProjectGrantMutation):
    """Revoke one exact current Project grant."""

    expected_version: int

    @field_validator("expected_version", mode="before")
    @classmethod
    def _validate_expected_version(cls, value: object) -> int:
        """Validate the exact current grant version.

        Args:
            value: Candidate positive version.

        Returns:
            The validated current version.

        """
        return validate_positive_integer(value, label="Expected grant version")


class IssueTokenMutation(_IdentityMutation):
    """Persist one pending Token using only its canonical digest."""

    token_id: TokenId
    subject: SubjectId | str
    token_digest: str = Field(repr=False, exclude=True)
    expires_at: datetime

    @field_validator("subject", mode="before")
    @classmethod
    def _validate_subject(cls, value: object) -> SubjectId | str:
        """Validate the Token target Subject selector.

        Args:
            value: Candidate Subject ID or handle.

        Returns:
            The validated selector.

        """
        return _validate_subject_selector(value)

    @field_validator("token_digest", mode="before")
    @classmethod
    def _validate_token_digest(cls, value: object) -> str:
        """Validate the non-reversible canonical Token digest.

        Args:
            value: Candidate lowercase SHA-256 digest.

        Returns:
            The validated digest.

        """
        return validate_lowercase_sha256(value, label="Token digest")

    @field_validator("expires_at", mode="before")
    @classmethod
    def _validate_expires_at(cls, value: object) -> datetime:
        """Validate the explicit exclusive Token expiry boundary.

        Args:
            value: Candidate UTC expiry.

        Returns:
            The validated timestamp.

        """
        return validate_utc_timestamp(value, label="Token expires_at")

    @model_validator(mode="after")
    def _validate_lifetime(self) -> IssueTokenMutation:
        """Require expiry to follow Token creation time.

        Returns:
            The validated pending-Token mutation.

        Raises:
            ValueError: If expiry is not later than creation.

        """
        if self.expires_at <= self.occurred_at:
            message = "Token expires_at must follow creation time."
            raise ValueError(message)
        return self


class ActivateTokenMutation(_IdentityMutation):
    """Activate one pending Token after its credential sink succeeds."""

    token_id: TokenId


class RevokeTokenMutation(_IdentityMutation):
    """Monotonically revoke one public Token identity."""

    token_id: TokenId


class RecoverLocalMutation(_CommandModel):
    """Perform the exact tokenless bootstrap-Human recovery operation."""

    instance_id: InstanceId
    bootstrap_handle: str
    token_id: TokenId
    token_digest: str = Field(repr=False, exclude=True)
    request_id: RequestId
    occurred_at: datetime
    expires_at: datetime

    @field_validator("bootstrap_handle", mode="before")
    @classmethod
    def _validate_bootstrap_handle(cls, value: object) -> str:
        """Require the immutable canonical bootstrap handle.

        Args:
            value: Candidate confirmed handle.

        Returns:
            The exact bootstrap handle.

        Raises:
            ValueError: If another valid handle is supplied.

        """
        handle = validate_subject_handle(value)
        if handle != "local-operator":
            message = "Local recovery requires the bootstrap Subject handle."
            raise ValueError(message)
        return handle

    @field_validator("token_digest", mode="before")
    @classmethod
    def _validate_token_digest(cls, value: object) -> str:
        """Validate the replacement Token digest.

        Args:
            value: Candidate lowercase SHA-256 digest.

        Returns:
            The validated digest.

        """
        return validate_lowercase_sha256(value, label="Token digest")

    @field_validator("occurred_at", "expires_at", mode="before")
    @classmethod
    def _validate_timestamp(cls, value: object, info: ValidationInfo) -> datetime:
        """Validate one explicit recovery lifecycle timestamp.

        Args:
            value: Candidate UTC timestamp.
            info: Pydantic validation metadata naming the field.

        Returns:
            The validated timestamp.

        """
        return validate_utc_timestamp(value, label=f"Recovery {info.field_name}")

    @model_validator(mode="after")
    def _validate_lifetime(self) -> RecoverLocalMutation:
        """Require replacement expiry to follow recovery time.

        Returns:
            The validated recovery mutation.

        Raises:
            ValueError: If the lifetime is empty or negative.

        """
        if self.expires_at <= self.occurred_at:
            message = "Recovery Token expires_at must follow recovery time."
            raise ValueError(message)
        return self


def _validate_lease_duration_seconds(
    value: object,
    *,
    attempt_id: AttemptId | None,
) -> int:
    """Validate exact integer Lease seconds through the pure domain bounds.

    Args:
        value: Candidate resolved duration in seconds.
        attempt_id: Null for Human bounds or one Agent Attempt identity.

    Returns:
        The validated integer second count.

    Raises:
        DomainValidationError: If value is not an in-range real integer.

    """
    if type(value) is not int or value < 1:
        message = "Lease duration seconds must be a positive integer."
        raise DomainValidationError(message)
    resolve_lease_duration(timedelta(seconds=value), attempt_id=attempt_id)
    return value


def _validate_distinct_event_ids(
    first: TaskEventId,
    *others: TaskEventId | None,
    label: str,
) -> None:
    """Require all supplied candidate TaskEvent identities to be distinct.

    Args:
        first: Required first event identity.
        others: Optional remaining event identities.
        label: Safe operation label for validation errors.

    Raises:
        ValueError: If any supplied identity is reused.

    """
    supplied = (first, *(item for item in others if item is not None))
    if len(set(supplied)) != len(supplied):
        message = f"{label} event identities must be distinct."
        raise ValueError(message)


def _validate_subject_selector(value: object) -> SubjectId | str:
    """Validate an exact Subject ID or immutable handle without ambiguity.

    Args:
        value: Candidate typed ID or serialized selector.

    Returns:
        A typed SubjectId or exact validated handle.

    Raises:
        DomainValidationError: If a typed-prefix string is malformed or the
            handle contract is violated.

    """
    if isinstance(value, SubjectId):
        return value
    if isinstance(value, str) and value.startswith("sub_"):
        return SubjectId(value)
    return validate_subject_handle(value)


def _validate_project_selector(value: object) -> ProjectId | str:
    """Validate an exact Project ID or immutable uppercase key.

    Args:
        value: Candidate typed ID or serialized selector.

    Returns:
        A typed ProjectId or exact validated Project key.

    Raises:
        DomainValidationError: If the selector is malformed.

    """
    if isinstance(value, ProjectId):
        return value
    if isinstance(value, str) and value.startswith("prj_"):
        return ProjectId(value)
    return validate_project_key(value)


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


def _validate_positive_version(value: object) -> int:
    """Validate an optimistic Task version without boolean coercion.

    Args:
        value: Candidate expected version.

    Returns:
        A strictly positive integer.

    Raises:
        DomainValidationError: If the value is not a positive integer.

    """
    if type(value) is not int or value < 1:
        message = "Expected version must be a positive integer."
        raise DomainValidationError(message)
    return value


def _validate_task_selector(value: object) -> TaskId | str:
    """Validate and disambiguate a canonical Task ID or Human key.

    Args:
        value: Candidate selector.

    Returns:
        A typed TaskId or validated stable key.

    Raises:
        ValueError: If the selector is malformed or ambiguous.

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


def _validate_optional_approval(value: object) -> ApprovalRequirement | None:
    """Validate an optional approval enum or exact serialized value.

    Args:
        value: Candidate approval or ``None``.

    Returns:
        The typed approval requirement or ``None``.

    Raises:
        ValueError: If the serialized value is unsupported.

    """
    if value is None or isinstance(value, ApprovalRequirement):
        return value
    if not isinstance(value, str):
        message = "Task approval must be none or human."
        raise ValueError(message)  # noqa: TRY004 - Pydantic wraps ValueError.
    return ApprovalRequirement(value)


def _validate_required_approval(value: object) -> ApprovalRequirement:
    """Validate one non-null approval requirement.

    Args:
        value: Candidate approval enum or exact serialized value.

    Returns:
        The typed approval requirement.

    Raises:
        ValueError: If the value is null or unsupported.

    """
    validated = _validate_optional_approval(value)
    if validated is None:
        message = "Task approval must be none or human."
        raise ValueError(message)
    return validated


def _validate_required_reason(value: object, *, label: str) -> str:
    """Normalize one required Phase 3 reason.

    Args:
        value: Candidate reason.
        label: Human-readable field label.

    Returns:
        The normalized bounded reason.

    """
    return normalize_bounded_printable_text(
        value,
        label=label,
        maximum=ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
    )


def _validate_optional_reason(value: object, *, label: str) -> str | None:
    """Normalize one optional Phase 3 reason.

    Args:
        value: Candidate reason or ``None``.
        label: Human-readable field label.

    Returns:
        The normalized bounded reason or ``None``.

    """
    if value is None:
        return None
    return _validate_required_reason(value, label=label)


def _validate_optional_result_text(value: object, *, label: str) -> str | None:
    """Normalize one optional bounded printable Result text field.

    Args:
        value: Candidate text or ``None``.
        label: Human-readable field label.

    Returns:
        The normalized text or ``None``.

    """
    if value is None:
        return None
    return normalize_bounded_printable_text(
        value,
        label=label,
        maximum=RESULT_TEXT_MAX_LENGTH,
    )


def _validate_structured_sequence(
    value: object,
    *,
    label: str,
    maximum: int,
) -> tuple[object, ...]:
    """Defensively copy a bounded ordered structured-input collection.

    Args:
        value: Candidate collection.
        label: Human-readable field label.
        maximum: Inclusive item-count bound.

    Returns:
        An immutable shallow copy for item-specific validation.

    Raises:
        ValueError: If the value is not ordered or exceeds its bound.

    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{label} must be an ordered collection."
        raise ValueError(message)  # noqa: TRY004 - Pydantic wraps ValueError.
    copied = tuple(value)
    if len(copied) > maximum:
        message = f"{label} must not contain more than {maximum} items."
        raise ValueError(message)
    return copied


def _validate_acceptance_collection(
    value: object,
    *,
    optional: bool,
) -> tuple[AcceptanceCriterion, ...] | None:
    """Validate closed structured acceptance-criterion input.

    Args:
        value: Candidate collection or ``None``.
        optional: Whether ``None`` represents an omitted patch field.

    Returns:
        Immutable criteria or ``None``.

    Raises:
        ValueError: If a collection entry has an open or invalid shape.

    """
    if value is None:
        if optional:
            return None
        message = "Task acceptance must be an ordered collection."
        raise ValueError(message)
    values = _validate_structured_sequence(
        value,
        label="Task acceptance",
        maximum=ACCEPTANCE_CRITERIA_MAX_ITEMS,
    )
    criteria: list[AcceptanceCriterion] = []
    for item in values:
        if isinstance(item, AcceptanceCriterion):
            criterion = item
        elif isinstance(item, Mapping) and set(item) == {"id", "text", "required"}:
            criterion = AcceptanceCriterion(
                id=item["id"],
                text=item["text"],
                required=item["required"],
            )
        else:
            message = "Task acceptance entries must use the closed criterion shape."
            raise ValueError(message)
        criteria.append(criterion)
    if len({criterion.id for criterion in criteria}) != len(criteria):
        message = "Task acceptance criterion IDs must be unique."
        raise ValueError(message)
    return tuple(criteria)


def _validate_context_collection(
    value: object,
    *,
    optional: bool,
) -> tuple[ContextReference, ...] | None:
    """Validate closed structured context-reference input.

    Args:
        value: Candidate collection or ``None``.
        optional: Whether ``None`` represents an omitted patch field.

    Returns:
        Immutable context references or ``None``.

    Raises:
        ValueError: If a collection entry has an open or invalid shape.

    """
    if value is None:
        if optional:
            return None
        message = "Task context must be an ordered collection."
        raise ValueError(message)
    values = _validate_structured_sequence(
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


def _validate_criterion_outcomes(value: object) -> tuple[CriterionOutcome, ...]:
    """Validate closed structured Result criterion outcomes.

    Args:
        value: Candidate outcome collection.

    Returns:
        Immutable criterion outcomes.

    Raises:
        ValueError: If an entry is open, malformed, or duplicated.

    """
    values = _validate_structured_sequence(
        value,
        label="Result criteria",
        maximum=RESULT_COLLECTION_MAX_ITEMS,
    )
    outcomes: list[CriterionOutcome] = []
    for item in values:
        if isinstance(item, CriterionOutcome):
            outcome = item
        elif (
            isinstance(item, Mapping)
            and {"criterion_id", "status"} <= set(item)
            and set(item) <= {"criterion_id", "status", "evidence"}
        ):
            status = item["status"]
            if not isinstance(status, CriterionStatus):
                if not isinstance(status, str):
                    message = "Result criterion status must be a supported string."
                    raise ValueError(message)  # noqa: TRY004 - Pydantic boundary.
                status = CriterionStatus(status)
            outcome = CriterionOutcome(
                criterion_id=item["criterion_id"],
                status=status,
                evidence=item.get("evidence"),
            )
        else:
            message = "Result criteria entries must use the closed outcome shape."
            raise ValueError(message)
        outcomes.append(outcome)
    if len({item.criterion_id for item in outcomes}) != len(outcomes):
        message = "Result criterion outcomes must have unique criterion IDs."
        raise ValueError(message)
    return tuple(outcomes)


def _validate_artifact_references(value: object) -> tuple[ArtifactReference, ...]:
    """Validate closed structured Result artifact references.

    Args:
        value: Candidate artifact collection.

    Returns:
        Immutable artifact references.

    Raises:
        ValueError: If an entry has an open or malformed shape.

    """
    values = _validate_structured_sequence(
        value,
        label="Result artifacts",
        maximum=RESULT_COLLECTION_MAX_ITEMS,
    )
    artifacts: list[ArtifactReference] = []
    for item in values:
        if isinstance(item, ArtifactReference):
            artifact = item
        elif (
            isinstance(item, Mapping)
            and "uri" in item
            and set(item) <= {"uri", "media_type", "sha256"}
        ):
            artifact = ArtifactReference(
                uri=item["uri"],
                media_type=item.get("media_type"),
                sha256=item.get("sha256"),
            )
        else:
            message = "Result artifacts entries must use the closed reference shape."
            raise ValueError(message)
        artifacts.append(artifact)
    return tuple(artifacts)


def _validate_proposed_follow_ups(value: object) -> tuple[ProposedFollowUp, ...]:
    """Validate closed structured inert follow-up input.

    Args:
        value: Candidate follow-up collection.

    Returns:
        Immutable proposed follow-ups.

    Raises:
        ValueError: If an entry has an open or malformed shape.

    """
    values = _validate_structured_sequence(
        value,
        label="Result proposed_follow_ups",
        maximum=RESULT_COLLECTION_MAX_ITEMS,
    )
    follow_ups: list[ProposedFollowUp] = []
    for item in values:
        if isinstance(item, ProposedFollowUp):
            follow_up = item
        elif isinstance(item, Mapping) and set(item) == {"title"}:
            follow_up = ProposedFollowUp(title=item["title"])
        else:
            message = "Result proposed_follow_ups entries must use the closed shape."
            raise ValueError(message)
        follow_ups.append(follow_up)
    return tuple(follow_ups)
