"""Integration tests for authorized attributable SQLite TaskEvent history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApproveResultMutation,
    BootstrapMutation,
    InvalidInputError,
    NotInitializedError,
    PermissionDeniedError,
    ProjectCreationMutation,
    ReadTaskEvents,
    RejectResultMutation,
    SubmitHumanResultMutation,
    TaskBlockMutation,
    TaskCancelMutation,
    TaskCreationMutation,
    TaskNotFoundError,
    TaskResultInput,
    TaskUnblockMutation,
    TaskUpdateMutation,
    TaskUpdatePatch,
    VersionConflictError,
)
from workaholic.domain import (
    ApprovalRequirement,
    InstanceId,
    ProjectId,
    RequestId,
    ResultId,
    SubjectId,
    SubjectKind,
    Task,
    TaskEventId,
    TaskEventType,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.application import TaskEventPage, TaskEventResult

pytestmark = pytest.mark.integration

_BASE_TIME = datetime(2026, 8, 1, 8, 0, 0, 111111, tzinfo=UTC)
_ACTOR_ID = SubjectId("sub_local")
_PROJECT_ID = ProjectId("prj_acme")
_PHASE_THREE_EVENT_TYPES = {
    TaskEventType.TASK_CREATED,
    TaskEventType.TASK_UPDATED,
    TaskEventType.TASK_BLOCKED,
    TaskEventType.TASK_UNBLOCKED,
    TaskEventType.RESULT_SUBMITTED,
    TaskEventType.REVIEW_APPROVED,
    TaskEventType.REVIEW_REJECTED,
    TaskEventType.TASK_COMPLETED,
    TaskEventType.TASK_CANCELLED,
}


@dataclass(frozen=True, slots=True)
class _EventScenario:
    """One repository containing every Phase 3 TaskEvent type."""

    repository: SQLiteRepository
    target: Task
    cancelled: Task


def _at(minutes: int) -> datetime:
    """Return a deterministic authoritative scenario timestamp.

    Args:
        minutes: Minutes after the common base instant.

    Returns:
        UTC timestamp with preserved microsecond precision.

    """
    return _BASE_TIME + timedelta(minutes=minutes)


def _create_task(
    repository: SQLiteRepository,
    *,
    suffix: str,
    occurred_at: datetime,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
) -> Task:
    """Create one deterministic attributable Task fixture.

    Args:
        repository: Initialized SQLite repository.
        suffix: Unique identity and title suffix.
        occurred_at: Authoritative creation timestamp.
        approval: Result approval policy.

    Returns:
        Committed Task.

    """
    return repository.create_task(
        TaskCreationMutation(
            task_id=TaskId(f"tsk_{suffix}"),
            event_id=TaskEventId(f"evt_{suffix}_created"),
            request_id=RequestId(f"req_{suffix}_created"),
            project_id=_PROJECT_ID,
            actor_subject_id=_ACTOR_ID,
            occurred_at=occurred_at,
            title=f"{suffix.title()} task",
            objective=f"Exercise {suffix} event history.",
            priority=50,
            approval=approval,
        )
    )


def _build_event_scenario(tmp_path: Path) -> _EventScenario:
    """Commit an interleaved history containing every Phase 3 event type.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and terminal Task snapshots.

    """
    repository = SQLiteRepository(tmp_path / "local.db")
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=_PROJECT_ID,
            subject_id=_ACTOR_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_at(0),
            project_key="ACME",
        )
    )
    target = _create_task(
        repository,
        suffix="target",
        occurred_at=_at(1),
        approval=ApprovalRequirement.HUMAN,
    )
    cancelled = _create_task(
        repository,
        suffix="cancelled",
        occurred_at=_at(2),
    )
    target = repository.update_task_if_version(
        TaskUpdateMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            event_id=TaskEventId("evt_target_updated"),
            claim_expired_event_id=TaskEventId("evt_target_updated_expired"),
            request_id=RequestId("req_target_updated"),
            occurred_at=_at(3),
            expected_version=target.version,
            patch=TaskUpdatePatch(priority=70),
        )
    ).task
    target = repository.block_task(
        TaskBlockMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            event_id=TaskEventId("evt_target_blocked"),
            claim_expired_event_id=TaskEventId("evt_target_blocked_expired"),
            request_id=RequestId("req_target_blocked"),
            occurred_at=_at(4),
            expected_version=target.version,
            reason="Waiting for a required decision.",
        )
    ).task
    cancelled = repository.cancel_task(
        TaskCancelMutation(
            task_uid=cancelled.uid,
            project_id=cancelled.project_id,
            actor_subject_id=_ACTOR_ID,
            event_id=TaskEventId("evt_cancelled_cancelled"),
            claim_expired_event_id=TaskEventId("evt_cancelled_expired"),
            request_id=RequestId("req_cancelled_cancelled"),
            occurred_at=_at(5),
            expected_version=cancelled.version,
            reason="No longer required.",
        )
    ).task
    target = repository.unblock_task(
        TaskUnblockMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            event_id=TaskEventId("evt_target_unblocked"),
            claim_expired_event_id=TaskEventId("evt_target_unblocked_expired"),
            request_id=RequestId("req_target_unblocked"),
            occurred_at=_at(6),
            expected_version=target.version,
        )
    ).task
    target = repository.submit_human_result(
        SubmitHumanResultMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            result_id=ResultId("res_target_first"),
            result_submitted_event_id=TaskEventId("evt_target_submitted_first"),
            claim_expired_event_id=TaskEventId("evt_target_first_expired"),
            request_id=RequestId("req_target_submitted_first"),
            occurred_at=_at(7),
            expected_version=target.version,
            comment="Ready for review.",
            result=TaskResultInput(summary="The first implementation is ready."),
        )
    ).task
    target = repository.reject_result(
        RejectResultMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            review_rejected_event_id=TaskEventId("evt_target_rejected"),
            request_id=RequestId("req_target_rejected"),
            occurred_at=_at(8),
            expected_version=target.version,
            reason="One verification step is missing.",
        )
    ).task
    target = repository.submit_human_result(
        SubmitHumanResultMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            result_id=ResultId("res_target_second"),
            result_submitted_event_id=TaskEventId("evt_target_submitted_second"),
            claim_expired_event_id=TaskEventId("evt_target_second_expired"),
            request_id=RequestId("req_target_submitted_second"),
            occurred_at=_at(9),
            expected_version=target.version,
            result=TaskResultInput(summary="The missing verification is complete."),
        )
    ).task
    target = repository.approve_result(
        ApproveResultMutation(
            task_uid=target.uid,
            project_id=target.project_id,
            actor_subject_id=_ACTOR_ID,
            review_approved_event_id=TaskEventId("evt_target_approved"),
            task_completed_event_id=TaskEventId("evt_target_completed"),
            request_id=RequestId("req_target_approved"),
            occurred_at=_at(10),
            expected_version=target.version,
            comment="Verified and accepted.",
        )
    ).task
    return _EventScenario(repository, target, cancelled)


def _read(  # noqa: PLR0913 - explicit query scope keeps call sites auditable.
    repository: SQLiteRepository,
    task: TaskId | str,
    *,
    project_id: ProjectId = _PROJECT_ID,
    subject_id: SubjectId = _ACTOR_ID,
    after: int = 0,
    limit: int = 100,
) -> TaskEventPage:
    """Read one validated Task event page.

    Args:
        repository: Initialized SQLite repository.
        task: Task UID or Human key.
        project_id: Selected Project scope.
        subject_id: Selected authorized Human actor.
        after: Exclusive Instance cursor.
        limit: Maximum number of events.

    Returns:
        Attributable event page.

    """
    return repository.read_task_events_after(
        ReadTaskEvents(
            project_id=project_id,
            subject_id=subject_id,
            task=task,
            after=after,
            limit=limit,
        )
    )


def _database_snapshot(database_path: Path) -> tuple[object, ...]:
    """Read mutation-owned tables for exact no-write comparisons.

    Args:
        database_path: Initialized SQLite database.

    Returns:
        Stable rows from Tasks, Results, events, and idempotency records.

    """
    with open_read_connection(database_path) as connection:
        return (
            connection.execute("SELECT * FROM tasks ORDER BY uid").fetchall(),
            connection.execute("SELECT * FROM task_results ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM task_events ORDER BY cursor").fetchall(),
            connection.execute(
                "SELECT * FROM idempotency_records ORDER BY operation, caller_key"
            ).fetchall(),
        )


def test_history_replays_every_committed_event_once_across_cursor_gaps(
    tmp_path: Path,
) -> None:
    """Cursor-zero pagination is stable, exhaustive, and Task-scoped."""
    scenario = _build_event_scenario(tmp_path)
    cursor = 0
    observed: list[TaskEventResult] = []
    while True:
        page = _read(scenario.repository, scenario.target.key, after=cursor, limit=2)
        assert page == _read(
            scenario.repository,
            scenario.target.key,
            after=cursor,
            limit=2,
        )
        if not page.events:
            assert page.next_cursor == cursor
            break
        observed.extend(page.events)
        assert page.next_cursor == page.events[-1].cursor
        cursor = page.next_cursor

    cursors = tuple(event.cursor for event in observed)
    assert cursors == tuple(sorted(set(cursors)))
    assert any(right - left > 1 for left, right in pairwise(cursors))
    assert tuple(event.id for event in observed) == (
        TaskEventId("evt_target_created"),
        TaskEventId("evt_target_updated"),
        TaskEventId("evt_target_blocked"),
        TaskEventId("evt_target_unblocked"),
        TaskEventId("evt_target_submitted_first"),
        TaskEventId("evt_target_rejected"),
        TaskEventId("evt_target_submitted_second"),
        TaskEventId("evt_target_approved"),
        TaskEventId("evt_target_completed"),
    )
    assert all(event.task_uid == scenario.target.uid for event in observed)
    assert all(event.project_id == _PROJECT_ID for event in observed)
    assert all(event.actor_subject_id == _ACTOR_ID for event in observed)
    assert all(event.actor_kind is SubjectKind.HUMAN for event in observed)
    assert all(event.attempt_id is None for event in observed)
    all_types = {event.event_type for event in observed}
    all_types.update(
        event.event_type
        for event in _read(scenario.repository, scenario.cancelled.uid).events
    )
    assert all_types == _PHASE_THREE_EVENT_TYPES


def test_multi_event_transitions_preserve_request_order_and_immutable_payload(
    tmp_path: Path,
) -> None:
    """Approval events stay adjacent, attributable, and deeply immutable."""
    scenario = _build_event_scenario(tmp_path)
    events = _read(scenario.repository, scenario.target.uid).events
    approved_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type is TaskEventType.REVIEW_APPROVED
    )
    approved, completed = events[approved_index : approved_index + 2]

    assert completed.event_type is TaskEventType.TASK_COMPLETED
    assert (
        approved.request_id == completed.request_id == RequestId("req_target_approved")
    )
    assert approved.cursor + 1 == completed.cursor
    updated = next(
        event for event in events if event.event_type is TaskEventType.TASK_UPDATED
    )
    with pytest.raises(TypeError):
        updated.payload["version"] = 99  # type: ignore[index]
    changes = updated.payload["changes"]
    assert changes == ("priority",)
    with pytest.raises(TypeError):
        changes[0] = "title"  # type: ignore[index]


def test_history_survives_repository_restart_and_reads_do_not_mutate(
    tmp_path: Path,
) -> None:
    """Restarted reads are identical and leave all owned rows unchanged."""
    scenario = _build_event_scenario(tmp_path)
    before = _database_snapshot(scenario.repository.database_path)
    original = _read(scenario.repository, scenario.target.uid)
    restarted = SQLiteRepository(scenario.repository.database_path)

    assert _read(restarted, scenario.target.uid) == original
    assert (
        _read(restarted, scenario.target.uid, after=original.next_cursor).events == ()
    )
    assert _database_snapshot(scenario.repository.database_path) == before


def test_empty_and_oversized_cursors_never_move_backward(tmp_path: Path) -> None:
    """Empty polling preserves the caller cursor, including beyond SQLite range."""
    scenario = _build_event_scenario(tmp_path)
    terminal = _read(scenario.repository, scenario.target.uid)

    assert (
        _read(
            scenario.repository,
            scenario.target.uid,
            after=terminal.next_cursor,
        ).next_cursor
        == terminal.next_cursor
    )
    huge_cursor = 10**30
    huge_page = _read(
        scenario.repository,
        scenario.target.uid,
        after=huge_cursor,
    )
    assert huge_page.events == ()
    assert huge_page.next_cursor == huge_cursor


def test_history_enforces_project_authorization_and_task_scope(tmp_path: Path) -> None:
    """Authorization is checked before exact Task resolution in one Project."""
    scenario = _build_event_scenario(tmp_path)
    scenario.repository.create_project(
        ProjectCreationMutation(
            project_id=ProjectId("prj_beta"),
            request_id=RequestId("req_project_beta"),
            instance_id=InstanceId("ins_local"),
            actor_subject_id=_ACTOR_ID,
            occurred_at=_at(20),
            project_key="BETA",
            project_name="Beta",
        )
    )

    with pytest.raises(PermissionDeniedError):
        _read(
            scenario.repository,
            scenario.target.uid,
            subject_id=SubjectId("sub_unknown"),
        )
    with pytest.raises(TaskNotFoundError):
        _read(
            scenario.repository,
            scenario.target.uid,
            project_id=ProjectId("prj_beta"),
        )
    with pytest.raises(TaskNotFoundError):
        _read(scenario.repository, "ACME-404")
    with pytest.raises(NotInitializedError):
        _read(
            scenario.repository,
            scenario.target.uid,
            project_id=ProjectId("prj_missing"),
        )

    with open_write_transaction(scenario.repository.database_path) as connection:
        connection.execute(
            "UPDATE subjects SET enabled = 0 WHERE id = ?",
            (str(_ACTOR_ID),),
        )
    with pytest.raises(PermissionDeniedError):
        _read(scenario.repository, scenario.target.uid)


def test_rejected_mutations_never_appear_in_history(tmp_path: Path) -> None:
    """A failed optimistic transaction cannot leak its proposed event."""
    repository = SQLiteRepository(tmp_path / "local.db")
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=_PROJECT_ID,
            subject_id=_ACTOR_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_at(0),
            project_key="ACME",
        )
    )
    task = _create_task(repository, suffix="rollback", occurred_at=_at(1))
    updated = repository.update_task_if_version(
        TaskUpdateMutation(
            task_uid=task.uid,
            project_id=task.project_id,
            actor_subject_id=_ACTOR_ID,
            event_id=TaskEventId("evt_rollback_updated"),
            claim_expired_event_id=TaskEventId("evt_rollback_updated_expired"),
            request_id=RequestId("req_rollback_updated"),
            occurred_at=_at(2),
            expected_version=task.version,
            patch=TaskUpdatePatch(title="Committed title"),
        )
    ).task

    with pytest.raises(VersionConflictError):
        repository.block_task(
            TaskBlockMutation(
                task_uid=updated.uid,
                project_id=updated.project_id,
                actor_subject_id=_ACTOR_ID,
                event_id=TaskEventId("evt_rollback_rejected"),
                claim_expired_event_id=TaskEventId("evt_rollback_rejected_expired"),
                request_id=RequestId("req_rollback_rejected"),
                occurred_at=_at(3),
                expected_version=task.version,
                reason="This mutation must roll back.",
            )
        )

    page = _read(repository, task.uid)
    assert tuple(event.id for event in page.events) == (
        TaskEventId("evt_rollback_created"),
        TaskEventId("evt_rollback_updated"),
    )


def test_runtime_input_bypass_and_corrupt_event_fail_closed(tmp_path: Path) -> None:
    """Malformed commands and persisted payloads do not escape strict adapters."""
    scenario = _build_event_scenario(tmp_path)
    with pytest.raises(InvalidInputError):
        scenario.repository.read_task_events_after(cast("ReadTaskEvents", object()))

    with open_write_transaction(scenario.repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE task_events SET payload_json = '[]' WHERE id = ?",
            ("evt_target_created",),
        )
    with pytest.raises(StorageUnavailableError):
        _read(scenario.repository, scenario.target.uid)
