"""Unit tests for Phase 4 Claim application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ClaimNextTaskMutation,
    ClaimTaskMutation,
    GetTask,
    NoTaskAvailableError,
    ReleaseClaimMutation,
    RenewClaimMutation,
    TaskClaimApplication,
    TaskClaimResult,
)
from workaholic.domain import (
    AttemptId,
    AttemptStatus,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workaholic.application.ports import Clock, ExecutionIdentifierFactory

_NOW = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)
_CREATED_AT = _NOW - timedelta(days=1)
_PROJECT_ID = ProjectId("prj_claim_application")
_SUBJECT_ID = SubjectId("sub_local")
_TASK_ID = TaskId("tsk_claim_application")


def _task() -> Task:
    """Build one ready Task returned by the strict repository fake."""
    return Task(
        uid=_TASK_ID,
        project_id=_PROJECT_ID,
        number=1,
        key="APP-1",
        title="Exercise Claim application",
        objective="Verify orchestration without persistence policy leakage.",
        state=TaskState.OPEN,
        priority=50,
        version=3,
        created_by=_SUBJECT_ID,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _timestamp(value: datetime) -> str:
    """Serialize one UTC timestamp using the public event payload grammar."""
    return value.isoformat().replace("+00:00", "Z")


def _event(  # noqa: PLR0913 - explicit event fixture controls keep tests clear.
    *,
    event_id: TaskEventId,
    event_type: TaskEventType,
    request_id: RequestId,
    occurred_at: datetime,
    attempt_id: AttemptId | None,
    lease_expires_at: datetime,
    cursor: int = 2,
) -> TaskEvent:
    """Build one exact Claim lifecycle event."""
    return TaskEvent(
        id=event_id,
        cursor=cursor,
        task_uid=_TASK_ID,
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=request_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload={"lease_expires_at": _timestamp(lease_expires_at)},
        attempt_id=attempt_id,
    )


class _Clock:
    """Record authoritative clock sampling."""

    def __init__(self, *, value: object = _NOW, log: list[str] | None = None) -> None:
        """Store one configurable returned value and shared call log."""
        self.value = value
        self.calls = 0
        self.log = [] if log is None else log

    def now(self) -> datetime:
        """Return the configured value and record one sample."""
        self.calls += 1
        self.log.append("clock")
        return cast("datetime", self.value)


class _Identifiers:
    """Generate deterministic identities while recording exact call order."""

    def __init__(self, *, log: list[str] | None = None) -> None:
        """Initialize independent Attempt and event counters."""
        self.log = [] if log is None else log
        self.attempt_value: object = AttemptId("atm_generated")
        self.event_value: object | None = None
        self._event_number = 0

    def new_attempt_id(self) -> AttemptId:
        """Return one generated Agent Attempt candidate."""
        self.log.append("attempt")
        return cast("AttemptId", self.attempt_value)

    def new_event_id(self) -> TaskEventId:
        """Return the next generated event candidate."""
        self._event_number += 1
        self.log.append(f"event:{self._event_number}")
        if self.event_value is not None:
            return cast("TaskEventId", self.event_value)
        return TaskEventId(f"evt_generated_{self._event_number}")

    def new_request_id(self) -> RequestId:
        """Return one generated request candidate."""
        self.log.append("request")
        return RequestId("req_generated")

    def new_result_id(self) -> ResultId:
        """Satisfy the cumulative execution factory without use in Claim tests."""
        self.log.append("unexpected-result")
        return ResultId("res_unused")


class _Repository:
    """Strict Claim repository spy with semantic default outcomes."""

    def __init__(self) -> None:
        """Initialize query state, mutation journals, and overrides."""
        self.task: object = _task()
        self.outcome: object | None = None
        self.error: ApplicationError | None = None
        self.queries: list[GetTask] = []
        self.claims: list[ClaimTaskMutation] = []
        self.agent_claims: list[ClaimNextTaskMutation] = []
        self.renewals: list[RenewClaimMutation] = []
        self.releases: list[ReleaseClaimMutation] = []

    def get_task(self, command: GetTask) -> Task:
        """Record one Task-key resolution query."""
        self.queries.append(command)
        return cast("Task", self.task)

    def claim_task(self, mutation: ClaimTaskMutation) -> TaskClaimResult:
        """Record and return one Human Claim outcome."""
        self.claims.append(mutation)
        self._raise_if_configured()
        return self._selected_or(self._acquired(mutation))

    def claim_next_task(self, mutation: ClaimNextTaskMutation) -> TaskClaimResult:
        """Record and return one Agent Claim outcome."""
        self.agent_claims.append(mutation)
        self._raise_if_configured()
        return self._selected_or(self._acquired(mutation))

    def renew_claim(self, mutation: RenewClaimMutation) -> TaskClaimResult:
        """Record and return one Human or Agent renewal outcome."""
        self.renewals.append(mutation)
        self._raise_if_configured()
        expiry = mutation.occurred_at + timedelta(
            seconds=mutation.lease_duration_seconds
        )
        claim = TaskClaim(
            task_uid=_TASK_ID,
            task_key="APP-1",
            subject_id=_SUBJECT_ID,
            attempt_id=mutation.attempt_id,
            claimed_at=_NOW - timedelta(minutes=1),
            lease_expires_at=expiry,
        )
        attempt = (
            None
            if mutation.attempt_id is None
            else TaskAttempt(
                id=mutation.attempt_id,
                task_uid=_TASK_ID,
                subject_id=_SUBJECT_ID,
                status=AttemptStatus.ACTIVE,
                lease_expires_at=expiry,
                started_at=claim.claimed_at,
                ended_at=None,
            )
        )
        event = _event(
            event_id=mutation.claim_renewed_event_id,
            event_type=TaskEventType.CLAIM_RENEWED,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            attempt_id=mutation.attempt_id,
            lease_expires_at=expiry,
        )
        return self._selected_or(
            TaskClaimResult(
                task=_task(),
                claim=claim,
                attempt=attempt,
                events=(event,),
            )
        )

    def release_claim(self, mutation: ReleaseClaimMutation) -> TaskClaimResult:
        """Record and return one Human or Agent release outcome."""
        self.releases.append(mutation)
        self._raise_if_configured()
        expiry = mutation.occurred_at + timedelta(minutes=5)
        attempt = (
            None
            if mutation.attempt_id is None
            else TaskAttempt(
                id=mutation.attempt_id,
                task_uid=_TASK_ID,
                subject_id=_SUBJECT_ID,
                status=AttemptStatus.RELEASED,
                lease_expires_at=expiry,
                started_at=mutation.occurred_at - timedelta(minutes=1),
                ended_at=mutation.occurred_at,
            )
        )
        event = _event(
            event_id=mutation.claim_released_event_id,
            event_type=TaskEventType.CLAIM_RELEASED,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            attempt_id=mutation.attempt_id,
            lease_expires_at=expiry,
        )
        return self._selected_or(
            TaskClaimResult(
                task=_task(),
                claim=None,
                attempt=attempt,
                events=(event,),
            )
        )

    def _acquired(
        self,
        mutation: ClaimTaskMutation | ClaimNextTaskMutation,
    ) -> TaskClaimResult:
        """Build an exact fresh Claim acquisition outcome."""
        attempt_id = (
            None if isinstance(mutation, ClaimTaskMutation) else mutation.attempt_id
        )
        expiry = mutation.occurred_at + timedelta(
            seconds=mutation.lease_duration_seconds
        )
        claim = TaskClaim(
            task_uid=_TASK_ID,
            task_key="APP-1",
            subject_id=_SUBJECT_ID,
            attempt_id=attempt_id,
            claimed_at=mutation.occurred_at,
            lease_expires_at=expiry,
        )
        attempt = (
            None
            if attempt_id is None
            else TaskAttempt(
                id=attempt_id,
                task_uid=_TASK_ID,
                subject_id=_SUBJECT_ID,
                status=AttemptStatus.ACTIVE,
                lease_expires_at=expiry,
                started_at=mutation.occurred_at,
                ended_at=None,
            )
        )
        event = _event(
            event_id=mutation.task_claimed_event_id,
            event_type=TaskEventType.TASK_CLAIMED,
            request_id=mutation.request_id,
            occurred_at=mutation.occurred_at,
            attempt_id=attempt_id,
            lease_expires_at=expiry,
        )
        return TaskClaimResult(
            task=_task(),
            claim=claim,
            attempt=attempt,
            events=(event,),
        )

    def _selected_or(self, default: TaskClaimResult) -> TaskClaimResult:
        """Return a configured outcome or the semantic default."""
        return (
            default if self.outcome is None else cast("TaskClaimResult", self.outcome)
        )

    def _raise_if_configured(self) -> None:
        """Raise one configured public error without translating it."""
        if self.error is not None:
            raise self.error


def _application(
    repository: _Repository,
    *,
    clock: _Clock | None = None,
    identifiers: _Identifiers | None = None,
) -> TaskClaimApplication:
    """Compose the service around deterministic strict dependencies."""
    return TaskClaimApplication(
        repository,
        cast("Clock", _Clock() if clock is None else clock),
        cast(
            "ExecutionIdentifierFactory",
            _Identifiers() if identifiers is None else identifiers,
        ),
    )


def _invoke_lease_operation(
    application: TaskClaimApplication,
    *,
    operation: str,
) -> object:
    """Invoke one Human Lease wrapper selected by its test label."""
    if operation == "renew":
        return application.renew_claim(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=None,
        )
    return application.release_claim(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=None,
    )


def test_human_claim_resolves_default_and_generates_exact_attribution() -> None:
    """Human Claim maps null Attempt, 8h default, and owned identities exactly."""
    log: list[str] = []
    clock = _Clock(log=log)
    identifiers = _Identifiers(log=log)
    repository = _Repository()

    outcome = _application(
        repository,
        clock=clock,
        identifiers=identifiers,
    ).claim_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task="APP-1",
        idempotency_key="human-claim",
    )

    assert outcome.claim is not None
    assert outcome.claim.attempt_id is None
    assert repository.queries == [
        GetTask(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task="APP-1")
    ]
    assert repository.claims == [
        ClaimTaskMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            task_uid=_TASK_ID,
            lease_duration_seconds=28_800,
            task_claimed_event_id=TaskEventId("evt_generated_1"),
            claim_expired_event_id=TaskEventId("evt_generated_2"),
            request_id=RequestId("req_generated"),
            occurred_at=_NOW,
            idempotency_key="human-claim",
        )
    ]
    assert log == ["event:1", "event:2", "request", "clock"]
    assert clock.calls == 1


def test_agent_claim_generates_attempt_before_events_and_uses_default() -> None:
    """Agent pull generates one Attempt and resolves the 15m default once."""
    log: list[str] = []
    clock = _Clock(log=log)
    identifiers = _Identifiers(log=log)
    repository = _Repository()

    outcome = _application(
        repository,
        clock=clock,
        identifiers=identifiers,
    ).claim_next_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
    )

    assert outcome.attempt is not None
    assert outcome.attempt.id == AttemptId("atm_generated")
    assert repository.queries == []
    assert repository.agent_claims[0].lease_duration_seconds == 900
    assert log == ["attempt", "event:1", "event:2", "request", "clock"]
    assert clock.calls == 1


@pytest.mark.parametrize(
    ("attempt_id", "expected_seconds"),
    [(None, 28_800), (AttemptId("atm_current"), 900)],
)
def test_renewal_maps_nullable_owner_and_owner_specific_default(
    attempt_id: AttemptId | None,
    expected_seconds: int,
) -> None:
    """One service method maps Human renewal and Agent heartbeat explicitly."""
    repository = _Repository()

    outcome = _application(repository).renew_claim(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=attempt_id,
    )

    assert outcome.claim is not None
    assert outcome.claim.attempt_id == attempt_id
    assert repository.queries == []
    assert repository.renewals[0].attempt_id == attempt_id
    assert repository.renewals[0].lease_duration_seconds == expected_seconds


@pytest.mark.parametrize("attempt_id", [None, AttemptId("atm_current")])
def test_release_maps_human_and_agent_owner_tokens(
    attempt_id: AttemptId | None,
) -> None:
    """Release preserves a null Human token or exact Agent Attempt token."""
    repository = _Repository()

    outcome = _application(repository).release_claim(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=attempt_id,
        idempotency_key="release",
    )

    assert outcome.claim is None
    assert (None if outcome.attempt is None else outcome.attempt.id) == attempt_id
    assert repository.releases[0].attempt_id == attempt_id
    assert repository.releases[0].idempotency_key == "release"


def test_custom_duration_is_resolved_to_exact_whole_seconds() -> None:
    """Application duration resolution delegates only whole seconds to storage."""
    repository = _Repository()

    _application(repository).claim_next_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        lease_duration=timedelta(hours=2),
    )

    assert repository.agent_claims[0].lease_duration_seconds == 7_200


def test_agent_claim_accepts_replay_with_original_generated_attempt() -> None:
    """Caller idempotency permits the durable Attempt from the original request."""
    repository = _Repository()
    mutation_time = _NOW - timedelta(minutes=2)
    original_attempt = AttemptId("atm_original")
    expiry = mutation_time + timedelta(minutes=15)
    claim = TaskClaim(
        task_uid=_TASK_ID,
        task_key="APP-1",
        subject_id=_SUBJECT_ID,
        attempt_id=original_attempt,
        claimed_at=mutation_time,
        lease_expires_at=expiry,
    )
    repository.outcome = TaskClaimResult(
        task=_task(),
        claim=claim,
        attempt=TaskAttempt(
            id=original_attempt,
            task_uid=_TASK_ID,
            subject_id=_SUBJECT_ID,
            status=AttemptStatus.ACTIVE,
            lease_expires_at=expiry,
            started_at=mutation_time,
            ended_at=None,
        ),
        events=(
            _event(
                event_id=TaskEventId("evt_original"),
                event_type=TaskEventType.TASK_CLAIMED,
                request_id=RequestId("req_original"),
                occurred_at=mutation_time,
                attempt_id=original_attempt,
                lease_expires_at=expiry,
            ),
        ),
    )

    outcome = _application(repository).claim_next_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        idempotency_key="agent-claim",
    )

    assert outcome is repository.outcome
    assert repository.agent_claims[0].attempt_id == AttemptId("atm_generated")
    assert outcome.attempt is not None
    assert outcome.attempt.id == original_attempt


def test_human_reclaim_accepts_exact_owned_noop() -> None:
    """An already owned current Human Claim may return no new events."""
    repository = _Repository()
    claim = TaskClaim(
        task_uid=_TASK_ID,
        task_key="APP-1",
        subject_id=_SUBJECT_ID,
        attempt_id=None,
        claimed_at=_NOW - timedelta(minutes=1),
        lease_expires_at=_NOW + timedelta(hours=7),
    )
    repository.outcome = TaskClaimResult(
        task=_task(),
        claim=claim,
        attempt=None,
        events=(),
    )

    outcome = _application(repository).claim_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
    )

    assert outcome.events == ()
    assert outcome.claim == claim


def test_human_claim_accepts_exact_expiry_then_reclaim_sequence() -> None:
    """A stale Claim event precedes the fresh attributable Human Claim event."""
    repository = _Repository()
    expiry = _NOW + timedelta(hours=8)
    claim = TaskClaim(
        task_uid=_TASK_ID,
        task_key="APP-1",
        subject_id=_SUBJECT_ID,
        attempt_id=None,
        claimed_at=_NOW,
        lease_expires_at=expiry,
    )
    expired = _event(
        event_id=TaskEventId("evt_generated_2"),
        event_type=TaskEventType.CLAIM_EXPIRED,
        request_id=RequestId("req_generated"),
        occurred_at=_NOW,
        attempt_id=None,
        lease_expires_at=_NOW - timedelta(minutes=1),
        cursor=1,
    )
    claimed = _event(
        event_id=TaskEventId("evt_generated_1"),
        event_type=TaskEventType.TASK_CLAIMED,
        request_id=RequestId("req_generated"),
        occurred_at=_NOW,
        attempt_id=None,
        lease_expires_at=expiry,
        cursor=2,
    )
    repository.outcome = TaskClaimResult(
        task=_task(),
        claim=claim,
        attempt=None,
        events=(expired, claimed),
    )

    outcome = _application(repository).claim_task(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
    )

    assert tuple(event.event_type for event in outcome.events) == (
        TaskEventType.CLAIM_EXPIRED,
        TaskEventType.TASK_CLAIMED,
    )


def test_acquisition_rejects_wrong_attempt_and_malformed_payload() -> None:
    """Fresh Agent identity and closed Lease payload remain exact output contracts."""
    for failure in ("attempt", "payload"):
        repository = _Repository()
        other_attempt = AttemptId("atm_other")
        attempt_id = (
            other_attempt if failure == "attempt" else AttemptId("atm_generated")
        )
        expiry = _NOW + timedelta(minutes=15)
        claim = TaskClaim(
            task_uid=_TASK_ID,
            task_key="APP-1",
            subject_id=_SUBJECT_ID,
            attempt_id=attempt_id,
            claimed_at=_NOW,
            lease_expires_at=expiry,
        )
        event = _event(
            event_id=TaskEventId("evt_generated_1"),
            event_type=TaskEventType.TASK_CLAIMED,
            request_id=RequestId("req_generated"),
            occurred_at=_NOW,
            attempt_id=attempt_id,
            lease_expires_at=expiry,
        )
        if failure == "payload":
            event = replace(event, payload={})
        repository.outcome = TaskClaimResult(
            task=_task(),
            claim=claim,
            attempt=TaskAttempt(
                id=attempt_id,
                task_uid=_TASK_ID,
                subject_id=_SUBJECT_ID,
                status=AttemptStatus.ACTIVE,
                lease_expires_at=expiry,
                started_at=_NOW,
                ended_at=None,
            ),
            events=(event,),
        )

        with pytest.raises(ApplicationError) as captured:
            _application(repository).claim_next_task(
                project_id=_PROJECT_ID,
                subject_id=_SUBJECT_ID,
            )

        assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_renewal_rejects_malformed_lease_payload() -> None:
    """An internally shaped renewal cannot return an open Lease payload."""
    repository = _Repository()
    mutation = RenewClaimMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        task_uid=_TASK_ID,
        attempt_id=None,
        lease_duration_seconds=28_800,
        claim_renewed_event_id=TaskEventId("evt_generated_1"),
        request_id=RequestId("req_generated"),
        occurred_at=_NOW,
    )
    malformed = repository.renew_claim(mutation)
    repository.renewals.clear()
    repository.outcome = TaskClaimResult(
        task=malformed.task,
        claim=malformed.claim,
        attempt=malformed.attempt,
        events=(replace(malformed.events[0], payload={}),),
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).renew_claim(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=None,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize("operation", ["renew", "release"])
def test_lease_operation_invalid_generated_identity_fails_before_storage(
    operation: str,
) -> None:
    """Both Lease wrappers reject malformed generated event identities."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.event_value = object()
    application = _application(repository, identifiers=identifiers)

    with pytest.raises(ApplicationError) as captured:
        _invoke_lease_operation(application, operation=operation)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.renewals == []
    assert repository.releases == []


def test_agent_claim_invalid_generated_attempt_fails_before_storage() -> None:
    """A malformed Attempt factory result cannot reach Claim persistence."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.attempt_value = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository, identifiers=identifiers).claim_next_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.agent_claims == []


def test_agent_claim_invalid_generated_event_fails_before_storage() -> None:
    """Agent pull validates event candidates after generating its Attempt."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.event_value = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository, identifiers=identifiers).claim_next_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.agent_claims == []


def test_invalid_task_selector_fails_before_query_or_storage() -> None:
    """Claim services reject an unvalidated selector without repository access."""
    repository = _Repository()

    with pytest.raises(ApplicationError) as captured:
        _application(repository).claim_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=cast("TaskId", object()),
        )

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.queries == []
    assert repository.claims == []


@pytest.mark.parametrize(
    "invoke",
    [
        lambda application: application.claim_task(
            project_id=cast("ProjectId", object()),
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
        ),
        lambda application: application.claim_next_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            lease_duration=timedelta(microseconds=1),
        ),
        lambda application: application.renew_claim(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=cast("AttemptId", object()),
        ),
        lambda application: application.release_claim(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=None,
            idempotency_key=" padded ",
        ),
    ],
)
def test_invalid_inputs_never_call_persistence(
    invoke: Callable[[TaskClaimApplication], object],
) -> None:
    """Malformed scope, duration, owner, and replay inputs fail before storage."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as captured:
        invoke(_application(repository))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.claims == []
    assert repository.agent_claims == []
    assert repository.renewals == []
    assert repository.releases == []


def test_repository_error_is_preserved_without_retry() -> None:
    """Application services preserve semantic errors and never retry persistence."""
    repository = _Repository()
    repository.error = NoTaskAvailableError()

    with pytest.raises(NoTaskAvailableError):
        _application(repository).claim_next_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
        )

    assert len(repository.agent_claims) == 1


@pytest.mark.parametrize("outcome", [object(), "invalid"])
def test_malformed_persistence_outcome_fails_closed(outcome: object) -> None:
    """Wrong result types and nullable outcomes map to a safe internal failure."""
    repository = _Repository()
    repository.outcome = outcome

    with pytest.raises(ApplicationError) as captured:
        _application(repository).claim_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert len(repository.claims) == 1


def test_invalid_generated_identity_maps_to_internal_error() -> None:
    """Malformed identity dependencies cannot reach semantic persistence."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.event_value = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository, identifiers=identifiers).claim_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.claims == []


@pytest.mark.parametrize(
    ("repository", "clock", "identifiers"),
    [
        (object(), _Clock(), _Identifiers()),
        (_Repository(), object(), _Identifiers()),
        (_Repository(), _Clock(), object()),
    ],
)
def test_constructor_requires_explicit_callable_dependencies(
    repository: object,
    clock: object,
    identifiers: object,
) -> None:
    """Composition rejects collaborators missing any required method."""
    with pytest.raises(TypeError):
        TaskClaimApplication(
            cast("_Repository", repository),
            cast("Clock", clock),
            cast("ExecutionIdentifierFactory", identifiers),
        )


def test_key_resolution_rejects_inconsistent_repository_task() -> None:
    """Human-key lookup output must preserve Project and selector identity."""
    repository = _Repository()
    repository.task = replace(_task(), number=2, key="APP-2")

    with pytest.raises(ApplicationError) as captured:
        _application(repository).claim_task(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task="APP-1",
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.claims == []
