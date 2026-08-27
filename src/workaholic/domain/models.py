"""Immutable cumulative domain entities and enumerated values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from workaholic.domain.enums import (
    ApprovalRequirement,
    AttemptStatus,
    CriterionStatus,
    ObservationKind,
    ResultReviewStatus,
    TaskEventType,
    TaskState,
)
from workaholic.domain.errors import DomainValidationError
from workaholic.domain.identifiers import (
    AttemptId,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    TaskEventId,
    TaskId,
)
from workaholic.domain.rules import (
    ACCEPTANCE_CRITERIA_MAX_ITEMS,
    ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
    CONTEXT_REFERENCES_MAX_ITEMS,
    PROGRESS_OBSERVATIONS_MAX_ITEMS,
    PROGRESS_PERCENT_MAXIMUM,
    PROGRESS_PERCENT_MINIMUM,
    PROGRESS_TEXT_MAX_LENGTH,
    REFERENCE_VERSION_MAX_LENGTH,
    RESULT_COLLECTION_MAX_ITEMS,
    RESULT_TEXT_MAX_LENGTH,
    normalize_bounded_printable_text,
    normalize_project_name,
    normalize_task_objective,
    normalize_task_title,
    validate_acceptance_criterion_id,
    validate_json_value,
    validate_lowercase_sha256,
    validate_media_type,
    validate_positive_integer,
    validate_profile_name,
    validate_project_key,
    validate_task_key,
    validate_task_priority,
    validate_uri_reference,
    validate_utc_timestamp,
    validate_workspace_root,
)

# Pydantic application boundaries resolve domain annotations at runtime.
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | tuple[JsonValue, ...] | Mapping[str, JsonValue]

_SUBJECT_DISPLAY_NAME_MIN_LENGTH = 1
_SUBJECT_DISPLAY_NAME_MAX_LENGTH = 200


class SubjectKind(StrEnum):
    """Kinds of independently operating Phase 1 Subjects."""

    HUMAN = "human"


class ProjectRole(StrEnum):
    """Project authorization roles available in Phase 1."""

    OWNER = "owner"


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
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate and normalize the Project invariant set."""
        _require_instance(self.id, ProjectId, label="Project id")
        _require_instance(self.instance_id, InstanceId, label="Project instance_id")
        validate_project_key(self.key)
        object.__setattr__(self, "name", normalize_project_name(self.name))
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
        """Validate and normalize the Workspace binding invariant set."""
        if type(self.context_version) is not int or self.context_version != 1:
            message = "Workspace context_version must be 1."
            raise DomainValidationError(message)
        object.__setattr__(self, "profile", validate_profile_name(self.profile))
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
        object.__setattr__(
            self,
            "workspace_root",
            validate_workspace_root(self.workspace_root),
        )


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    """One stable, ordered condition used to evaluate a Task Result."""

    id: str
    text: str
    required: bool

    def __post_init__(self) -> None:
        """Validate and normalize the acceptance-criterion contract."""
        validate_acceptance_criterion_id(self.id)
        object.__setattr__(
            self,
            "text",
            normalize_bounded_printable_text(
                self.text,
                label="Acceptance criterion text",
                maximum=ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
            ),
        )
        _require_boolean(self.required, label="Acceptance criterion required")


@dataclass(frozen=True, slots=True)
class ContextReference:
    """One inert, versionable external reference supplied with a Task."""

    uri: str
    version: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the context-reference contract."""
        object.__setattr__(
            self,
            "uri",
            validate_uri_reference(self.uri, label="Context URI"),
        )
        if self.version is not None:
            object.__setattr__(
                self,
                "version",
                normalize_bounded_printable_text(
                    self.version,
                    label="Context version",
                    maximum=REFERENCE_VERSION_MAX_LENGTH,
                ),
            )


@dataclass(frozen=True, slots=True)
class CriterionOutcome:
    """Submitted outcome and optional evidence for one acceptance criterion."""

    criterion_id: str
    status: CriterionStatus
    evidence: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the criterion-outcome contract."""
        validate_acceptance_criterion_id(self.criterion_id)
        _require_instance(self.status, CriterionStatus, label="Criterion status")
        if self.evidence is not None:
            object.__setattr__(
                self,
                "evidence",
                normalize_bounded_printable_text(
                    self.evidence,
                    label="Criterion evidence",
                    maximum=RESULT_TEXT_MAX_LENGTH,
                ),
            )


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """One inert external artifact reference attached to a Result."""

    uri: str
    media_type: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        """Validate the artifact-reference contract without opening the URI."""
        object.__setattr__(
            self,
            "uri",
            validate_uri_reference(self.uri, label="Artifact URI"),
        )
        if self.media_type is not None:
            validate_media_type(self.media_type)
        if self.sha256 is not None:
            validate_lowercase_sha256(self.sha256)


@dataclass(frozen=True, slots=True)
class ProposedFollowUp:
    """Inert suggested follow-up work stored with a Result."""

    title: str

    def __post_init__(self) -> None:
        """Normalize the proposed Task title without creating a Task."""
        object.__setattr__(self, "title", normalize_task_title(self.title))


@dataclass(frozen=True, slots=True)
class ResultReview:
    """Review disposition and attribution stored with one Result."""

    status: ResultReviewStatus
    reviewed_by: SubjectId | None = None
    reviewed_at: datetime | None = None
    comment: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate review state, attribution, and mutually exclusive notes."""
        _require_instance(self.status, ResultReviewStatus, label="Review status")
        if self.reviewed_by is not None:
            _require_instance(
                self.reviewed_by,
                SubjectId,
                label="Review reviewed_by",
            )
        if self.reviewed_at is not None:
            validate_utc_timestamp(self.reviewed_at, label="Review reviewed_at")
        if self.comment is not None:
            object.__setattr__(
                self,
                "comment",
                normalize_bounded_printable_text(
                    self.comment,
                    label="Review comment",
                    maximum=RESULT_TEXT_MAX_LENGTH,
                ),
            )
        if self.reason is not None:
            object.__setattr__(
                self,
                "reason",
                normalize_bounded_printable_text(
                    self.reason,
                    label="Review reason",
                    maximum=ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
                ),
            )

        completed_review = self.status in (
            ResultReviewStatus.APPROVED,
            ResultReviewStatus.REJECTED,
        )
        if completed_review != (
            self.reviewed_by is not None and self.reviewed_at is not None
        ):
            message = (
                "Approved or rejected reviews require both reviewer identity "
                "and timestamp; other reviews require neither."
            )
            raise DomainValidationError(message)
        if self.status is ResultReviewStatus.APPROVED:
            if self.reason is not None:
                message = "Approved reviews must not contain a rejection reason."
                raise DomainValidationError(message)
        elif self.status is ResultReviewStatus.REJECTED:
            if self.reason is None or self.comment is not None:
                message = (
                    "Rejected reviews require a reason and must not contain "
                    "an approval comment."
                )
                raise DomainValidationError(message)
        elif self.comment is not None or self.reason is not None:
            message = "Pending or not-required reviews must not contain review notes."
            raise DomainValidationError(message)


@dataclass(frozen=True, slots=True)
class TaskResult:
    """One immutable Human- or Agent-attributed structured Task Result."""

    id: ResultId
    task_uid: TaskId
    submitted_by: SubjectId
    attempt_id: AttemptId | None
    submitted_at: datetime
    comment: str | None
    summary: str | None
    criteria: tuple[CriterionOutcome, ...]
    artifacts: tuple[ArtifactReference, ...]
    proposed_follow_ups: tuple[ProposedFollowUp, ...]
    review: ResultReview

    def __post_init__(self) -> None:
        """Validate identities, bounded content, and immutable collection copies."""
        _require_instance(self.id, ResultId, label="Result id")
        _require_instance(self.task_uid, TaskId, label="Result task_uid")
        _require_instance(
            self.submitted_by,
            SubjectId,
            label="Result submitted_by",
        )
        if self.attempt_id is not None:
            _require_instance(
                self.attempt_id,
                AttemptId,
                label="Result attempt_id",
            )
        validate_utc_timestamp(self.submitted_at, label="Result submitted_at")
        if self.comment is not None:
            object.__setattr__(
                self,
                "comment",
                normalize_bounded_printable_text(
                    self.comment,
                    label="Result comment",
                    maximum=RESULT_TEXT_MAX_LENGTH,
                ),
            )
        if self.summary is not None:
            object.__setattr__(
                self,
                "summary",
                normalize_bounded_printable_text(
                    self.summary,
                    label="Result summary",
                    maximum=RESULT_TEXT_MAX_LENGTH,
                ),
            )
        criteria = _validated_tuple(
            self.criteria,
            CriterionOutcome,
            label="Result criteria",
            maximum=RESULT_COLLECTION_MAX_ITEMS,
        )
        if len({item.criterion_id for item in criteria}) != len(criteria):
            message = "Result criterion outcomes must have unique criterion IDs."
            raise DomainValidationError(message)
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(
            self,
            "artifacts",
            _validated_tuple(
                self.artifacts,
                ArtifactReference,
                label="Result artifacts",
                maximum=RESULT_COLLECTION_MAX_ITEMS,
            ),
        )
        object.__setattr__(
            self,
            "proposed_follow_ups",
            _validated_tuple(
                self.proposed_follow_ups,
                ProposedFollowUp,
                label="Result proposed_follow_ups",
                maximum=RESULT_COLLECTION_MAX_ITEMS,
            ),
        )
        _require_instance(self.review, ResultReview, label="Result review")


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
    available_at: datetime | None = None
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    acceptance: tuple[AcceptanceCriterion, ...] = ()
    context: tuple[ContextReference, ...] = ()
    depends_on: tuple[TaskId, ...] = ()
    blocking_reason: str | None = None
    current_result_id: ResultId | None = None

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
        if self.available_at is not None:
            validate_utc_timestamp(self.available_at, label="Task available_at")
        _require_instance(
            self.approval,
            ApprovalRequirement,
            label="Task approval",
        )
        acceptance = _validated_tuple(
            self.acceptance,
            AcceptanceCriterion,
            label="Task acceptance",
            maximum=ACCEPTANCE_CRITERIA_MAX_ITEMS,
        )
        if len({item.id for item in acceptance}) != len(acceptance):
            message = "Task acceptance criterion IDs must be unique."
            raise DomainValidationError(message)
        object.__setattr__(self, "acceptance", acceptance)
        context = _validated_tuple(
            self.context,
            ContextReference,
            label="Task context",
            maximum=CONTEXT_REFERENCES_MAX_ITEMS,
        )
        if len({(item.uri, item.version) for item in context}) != len(context):
            message = "Task context references must be unique by URI and version."
            raise DomainValidationError(message)
        object.__setattr__(self, "context", context)
        dependencies = _validated_tuple(
            self.depends_on,
            TaskId,
            label="Task depends_on",
            maximum=RESULT_COLLECTION_MAX_ITEMS,
        )
        if len(set(dependencies)) != len(dependencies):
            message = "Task dependencies must be unique."
            raise DomainValidationError(message)
        if self.uid in dependencies:
            message = "A Task cannot depend on itself."
            raise DomainValidationError(message)
        object.__setattr__(self, "depends_on", dependencies)
        if self.blocking_reason is not None:
            object.__setattr__(
                self,
                "blocking_reason",
                normalize_bounded_printable_text(
                    self.blocking_reason,
                    label="Task blocking_reason",
                    maximum=ACCEPTANCE_CRITERION_TEXT_MAX_LENGTH,
                ),
            )
        if (self.state is TaskState.BLOCKED) != (self.blocking_reason is not None):
            message = "Only blocked Tasks require and retain a blocking_reason."
            raise DomainValidationError(message)
        if self.current_result_id is not None:
            _require_instance(
                self.current_result_id,
                ResultId,
                label="Task current_result_id",
            )


@dataclass(frozen=True, slots=True)
class ProgressObservation:
    """One inert structured observation reported by an Agent."""

    kind: ObservationKind
    text: str

    def __post_init__(self) -> None:
        """Validate and normalize the observation contract."""
        _require_instance(self.kind, ObservationKind, label="Observation kind")
        object.__setattr__(
            self,
            "text",
            normalize_bounded_printable_text(
                self.text,
                label="Observation text",
                maximum=PROGRESS_TEXT_MAX_LENGTH,
            ),
        )


@dataclass(frozen=True, slots=True)
class TaskProgress:
    """One bounded immutable Agent progress report."""

    message: str | None = None
    percent_complete: int | None = None
    observations: tuple[ProgressObservation, ...] | None = None

    def __post_init__(self) -> None:
        """Validate fields, presence, bounds, and immutable observation order."""
        if (
            self.message is None
            and self.percent_complete is None
            and self.observations is None
        ):
            message = "Task progress must contain at least one field."
            raise DomainValidationError(message)
        if self.message is not None:
            object.__setattr__(
                self,
                "message",
                normalize_bounded_printable_text(
                    self.message,
                    label="Progress message",
                    maximum=PROGRESS_TEXT_MAX_LENGTH,
                ),
            )
        if self.percent_complete is not None and (
            type(self.percent_complete) is not int
            or not PROGRESS_PERCENT_MINIMUM
            <= self.percent_complete
            <= PROGRESS_PERCENT_MAXIMUM
        ):
            message = "Progress percent_complete must be an integer from 0 to 100."
            raise DomainValidationError(message)
        if self.observations is not None:
            object.__setattr__(
                self,
                "observations",
                _validated_tuple(
                    self.observations,
                    ProgressObservation,
                    label="Progress observations",
                    maximum=PROGRESS_OBSERVATIONS_MAX_ITEMS,
                ),
            )


@dataclass(frozen=True, slots=True)
class TaskClaim:
    """Current exclusive and expiring ownership record for one Task."""

    task_uid: TaskId
    task_key: str
    subject_id: SubjectId
    attempt_id: AttemptId | None
    claimed_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        """Validate Claim identities and its positive UTC Lease window."""
        _require_instance(self.task_uid, TaskId, label="Claim task_uid")
        _validate_embedded_task_key(self.task_key, label="Claim task_key")
        _require_instance(self.subject_id, SubjectId, label="Claim subject_id")
        if self.attempt_id is not None:
            _require_instance(self.attempt_id, AttemptId, label="Claim attempt_id")
        validate_utc_timestamp(self.claimed_at, label="Claim claimed_at")
        validate_utc_timestamp(
            self.lease_expires_at,
            label="Claim lease_expires_at",
        )
        if self.lease_expires_at <= self.claimed_at:
            message = "Claim lease_expires_at must follow claimed_at."
            raise DomainValidationError(message)


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    """One immutable Agent execution record associated with a Claim."""

    id: AttemptId
    task_uid: TaskId
    subject_id: SubjectId
    status: AttemptStatus
    lease_expires_at: datetime
    started_at: datetime
    ended_at: datetime | None

    def __post_init__(self) -> None:
        """Validate Attempt identity, status, and terminal timestamp rules."""
        _require_instance(self.id, AttemptId, label="Attempt id")
        _require_instance(self.task_uid, TaskId, label="Attempt task_uid")
        _require_instance(self.subject_id, SubjectId, label="Attempt subject_id")
        _require_instance(self.status, AttemptStatus, label="Attempt status")
        validate_utc_timestamp(
            self.lease_expires_at,
            label="Attempt lease_expires_at",
        )
        validate_utc_timestamp(self.started_at, label="Attempt started_at")
        if self.lease_expires_at <= self.started_at:
            message = "Attempt lease_expires_at must follow started_at."
            raise DomainValidationError(message)
        if self.ended_at is not None:
            validate_utc_timestamp(self.ended_at, label="Attempt ended_at")
        if self.status is AttemptStatus.ACTIVE:
            if self.ended_at is not None:
                message = "An active Attempt must not have ended_at."
                raise DomainValidationError(message)
            return
        if self.ended_at is None:
            message = "A terminal Attempt requires ended_at."
            raise DomainValidationError(message)
        if self.ended_at < self.started_at:
            message = "Attempt ended_at must not precede started_at."
            raise DomainValidationError(message)
        if self.status is AttemptStatus.EXPIRED:
            if self.ended_at != self.lease_expires_at:
                message = "An expired Attempt must end at lease_expires_at."
                raise DomainValidationError(message)
        elif self.ended_at >= self.lease_expires_at:
            message = "A released or submitted Attempt must end before Lease expiry."
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
    payload: Mapping[str, JsonValue] = field(hash=False)
    attempt_id: AttemptId | None = None

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
        if self.attempt_id is not None:
            _require_instance(
                self.attempt_id,
                AttemptId,
                label="TaskEvent attempt_id",
            )
        object.__setattr__(self, "payload", _freeze_event_payload(self.payload))


def _validate_embedded_task_key(value: object, *, label: str) -> str:
    """Validate a standalone human Task key by deriving its number suffix.

    Args:
        value: Candidate stable Task key.
        label: Human-readable field label.

    Returns:
        The validated Task key.

    Raises:
        DomainValidationError: If the value is not a canonical Task key.

    """
    if not isinstance(value, str):
        message = f"{label} must be a string."
        raise DomainValidationError(message)
    _, separator, number_text = value.rpartition("-")
    if separator != "-" or not number_text.isascii() or not number_text.isdecimal():
        message = f"{label} must use the immutable PROJECT-NUMBER form."
        raise DomainValidationError(message)
    try:
        task_number = int(number_text)
        return validate_task_key(value, task_number=task_number)
    except DomainValidationError as error:
        message = f"{label} must use the immutable PROJECT-NUMBER form."
        raise DomainValidationError(message) from error


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


def _validated_tuple[T](
    value: object,
    expected: type[T],
    *,
    label: str,
    maximum: int,
) -> tuple[T, ...]:
    """Validate and defensively copy one bounded typed sequence.

    Args:
        value: Candidate ordered collection.
        expected: Required item runtime type.
        label: Human-readable collection label for safe errors.
        maximum: Inclusive item-count limit.

    Returns:
        An immutable tuple containing the validated items.

    Raises:
        DomainValidationError: If the collection or any item is invalid.

    """
    if type(maximum) is not int or maximum < 0:
        message = "Collection maximum must be a nonnegative integer."
        raise DomainValidationError(message)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        message = f"{label} must be an ordered collection."
        raise DomainValidationError(message)
    copied = tuple(value)
    if len(copied) > maximum:
        message = f"{label} must not contain more than {maximum} items."
        raise DomainValidationError(message)
    if not all(isinstance(item, expected) for item in copied):
        message = f"{label} entries must be {expected.__name__} values."
        raise DomainValidationError(message)
    return cast("tuple[T, ...]", copied)


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
) -> Mapping[str, JsonValue]:
    """Copy, validate, and expose an immutable TaskEvent payload.

    Args:
        value: Candidate string-to-bounded-JSON mapping.

    Returns:
        A read-only mapping over a defensive shallow copy.

    Raises:
        DomainValidationError: If keys or recursive JSON values are invalid.

    """
    if not isinstance(value, Mapping):
        message = "TaskEvent payload must be a mapping."
        raise DomainValidationError(message)
    validate_json_value(value, label="TaskEvent payload")
    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        message = "TaskEvent payload must be a mapping."
        raise DomainValidationError(message)
    return frozen


def _freeze_json_value(value: object) -> JsonValue:
    """Create an immutable recursive copy of an already validated JSON value.

    Args:
        value: Validated JSON value.

    Returns:
        Scalar values unchanged, arrays as tuples, and objects as read-only
        mapping proxies.

    Raises:
        DomainValidationError: If called with an unsupported value.

    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        copied = {
            cast("str", key): _freeze_json_value(item) for key, item in value.items()
        }
        return MappingProxyType(copied)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json_value(item) for item in value)
    message = "TaskEvent payload must contain only JSON values."
    raise DomainValidationError(message)
