"""Pure validation and authorization rules for the cumulative domain."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PureWindowsPath
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from workaholic.domain.enums import (
    ApprovalRequirement,
    ReadinessReason,
    TaskOperationalView,
    TaskState,
    TaskTransition,
)
from workaholic.domain.errors import (
    DomainPermissionError,
    DomainValidationError,
)
from workaholic.domain.identifiers import ProjectId, SubjectId, TaskId

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PROJECT_NAME_MIN_LENGTH = 1
PROJECT_NAME_MAX_LENGTH = 200
TASK_TITLE_MIN_LENGTH = 1
TASK_TITLE_MAX_LENGTH = 200
TASK_OBJECTIVE_MIN_LENGTH = 1
TASK_OBJECTIVE_MAX_LENGTH = 4_000
TASK_PRIORITY_MIN = 0
TASK_PRIORITY_MAX = 100
DEFAULT_TASK_PRIORITY = 50
INITIAL_TASK_VERSION = 1
ACCEPTANCE_CRITERIA_MAX_ITEMS = 100
CONTEXT_REFERENCES_MAX_ITEMS = 100
RESULT_COLLECTION_MAX_ITEMS = 100
ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH = 1_000
RESULT_TEXT_MAX_LENGTH = 4_000
URI_REFERENCE_MAX_LENGTH = 2_048
REFERENCE_VERSION_MAX_LENGTH = 256
JSON_MAX_DEPTH = 16
JSON_MAX_OBJECT_ITEMS = 128
JSON_MAX_ARRAY_ITEMS = 500
JSON_MAX_STRING_LENGTH = 16_384
JSON_MAX_KEY_LENGTH = 128
MEDIA_TYPE_MAX_LENGTH = 127

ACCEPTANCE_CRITERION_ID_PATTERN = re.compile(r"^ac_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RFC3339_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_URI_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_INVALID_PERCENT_ESCAPE_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MEDIA_TYPE_PATTERN = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_LOWERCASE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TaskReadiness:
    """Complete derived operational view of one Task at an explicit time."""

    ready: bool
    running: bool
    scheduled: bool
    stale: bool
    awaiting_review: bool
    reasons: tuple[ReadinessReason, ...]

    def __post_init__(self) -> None:
        """Validate the immutable readiness projection."""
        for label, value in (
            ("ready", self.ready),
            ("running", self.running),
            ("scheduled", self.scheduled),
            ("stale", self.stale),
            ("awaiting_review", self.awaiting_review),
        ):
            _validate_boolean(value, label=f"Task readiness {label}")
        reasons = tuple(self.reasons)
        if not all(isinstance(reason, ReadinessReason) for reason in reasons):
            message = "Task readiness reasons must be ReadinessReason values."
            raise DomainValidationError(message)
        object.__setattr__(self, "reasons", reasons)

    def includes(self, view: object) -> bool:
        """Return whether this projection belongs to an operational view.

        Args:
            view: Operational view to inspect.

        Returns:
            Whether the corresponding derived flag is true.

        Raises:
            DomainValidationError: If ``view`` is not a TaskOperationalView.

        """
        if not isinstance(view, TaskOperationalView):
            message = "Task readiness view must be a TaskOperationalView."
            raise DomainValidationError(message)
        return {
            TaskOperationalView.READY: self.ready,
            TaskOperationalView.RUNNING: self.running,
            TaskOperationalView.SCHEDULED: self.scheduled,
            TaskOperationalView.STALE: self.stale,
            TaskOperationalView.AWAITING_REVIEW: self.awaiting_review,
        }[view]


@runtime_checkable
class _LifecycleTaskAccess(Protocol):
    """Minimal immutable Task projection required by Phase 3 rules."""

    uid: TaskId
    project_id: ProjectId
    number: int
    key: str
    state: TaskState
    priority: int
    available_at: datetime | None
    approval: ApprovalRequirement
    depends_on: Sequence[TaskId]
    acceptance: Sequence[object]


@runtime_checkable
class _AcceptanceAccess(Protocol):
    """Acceptance-criterion projection needed for Result validation."""

    id: str
    required: bool


@runtime_checkable
class _CriterionOutcomeAccess(Protocol):
    """Criterion-outcome projection needed for Result validation."""

    criterion_id: str


@runtime_checkable
class _ResultAccess(Protocol):
    """Result projection needed for Task/result consistency checks."""

    task_uid: TaskId
    submitted_by: SubjectId
    attempt_id: object | None
    criteria: Sequence[_CriterionOutcomeAccess]


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


def normalize_project_name(value: object) -> str:
    """Normalize and validate a Project display name.

    Unicode is normalized to NFC before applying the inclusive character
    bounds so canonically equivalent input has one stable domain value.

    Args:
        value: Candidate Project display name.

    Returns:
        The trimmed, NFC-normalized display name.

    Raises:
        DomainValidationError: If the name is not a printable 1-200 character
            string after normalization.

    """
    if not isinstance(value, str):
        message = "Project name must be a string."
        raise DomainValidationError(message)
    normalized = unicodedata.normalize("NFC", value.strip())
    if not (PROJECT_NAME_MIN_LENGTH <= len(normalized) <= PROJECT_NAME_MAX_LENGTH):
        message = (
            "Project name must contain 1 through 200 Unicode characters "
            "after trimming and normalization."
        )
        raise DomainValidationError(message)
    if not all(character.isprintable() for character in normalized):
        message = "Project name must contain only printable characters."
        raise DomainValidationError(message)
    return normalized


def validate_profile_name(value: object) -> str:
    """Validate one trusted embedded profile name.

    Args:
        value: Candidate profile name.

    Returns:
        The validated lowercase ASCII profile name.

    Raises:
        DomainValidationError: If the value violates the Phase 2 grammar.

    """
    if not isinstance(value, str) or PROFILE_NAME_PATTERN.fullmatch(value) is None:
        message = "Profile name must match [a-z][a-z0-9_-]{0,31}."
        raise DomainValidationError(message)
    return value


def validate_workspace_root(value: object) -> str:
    """Normalize one safe repository-relative Workspace root.

    Both slash styles are treated as separators so a binding remains safe if
    copied across operating systems. Lexical ``..`` components may remove a
    preceding component but may never escape above the context directory.

    Args:
        value: Candidate relative Workspace root.

    Returns:
        A normalized slash-separated relative path, or ``"."`` for the
        context directory itself.

    Raises:
        DomainValidationError: If the value is empty, absolute, unsafe, or
            escapes its context directory lexically.

    """
    if not isinstance(value, str) or not value:
        message = "Workspace root must be a nonempty string."
        raise DomainValidationError(message)
    if "\x00" in value:
        message = "Workspace root must not contain a null character."
        raise DomainValidationError(message)
    if not all(character.isprintable() for character in value):
        message = "Workspace root must contain only printable characters."
        raise DomainValidationError(message)

    windows_path = PureWindowsPath(value)
    if value.startswith("/") or windows_path.drive or windows_path.root:
        message = "Workspace root must be a relative path."
        raise DomainValidationError(message)

    components: list[str] = []
    for component in value.replace("\\", "/").split("/"):
        if component in ("", "."):
            continue
        if component == "..":
            if not components:
                message = "Workspace root must not escape its context directory."
                raise DomainValidationError(message)
            components.pop()
            continue
        components.append(component)
    return "/".join(components) if components else "."


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


def normalize_bounded_printable_text(
    value: object,
    *,
    label: str,
    maximum: int,
    minimum: int = 1,
) -> str:
    """Normalize text with explicit inclusive printable-character bounds.

    Args:
        value: Candidate string.
        label: Human-readable field name for safe errors.
        maximum: Inclusive maximum Unicode character count.
        minimum: Inclusive minimum Unicode character count.

    Returns:
        Trimmed NFC-normalized text.

    Raises:
        DomainValidationError: If the contract or candidate is invalid.

    """
    if type(minimum) is not int or type(maximum) is not int:
        message = "Text bounds must be integers."
        raise DomainValidationError(message)
    if minimum < 0 or maximum < minimum:
        message = "Text bounds must define a nonnegative inclusive range."
        raise DomainValidationError(message)
    if not isinstance(value, str):
        message = f"{label} must be a string."
        raise DomainValidationError(message)
    normalized = unicodedata.normalize("NFC", value.strip())
    if not minimum <= len(normalized) <= maximum:
        message = (
            f"{label} must contain {minimum} through {maximum} Unicode "
            "characters after trimming and normalization."
        )
        raise DomainValidationError(message)
    if not all(character.isprintable() for character in normalized):
        message = f"{label} must contain only printable characters."
        raise DomainValidationError(message)
    return normalized


def validate_acceptance_criterion_id(value: object) -> str:
    """Validate one stable acceptance-criterion identifier.

    Args:
        value: Candidate identifier.

    Returns:
        The validated identifier.

    Raises:
        DomainValidationError: If the value violates the Phase 3 grammar.

    """
    if (
        not isinstance(value, str)
        or ACCEPTANCE_CRITERION_ID_PATTERN.fullmatch(value) is None
    ):
        message = (
            "Acceptance criterion ID must match ac_[A-Za-z0-9][A-Za-z0-9_-]{0,63}."
        )
        raise DomainValidationError(message)
    return value


def parse_rfc3339_utc_timestamp(value: object, *, label: str) -> datetime:
    """Parse a canonical RFC 3339 UTC timestamp ending in ``Z``.

    Args:
        value: Candidate timestamp string.
        label: Human-readable field name for safe errors.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        DomainValidationError: If the value is not a valid canonical timestamp.

    """
    if not isinstance(value, str) or _RFC3339_UTC_PATTERN.fullmatch(value) is None:
        message = f"{label} must be an RFC 3339 UTC timestamp ending in Z."
        raise DomainValidationError(message)
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        message = f"{label} must be an RFC 3339 UTC timestamp ending in Z."
        raise DomainValidationError(message) from error
    return validate_utc_timestamp(parsed, label=label)


def validate_uri_reference(value: object, *, label: str) -> str:
    """Validate one inert, absolute URI reference without dereferencing it.

    Args:
        value: Candidate URI.
        label: Human-readable field name for safe errors.

    Returns:
        The validated URI unchanged.

    Raises:
        DomainValidationError: If the value is malformed or out of bounds.

    """
    uri = normalize_bounded_printable_text(
        value,
        label=label,
        maximum=URI_REFERENCE_MAX_LENGTH,
    )
    if any(character.isspace() for character in uri) or "\\" in uri:
        message = f"{label} must be an absolute URI without whitespace."
        raise DomainValidationError(message)
    try:
        parsed = urlsplit(uri)
    except ValueError as error:
        message = f"{label} must be a valid absolute URI."
        raise DomainValidationError(message) from error
    if (
        _URI_SCHEME_PATTERN.fullmatch(parsed.scheme) is None
        or not uri.startswith(f"{parsed.scheme}:")
        or (not parsed.netloc and not parsed.path)
        or _INVALID_PERCENT_ESCAPE_PATTERN.search(uri) is not None
    ):
        message = f"{label} must be a valid absolute URI."
        raise DomainValidationError(message)
    return uri


def validate_media_type(value: object, *, label: str = "Artifact media_type") -> str:
    """Validate a lowercase ASCII ``type/subtype`` media type.

    Args:
        value: Candidate media type.
        label: Human-readable field name for safe errors.

    Returns:
        The validated media type.

    Raises:
        DomainValidationError: If the value is malformed or exceeds 127 bytes.

    """
    if (
        not isinstance(value, str)
        or len(value) > MEDIA_TYPE_MAX_LENGTH
        or not value.isascii()
        or _MEDIA_TYPE_PATTERN.fullmatch(value) is None
    ):
        message = f"{label} must be a lowercase type/subtype token up to 127 bytes."
        raise DomainValidationError(message)
    return value


def validate_lowercase_sha256(value: object, *, label: str = "Artifact sha256") -> str:
    """Validate one exact lowercase hexadecimal SHA-256 digest.

    Args:
        value: Candidate digest.
        label: Human-readable field name for safe errors.

    Returns:
        The validated digest.

    Raises:
        DomainValidationError: If the digest is not exactly 64 lowercase hex chars.

    """
    if not isinstance(value, str) or _LOWERCASE_SHA256_PATTERN.fullmatch(value) is None:
        message = f"{label} must be exactly 64 lowercase hexadecimal characters."
        raise DomainValidationError(message)
    return value


def validate_json_value(  # noqa: PLR0912
    value: object,
    *,
    label: str,
    _depth: int = 0,
) -> None:
    """Validate one finite, bounded recursive JSON value.

    Args:
        value: Candidate JSON value.
        label: Human-readable field path for safe errors.
        _depth: Internal recursive depth, where the root is zero.

    Raises:
        DomainValidationError: If the value is unsupported or exceeds a bound.

    """
    if _depth > JSON_MAX_DEPTH:
        message = f"{label} exceeds the maximum JSON depth of {JSON_MAX_DEPTH}."
        raise DomainValidationError(message)
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        message = f"{label} must contain only finite JSON numbers."
        raise DomainValidationError(message)
    if isinstance(value, str):
        if len(value) > JSON_MAX_STRING_LENGTH:
            message = (
                f"{label} strings must not exceed {JSON_MAX_STRING_LENGTH} "
                "Unicode characters."
            )
            raise DomainValidationError(message)
        return
    if isinstance(value, Mapping):
        if len(value) > JSON_MAX_OBJECT_ITEMS:
            message = (
                f"{label} objects must not exceed {JSON_MAX_OBJECT_ITEMS} members."
            )
            raise DomainValidationError(message)
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or key.strip() != key
                or len(key) > JSON_MAX_KEY_LENGTH
                or not all(character.isprintable() for character in key)
            ):
                message = (
                    f"{label} object keys must be nonempty, trimmed, printable "
                    "strings up to 128 characters."
                )
                raise DomainValidationError(message)
            validate_json_value(item, label=f"{label}.{key}", _depth=_depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > JSON_MAX_ARRAY_ITEMS:
            message = f"{label} arrays must not exceed {JSON_MAX_ARRAY_ITEMS} items."
            raise DomainValidationError(message)
        for index, item in enumerate(value):
            validate_json_value(
                item,
                label=f"{label}[{index}]",
                _depth=_depth + 1,
            )
        return
    message = f"{label} must be a JSON value."
    raise DomainValidationError(message)


def transition_task_state(
    current_state: object,
    transition: object,
    *,
    approval: object = ApprovalRequirement.NONE,
) -> TaskState:
    """Resolve one semantic Phase 3 transition without mutating a Task.

    Args:
        current_state: Authoritative current Task state.
        transition: Requested semantic operation.
        approval: Task approval requirement used by submission.

    Returns:
        The validated target state, which may equal the current state.

    Raises:
        DomainValidationError: If inputs are invalid or the transition is illegal.

    """
    if not isinstance(current_state, TaskState):
        message = "Current state must be a TaskState."
        raise DomainValidationError(message)
    if not isinstance(transition, TaskTransition):
        message = "Transition must be a TaskTransition."
        raise DomainValidationError(message)
    if not isinstance(approval, ApprovalRequirement):
        message = "Approval must be an ApprovalRequirement."
        raise DomainValidationError(message)

    allowed: dict[TaskTransition, dict[TaskState, TaskState]] = {
        TaskTransition.UPDATE: {
            TaskState.OPEN: TaskState.OPEN,
            TaskState.BLOCKED: TaskState.BLOCKED,
        },
        TaskTransition.BLOCK: {TaskState.OPEN: TaskState.BLOCKED},
        TaskTransition.UNBLOCK: {TaskState.BLOCKED: TaskState.OPEN},
        TaskTransition.CANCEL: {
            TaskState.OPEN: TaskState.CANCELLED,
            TaskState.BLOCKED: TaskState.CANCELLED,
            TaskState.REVIEW: TaskState.CANCELLED,
        },
        TaskTransition.ADD_DEPENDENCY: {
            TaskState.OPEN: TaskState.OPEN,
            TaskState.BLOCKED: TaskState.BLOCKED,
        },
        TaskTransition.REMOVE_DEPENDENCY: {
            TaskState.OPEN: TaskState.OPEN,
            TaskState.BLOCKED: TaskState.BLOCKED,
        },
        TaskTransition.SUBMIT: {
            TaskState.OPEN: (
                TaskState.REVIEW
                if approval is ApprovalRequirement.HUMAN
                else TaskState.DONE
            )
        },
        TaskTransition.APPROVE: {TaskState.REVIEW: TaskState.DONE},
        TaskTransition.REJECT: {TaskState.REVIEW: TaskState.OPEN},
    }
    try:
        return allowed[transition][current_state]
    except KeyError as error:
        message = "The Task cannot perform the requested lifecycle transition."
        raise DomainValidationError(message) from error


def validate_dependency_addition(
    *,
    dependant: object,
    prerequisite: object,
    dependency_graph: Mapping[TaskId, Collection[TaskId]],
) -> None:
    """Validate a proposed same-Project dependency edge and cycle safety.

    Args:
        dependant: Task receiving the new prerequisite.
        prerequisite: Task that must complete first.
        dependency_graph: Current directed Task-to-prerequisites graph.

    Raises:
        DomainValidationError: If an input or proposed edge is invalid.

    """
    dependant_task = _validate_lifecycle_task(dependant, label="Dependant Task")
    prerequisite_task = _validate_lifecycle_task(
        prerequisite,
        label="Prerequisite Task",
    )
    transition_task_state(
        dependant_task.state,
        TaskTransition.ADD_DEPENDENCY,
        approval=dependant_task.approval,
    )
    graph = _copy_dependency_graph(dependency_graph)
    if dependant_task.project_id != prerequisite_task.project_id:
        message = "Dependencies must connect Tasks in the same Project."
        raise DomainValidationError(message)
    if dependant_task.uid == prerequisite_task.uid:
        message = "A Task cannot depend on itself."
        raise DomainValidationError(message)
    current = graph.get(dependant_task.uid, ())
    if (
        prerequisite_task.uid in current
        or prerequisite_task.uid in dependant_task.depends_on
    ):
        message = "The dependency already exists."
        raise DomainValidationError(message)
    graph[dependant_task.uid] = (*current, prerequisite_task.uid)
    if _path_exists(graph, start=prerequisite_task.uid, target=dependant_task.uid):
        message = "The dependency change would create a cycle."
        raise DomainValidationError(message)


def validate_dependency_removal(
    *,
    dependant: object,
    prerequisite: object,
) -> None:
    """Validate removal of one existing same-Project dependency edge.

    Args:
        dependant: Task whose prerequisite is being removed.
        prerequisite: Existing prerequisite Task.

    Raises:
        DomainValidationError: If the transition or edge is invalid.

    """
    dependant_task = _validate_lifecycle_task(dependant, label="Dependant Task")
    prerequisite_task = _validate_lifecycle_task(
        prerequisite,
        label="Prerequisite Task",
    )
    transition_task_state(
        dependant_task.state,
        TaskTransition.REMOVE_DEPENDENCY,
        approval=dependant_task.approval,
    )
    if dependant_task.project_id != prerequisite_task.project_id:
        message = "Dependencies must connect Tasks in the same Project."
        raise DomainValidationError(message)
    if prerequisite_task.uid not in dependant_task.depends_on:
        message = "The dependency does not exist."
        raise DomainValidationError(message)


def derive_task_readiness(
    *,
    task: object,
    prerequisites: Iterable[object],
    now: object,
    has_active_attempt: object = False,
    has_stale_attempt: object = False,
) -> TaskReadiness:
    """Derive Task operational views from explicit authoritative inputs.

    Args:
        task: Task to evaluate.
        prerequisites: Complete Task projections named by ``task.depends_on``.
        now: Authoritative current UTC timestamp.
        has_active_attempt: Whether one unexpired Attempt currently owns the Task.
        has_stale_attempt: Whether an expired Attempt remains visible as stale.

    Returns:
        An immutable readiness projection with deterministically ordered reasons.

    Raises:
        DomainValidationError: If inputs are invalid or incomplete.

    """
    candidate = _validate_lifecycle_task(task, label="Task")
    current_time = validate_utc_timestamp(now, label="Readiness time")
    if type(has_active_attempt) is not bool or type(has_stale_attempt) is not bool:
        message = "Attempt readiness flags must be booleans."
        raise DomainValidationError(message)
    if has_active_attempt and has_stale_attempt:
        message = "A Task cannot have active and stale Attempt views simultaneously."
        raise DomainValidationError(message)
    ordered_prerequisites = _validated_prerequisites(candidate, prerequisites)

    reasons: list[ReadinessReason] = []
    state_reason = {
        TaskState.BLOCKED: ReadinessReason.TASK_BLOCKED,
        TaskState.REVIEW: ReadinessReason.TASK_AWAITING_REVIEW,
        TaskState.DONE: ReadinessReason.TASK_DONE,
        TaskState.CANCELLED: ReadinessReason.TASK_CANCELLED,
    }.get(candidate.state)
    if state_reason is not None:
        reasons.append(state_reason)

    scheduled = (
        candidate.state is TaskState.OPEN
        and candidate.available_at is not None
        and candidate.available_at > current_time
    )
    if scheduled:
        reasons.append(ReadinessReason.NOT_YET_AVAILABLE)

    for prerequisite in ordered_prerequisites:
        if prerequisite.state is TaskState.DONE:
            continue
        reasons.append(
            ReadinessReason.UNSATISFIABLE_DEPENDENCY
            if prerequisite.state is TaskState.CANCELLED
            else ReadinessReason.UNSATISFIED_DEPENDENCY
        )

    if has_active_attempt:
        reasons.append(ReadinessReason.ACTIVE_ATTEMPT)
    if has_stale_attempt:
        reasons.append(ReadinessReason.STALE_ATTEMPT)

    return TaskReadiness(
        ready=not reasons,
        running=has_active_attempt,
        scheduled=scheduled,
        stale=has_stale_attempt,
        awaiting_review=candidate.state is TaskState.REVIEW,
        reasons=tuple(reasons),
    )


def validate_task_result_consistency(
    *,
    task: object,
    result: object,
    human_submission: object,
) -> None:
    """Validate Result identity and criterion attribution against its Task.

    Args:
        task: Task whose Result is being submitted or read.
        result: Structured Result projection.
        human_submission: Whether null Attempt attribution is required.

    Raises:
        DomainValidationError: If Result and Task data are inconsistent.

    """
    candidate = _validate_lifecycle_task(task, label="Task")
    if not isinstance(result, _ResultAccess):
        message = "Result must expose the Phase 3 Result contract."
        raise DomainValidationError(message)
    if type(human_submission) is not bool:
        message = "Human submission must be a boolean."
        raise DomainValidationError(message)
    if result.task_uid != candidate.uid:
        message = "Result task_uid must match the Task uid."
        raise DomainValidationError(message)
    _require_domain_identifier(
        result.submitted_by,
        SubjectId,
        label="Result submitted_by",
    )
    if human_submission and result.attempt_id is not None:
        message = "Human Results must not have an Attempt identity."
        raise DomainValidationError(message)

    acceptance: dict[str, bool] = {}
    for criterion in candidate.acceptance:
        if not isinstance(criterion, _AcceptanceAccess):
            message = "Task acceptance entries must expose criterion contracts."
            raise DomainValidationError(message)
        acceptance[criterion.id] = criterion.required
    outcome_ids = tuple(outcome.criterion_id for outcome in result.criteria)
    if any(criterion_id not in acceptance for criterion_id in outcome_ids):
        message = "Result criterion IDs must match the Task acceptance set."
        raise DomainValidationError(message)
    missing_required = {
        criterion_id
        for criterion_id, required in acceptance.items()
        if required and criterion_id not in outcome_ids
    }
    if missing_required:
        message = "Result must report every required acceptance criterion."
        raise DomainValidationError(message)


def validate_human_submission(
    *,
    task: object,
    prerequisites: Iterable[object],
    result: object,
) -> TaskState:
    """Validate Human submission and return its approval-dependent target state.

    Deliberate Human submission ignores future availability but still requires
    an open Task and every declared prerequisite to be done.

    Args:
        task: Authoritative Task being submitted.
        prerequisites: Complete Task projections named by ``task.depends_on``.
        result: Human-attributed Result for the Task.

    Returns:
        ``done`` when approval is none, otherwise ``review``.

    Raises:
        DomainValidationError: If submission violates a lifecycle invariant.

    """
    candidate = _validate_lifecycle_task(task, label="Task")
    target_state = transition_task_state(
        candidate.state,
        TaskTransition.SUBMIT,
        approval=candidate.approval,
    )
    for prerequisite in _validated_prerequisites(candidate, prerequisites):
        if prerequisite.state is not TaskState.DONE:
            message = "Human submission requires every prerequisite to be done."
            raise DomainValidationError(message)
    validate_task_result_consistency(
        task=candidate,
        result=result,
        human_submission=True,
    )
    return target_state


def ready_task_ordering_key(
    task: object,
    *,
    project_key: object | None = None,
) -> tuple[object, ...]:
    """Build the deterministic Phase 3 ready-view ordering key.

    Args:
        task: Task to order.
        project_key: Immutable Project key for an all-Project view.

    Returns:
        A key ordering priority descending, null-first availability ascending,
        optional Project key, and Task number ascending.

    Raises:
        DomainValidationError: If the Task or optional Project key is invalid.

    """
    candidate = _validate_lifecycle_task(task, label="Task")
    availability = candidate.available_at or datetime.min.replace(tzinfo=UTC)
    prefix: tuple[object, ...] = (
        -candidate.priority,
        0 if candidate.available_at is None else 1,
        availability,
    )
    if project_key is None:
        return (*prefix, candidate.number)
    return (*prefix, validate_project_key(project_key), candidate.number)


def _validate_lifecycle_task(value: object, *, label: str) -> _LifecycleTaskAccess:
    """Validate one Task projection used by pure lifecycle rules.

    Args:
        value: Candidate Task projection.
        label: Human-readable boundary label.

    Returns:
        The structurally validated Task projection.

    Raises:
        DomainValidationError: If the projection violates the expected contract.

    """
    if not isinstance(value, _LifecycleTaskAccess):
        message = f"{label} must expose the Phase 3 Task lifecycle contract."
        raise DomainValidationError(message)
    uid: object = value.uid
    project_id: object = value.project_id
    state: object = value.state
    approval: object = value.approval
    depends_on: object = value.depends_on
    acceptance: object = value.acceptance
    _require_domain_identifier(uid, TaskId, label=f"{label} uid")
    _require_domain_identifier(project_id, ProjectId, label=f"{label} project_id")
    validate_positive_integer(value.number, label=f"{label} number")
    validate_task_key(value.key, task_number=value.number)
    if not isinstance(state, TaskState):
        message = f"{label} state must be a TaskState."
        raise DomainValidationError(message)
    validate_task_priority(value.priority)
    if value.available_at is not None:
        validate_utc_timestamp(value.available_at, label=f"{label} available_at")
    if not isinstance(approval, ApprovalRequirement):
        message = f"{label} approval must be an ApprovalRequirement."
        raise DomainValidationError(message)
    if not isinstance(depends_on, Sequence):
        message = f"{label} depends_on must be a sequence."
        raise DomainValidationError(message)
    if not isinstance(acceptance, Sequence):
        message = f"{label} acceptance must be a sequence."
        raise DomainValidationError(message)
    return value


def _validated_prerequisites(
    task: _LifecycleTaskAccess,
    prerequisites: Iterable[object],
) -> tuple[_LifecycleTaskAccess, ...]:
    """Validate and order the complete prerequisite projection set.

    Args:
        task: Dependant Task.
        prerequisites: Candidate prerequisite projections.

    Returns:
        Prerequisites ordered by stable Task key.

    Raises:
        DomainValidationError: If identities are missing, duplicated, or foreign.

    """
    try:
        values = tuple(prerequisites)
    except TypeError as error:
        message = "Prerequisites must be iterable Task projections."
        raise DomainValidationError(message) from error
    validated = tuple(
        _validate_lifecycle_task(item, label="Prerequisite Task") for item in values
    )
    identities = tuple(item.uid for item in validated)
    if len(set(identities)) != len(identities):
        message = "Prerequisite projections must have unique Task identities."
        raise DomainValidationError(message)
    if set(identities) != set(task.depends_on):
        message = "Prerequisite projections must exactly match Task depends_on."
        raise DomainValidationError(message)
    if any(item.project_id != task.project_id for item in validated):
        message = "Prerequisites must belong to the dependant Task Project."
        raise DomainValidationError(message)
    return tuple(sorted(validated, key=lambda item: item.key))


def _copy_dependency_graph(
    value: object,
) -> dict[TaskId, tuple[TaskId, ...]]:
    """Validate and defensively copy a dependency graph.

    Args:
        value: Directed Task-to-prerequisite adjacency mapping.

    Returns:
        A tuple-backed mutable copy used only for pure validation.

    Raises:
        DomainValidationError: If identities or adjacency values are invalid.

    """
    if not isinstance(value, Mapping):
        message = "Dependency graph must be a mapping."
        raise DomainValidationError(message)
    copied: dict[TaskId, tuple[TaskId, ...]] = {}
    for task_uid, dependencies in value.items():
        if not isinstance(task_uid, TaskId) or not isinstance(dependencies, Collection):
            message = "Dependency graph entries must use TaskId collections."
            raise DomainValidationError(message)
        dependency_tuple = tuple(dependencies)
        if not all(isinstance(item, TaskId) for item in dependency_tuple):
            message = "Dependency graph entries must use TaskId collections."
            raise DomainValidationError(message)
        if len(set(dependency_tuple)) != len(dependency_tuple):
            message = "Dependency graph adjacency lists must not contain duplicates."
            raise DomainValidationError(message)
        copied[task_uid] = dependency_tuple
    return copied


def _require_domain_identifier[T](
    value: object,
    expected: type[T],
    *,
    label: str,
) -> T:
    """Require one exact domain identifier category at a rule boundary.

    Args:
        value: Candidate identifier.
        expected: Required identifier type.
        label: Human-readable field label.

    Returns:
        The validated identifier.

    Raises:
        DomainValidationError: If the runtime type is incorrect.

    """
    if not isinstance(value, expected):
        message = f"{label} must be a {expected.__name__}."
        raise DomainValidationError(message)
    return value


def _validate_boolean(value: object, *, label: str) -> bool:
    """Require a real boolean rather than an integer lookalike.

    Args:
        value: Candidate boolean.
        label: Human-readable field label.

    Returns:
        The validated boolean.

    Raises:
        DomainValidationError: If the runtime type is not exactly bool.

    """
    if type(value) is not bool:
        message = f"{label} must be a boolean."
        raise DomainValidationError(message)
    return value


def _path_exists(
    graph: Mapping[TaskId, Collection[TaskId]],
    *,
    start: TaskId,
    target: TaskId,
) -> bool:
    """Return whether a directed path connects two Task identities.

    Args:
        graph: Directed Task-to-prerequisite adjacency mapping.
        start: Search origin.
        target: Search destination.

    Returns:
        Whether ``target`` is reachable from ``start``.

    """
    pending = [start]
    visited: set[TaskId] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, ()))
    return False


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
