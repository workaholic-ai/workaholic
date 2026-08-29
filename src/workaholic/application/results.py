"""Strict result models returned by cumulative application operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path  # noqa: TC003
from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from workaholic.application.commands import TaskListView
from workaholic.domain import (
    PROGRESS_OBSERVATIONS_MAX_ITEMS,
    AttemptId,
    AttemptStatus,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    DomainValidationError,
    Instance,
    InstanceId,
    JsonValue,
    ObservationKind,
    ProgressObservation,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    ResultReviewStatus,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskProgress,
    TaskReadiness,
    TaskResult,
    TaskState,
    TokenId,
    TokenStatus,
    TokenSummary,
    WorkspaceBinding,
    ready_task_ordering_key,
    validate_claim_attempt_consistency,
    validate_profile_name,
)

_CURSOR_MAX_LENGTH = 2_048
_MAX_TASK_MUTATION_EVENTS = 2


class _ResultModel(BaseModel):
    """Shared strictness policy for application result models."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class BootstrapResult(_ResultModel):
    """Persisted entities and safe binding produced by local bootstrap."""

    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace: WorkspaceBinding

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity bootstrap relationships.

        Returns:
            The internally consistent bootstrap result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        if (
            self.workspace.instance_id != self.instance.id
            or self.workspace.project_id != self.project.id
            or self.workspace.project_key != self.project.key
        ):
            message = "Bootstrap workspace does not match its Instance and Project."
            raise ValueError(message)
        return self


class StatusResult(_ResultModel):
    """Current embedded local status for one authorized Project."""

    mode: Literal["embedded"] = "embedded"
    profile: str = "local"
    schema_version: Literal[4] = 4
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile represented by status.

        Args:
            value: Candidate profile name.

        Returns:
            Validated trusted profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate all cross-entity status relationships.

        Returns:
            The internally consistent status result.

        Raises:
            ValueError: If the result combines unrelated or unauthorized entities.

        """
        _validate_status_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        return self


class ProjectCreationResult(_ResultModel):
    """Committed Project and creator Owner grant."""

    project: Project
    grant: ProjectGrant

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate the committed Project and grant relationship.

        Returns:
            The internally consistent Project creation result.

        Raises:
            ValueError: If the grant does not own the created Project.

        """
        if (
            self.grant.instance_id != self.project.instance_id
            or self.grant.created_at != self.project.created_at
            or self.grant.project_id != self.project.id
            or self.grant.role is not ProjectRole.OWNER
        ):
            message = "Project creation grant must own the created Project."
            raise ValueError(message)
        return self


class ContextResult(_ResultModel):
    """One effective embedded profile, identity, and safe Workspace selection."""

    mode: Literal["embedded"] = "embedded"
    profile: str
    schema_version: Literal[4] = 4
    instance: Instance
    project: Project
    subject: Subject
    grant: ProjectGrant
    workspace_root: Path | None
    context_source: Path | None

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the selected trusted profile name.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Validate identity relationships and safe path disclosure.

        Returns:
            The internally consistent effective context.

        Raises:
            ValueError: If identities or optional context paths disagree.

        """
        _validate_identity_consistency(
            instance=self.instance,
            project=self.project,
            subject=self.subject,
            grant=self.grant,
        )
        paths = (self.workspace_root, self.context_source)
        if (paths[0] is None) != (paths[1] is None):
            message = "Context paths must both be present or both be null."
            raise ValueError(message)
        if self.workspace_root is None or self.context_source is None:
            return self
        if (
            not self.workspace_root.is_absolute()
            or not self.context_source.is_absolute()
        ):
            message = "Context paths must be absolute."
            raise ValueError(message)
        if self.context_source.name != ".workaholic.env":
            message = "Context source must identify .workaholic.env."
            raise ValueError(message)
        if not self.workspace_root.is_relative_to(self.context_source.parent):
            message = "Workspace root must remain within its context directory."
            raise ValueError(message)
        return self


class CurrentIdentityResult(_ResultModel):
    """Authenticated Subject and active non-secret Token metadata."""

    subject: Subject
    token: TokenSummary

    @model_validator(mode="after")
    def _validate_consistency(self) -> CurrentIdentityResult:
        """Bind the active Token to the enabled Subject.

        Returns:
            The internally consistent identity result.

        Raises:
            ValueError: If Token ownership or active Subject state disagrees.

        """
        if (
            self.token.subject_id != self.subject.id
            or self.token.status is not TokenStatus.ACTIVE
            or not self.subject.enabled
        ):
            message = "Current identity requires an active Token for the Subject."
            raise ValueError(message)
        return self


class SubjectResult(_ResultModel):
    """One closed Subject mutation outcome."""

    subject: Subject


class SubjectPage(_ResultModel):
    """One stable handle-ordered page of Subjects."""

    subjects: tuple[Subject, ...]
    next_cursor: str | None

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned Phase 5 identity cursor.

        Args:
            value: Candidate cursor or null.

        Returns:
            The validated cursor or null.

        """
        return _validate_identity_next_cursor(value)

    @model_validator(mode="after")
    def _validate_order(self) -> SubjectPage:
        """Require unique Subjects in strict `(handle, id)` order.

        Returns:
            The validated Subject page.

        Raises:
            ValueError: If order, identity, or Instance scope is inconsistent.

        """
        positions = tuple((item.handle, str(item.id)) for item in self.subjects)
        if positions != tuple(sorted(positions)) or len(set(positions)) != len(
            positions
        ):
            message = "Subject page must be strictly ordered by handle and ID."
            raise ValueError(message)
        if self.subjects and any(
            item.instance_id != self.subjects[0].instance_id for item in self.subjects
        ):
            message = "Subject page entries must share one Instance."
            raise ValueError(message)
        return self


class ProjectGrantResult(_ResultModel):
    """One closed assigned or revoked ProjectGrant snapshot."""

    grant: ProjectGrant


class ProjectGrantPage(_ResultModel):
    """One stable page of current grants for exactly one Project."""

    grants: tuple[ProjectGrant, ...]
    next_cursor: str | None

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned Phase 5 identity cursor.

        Args:
            value: Candidate cursor or null.

        Returns:
            The validated cursor or null.

        """
        return _validate_identity_next_cursor(value)

    @model_validator(mode="after")
    def _validate_scope(self) -> ProjectGrantPage:
        """Require unique Subject grants in one Instance and Project.

        Returns:
            The validated grant page.

        Raises:
            ValueError: If scopes or Subject identities disagree.

        """
        if not self.grants:
            return self
        first = self.grants[0]
        subject_ids = tuple(grant.subject_id for grant in self.grants)
        if len(set(subject_ids)) != len(subject_ids) or any(
            grant.instance_id != first.instance_id
            or grant.project_id != first.project_id
            for grant in self.grants
        ):
            message = "ProjectGrant page entries must be unique in one Project."
            raise ValueError(message)
        return self


class TokenResult(_ResultModel):
    """One closed non-secret Token lifecycle outcome."""

    token: TokenSummary


class TokenPage(_ResultModel):
    """One stable creation-ordered page of non-secret Token metadata."""

    tokens: tuple[TokenSummary, ...]
    next_cursor: str | None

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned Phase 5 identity cursor.

        Args:
            value: Candidate cursor or null.

        Returns:
            The validated cursor or null.

        """
        return _validate_identity_next_cursor(value)

    @model_validator(mode="after")
    def _validate_order(self) -> TokenPage:
        """Require unique Tokens in strict `(created_at, id)` order.

        Returns:
            The validated Token page.

        Raises:
            ValueError: If ordering or target Subject scope disagrees.

        """
        positions = tuple((item.created_at, str(item.id)) for item in self.tokens)
        if positions != tuple(sorted(positions)) or len(set(positions)) != len(
            positions
        ):
            message = "Token page must be strictly ordered by creation time and ID."
            raise ValueError(message)
        if self.tokens and any(
            item.subject_id != self.tokens[0].subject_id for item in self.tokens
        ):
            message = "Token page entries must share one target Subject."
            raise ValueError(message)
        return self


class AuditEventResult(_ResultModel):
    """One closed serializable administrative AuditEvent."""

    id: AuditEventId
    cursor: int
    instance_id: InstanceId
    actor_subject_id: SubjectId
    actor_kind: SubjectKind
    actor_token_id: TokenId | None
    request_id: RequestId
    event_type: AuditEventType
    occurred_at: datetime
    payload: Mapping[str, JsonValue]

    @field_serializer("payload")
    def _serialize_payload(
        self,
        value: Mapping[str, JsonValue],
    ) -> dict[str, object]:
        """Serialize a detached JSON-compatible audit payload.

        Args:
            value: Recursively frozen domain payload.

        Returns:
            A mutable detached representation for serialization only.

        """
        return {key: _mutable_json_value(item) for key, item in value.items()}

    @model_validator(mode="after")
    def _validate_event(self) -> AuditEventResult:
        """Reconstruct the domain event and retain its frozen payload.

        Returns:
            The validated flat audit event.

        """
        event = AuditEvent(
            id=self.id,
            cursor=self.cursor,
            instance_id=self.instance_id,
            actor_subject_id=self.actor_subject_id,
            actor_kind=self.actor_kind,
            actor_token_id=self.actor_token_id,
            request_id=self.request_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            payload=self.payload,
        )
        object.__setattr__(self, "payload", event.payload)
        return self


class AuditEventPage(_ResultModel):
    """One polling-safe ascending administrative AuditEvent page."""

    events: tuple[AuditEventResult, ...]
    next_cursor: int

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> int:
        """Validate a nonnegative Instance audit cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated nonnegative integer.

        Raises:
            DomainValidationError: If the cursor is not a real nonnegative int.

        """
        if type(value) is not int or value < 0:
            message = "AuditEvent next_cursor must be a nonnegative integer."
            raise DomainValidationError(message)
        return value

    @model_validator(mode="after")
    def _validate_order(self) -> AuditEventPage:
        """Require strict ascending cursors within one Instance.

        Returns:
            The validated audit page.

        Raises:
            ValueError: If cursor order, Instance scope, or final cursor differs.

        """
        if not self.events:
            return self
        cursors = tuple(event.cursor for event in self.events)
        if (
            cursors != tuple(sorted(cursors))
            or len(set(cursors)) != len(cursors)
            or self.next_cursor != cursors[-1]
            or any(
                event.instance_id != self.events[0].instance_id for event in self.events
            )
        ):
            message = "AuditEvent page order, scope, or next cursor is invalid."
            raise ValueError(message)
        return self


class CredentialLogoutResult(_ResultModel):
    """Closed local Human credential-removal result."""

    profile: str
    credential_stored: Literal[False] = False

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, value: object) -> str:
        """Validate the trusted profile whose credential was removed.

        Args:
            value: Candidate profile name.

        Returns:
            The validated profile name.

        """
        return validate_profile_name(value)


def _validate_identity_next_cursor(value: object) -> str | None:
    """Validate one returned opaque Phase 5 continuation cursor.

    Args:
        value: Candidate cursor or null.

    Returns:
        The validated cursor or null.

    Raises:
        DomainValidationError: If the cursor is malformed or unbounded.

    """
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.startswith("v5.")
        or value != value.strip()
        or len(value) > _CURSOR_MAX_LENGTH
        or any(
            character.isspace() or not character.isprintable() for character in value
        )
    ):
        message = "Identity next_cursor must be a bounded v5 cursor or null."
        raise DomainValidationError(message)
    return value


class TaskDetails(_ResultModel):
    """Complete Task, readiness, prerequisites, and selected Result details."""

    task: Task
    readiness: TaskReadiness
    prerequisites: tuple[Task, ...]
    current_result: TaskResult | None
    claim: TaskClaim | None = None
    attempt: TaskAttempt | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskDetails:
        """Validate detail relationships and deterministic prerequisite order.

        Returns:
            The internally consistent Task details.

        Raises:
            ValueError: If Task, prerequisite, or Result identities disagree.

        """
        prerequisite_ids = tuple(item.uid for item in self.prerequisites)
        if prerequisite_ids != tuple(
            item.uid for item in sorted(self.prerequisites, key=lambda item: item.key)
        ):
            message = "Task details prerequisites must be ordered by stable Task key."
            raise ValueError(message)
        if set(prerequisite_ids) != set(self.task.depends_on):
            message = "Task details prerequisites must exactly match Task depends_on."
            raise ValueError(message)
        if any(item.project_id != self.task.project_id for item in self.prerequisites):
            message = "Task details prerequisites must belong to the Task Project."
            raise ValueError(message)
        if self.task.current_result_id is None:
            if self.current_result is not None:
                message = "Task details must not select a Result absent from the Task."
                raise ValueError(message)
        elif (
            self.current_result is None
            or self.current_result.id != self.task.current_result_id
            or self.current_result.task_uid != self.task.uid
        ):
            message = "Task details current Result must match the Task selection."
            raise ValueError(message)
        if self.claim is None:
            if self.attempt is not None or self.readiness.running:
                message = "Task details without a current Claim cannot be running."
                raise ValueError(message)
        else:
            _validate_claim_task_identity(self.task, self.claim)
            validate_claim_attempt_consistency(
                claim=self.claim,
                attempt=self.attempt,
            )
            if (
                not self.readiness.running
                or self.readiness.ready
                or self.readiness.stale
            ):
                message = "Task details current Claim must match running readiness."
                raise ValueError(message)
        return self


class TaskMutationResult(_ResultModel):
    """Committed Task with an optional lazy-expiry event prefix."""

    task: Task
    events: tuple[TaskEvent, ...]

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskMutationResult:
        """Require one mutation event after an optional expiry prefix.

        Returns:
            The internally consistent mutation result.

        Raises:
            ValueError: If event count or identities are inconsistent.

        """
        if len(self.events) not in (1, _MAX_TASK_MUTATION_EVENTS) or (
            len(self.events) == _MAX_TASK_MUTATION_EVENTS
            and self.events[0].event_type is not TaskEventType.CLAIM_EXPIRED
        ):
            message = "Task mutation result has an invalid TaskEvent sequence."
            raise ValueError(message)
        _validate_event_batch(self.task, self.events)
        if len(self.events) == _MAX_TASK_MUTATION_EVENTS:
            _validate_claim_expiry_event(self.events[0])
        return self


class TaskClaimResult(_ResultModel):
    """Task plus current or released Claim state and ordered Claim events."""

    task: Task
    claim: TaskClaim | None
    attempt: TaskAttempt | None
    events: tuple[TaskEvent, ...]

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskClaimResult:
        """Validate current/no-op and explicit release outcome shapes.

        Returns:
            The internally consistent Claim operation result.

        Raises:
            ValueError: If Task, Claim, Attempt, or events disagree.

        """
        if self.events:
            _validate_event_batch(self.task, self.events)
        if self.claim is not None:
            _validate_claim_task_identity(self.task, self.claim)
            validate_claim_attempt_consistency(
                claim=self.claim,
                attempt=self.attempt,
            )
            event_types = tuple(event.event_type for event in self.events)
            allowed_sequences = {
                (),
                (TaskEventType.TASK_CLAIMED,),
                (TaskEventType.CLAIM_EXPIRED, TaskEventType.TASK_CLAIMED),
                (TaskEventType.CLAIM_RENEWED,),
            }
            if event_types not in allowed_sequences:
                message = "Current Claim result has an invalid event sequence."
                raise ValueError(message)
            if self.events:
                last_attempt = self.events[-1].attempt_id
                if last_attempt != self.claim.attempt_id or any(
                    event.actor_subject_id != self.claim.subject_id
                    for event in self.events
                ):
                    message = "Current Claim events must match its owner attribution."
                    raise ValueError(message)
            return self

        if tuple(event.event_type for event in self.events) != (
            TaskEventType.CLAIM_RELEASED,
        ):
            message = "Released Claim result requires one claim_released event."
            raise ValueError(message)
        event_attempt = self.events[0].attempt_id
        if self.attempt is None:
            if event_attempt is not None:
                message = "Human release must have null Attempt attribution."
                raise ValueError(message)
        elif (
            self.attempt.task_uid != self.task.uid
            or self.attempt.status is not AttemptStatus.RELEASED
            or self.attempt.id != event_attempt
            or self.attempt.subject_id != self.events[0].actor_subject_id
            or self.attempt.ended_at != self.events[0].occurred_at
        ):
            message = "Agent release must return its terminal released Attempt."
            raise ValueError(message)
        return self


class TaskProgressResult(_ResultModel):
    """Current Agent ownership plus one ordered structured progress event batch."""

    task: Task
    claim: TaskClaim
    attempt: TaskAttempt
    events: tuple[TaskEvent, ...]

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskProgressResult:
        """Validate active Agent ownership and exact progress event ordering.

        Returns:
            The internally consistent progress result.

        Raises:
            ValueError: If ownership, attribution, or event order is invalid.

        """
        _validate_claim_task_identity(self.task, self.claim)
        validate_claim_attempt_consistency(claim=self.claim, attempt=self.attempt)
        _validate_event_batch(self.task, self.events)
        event_types = tuple(event.event_type for event in self.events)
        if not event_types or event_types[0] is not TaskEventType.PROGRESS_REPORTED:
            message = "Task progress must begin with progress_reported."
            raise ValueError(message)
        if any(
            event_type is not TaskEventType.OBSERVATION_ADDED
            for event_type in event_types[1:]
        ):
            message = "Task progress may contain only observation_added after progress."
            raise ValueError(message)
        maximum_event_count = PROGRESS_OBSERVATIONS_MAX_ITEMS + 1
        if len(self.events) > maximum_event_count or any(
            event.attempt_id != self.attempt.id
            or event.actor_subject_id != self.claim.subject_id
            or event.occurred_at >= self.claim.lease_expires_at
            for event in self.events
        ):
            message = "Task progress event count and Attempt attribution are invalid."
            raise ValueError(message)
        _validate_progress_event_payloads(self.events)
        return self


class TaskSubmissionResult(_ResultModel):
    """Committed Task, retained Result, and ordered submission/review events."""

    task: Task
    result: TaskResult
    events: tuple[TaskEvent, ...]
    attempt: TaskAttempt | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> TaskSubmissionResult:
        """Validate Task, Result, review, and exact event-sequence consistency.

        Returns:
            The internally consistent submission or review result.

        Raises:
            ValueError: If identities, state, or events disagree.

        """
        if self.result.task_uid != self.task.uid:
            message = "Task submission Result must belong to the returned Task."
            raise ValueError(message)
        _validate_event_batch(self.task, self.events)
        status = self.result.review.status
        expected: tuple[TaskEventType, ...]
        if status is ResultReviewStatus.NOT_REQUIRED:
            expected = (TaskEventType.RESULT_SUBMITTED, TaskEventType.TASK_COMPLETED)
            consistent = (
                self.task.state is TaskState.DONE
                and self.task.current_result_id == self.result.id
            )
        elif status is ResultReviewStatus.PENDING:
            expected = (TaskEventType.RESULT_SUBMITTED,)
            consistent = (
                self.task.state is TaskState.REVIEW
                and self.task.current_result_id == self.result.id
            )
        elif status is ResultReviewStatus.APPROVED:
            expected = (TaskEventType.REVIEW_APPROVED, TaskEventType.TASK_COMPLETED)
            consistent = (
                self.task.state is TaskState.DONE
                and self.task.current_result_id == self.result.id
            )
        else:
            expected = (TaskEventType.REVIEW_REJECTED,)
            consistent = (
                self.task.state is TaskState.OPEN
                and self.task.current_result_id is None
            )
        if not consistent:
            message = "Task submission state must match the Result review disposition."
            raise ValueError(message)
        event_types = tuple(event.event_type for event in self.events)
        has_expiry_prefix = (
            event_types[:1] == (TaskEventType.CLAIM_EXPIRED,)
            and self.result.attempt_id is None
            and status
            in (
                ResultReviewStatus.NOT_REQUIRED,
                ResultReviewStatus.PENDING,
            )
        )
        if has_expiry_prefix:
            _validate_claim_expiry_event(self.events[0])
        operation_events = self.events[1:] if has_expiry_prefix else self.events
        if tuple(event.event_type for event in operation_events) != expected:
            message = "Task submission events must match the Result review disposition."
            raise ValueError(message)
        first = operation_events[0]
        if status in (
            ResultReviewStatus.NOT_REQUIRED,
            ResultReviewStatus.PENDING,
        ):
            attribution_matches = (
                self.result.submitted_by == first.actor_subject_id
                and self.result.submitted_at == first.occurred_at
            )
        else:
            attribution_matches = (
                self.result.review.reviewed_by == first.actor_subject_id
                and self.result.review.reviewed_at == first.occurred_at
            )
        if not attribution_matches or self.task.updated_at != first.occurred_at:
            message = (
                "Task submission attribution and timestamps must match its events."
            )
            raise ValueError(message)
        _validate_submission_attempt(
            task=self.task,
            result=self.result,
            events=self.events,
            attempt=self.attempt,
        )
        return self


class TaskEventResult(_ResultModel):
    """One flat attributable TaskEvent safe for application consumers."""

    id: TaskEventId
    cursor: int
    task_uid: TaskId
    project_id: ProjectId
    actor_subject_id: SubjectId
    actor_kind: SubjectKind
    attempt_id: AttemptId | None = None
    request_id: RequestId
    event_type: TaskEventType = Field(serialization_alias="type")
    occurred_at: datetime
    payload: Mapping[str, JsonValue]

    @field_serializer("payload")
    def _serialize_payload(
        self,
        value: Mapping[str, JsonValue],
    ) -> dict[str, object]:
        """Serialize the immutable payload without exposing its backing copy.

        Args:
            value: Recursively frozen domain payload.

        Returns:
            A detached JSON-compatible mapping for boundary serialization.

        """
        return {key: _mutable_json_value(item) for key, item in value.items()}

    @model_validator(mode="after")
    def _validate_event(self) -> TaskEventResult:
        """Validate core event fields and freeze payload recursively.

        Returns:
            The validated flat attributable event.

        Raises:
            ValueError: If core event data or Phase 3 attribution is invalid.

        """
        event = TaskEvent(
            id=self.id,
            cursor=self.cursor,
            task_uid=self.task_uid,
            project_id=self.project_id,
            actor_subject_id=self.actor_subject_id,
            request_id=self.request_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            payload=self.payload,
            attempt_id=self.attempt_id,
        )
        object.__setattr__(self, "payload", event.payload)
        return self


def _mutable_json_value(value: JsonValue) -> object:
    """Copy one immutable JSON value into serialization-friendly containers.

    Args:
        value: Validated recursive JSON value.

    Returns:
        Scalars unchanged, arrays as lists, and objects as detached dictionaries.

    """
    if isinstance(value, Mapping):
        return {key: _mutable_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json_value(item) for item in value]
    return value


class TaskEventPage(_ResultModel):
    """One polling-safe, strictly ascending TaskEvent snapshot."""

    events: tuple[TaskEventResult, ...]
    next_cursor: int

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> int:
        """Validate a nonnegative Instance event cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated nonnegative integer.

        Raises:
            DomainValidationError: If the value is negative or not an integer.

        """
        if type(value) is not int or value < 0:
            message = "TaskEvent next_cursor must be a nonnegative integer."
            raise DomainValidationError(message)
        return value

    @model_validator(mode="after")
    def _validate_event_order(self) -> TaskEventPage:
        """Require one scoped ascending event batch ending at next_cursor.

        Returns:
            The validated TaskEvent page.

        Raises:
            ValueError: If cursors or Task/Project identities disagree.

        """
        if not self.events:
            return self
        cursors = tuple(event.cursor for event in self.events)
        if tuple(sorted(cursors)) != cursors or len(set(cursors)) != len(cursors):
            message = "TaskEvent page events must have strictly ascending cursors."
            raise ValueError(message)
        first = self.events[0]
        if any(
            event.task_uid != first.task_uid or event.project_id != first.project_id
            for event in self.events
        ):
            message = "TaskEvent page events must share one Task and Project scope."
            raise ValueError(message)
        if self.next_cursor != cursors[-1]:
            message = "TaskEvent page next_cursor must equal the last event cursor."
            raise ValueError(message)
        return self


class TaskPage(_ResultModel):
    """One deterministic Project- or Instance-scoped Task page with readiness."""

    tasks: tuple[Task, ...]
    readiness: tuple[TaskReadiness, ...] = ()
    next_cursor: str | None
    view: TaskListView = TaskListView.ALL

    @field_validator("next_cursor", mode="before")
    @classmethod
    def _validate_next_cursor(cls, value: object) -> str | None:
        """Validate a returned opaque cursor.

        Args:
            value: Candidate cursor.

        Returns:
            The validated cursor or ``None``.

        Raises:
            ValueError: If the cursor is malformed.

        """
        if value is None:
            return None
        if not isinstance(value, str):
            message = "Next cursor must be a string or null."
            raise DomainValidationError(message)
        if (
            not value
            or value != value.strip()
            or len(value) > _CURSOR_MAX_LENGTH
            or any(
                character.isspace() or not character.isprintable()
                for character in value
            )
        ):
            message = (
                "Next cursor must contain 1 through 2048 characters without "
                "whitespace or control characters."
            )
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _validate_task_order(self) -> Self:
        """Require strict Project-key and task-number ordering.

        Returns:
            The validated deterministic Task page.

        Raises:
            ValueError: If Tasks are not strictly ascending.

        """
        if self.readiness and len(self.readiness) != len(self.tasks):
            message = "Task page readiness must align one-for-one with Tasks."
            raise ValueError(message)
        previous_position: tuple[object, ...] | None = None
        for task in self.tasks:
            project_key, separator, _number = task.key.rpartition("-")
            if separator != "-":
                message = "Task page contains an invalid stable Task key."
                raise ValueError(message)
            position = (
                ready_task_ordering_key(task, project_key=project_key)
                if self.view is TaskListView.READY
                else (project_key, task.number)
            )
            if previous_position is not None and position <= previous_position:
                message = (
                    "Task page must be ordered by Project key and task number "
                    "ascending."
                )
                raise ValueError(message)
            previous_position = position
        return self


def _validate_claim_task_identity(task: Task, claim: TaskClaim) -> None:
    """Require a Claim to name the returned Task by both identities.

    Args:
        task: Returned authoritative Task.
        claim: Current Claim projection.

    Raises:
        ValueError: If canonical or Human identities disagree.

    """
    if claim.task_uid != task.uid or claim.task_key != task.key:
        message = "Task Claim must match the returned Task identities."
        raise ValueError(message)


def _validate_submission_attempt(
    *,
    task: Task,
    result: TaskResult,
    events: tuple[TaskEvent, ...],
    attempt: TaskAttempt | None,
) -> None:
    """Validate nullable Human or Agent submission attribution.

    Args:
        task: Task returned by the submission or review operation.
        result: Retained Human- or Agent-submitted Result.
        events: Ordered events appended by the current operation.
        attempt: Terminal Agent Attempt returned only during Agent submission.

    Raises:
        ValueError: If Attempt ownership or event attribution is inconsistent.

    """
    operation_events = (
        events[1:] if events[0].event_type is TaskEventType.CLAIM_EXPIRED else events
    )
    if result.attempt_id is None:
        if attempt is not None or any(
            event.attempt_id is not None for event in operation_events
        ):
            message = "Human submission and review require null Attempt data."
            raise ValueError(message)
        return
    if attempt is None:
        if operation_events[0].event_type is TaskEventType.RESULT_SUBMITTED:
            message = "Agent submission must return its submitted Attempt."
            raise ValueError(message)
        if any(event.attempt_id is not None for event in operation_events):
            message = "Human review events require null Attempt attribution."
            raise ValueError(message)
        return
    if (
        attempt.id != result.attempt_id
        or attempt.task_uid != task.uid
        or attempt.subject_id != result.submitted_by
        or attempt.status is not AttemptStatus.SUBMITTED
        or attempt.ended_at != operation_events[0].occurred_at
        or any(event.attempt_id != attempt.id for event in operation_events)
    ):
        message = "Agent submission Attempt and event attribution must match."
        raise ValueError(message)


def _validate_claim_expiry_event(event: TaskEvent) -> None:
    """Validate the closed payload and half-open time of an expiry prefix.

    Args:
        event: Candidate leading ``claim_expired`` event.

    Raises:
        ValueError: If the event payload is open, malformed, or future-dated.

    """
    payload = event.payload
    if (
        event.event_type is not TaskEventType.CLAIM_EXPIRED
        or set(payload) != {"lease_expires_at"}
        or not isinstance(payload["lease_expires_at"], str)
    ):
        message = "Claim expiry event payload is invalid."
        raise ValueError(message)
    value = payload["lease_expires_at"]
    try:
        lease_expires_at = datetime.fromisoformat(value)
    except ValueError as error:
        message = "Claim expiry event Lease timestamp is invalid."
        raise ValueError(message) from error
    if (
        not value.endswith("Z")
        or lease_expires_at.utcoffset() != timedelta(0)
        or lease_expires_at > event.occurred_at
    ):
        message = "Claim expiry event Lease timestamp is invalid."
        raise ValueError(message)


def _validate_progress_event_payloads(events: tuple[TaskEvent, ...]) -> None:
    """Reconstruct bounded structured progress from its closed event payloads.

    Args:
        events: Ordered progress header and observation event batch.

    Raises:
        ValueError: If any payload is open, malformed, or outside its bounds.

    """
    header = events[0].payload
    if (
        not set(header) <= {"message", "percent_complete"}
        or ("message" in header and not isinstance(header["message"], str))
        or (
            "percent_complete" in header and type(header["percent_complete"]) is not int
        )
    ):
        message = "Task progress event payload is invalid."
        raise ValueError(message)
    try:
        progress = TaskProgress(
            message=cast("str | None", header.get("message")),
            percent_complete=cast("int | None", header.get("percent_complete")),
            observations=tuple(
                _progress_observation_from_event(event) for event in events[1:]
            ),
        )
    except (DomainValidationError, TypeError, ValueError) as error:
        message = "Task progress event payload is invalid."
        raise ValueError(message) from error
    if progress.message != header.get("message"):
        message = "Task progress event payload is not canonical."
        raise ValueError(message)


def _progress_observation_from_event(event: TaskEvent) -> ProgressObservation:
    """Hydrate one exact observation payload for result validation.

    Args:
        event: Candidate ``observation_added`` event.

    Returns:
        Validated immutable observation.

    Raises:
        ValueError: If the payload shape or values are invalid.

    """
    payload = event.payload
    if (
        set(payload) != {"kind", "text"}
        or not isinstance(payload["kind"], str)
        or not isinstance(payload["text"], str)
    ):
        message = "Task observation event payload is invalid."
        raise ValueError(message)
    observation = ProgressObservation(
        kind=ObservationKind(payload["kind"]),
        text=payload["text"],
    )
    if observation.text != payload["text"]:
        message = "Task observation event payload is not canonical."
        raise ValueError(message)
    return observation


def _validate_event_batch(task: Task, events: tuple[TaskEvent, ...]) -> None:
    """Validate one attributable ordered event batch against a Task.

    Args:
        task: Committed Task returned by an operation.
        events: Events appended by that same semantic operation.

    Raises:
        ValueError: If identities, attribution, time, request, or order disagree.

    """
    if not events:
        message = "Task operation result must contain at least one TaskEvent."
        raise ValueError(message)
    first = events[0]
    cursors = tuple(event.cursor for event in events)
    if tuple(sorted(cursors)) != cursors or len(set(cursors)) != len(cursors):
        message = "Task operation events must have strictly ascending cursors."
        raise ValueError(message)
    if any(
        event.task_uid != task.uid
        or event.project_id != task.project_id
        or event.actor_subject_id != first.actor_subject_id
        or event.request_id != first.request_id
        or event.occurred_at != first.occurred_at
        for event in events
    ):
        message = "Task operation events must share Task and attribution identities."
        raise ValueError(message)


def _validate_identity_consistency(
    *,
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
) -> None:
    """Validate the embedded bootstrap-Human and Owner relationship.

    Args:
        instance: Selected local Instance.
        project: Selected local Project.
        subject: Selected local Human.
        grant: Subject's ProjectGrant.

    Raises:
        ValueError: If any entity or authorization relationship is inconsistent.

    """
    if (
        project.instance_id != instance.id
        or subject.instance_id != instance.id
        or grant.instance_id != instance.id
    ):
        message = "Identity graph does not belong to the selected Instance."
        raise ValueError(message)
    subject_kind: object = subject.kind
    if (
        subject_kind is not SubjectKind.HUMAN
        or not subject.enabled
        or not subject.is_instance_admin
    ):
        message = "Embedded context requires an enabled Human administrator."
        raise ValueError(message)
    if (
        grant.subject_id != subject.id
        or grant.project_id != project.id
        or grant.role is not ProjectRole.OWNER
    ):
        message = "Embedded context requires the selected Human to own the Project."
        raise ValueError(message)


def _validate_status_identity_consistency(
    *,
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
) -> None:
    """Validate a Phase 5 authenticated Viewer-or-stronger status graph.

    Args:
        instance: Selected local Instance.
        project: Selected granted Project.
        subject: Current authenticated Subject.
        grant: Subject's current ProjectGrant.

    Raises:
        ValueError: If entities cross scope or the Subject is disabled.

    """
    if (
        project.instance_id != instance.id
        or subject.instance_id != instance.id
        or grant.instance_id != instance.id
        or grant.subject_id != subject.id
        or grant.project_id != project.id
        or not subject.enabled
    ):
        message = "Status identity graph is not an enabled granted Project context."
        raise ValueError(message)
