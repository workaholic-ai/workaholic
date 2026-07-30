"""Pure validation and authorization rules for the Phase 1 domain."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from workaholic.domain.errors import (
    DomainPermissionError,
    DomainValidationError,
)
from workaholic.domain.identifiers import ProjectId, SubjectId

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
TASK_TITLE_MIN_LENGTH = 1
TASK_TITLE_MAX_LENGTH = 200
TASK_OBJECTIVE_MIN_LENGTH = 1
TASK_OBJECTIVE_MAX_LENGTH = 4_000
TASK_PRIORITY_MIN = 0
TASK_PRIORITY_MAX = 100
DEFAULT_TASK_PRIORITY = 50
INITIAL_TASK_VERSION = 1


@runtime_checkable
class _SubjectAccess(Protocol):
    """Minimal Subject view required by the Phase 1 authorization rule."""

    @property
    def id(self) -> SubjectId:
        """Return the active Subject identity."""
        ...

    @property
    def enabled(self) -> bool:
        """Return whether the active Subject may act."""
        ...


@runtime_checkable
class _ProjectGrantAccess(Protocol):
    """Minimal ProjectGrant view required by the Phase 1 authorization rule."""

    @property
    def subject_id(self) -> SubjectId:
        """Return the Subject receiving the grant."""
        ...

    @property
    def project_id(self) -> ProjectId:
        """Return the Project governed by the grant."""
        ...

    @property
    def role(self) -> str:
        """Return the serialized Project role."""
        ...


def validate_project_key(value: object) -> str:
    """Validate an immutable Project key.

    Args:
        value: Candidate Project key.

    Returns:
        The validated uppercase key.

    Raises:
        DomainValidationError: If the key violates the Phase 1 format.

    """
    if not isinstance(value, str) or PROJECT_KEY_PATTERN.fullmatch(value) is None:
        message = "Project key must match [A-Z][A-Z0-9]{1,15}."
        raise DomainValidationError(message)
    return value


def normalize_task_title(value: object) -> str:
    """Trim and validate a Task title.

    Args:
        value: Candidate title.

    Returns:
        The trimmed title.

    Raises:
        DomainValidationError: If the title is not a string or is out of bounds.

    """
    return _normalize_bounded_text(
        value,
        label="Task title",
        minimum=TASK_TITLE_MIN_LENGTH,
        maximum=TASK_TITLE_MAX_LENGTH,
    )


def normalize_task_objective(value: object) -> str:
    """Trim and validate a Task objective.

    Args:
        value: Candidate objective.

    Returns:
        The trimmed objective.

    Raises:
        DomainValidationError: If the objective is not a string or is out of bounds.

    """
    return _normalize_bounded_text(
        value,
        label="Task objective",
        minimum=TASK_OBJECTIVE_MIN_LENGTH,
        maximum=TASK_OBJECTIVE_MAX_LENGTH,
    )


def validate_task_priority(value: object) -> int:
    """Validate a Task priority without accepting booleans as integers.

    Args:
        value: Candidate priority.

    Returns:
        The validated priority.

    Raises:
        DomainValidationError: If the value is not an integer from 0 through 100.

    """
    if type(value) is not int or not TASK_PRIORITY_MIN <= value <= TASK_PRIORITY_MAX:
        message = "Task priority must be an integer from 0 through 100."
        raise DomainValidationError(message)
    return value


def validate_positive_integer(value: object, *, label: str) -> int:
    """Validate a strictly positive integer domain counter.

    Args:
        value: Candidate integer.
        label: Human-readable field name for safe errors.

    Returns:
        The validated positive integer.

    Raises:
        DomainValidationError: If the value is a boolean, non-integer, or not positive.

    """
    if type(value) is not int or value < 1:
        message = f"{label} must be a positive integer."
        raise DomainValidationError(message)
    return value


def build_task_key(project_key: object, task_number: object) -> str:
    """Build a stable human Task key from validated components.

    Args:
        project_key: Immutable Project key.
        task_number: Positive Project-local Task number.

    Returns:
        A stable key in ``PROJECT-NUMBER`` form.

    Raises:
        DomainValidationError: If either component is invalid.

    """
    key = validate_project_key(project_key)
    number = validate_positive_integer(task_number, label="Task number")
    return f"{key}-{number}"


def validate_task_key(
    value: object,
    *,
    task_number: object,
    project_key: object | None = None,
) -> str:
    """Validate a stored human Task key against its Task number.

    Args:
        value: Candidate stable Task key.
        task_number: Expected Project-local Task number.
        project_key: Authoritative Project key when validating across entities.

    Returns:
        The validated Task key.

    Raises:
        DomainValidationError: If the key format or numeric suffix is inconsistent.

    """
    if not isinstance(value, str):
        message = "Task key must be a string."
        raise DomainValidationError(message)
    embedded_project_key, separator, number_text = value.rpartition("-")
    if separator != "-" or not number_text.isascii() or not number_text.isdecimal():
        message = "Task key must use the immutable PROJECT-NUMBER form."
        raise DomainValidationError(message)
    try:
        expected = build_task_key(embedded_project_key, task_number)
    except DomainValidationError as error:
        message = "Task key must use the immutable PROJECT-NUMBER form."
        raise DomainValidationError(message) from error
    if value != expected:
        message = "Task key does not match its Project key and Task number."
        raise DomainValidationError(message)
    if project_key is not None and embedded_project_key != validate_project_key(
        project_key
    ):
        message = "Task key does not match its Project key and Task number."
        raise DomainValidationError(message)
    return value


def validate_utc_timestamp(value: object, *, label: str) -> datetime:
    """Validate a timezone-aware timestamp with a UTC offset.

    Args:
        value: Candidate timestamp.
        label: Human-readable field name for safe errors.

    Returns:
        The validated UTC datetime.

    Raises:
        DomainValidationError: If the value is not aware or has a non-UTC offset.

    """
    if not isinstance(value, datetime):
        message = f"{label} must be a datetime."
        raise DomainValidationError(message)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        message = f"{label} must be timezone-aware UTC."
        raise DomainValidationError(message)
    return value


def validate_json_scalar(value: object, *, label: str) -> None:
    """Validate one interoperable JSON scalar value.

    Args:
        value: Candidate scalar.
        label: Human-readable field path for safe errors.

    Raises:
        DomainValidationError: If the value is nested, unsupported, or non-finite.

    """
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    message = f"{label} must be a finite JSON scalar."
    raise DomainValidationError(message)


def require_phase_one_owner(
    *,
    subject: object,
    grant: object,
    target_project_id: object,
) -> None:
    """Require the enabled Owner grant used by Phase 1 Project writes.

    Args:
        subject: Active Subject exposing identity and enabled state.
        grant: ProjectGrant exposing Subject, Project, and role.
        target_project_id: Project being mutated.

    Raises:
        DomainValidationError: If the supplied rule inputs have invalid types.
        DomainPermissionError: If the Subject is disabled or does not own the Project.

    """
    if not isinstance(subject, _SubjectAccess):
        message = "Owner authorization requires a Subject value."
        raise DomainValidationError(message)
    if not isinstance(grant, _ProjectGrantAccess):
        message = "Owner authorization requires a ProjectGrant value."
        raise DomainValidationError(message)
    subject_id: object = subject.id
    grant_subject_id: object = grant.subject_id
    grant_project_id: object = grant.project_id
    subject_enabled: object = subject.enabled
    grant_role: object = grant.role
    if not isinstance(subject_id, SubjectId) or not isinstance(
        grant_subject_id, SubjectId
    ):
        message = "Owner authorization requires SubjectId values."
        raise DomainValidationError(message)
    if not isinstance(grant_project_id, ProjectId) or not isinstance(
        target_project_id, ProjectId
    ):
        message = "Owner authorization requires ProjectId values."
        raise DomainValidationError(message)
    if type(subject_enabled) is not bool:
        message = "Subject enabled state must be a boolean."
        raise DomainValidationError(message)
    if not isinstance(grant_role, str):
        message = "Project role must be a string."
        raise DomainValidationError(message)
    if (
        not subject_enabled
        or subject_id != grant_subject_id
        or grant_project_id != target_project_id
        or grant_role != "owner"
    ):
        message = "The active Subject requires an Owner grant for this Project."
        raise DomainPermissionError(message)


def _normalize_bounded_text(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> str:
    """Trim one string and enforce inclusive Unicode character bounds.

    Args:
        value: Candidate string.
        label: Human-readable field name for safe errors.
        minimum: Inclusive minimum character count after trimming.
        maximum: Inclusive maximum character count after trimming.

    Returns:
        The trimmed and validated string.

    Raises:
        DomainValidationError: If the value is not a string or is out of bounds.

    """
    if not isinstance(value, str):
        message = f"{label} must be a string."
        raise DomainValidationError(message)
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        message = (
            f"{label} must contain {minimum} through {maximum} Unicode "
            "characters after trimming."
        )
        raise DomainValidationError(message)
    return normalized
