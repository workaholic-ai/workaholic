"""Immutable Phase 1 domain entities and enumerated values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from workaholic.domain.errors import DomainValidationError
from workaholic.domain.identifiers import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    TaskEventId,
    TaskId,
)
from workaholic.domain.rules import (
    normalize_task_objective,
    normalize_task_title,
    validate_json_scalar,
    validate_positive_integer,
    validate_project_key,
    validate_task_key,
    validate_task_priority,
    validate_utc_timestamp,
)

if TYPE_CHECKING:
    from datetime import datetime

type JsonScalar = None | bool | int | float | str

_SUBJECT_DISPLAY_NAME_MIN_LENGTH = 1
_SUBJECT_DISPLAY_NAME_MAX_LENGTH = 200


class SubjectKind(StrEnum):
    """Kinds of independently operating Phase 1 Subjects."""

    HUMAN = "human"


class ProjectRole(StrEnum):
    """Project authorization roles available in Phase 1."""

    OWNER = "owner"


class TaskState(StrEnum):
    """Persisted Task states available in Phase 1."""

    OPEN = "open"


class TaskEventType(StrEnum):
    """Append-only Task event types available in Phase 1."""

    TASK_CREATED = "task_created"


@dataclass(frozen=True, slots=True)
class Instance:
    """One initialized Workaholic installation."""

    id: InstanceId
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate the Instance invariant set."""
        _require_instance(self.id, InstanceId, label="Instance id")
        validate_utc_timestamp(self.created_at, label="Instance created_at")


@dataclass(frozen=True, slots=True)
class Subject:
    """One attributable Human or Agent identity."""

    id: SubjectId
    kind: SubjectKind
    display_name: str
    enabled: bool
    is_instance_admin: bool

    def __post_init__(self) -> None:
        """Validate and normalize the Subject invariant set."""
        _require_instance(self.id, SubjectId, label="Subject id")
        _require_instance(self.kind, SubjectKind, label="Subject kind")
        object.__setattr__(
            self,
            "display_name",
            _normalize_display_name(self.display_name),
        )
        _require_boolean(self.enabled, label="Subject enabled")
        _require_boolean(
            self.is_instance_admin,
            label="Subject is_instance_admin",
        )


@dataclass(frozen=True, slots=True)
class Project:
    """One immutable task-number namespace within an Instance."""

    id: ProjectId
    instance_id: InstanceId
    key: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate the Project invariant set."""
        _require_instance(self.id, ProjectId, label="Project id")
        _require_instance(self.instance_id, InstanceId, label="Project instance_id")
        validate_project_key(self.key)
        validate_utc_timestamp(self.created_at, label="Project created_at")


@dataclass(frozen=True, slots=True)
class ProjectGrant:
    """One Subject's role within one Project."""

    subject_id: SubjectId
    project_id: ProjectId
    role: ProjectRole

    def __post_init__(self) -> None:
        """Validate the ProjectGrant invariant set."""
        _require_instance(
            self.subject_id,
            SubjectId,
            label="ProjectGrant subject_id",
        )
        _require_instance(
            self.project_id,
            ProjectId,
            label="ProjectGrant project_id",
        )
        _require_instance(self.role, ProjectRole, label="ProjectGrant role")


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """Safe repository-local binding to an authoritative Project."""

    context_version: int
    profile: str
    instance_id: InstanceId
    project_id: ProjectId
    project_key: str
    workspace_root: str

    def __post_init__(self) -> None:
        """Validate the Phase 1 Workspace binding invariant set."""
        if type(self.context_version) is not int or self.context_version != 1:
            message = "Workspace context_version must be 1."
            raise DomainValidationError(message)
        if self.profile != "local":
            message = "Workspace profile must be 'local' in Phase 1."
            raise DomainValidationError(message)
        _require_instance(
            self.instance_id,
            InstanceId,
            label="Workspace instance_id",
        )
        _require_instance(
            self.project_id,
            ProjectId,
            label="Workspace project_id",
        )
        validate_project_key(self.project_key)
        workspace_root: object = self.workspace_root
        if not isinstance(workspace_root, str) or not workspace_root:
            message = "Workspace root must be a nonempty string."
            raise DomainValidationError(message)
        if "\x00" in workspace_root:
            message = "Workspace root must not contain a null character."
            raise DomainValidationError(message)


@dataclass(frozen=True, slots=True)
class Task:
    """One desired outcome with stable Project-local and canonical identities."""

    uid: TaskId
    project_id: ProjectId
    number: int
    key: str
    title: str
    objective: str
    state: TaskState
    priority: int
    version: int
    created_by: SubjectId
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        """Validate and normalize the Task invariant set."""
        _require_instance(self.uid, TaskId, label="Task uid")
        _require_instance(self.project_id, ProjectId, label="Task project_id")
        validate_positive_integer(self.number, label="Task number")
        validate_task_key(self.key, task_number=self.number)
        object.__setattr__(self, "title", normalize_task_title(self.title))
        object.__setattr__(
            self,
            "objective",
            normalize_task_objective(self.objective),
        )
        _require_instance(self.state, TaskState, label="Task state")
        validate_task_priority(self.priority)
        validate_positive_integer(self.version, label="Task version")
        _require_instance(self.created_by, SubjectId, label="Task created_by")
        validate_utc_timestamp(self.created_at, label="Task created_at")
        validate_utc_timestamp(self.updated_at, label="Task updated_at")
        if self.updated_at < self.created_at:
            message = "Task updated_at must not precede created_at."
            raise DomainValidationError(message)


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """One append-only, attributable record of a Task mutation."""

    id: TaskEventId
    cursor: int
    task_uid: TaskId
    project_id: ProjectId
    actor_subject_id: SubjectId
    request_id: RequestId
    event_type: TaskEventType
    occurred_at: datetime
    payload: Mapping[str, JsonScalar] = field(hash=False)

    def __post_init__(self) -> None:
        """Validate the TaskEvent invariant set and freeze its payload copy."""
        _require_instance(self.id, TaskEventId, label="TaskEvent id")
        validate_positive_integer(self.cursor, label="TaskEvent cursor")
        _require_instance(self.task_uid, TaskId, label="TaskEvent task_uid")
        _require_instance(
            self.project_id,
            ProjectId,
            label="TaskEvent project_id",
        )
        _require_instance(
            self.actor_subject_id,
            SubjectId,
            label="TaskEvent actor_subject_id",
        )
        _require_instance(
            self.request_id,
            RequestId,
            label="TaskEvent request_id",
        )
        _require_instance(
            self.event_type,
            TaskEventType,
            label="TaskEvent event_type",
        )
        validate_utc_timestamp(self.occurred_at, label="TaskEvent occurred_at")
        object.__setattr__(self, "payload", _freeze_event_payload(self.payload))


def _require_instance(value: object, expected: type[object], *, label: str) -> None:
    """Require an exact domain value category.

    Args:
        value: Candidate value.
        expected: Required runtime type.
        label: Human-readable field name for safe errors.

    Raises:
        DomainValidationError: If the value is not an instance of ``expected``.

    """
    if not isinstance(value, expected):
        message = f"{label} must be a {expected.__name__}."
        raise DomainValidationError(message)


def _require_boolean(value: object, *, label: str) -> None:
    """Require a real boolean rather than an integer lookalike.

    Args:
        value: Candidate boolean.
        label: Human-readable field name for safe errors.

    Raises:
        DomainValidationError: If the value is not exactly ``bool``.

    """
    if type(value) is not bool:
        message = f"{label} must be a boolean."
        raise DomainValidationError(message)


def _normalize_display_name(value: object) -> str:
    """Trim and validate a Subject display name.

    Args:
        value: Candidate display name.

    Returns:
        The normalized display name.

    Raises:
        DomainValidationError: If the name is not a 1-200 character string.

    """
    if not isinstance(value, str):
        message = "Subject display_name must be a string."
        raise DomainValidationError(message)
    normalized = value.strip()
    if not (
        _SUBJECT_DISPLAY_NAME_MIN_LENGTH
        <= len(normalized)
        <= _SUBJECT_DISPLAY_NAME_MAX_LENGTH
    ):
        message = (
            "Subject display_name must contain 1 through 200 Unicode characters "
            "after trimming."
        )
        raise DomainValidationError(message)
    return normalized


def _freeze_event_payload(
    value: object,
) -> Mapping[str, JsonScalar]:
    """Copy, validate, and expose an immutable TaskEvent payload.

    Args:
        value: Candidate string-to-JSON-scalar mapping.

    Returns:
        A read-only mapping over a defensive shallow copy.

    Raises:
        DomainValidationError: If keys or scalar values are invalid.

    """
    if not isinstance(value, Mapping):
        message = "TaskEvent payload must be a mapping."
        raise DomainValidationError(message)
    copied: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or key.strip() != key:
            message = "TaskEvent payload keys must be nonempty trimmed strings."
            raise DomainValidationError(message)
        validate_json_scalar(item, label=f"TaskEvent payload {key!r}")
        copied[key] = item
    return MappingProxyType(copied)
