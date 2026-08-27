"""Enumerated values shared by cumulative domain entities and pure rules."""

from enum import StrEnum


class TaskState(StrEnum):
    """Persisted lifecycle states of a Task."""

    OPEN = "open"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskEventType(StrEnum):
    """Append-only Task event types emitted through Phase 4."""

    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_BLOCKED = "task_blocked"
    TASK_UNBLOCKED = "task_unblocked"
    RESULT_SUBMITTED = "result_submitted"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    TASK_CLAIMED = "task_claimed"
    CLAIM_RENEWED = "claim_renewed"
    CLAIM_RELEASED = "claim_released"
    CLAIM_EXPIRED = "claim_expired"
    PROGRESS_REPORTED = "progress_reported"
    OBSERVATION_ADDED = "observation_added"


class ApprovalRequirement(StrEnum):
    """Review required before an accepted Result completes its Task."""

    NONE = "none"
    HUMAN = "human"


class CriterionStatus(StrEnum):
    """Reported outcome of one Task acceptance criterion."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ResultReviewStatus(StrEnum):
    """Review disposition stored with a Task Result."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TaskOperationalView(StrEnum):
    """Derived operational views exposed for a Task."""

    READY = "ready"
    RUNNING = "running"
    SCHEDULED = "scheduled"
    STALE = "stale"
    AWAITING_REVIEW = "awaiting_review"


class ReadinessReason(StrEnum):
    """Stable reasons contributing to a Task operational projection."""

    TASK_BLOCKED = "task_blocked"
    TASK_AWAITING_REVIEW = "task_awaiting_review"
    TASK_DONE = "task_done"
    TASK_CANCELLED = "task_cancelled"
    NOT_YET_AVAILABLE = "not_yet_available"
    UNSATISFIED_DEPENDENCY = "unsatisfied_dependency"
    UNSATISFIABLE_DEPENDENCY = "unsatisfiable_dependency"
    ACTIVE_CLAIM = "active_claim"
    STALE_CLAIM = "stale_claim"


class AttemptStatus(StrEnum):
    """Lifecycle states of an Agent execution Attempt."""

    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    SUBMITTED = "submitted"


class ObservationKind(StrEnum):
    """Inert categories for structured Agent observations."""

    NOTE = "note"
    RISK = "risk"
    BLOCKER = "blocker"
    QUESTION = "question"


class TaskTransition(StrEnum):
    """Semantic Task operations that may affect lifecycle state."""

    UPDATE = "update"
    BLOCK = "block"
    UNBLOCK = "unblock"
    CANCEL = "cancel"
    ADD_DEPENDENCY = "add_dependency"
    REMOVE_DEPENDENCY = "remove_dependency"
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
