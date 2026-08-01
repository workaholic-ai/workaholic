"""Integration tests for atomic SQLite Human Result transitions."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    AddTaskDependencyMutation,
    ApproveResultMutation,
    BootstrapMutation,
    GetTaskDetails,
    IdempotencyConflictError,
    InvalidTransitionError,
    PermissionDeniedError,
    RejectResultMutation,
    ResultInvalidError,
    SubmitHumanResultMutation,
    TaskCreationMutation,
    TaskResultInput,
    UnsatisfiableDependencyError,
    VersionConflictError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    CriterionOutcome,
    CriterionStatus,
    InstanceId,
    ProjectId,
    ProposedFollowUp,
    RequestId,
    ResultId,
    ResultReviewStatus,
    SubjectId,
    Task,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.integration

_CREATED_AT = datetime(2026, 8, 1, 8, 0, 0, 111111, tzinfo=UTC)
_SUBMITTED_AT = datetime(2026, 8, 1, 9, 0, 0, 222222, tzinfo=UTC)
_REVIEWED_AT = datetime(2026, 8, 1, 10, 0, 0, 333333, tzinfo=UTC)
_RESUBMITTED_AT = datetime(2026, 8, 1, 11, 0, 0, 444444, tzinfo=UTC)
_REQUIRED_ACCEPTANCE = (
    AcceptanceCriterion("ac_done", "Implementation is complete.", required=True),
)


def _repository(
    tmp_path: Path,
    *,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    acceptance: tuple[AcceptanceCriterion, ...] = (),
) -> tuple[SQLiteRepository, Task]:
    """Create one initialized store and target Task.

    Args:
        tmp_path: Isolated pytest directory.
        approval: Target Task approval requirement.
        acceptance: Target Task acceptance definition.

    Returns:
        Repository and authoritative initial Task.

    """
    repository = SQLiteRepository(tmp_path / "local.db")
    bootstrap = repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            request_id=RequestId("req_bootstrap"),
            occurred_at=_CREATED_AT - timedelta(minutes=1),
            project_key="ACME",
        )
    )
    task = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_target"),
            event_id=TaskEventId("evt_create_target"),
            request_id=RequestId("req_create_target"),
            project_id=bootstrap.project.id,
            actor_subject_id=bootstrap.subject.id,
            occurred_at=_CREATED_AT,
            title="Implement the requested change",
            objective="Produce and verify the requested outcome.",
            priority=60,
            approval=approval,
            acceptance=acceptance,
        )
    )
    return repository, task


def _structured_result() -> TaskResultInput:
    """Return complete validated Human Result content."""
    return TaskResultInput(
        summary="Implemented and verified the requested behavior.",
        criteria=(
            CriterionOutcome(
                criterion_id="ac_done",
                status=CriterionStatus.PASSED,
                evidence="The focused and regression suites pass.",
            ),
        ),
        artifacts=(
            ArtifactReference(
                uri="workspace://repo/reports/result.md",
                media_type="text/markdown",
                sha256="a" * 64,
            ),
            ArtifactReference(uri="https://example.test/build/123"),
        ),
        proposed_follow_ups=(ProposedFollowUp("Add an end-to-end smoke test"),),
    )


def _submit(  # noqa: PLR0913 - explicit mutation controls aid adapter tests.
    task: Task,
    *,
    suffix: str = "submit",
    expected_version: int | None = None,
    occurred_at: datetime = _SUBMITTED_AT,
    approval: ApprovalRequirement | None = None,
    comment: str | None = None,
    result: TaskResultInput | None = None,
    idempotency_key: str | None = None,
) -> SubmitHumanResultMutation:
    """Build one valid Human submission mutation.

    Args:
        task: Target Task snapshot.
        suffix: Generated identity suffix.
        expected_version: Optional optimistic-version override.
        occurred_at: Authoritative transaction timestamp.
        approval: Optional approval override used only for event allocation.
        comment: Optional manual comment.
        result: Optional structured Result content.
        idempotency_key: Optional caller replay key.

    Returns:
        Valid attributable Human submission mutation.

    """
    selected_approval = task.approval if approval is None else approval
    return SubmitHumanResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        result_id=ResultId(f"res_{suffix}"),
        result_submitted_event_id=TaskEventId(f"evt_{suffix}_submitted"),
        task_completed_event_id=(
            TaskEventId(f"evt_{suffix}_completed")
            if selected_approval is ApprovalRequirement.NONE
            else None
        ),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        comment=comment,
        result=TaskResultInput() if result is None else result,
        idempotency_key=idempotency_key,
    )


def _approve(  # noqa: PLR0913 - explicit fixture controls aid adapter tests.
    task: Task,
    *,
    suffix: str = "approve",
    expected_version: int | None = None,
    occurred_at: datetime = _REVIEWED_AT,
    comment: str | None = None,
    idempotency_key: str | None = None,
) -> ApproveResultMutation:
    """Build one valid approval mutation."""
    return ApproveResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        review_approved_event_id=TaskEventId(f"evt_{suffix}_approved"),
        task_completed_event_id=TaskEventId(f"evt_{suffix}_completed"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        comment=comment,
        idempotency_key=idempotency_key,
    )


def _reject(  # noqa: PLR0913 - explicit fixture controls aid adapter tests.
    task: Task,
    *,
    suffix: str = "reject",
    expected_version: int | None = None,
    occurred_at: datetime = _REVIEWED_AT,
    reason: str = "The evidence is incomplete.",
    idempotency_key: str | None = None,
) -> RejectResultMutation:
    """Build one valid rejection mutation."""
    return RejectResultMutation(
        task_uid=task.uid,
        project_id=task.project_id,
        actor_subject_id=SubjectId("sub_local"),
        review_rejected_event_id=TaskEventId(f"evt_{suffix}_rejected"),
        request_id=RequestId(f"req_{suffix}"),
        occurred_at=occurred_at,
        expected_version=(
            task.version if expected_version is None else expected_version
        ),
        reason=reason,
        idempotency_key=idempotency_key,
    )


def _result_snapshot(database_path: Path) -> tuple[object, ...]:
    """Read all Result-operation-owned rows for rollback comparisons."""
    with open_read_connection(database_path) as connection:
        tasks = connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall()
        results = connection.execute(
            "SELECT * FROM task_results ORDER BY submitted_at, id"
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM task_events ORDER BY cursor"
        ).fetchall()
        replay = connection.execute(
            """
            SELECT * FROM idempotency_records
            WHERE operation LIKE 'task.result.%'
            ORDER BY operation, caller_key
            """
        ).fetchall()
    return tasks, results, events, replay


def test_empty_manual_submission_completes_without_attempt(tmp_path: Path) -> None:
    """Submitting no comment or content remains valid Human completion."""
    repository, original = _repository(tmp_path)

    outcome = repository.submit_human_result(_submit(original))

    assert outcome.task.state is TaskState.DONE
    assert outcome.task.version == 2
    assert outcome.task.updated_at == _SUBMITTED_AT
    assert outcome.task.current_result_id == ResultId("res_submit")
    assert outcome.result.attempt_id is None
    assert outcome.result.submitted_by == SubjectId("sub_local")
    assert outcome.result.comment is None
    assert outcome.result.summary is None
    assert outcome.result.review.status is ResultReviewStatus.NOT_REQUIRED
    assert tuple(event.event_type for event in outcome.events) == (
        TaskEventType.RESULT_SUBMITTED,
        TaskEventType.TASK_COMPLETED,
    )
    assert tuple(dict(event.payload) for event in outcome.events) == (
        {
            "result_id": "res_submit",
            "review_status": "not_required",
            "version": 2,
        },
        {"result_id": "res_submit", "version": 2},
    )
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            "SELECT attempt_id FROM task_results WHERE id = 'res_submit'"
        ).fetchone() == (None,)


def test_structured_submission_review_and_approval_round_trip_after_restart(
    tmp_path: Path,
) -> None:
    """Complete evidence survives review, approval, and repository restart."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
        acceptance=_REQUIRED_ACCEPTANCE,
    )
    content = _structured_result()

    submitted = repository.submit_human_result(
        _submit(
            original,
            comment="Manual implementation",
            result=content,
            idempotency_key="submit-structured-1",
        )
    )

    assert submitted.task.state is TaskState.REVIEW
    assert submitted.task.version == 2
    assert submitted.result.comment == "Manual implementation"
    assert submitted.result.summary == content.summary
    assert submitted.result.criteria == content.criteria
    assert submitted.result.artifacts == content.artifacts
    assert submitted.result.proposed_follow_ups == content.proposed_follow_ups
    assert submitted.result.review.status is ResultReviewStatus.PENDING
    assert tuple(event.event_type for event in submitted.events) == (
        TaskEventType.RESULT_SUBMITTED,
    )
    details = repository.get_task_details(
        GetTaskDetails(
            project_id=original.project_id,
            subject_id=SubjectId("sub_local"),
            task=original.uid,
        )
    )
    assert details.current_result == submitted.result

    restarted = SQLiteRepository(repository.database_path)
    approved = restarted.approve_result(
        _approve(
            submitted.task,
            comment="Evidence accepted.",
            idempotency_key="approve-1",
        )
    )

    assert approved.task.state is TaskState.DONE
    assert approved.task.version == 3
    assert approved.result.id == submitted.result.id
    assert approved.result.summary == content.summary
    assert approved.result.review.status is ResultReviewStatus.APPROVED
    assert approved.result.review.reviewed_by == SubjectId("sub_local")
    assert approved.result.review.reviewed_at == _REVIEWED_AT
    assert approved.result.review.comment == "Evidence accepted."
    assert tuple(event.event_type for event in approved.events) == (
        TaskEventType.REVIEW_APPROVED,
        TaskEventType.TASK_COMPLETED,
    )
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM task_results").fetchone() == (
            1,
        )


def test_rejection_retains_result_then_resubmission_selects_a_new_result(
    tmp_path: Path,
) -> None:
    """Rejected audit data remains while a later Result becomes current."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    submitted = repository.submit_human_result(
        _submit(original, suffix="first", comment="First attempt")
    )

    rejected = repository.reject_result(
        _reject(
            submitted.task,
            reason="Please provide a reproducible verification.",
            idempotency_key="reject-1",
        )
    )

    assert rejected.task.state is TaskState.OPEN
    assert rejected.task.version == 3
    assert rejected.task.current_result_id is None
    assert rejected.result.id == ResultId("res_first")
    assert rejected.result.review.status is ResultReviewStatus.REJECTED
    assert rejected.result.review.reason == (
        "Please provide a reproducible verification."
    )
    assert tuple(event.event_type for event in rejected.events) == (
        TaskEventType.REVIEW_REJECTED,
    )

    resubmitted = repository.submit_human_result(
        _submit(
            rejected.task,
            suffix="second",
            occurred_at=_RESUBMITTED_AT,
            comment="Added reproducible verification.",
        )
    )

    assert resubmitted.task.state is TaskState.REVIEW
    assert resubmitted.task.version == 4
    assert resubmitted.task.current_result_id == ResultId("res_second")
    with open_read_connection(repository.database_path) as connection:
        assert connection.execute(
            """
            SELECT id, review_status FROM task_results
            ORDER BY submitted_at, id
            """
        ).fetchall() == [
            ("res_first", "rejected"),
            ("res_second", "pending"),
        ]


@pytest.mark.parametrize(
    "content",
    [
        TaskResultInput(),
        TaskResultInput(
            criteria=(
                CriterionOutcome(
                    criterion_id="ac_unknown",
                    status=CriterionStatus.PASSED,
                ),
            )
        ),
    ],
    ids=["missing-required", "unknown-criterion"],
)
def test_criterion_mismatch_returns_result_invalid_and_rolls_back(
    content: TaskResultInput,
    tmp_path: Path,
) -> None:
    """Task acceptance and submitted outcomes must match in-transaction."""
    repository, original = _repository(
        tmp_path,
        acceptance=_REQUIRED_ACCEPTANCE,
    )
    before = _result_snapshot(repository.database_path)

    with pytest.raises(ResultInvalidError):
        repository.submit_human_result(_submit(original, result=content))

    assert _result_snapshot(repository.database_path) == before


def test_submission_requires_done_dependencies_and_classifies_cancelled(
    tmp_path: Path,
) -> None:
    """Unfinished dependencies block submission; cancelled ones are unsatisfiable."""
    repository, target = _repository(tmp_path)
    prerequisite = repository.create_task(
        TaskCreationMutation(
            task_id=TaskId("tsk_prerequisite"),
            event_id=TaskEventId("evt_create_prerequisite"),
            request_id=RequestId("req_create_prerequisite"),
            project_id=target.project_id,
            actor_subject_id=SubjectId("sub_local"),
            occurred_at=_CREATED_AT,
            title="Prerequisite",
            objective="Complete first.",
            priority=50,
        )
    )
    dependant = repository.add_task_dependency(
        AddTaskDependencyMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=SubjectId("sub_local"),
            prerequisite_uid=prerequisite.uid,
            event_id=TaskEventId("evt_dependency"),
            request_id=RequestId("req_dependency"),
            occurred_at=_CREATED_AT + timedelta(minutes=1),
            expected_version=target.version,
        )
    ).task

    with pytest.raises(InvalidTransitionError):
        repository.submit_human_result(_submit(dependant))

    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET state = 'cancelled', version = version + 1, updated_at = ?
            WHERE uid = ?
            """,
            (
                (_CREATED_AT + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                str(prerequisite.uid),
            ),
        )
    with pytest.raises(UnsatisfiableDependencyError):
        repository.submit_human_result(_submit(dependant, suffix="cancelled"))

    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET state = 'done', version = version + 1, updated_at = ?
            WHERE uid = ?
            """,
            (
                (_CREATED_AT + timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
                str(prerequisite.uid),
            ),
        )
    assert (
        repository.submit_human_result(
            _submit(dependant, suffix="satisfied")
        ).task.state
        is TaskState.DONE
    )


def test_submission_and_review_stale_versions_leave_no_partial_rows(
    tmp_path: Path,
) -> None:
    """Every Result mutation enforces its exact optimistic precondition."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    before = _result_snapshot(repository.database_path)
    with pytest.raises(VersionConflictError):
        repository.submit_human_result(
            _submit(original, expected_version=original.version + 1)
        )
    assert _result_snapshot(repository.database_path) == before

    submitted = repository.submit_human_result(_submit(original))
    before_review = _result_snapshot(repository.database_path)
    operations: tuple[Callable[[], object], ...] = (
        lambda: repository.approve_result(_approve(submitted.task, expected_version=1)),
        lambda: repository.reject_result(_reject(submitted.task, expected_version=1)),
    )
    for operation in operations:
        with pytest.raises(VersionConflictError):
            operation()
        assert _result_snapshot(repository.database_path) == before_review


@pytest.mark.parametrize(
    ("approval", "completion_event"),
    [
        (ApprovalRequirement.NONE, None),
        (ApprovalRequirement.HUMAN, TaskEventId("evt_wrong_completed")),
    ],
)
def test_submission_rejects_event_shape_inconsistent_with_task_approval(
    approval: ApprovalRequirement,
    completion_event: TaskEventId | None,
    tmp_path: Path,
) -> None:
    """Callers cannot omit or add completion events against Task policy."""
    repository, original = _repository(tmp_path, approval=approval)
    before = _result_snapshot(repository.database_path)
    mutation = _submit(original).model_copy(
        update={"task_completed_event_id": completion_event}
    )

    with pytest.raises(ResultInvalidError):
        repository.submit_human_result(mutation)

    assert _result_snapshot(repository.database_path) == before


def test_disabled_human_cannot_submit_or_create_a_result(tmp_path: Path) -> None:
    """Result persistence rechecks enabled Human Owner authorization."""
    repository, original = _repository(tmp_path)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute("UPDATE subjects SET enabled = 0 WHERE id = 'sub_local'")
    before = _result_snapshot(repository.database_path)

    with pytest.raises(PermissionDeniedError):
        repository.submit_human_result(_submit(original))

    assert _result_snapshot(repository.database_path) == before


def test_idempotent_submission_replays_historic_pending_result_after_approval(
    tmp_path: Path,
) -> None:
    """Replay returns the original outcome even after the Result was reviewed."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    first_mutation = _submit(
        original,
        suffix="first",
        comment="Manual result",
        idempotency_key="submit-replay-1",
    )
    first = repository.submit_human_result(first_mutation)
    repository.approve_result(_approve(first.task))
    before_replay = _result_snapshot(repository.database_path)
    replay_mutation = _submit(
        original,
        suffix="different_generated_ids",
        comment="Manual result",
        idempotency_key="submit-replay-1",
    )

    replay = repository.submit_human_result(replay_mutation)

    assert replay == first
    assert replay.result.review.status is ResultReviewStatus.PENDING
    assert _result_snapshot(repository.database_path) == before_replay

    conflicting = _submit(
        original,
        suffix="conflict",
        comment="Different semantic content",
        idempotency_key="submit-replay-1",
    )
    with pytest.raises(IdempotencyConflictError):
        repository.submit_human_result(conflicting)
    assert _result_snapshot(repository.database_path) == before_replay


@pytest.mark.parametrize("operation", ["approve", "reject"])
def test_review_idempotency_replays_one_disposition_without_duplicate_events(
    operation: str,
    tmp_path: Path,
) -> None:
    """Review replay retains the original Result and event batch exactly."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    submitted = repository.submit_human_result(_submit(original))
    if operation == "approve":
        first_approval = _approve(
            submitted.task,
            comment="Accepted.",
            idempotency_key="review-replay-1",
        )
        first = repository.approve_result(first_approval)
        replay_approval = _approve(
            submitted.task,
            suffix="replay",
            comment="Accepted.",
            idempotency_key="review-replay-1",
        )
        before = _result_snapshot(repository.database_path)
        replay = repository.approve_result(replay_approval)
    else:
        first_rejection = _reject(
            submitted.task,
            reason="Needs more evidence.",
            idempotency_key="review-replay-1",
        )
        first = repository.reject_result(first_rejection)
        replay_rejection = _reject(
            submitted.task,
            suffix="replay",
            reason="Needs more evidence.",
            idempotency_key="review-replay-1",
        )
        before = _result_snapshot(repository.database_path)
        replay = repository.reject_result(replay_rejection)

    assert replay == first
    assert _result_snapshot(repository.database_path) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "wrong-keys",
        "wrong-shape",
        "bad-event-item",
        "cross-task-result",
        "missing-event",
    ],
)
def test_corrupt_idempotency_outcome_or_event_fails_closed(
    corruption: str,
    tmp_path: Path,
) -> None:
    """Replay validates its exact durable shape and referenced event records."""
    repository, original = _repository(tmp_path)
    mutation = _submit(
        original,
        idempotency_key="submit-corruption-1",
    )
    first = repository.submit_human_result(mutation)
    with open_write_transaction(repository.database_path) as connection:
        if corruption == "missing-event":
            connection.execute(
                "DELETE FROM task_events WHERE id = ?",
                (str(first.events[0].id),),
            )
        else:
            row = connection.execute(
                """
                SELECT outcome_json FROM idempotency_records
                WHERE operation = 'task.result.submit'
                  AND caller_key = 'submit-corruption-1'
                """
            ).fetchone()
            assert row is not None
            outcome = json.loads(row[0])
            if corruption == "wrong-keys":
                outcome = {"invalid": True}
            elif corruption == "wrong-shape":
                outcome["task"] = None
            elif corruption == "bad-event-item":
                outcome["events"] = [1]
            else:
                outcome["result"]["task_uid"] = "tsk_other"
            connection.execute(
                """
                UPDATE idempotency_records SET outcome_json = ?
                WHERE operation = 'task.result.submit'
                  AND caller_key = 'submit-corruption-1'
                """,
                (json.dumps(outcome, sort_keys=True, separators=(",", ":")),),
            )

    with pytest.raises(StorageUnavailableError):
        repository.submit_human_result(mutation)


@pytest.mark.parametrize("operation", ["approve", "reject"])
def test_concurrent_review_allows_exactly_one_versioned_disposition(
    operation: str,
    tmp_path: Path,
) -> None:
    """Concurrent reviewers cannot both commit against one pending version."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    submitted = repository.submit_human_result(_submit(original))

    def execute(suffix: str) -> object:
        """Run one independent repository disposition."""
        worker = SQLiteRepository(repository.database_path)
        if operation == "approve":
            return worker.approve_result(_approve(submitted.task, suffix=suffix))
        return worker.reject_result(_reject(submitted.task, suffix=suffix))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute, suffix) for suffix in ("one", "two")]
    successes: list[object] = []
    conflicts: list[BaseException] = []
    for future in futures:
        try:
            successes.append(future.result())
        except BaseException as error:  # noqa: BLE001 - assert exact race outcome.
            conflicts.append(error)

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], VersionConflictError)
    with open_read_connection(repository.database_path) as connection:
        event_type = "review_approved" if operation == "approve" else "review_rejected"
        assert connection.execute(
            "SELECT count(*) FROM task_events WHERE event_type = ?",
            (event_type,),
        ).fetchone() == (1,)


def test_generated_identity_collision_rolls_back_result_task_and_events(
    tmp_path: Path,
) -> None:
    """Late event insertion failure cannot expose a partial Result mutation."""
    repository, original = _repository(tmp_path)
    before = _result_snapshot(repository.database_path)
    collision = _submit(original, suffix="collision")
    collision = collision.model_copy(
        update={"result_submitted_event_id": TaskEventId("evt_create_target")}
    )

    with pytest.raises(StorageUnavailableError):
        repository.submit_human_result(collision)

    assert _result_snapshot(repository.database_path) == before


def test_review_requires_pending_selection_and_monotonic_time(
    tmp_path: Path,
) -> None:
    """Review rejects absent selections and authoritative clock regression."""
    repository, original = _repository(
        tmp_path,
        approval=ApprovalRequirement.HUMAN,
    )
    with pytest.raises(InvalidTransitionError):
        repository.approve_result(_approve(original))

    submitted = repository.submit_human_result(_submit(original))
    before = _result_snapshot(repository.database_path)
    with pytest.raises(StorageUnavailableError):
        repository.approve_result(
            _approve(
                submitted.task,
                suffix="past",
                occurred_at=_CREATED_AT - timedelta(seconds=1),
            )
        )
    assert _result_snapshot(repository.database_path) == before


def test_wrong_runtime_mutation_types_are_rejected(tmp_path: Path) -> None:
    """Adapter entry points validate exact semantic mutation categories."""
    repository, original = _repository(tmp_path)
    submission = _submit(original)

    with pytest.raises(StorageUnavailableError):
        repository.submit_human_result(
            cast("SubmitHumanResultMutation", _approve(original))
        )
    with pytest.raises(StorageUnavailableError):
        repository.approve_result(submission)  # type: ignore[arg-type]
    with pytest.raises(StorageUnavailableError):
        repository.reject_result(submission)  # type: ignore[arg-type]
