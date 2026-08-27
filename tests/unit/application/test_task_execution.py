"""Unit tests for Phase 4 Agent execution application orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetTask,
    LeaseLostError,
    ReportTaskProgressMutation,
    SubmitAgentResultMutation,
    TaskExecutionApplication,
    TaskProgressResult,
    TaskResultInput,
    TaskSubmissionResult,
)
from workaholic.domain import (
    ApprovalRequirement,
    AttemptId,
    AttemptStatus,
    ObservationKind,
    ProgressObservation,
    ProjectId,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskAttempt,
    TaskClaim,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskProgress,
    TaskResult,
    TaskState,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from workaholic.application.ports import Clock, ExecutionIdentifierFactory
    from workaholic.domain import JsonValue

_NOW = datetime(2026, 8, 26, 11, 45, tzinfo=UTC)
_CREATED_AT = _NOW - timedelta(days=1)
_PROJECT_ID = ProjectId("prj_execution_application")
_SUBJECT_ID = SubjectId("sub_local")
_TASK_ID = TaskId("tsk_execution_application")
_ATTEMPT_ID = AttemptId("atm_current")


def _task(
    *,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
) -> Task:
    """Build one open Agent execution target."""
    return Task(
        uid=_TASK_ID,
        project_id=_PROJECT_ID,
        number=1,
        key="EXEC-1",
        title="Exercise Agent application",
        objective="Verify attributable progress and Result orchestration.",
        state=TaskState.OPEN,
        priority=50,
        approval=approval,
        version=4,
        created_by=_SUBJECT_ID,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _progress() -> TaskProgress:
    """Build one complete structured progress payload."""
    return TaskProgress(
        message="Implemented persistence; running tests.",
        percent_complete=70,
        observations=(
            ProgressObservation(ObservationKind.RISK, "Upstream schema may change."),
            ProgressObservation(ObservationKind.NOTE, "Focused tests pass."),
        ),
    )


def _result_input() -> TaskResultInput:
    """Build one bounded caller-controlled Agent Result body."""
    return TaskResultInput(summary="Implemented and verified Agent execution.")


def _event(  # noqa: PLR0913 - explicit event fixture controls keep tests clear.
    *,
    event_id: TaskEventId,
    event_type: TaskEventType,
    request_id: RequestId,
    occurred_at: datetime,
    payload: Mapping[str, JsonValue],
    cursor: int,
) -> TaskEvent:
    """Build one exact Agent-attributed TaskEvent fixture."""
    return TaskEvent(
        id=event_id,
        cursor=cursor,
        task_uid=_TASK_ID,
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        request_id=request_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        attempt_id=_ATTEMPT_ID,
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
    """Generate deterministic execution identities in observable order."""

    def __init__(self, *, log: list[str] | None = None) -> None:
        """Initialize configurable candidates and event sequencing."""
        self.log = [] if log is None else log
        self.result_value: object = ResultId("res_generated")
        self.event_value: object | None = None
        self._event_number = 0

    def new_attempt_id(self) -> AttemptId:
        """Satisfy the cumulative factory without use by execution services."""
        self.log.append("unexpected-attempt")
        return AttemptId("atm_unused")

    def new_result_id(self) -> ResultId:
        """Return one generated Result candidate."""
        self.log.append("result")
        return cast("ResultId", self.result_value)

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


class _Repository:
    """Strict Agent execution repository spy with semantic default outcomes."""

    def __init__(
        self,
        *,
        approval: ApprovalRequirement = ApprovalRequirement.NONE,
    ) -> None:
        """Initialize Task state, mutation journals, and output overrides."""
        self.task: object = _task(approval=approval)
        self.progress_outcome: object | None = None
        self.submission_outcome: object | None = None
        self.error: ApplicationError | None = None
        self.queries: list[GetTask] = []
        self.progress_mutations: list[ReportTaskProgressMutation] = []
        self.submission_mutations: list[SubmitAgentResultMutation] = []

    def get_task(self, command: GetTask) -> Task:
        """Record and resolve one scoped Task lookup."""
        self.queries.append(command)
        return cast("Task", self.task)

    def report_task_progress(
        self,
        mutation: ReportTaskProgressMutation,
    ) -> TaskProgressResult:
        """Record and return one structured progress outcome."""
        self.progress_mutations.append(mutation)
        self._raise_if_configured()
        if self.progress_outcome is not None:
            return cast("TaskProgressResult", self.progress_outcome)
        return self._progress_result(mutation)

    def submit_agent_result(
        self,
        mutation: SubmitAgentResultMutation,
    ) -> TaskSubmissionResult:
        """Record and return one Agent Result submission outcome."""
        self.submission_mutations.append(mutation)
        self._raise_if_configured()
        if self.submission_outcome is not None:
            return cast("TaskSubmissionResult", self.submission_outcome)
        return self._submission_result(mutation)

    def _progress_result(
        self,
        mutation: ReportTaskProgressMutation,
    ) -> TaskProgressResult:
        """Build an exact current-Agent progress outcome."""
        expiry = mutation.occurred_at + timedelta(minutes=10)
        claimed_at = mutation.occurred_at - timedelta(minutes=1)
        claim = TaskClaim(
            task_uid=_TASK_ID,
            task_key="EXEC-1",
            subject_id=_SUBJECT_ID,
            attempt_id=_ATTEMPT_ID,
            claimed_at=claimed_at,
            lease_expires_at=expiry,
        )
        attempt = TaskAttempt(
            id=_ATTEMPT_ID,
            task_uid=_TASK_ID,
            subject_id=_SUBJECT_ID,
            status=AttemptStatus.ACTIVE,
            lease_expires_at=expiry,
            started_at=claimed_at,
            ended_at=None,
        )
        header: dict[str, JsonValue] = {}
        if mutation.progress.message is not None:
            header["message"] = mutation.progress.message
        if mutation.progress.percent_complete is not None:
            header["percent_complete"] = mutation.progress.percent_complete
        events = [
            _event(
                event_id=mutation.progress_reported_event_id,
                event_type=TaskEventType.PROGRESS_REPORTED,
                request_id=mutation.request_id,
                occurred_at=mutation.occurred_at,
                payload=header,
                cursor=1,
            )
        ]
        for index, (observation, event_id) in enumerate(
            zip(
                mutation.progress.observations or (),
                mutation.observation_event_ids,
                strict=True,
            ),
            start=2,
        ):
            events.append(
                _event(
                    event_id=event_id,
                    event_type=TaskEventType.OBSERVATION_ADDED,
                    request_id=mutation.request_id,
                    occurred_at=mutation.occurred_at,
                    payload={
                        "kind": observation.kind.value,
                        "text": observation.text,
                    },
                    cursor=index,
                )
            )
        return TaskProgressResult(
            task=cast("Task", self.task),
            claim=claim,
            attempt=attempt,
            events=tuple(events),
        )

    def _submission_result(
        self,
        mutation: SubmitAgentResultMutation,
    ) -> TaskSubmissionResult:
        """Build an exact terminal Agent submission outcome."""
        source = cast("Task", self.task)
        review_status = (
            ResultReviewStatus.NOT_REQUIRED
            if source.approval is ApprovalRequirement.NONE
            else ResultReviewStatus.PENDING
        )
        state = (
            TaskState.DONE
            if review_status is ResultReviewStatus.NOT_REQUIRED
            else TaskState.REVIEW
        )
        task = replace(
            source,
            state=state,
            version=mutation.expected_version + 1,
            current_result_id=mutation.result_id,
            updated_at=mutation.occurred_at,
        )
        result = TaskResult(
            id=mutation.result_id,
            task_uid=task.uid,
            submitted_by=_SUBJECT_ID,
            attempt_id=_ATTEMPT_ID,
            submitted_at=mutation.occurred_at,
            comment=None,
            summary=mutation.result.summary,
            criteria=mutation.result.criteria,
            artifacts=mutation.result.artifacts,
            proposed_follow_ups=mutation.result.proposed_follow_ups,
            review=ResultReview(status=review_status),
        )
        event_specs = [
            (
                mutation.result_submitted_event_id,
                TaskEventType.RESULT_SUBMITTED,
                {
                    "result_id": str(result.id),
                    "review_status": review_status.value,
                    "version": task.version,
                },
            )
        ]
        if mutation.task_completed_event_id is not None:
            event_specs.append(
                (
                    mutation.task_completed_event_id,
                    TaskEventType.TASK_COMPLETED,
                    {"result_id": str(result.id), "version": task.version},
                )
            )
        events = tuple(
            _event(
                event_id=event_id,
                event_type=event_type,
                request_id=mutation.request_id,
                occurred_at=mutation.occurred_at,
                payload=payload,
                cursor=index,
            )
            for index, (event_id, event_type, payload) in enumerate(
                event_specs,
                start=1,
            )
        )
        attempt = TaskAttempt(
            id=_ATTEMPT_ID,
            task_uid=_TASK_ID,
            subject_id=_SUBJECT_ID,
            status=AttemptStatus.SUBMITTED,
            lease_expires_at=mutation.occurred_at + timedelta(minutes=5),
            started_at=mutation.occurred_at - timedelta(minutes=1),
            ended_at=mutation.occurred_at,
        )
        return TaskSubmissionResult(
            task=task,
            result=result,
            attempt=attempt,
            events=events,
        )

    def _raise_if_configured(self) -> None:
        """Raise one configured semantic error without translation."""
        if self.error is not None:
            raise self.error


def _application(
    repository: _Repository,
    *,
    clock: _Clock | None = None,
    identifiers: _Identifiers | None = None,
) -> TaskExecutionApplication:
    """Compose the service around deterministic strict dependencies."""
    return TaskExecutionApplication(
        repository,
        cast("Clock", _Clock() if clock is None else clock),
        cast(
            "ExecutionIdentifierFactory",
            _Identifiers() if identifiers is None else identifiers,
        ),
    )


def _invoke_with_malformed_outcome(
    repository: _Repository,
    *,
    operation: str,
) -> object:
    """Invoke one Agent service after configuring its malformed output."""
    if operation == "progress":
        repository.progress_outcome = object()
        return _application(repository).report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            progress=TaskProgress(message="Working"),
        )
    repository.submission_outcome = object()
    return _application(repository).submit_result(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result=_result_input(),
    )


def test_progress_generates_ordered_events_and_samples_clock_once() -> None:
    """Progress maps one header plus observation IDs in exact input order."""
    log: list[str] = []
    clock = _Clock(log=log)
    identifiers = _Identifiers(log=log)
    repository = _Repository()
    progress = _progress()

    outcome = _application(
        repository,
        clock=clock,
        identifiers=identifiers,
    ).report_progress(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        progress=progress,
        idempotency_key="progress-1",
    )

    assert progress.observations is not None
    assert outcome.events[1].payload["text"] == progress.observations[0].text
    assert repository.queries == []
    assert repository.progress_mutations == [
        ReportTaskProgressMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            task_uid=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            progress=progress,
            progress_reported_event_id=TaskEventId("evt_generated_1"),
            observation_event_ids=(
                TaskEventId("evt_generated_2"),
                TaskEventId("evt_generated_3"),
            ),
            request_id=RequestId("req_generated"),
            occurred_at=_NOW,
            idempotency_key="progress-1",
        )
    ]
    assert log == ["event:1", "event:2", "event:3", "request", "clock"]
    assert clock.calls == 1


def test_progress_resolves_human_key_once() -> None:
    """A Human key is resolved once before the exact Agent mutation is built."""
    repository = _Repository()

    _application(repository).report_progress(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task="EXEC-1",
        attempt_id=_ATTEMPT_ID,
        progress=TaskProgress(percent_complete=50),
    )

    assert repository.queries == [
        GetTask(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task="EXEC-1")
    ]
    assert len(repository.progress_mutations) == 1


def test_submit_generates_result_completion_and_exact_terminal_attribution() -> None:
    """No-review submission owns Result IDs, both events, and one clock sample."""
    log: list[str] = []
    clock = _Clock(log=log)
    identifiers = _Identifiers(log=log)
    repository = _Repository()
    content = _result_input()

    outcome = _application(
        repository,
        clock=clock,
        identifiers=identifiers,
    ).submit_result(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result=content,
    )

    assert outcome.attempt is not None
    assert outcome.attempt.status is AttemptStatus.SUBMITTED
    assert repository.queries == [
        GetTask(project_id=_PROJECT_ID, subject_id=_SUBJECT_ID, task=_TASK_ID)
    ]
    assert repository.submission_mutations == [
        SubmitAgentResultMutation(
            project_id=_PROJECT_ID,
            actor_subject_id=_SUBJECT_ID,
            task_uid=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result_id=ResultId("res_generated"),
            result_submitted_event_id=TaskEventId("evt_generated_1"),
            task_completed_event_id=TaskEventId("evt_generated_2"),
            result=content,
            request_id=RequestId("req_generated"),
            occurred_at=_NOW,
        )
    ]
    assert log == ["result", "event:1", "event:2", "request", "clock"]
    assert clock.calls == 1


def test_review_submission_generates_no_completion_event_identity() -> None:
    """Human approval requirement leaves completion identity for later review."""
    log: list[str] = []
    repository = _Repository(approval=ApprovalRequirement.HUMAN)

    outcome = _application(
        repository,
        identifiers=_Identifiers(log=log),
        clock=_Clock(log=log),
    ).submit_result(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task="EXEC-1",
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result=_result_input(),
    )

    assert outcome.task.state is TaskState.REVIEW
    assert repository.submission_mutations[0].task_completed_event_id is None
    assert log == ["result", "event:1", "request", "clock"]


def test_submission_accepts_idempotent_replay_generated_identities() -> None:
    """Replay returns original generated Result and events with exact Attempt input."""
    repository = _Repository()
    original_time = _NOW - timedelta(minutes=2)
    original_mutation = SubmitAgentResultMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        task_uid=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result_id=ResultId("res_original"),
        result_submitted_event_id=TaskEventId("evt_original_submitted"),
        task_completed_event_id=TaskEventId("evt_original_completed"),
        result=_result_input(),
        request_id=RequestId("req_original"),
        occurred_at=original_time,
        idempotency_key="submit-1",
    )
    repository.submission_outcome = repository._submission_result(original_mutation)

    outcome = _application(repository).submit_result(
        project_id=_PROJECT_ID,
        subject_id=_SUBJECT_ID,
        task=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result=_result_input(),
        idempotency_key="submit-1",
    )

    assert outcome.result.id == ResultId("res_original")
    assert repository.submission_mutations[0].result_id == ResultId("res_generated")


@pytest.mark.parametrize(
    "invoke",
    [
        lambda application: application.report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=cast("AttemptId", object()),
            progress=TaskProgress(message="Working"),
        ),
        lambda application: application.report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            progress=cast("TaskProgress", object()),
        ),
        lambda application: application.submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            expected_version=0,
            result=_result_input(),
        ),
        lambda application: application.submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result=cast("TaskResultInput", object()),
            idempotency_key="bad key",
        ),
    ],
)
def test_invalid_inputs_never_call_semantic_persistence(
    invoke: Callable[[TaskExecutionApplication], object],
) -> None:
    """Malformed Attempt, payload, version, and replay input fail before mutation."""
    repository = _Repository()

    with pytest.raises(ApplicationError) as captured:
        invoke(_application(repository))

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.progress_mutations == []
    assert repository.submission_mutations == []


def test_repository_error_is_preserved_without_retry_or_refresh() -> None:
    """Lease loss propagates unchanged after one query and one semantic call."""
    repository = _Repository()
    repository.error = LeaseLostError()

    with pytest.raises(LeaseLostError):
        _application(repository).report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task="EXEC-1",
            attempt_id=_ATTEMPT_ID,
            progress=TaskProgress(message="Working"),
        )

    assert len(repository.queries) == 1
    assert len(repository.progress_mutations) == 1


@pytest.mark.parametrize("operation", ["progress", "submission"])
def test_malformed_persistence_outcome_fails_closed(operation: str) -> None:
    """Wrong output types map to a safe internal error for both Agent services."""
    repository = _Repository()
    with pytest.raises(ApplicationError) as captured:
        _invoke_with_malformed_outcome(repository, operation=operation)

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_semantically_different_progress_outcome_fails_closed() -> None:
    """Internally valid events still must match the exact caller progress payload."""
    repository = _Repository()
    foreign_mutation = ReportTaskProgressMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        task_uid=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        progress=TaskProgress(percent_complete=10),
        progress_reported_event_id=TaskEventId("evt_foreign"),
        request_id=RequestId("req_foreign"),
        occurred_at=_NOW,
    )
    repository.progress_outcome = repository._progress_result(foreign_mutation)

    with pytest.raises(ApplicationError) as captured:
        _application(repository).report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            progress=TaskProgress(percent_complete=20),
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_submission_event_shape_mismatch_fails_closed_without_leaking() -> None:
    """A valid but different approval outcome maps to one safe internal failure."""
    repository = _Repository()
    foreign_repository = _Repository(approval=ApprovalRequirement.HUMAN)
    foreign_mutation = SubmitAgentResultMutation(
        project_id=_PROJECT_ID,
        actor_subject_id=_SUBJECT_ID,
        task_uid=_TASK_ID,
        attempt_id=_ATTEMPT_ID,
        expected_version=4,
        result_id=ResultId("res_foreign_review"),
        result_submitted_event_id=TaskEventId("evt_foreign_review"),
        task_completed_event_id=None,
        result=_result_input(),
        request_id=RequestId("req_foreign_review"),
        occurred_at=_NOW,
    )
    repository.submission_outcome = foreign_repository._submission_result(
        foreign_mutation
    )

    with pytest.raises(ApplicationError) as captured:
        _application(repository).submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result=_result_input(),
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR


def test_invalid_generated_result_identity_maps_to_internal_error() -> None:
    """Malformed generated identities cannot reach semantic persistence."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.result_value = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository, identifiers=identifiers).submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result=_result_input(),
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.submission_mutations == []


def test_invalid_generated_progress_event_maps_to_internal_error() -> None:
    """Malformed progress event candidates cannot reach semantic persistence."""
    repository = _Repository()
    identifiers = _Identifiers()
    identifiers.event_value = object()

    with pytest.raises(ApplicationError) as captured:
        _application(repository, identifiers=identifiers).report_progress(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=_TASK_ID,
            attempt_id=_ATTEMPT_ID,
            progress=TaskProgress(message="Working"),
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.progress_mutations == []


def test_invalid_task_selector_maps_to_invalid_input() -> None:
    """Agent submission rejects malformed selectors before generating identities."""
    repository = _Repository()

    with pytest.raises(ApplicationError) as captured:
        _application(repository).submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task=cast("TaskId", object()),
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result=_result_input(),
        )

    assert captured.value.code is ApplicationErrorCode.INVALID_INPUT
    assert repository.queries == []
    assert repository.submission_mutations == []


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
        TaskExecutionApplication(
            cast("_Repository", repository),
            cast("Clock", clock),
            cast("ExecutionIdentifierFactory", identifiers),
        )


def test_task_resolution_rejects_inconsistent_repository_output() -> None:
    """Submission Task lookup must preserve selector and Project identity."""
    repository = _Repository()
    repository.task = replace(_task(), number=2, key="EXEC-2")

    with pytest.raises(ApplicationError) as captured:
        _application(repository).submit_result(
            project_id=_PROJECT_ID,
            subject_id=_SUBJECT_ID,
            task="EXEC-1",
            attempt_id=_ATTEMPT_ID,
            expected_version=4,
            result=_result_input(),
        )

    assert captured.value.code is ApplicationErrorCode.INTERNAL_ERROR
    assert repository.submission_mutations == []
