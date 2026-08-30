"""Unit tests for strict transport-neutral Phase 3 Session requests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from workaholic.application import TaskListView, TaskResultInput, TaskUpdatePatch
from workaholic.domain import ArtifactReference, TaskId
from workaholic.session import (
    TaskAddDependencyRequest,
    TaskApproveRequest,
    TaskBlockRequest,
    TaskCancelRequest,
    TaskCreateRequest,
    TaskDetailsRequest,
    TaskEventsRequest,
    TaskListByViewRequest,
    TaskRejectRequest,
    TaskRemoveDependencyRequest,
    TaskSubmitRequest,
    TaskUnblockRequest,
    TaskUpdateRequest,
    WorkaholicSession,
)

_EXPECTED_VERSION = 3

type _MutationRequest = (
    TaskUpdateRequest
    | TaskBlockRequest
    | TaskUnblockRequest
    | TaskCancelRequest
    | TaskAddDependencyRequest
    | TaskRemoveDependencyRequest
    | TaskSubmitRequest
    | TaskApproveRequest
    | TaskRejectRequest
)


def _mutation_requests() -> tuple[_MutationRequest, ...]:
    """Build one valid request for every existing-Task mutation.

    Returns:
        Closed Phase 3 mutation request inventory.

    """
    return (
        TaskUpdateRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            patch=TaskUpdatePatch(title="Updated title"),
        ),
        TaskBlockRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            reason="Waiting for input.",
        ),
        TaskUnblockRequest(task="ACME-1", expected_version=_EXPECTED_VERSION),
        TaskCancelRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            reason="No longer required.",
        ),
        TaskAddDependencyRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            prerequisite="ACME-2",
        ),
        TaskRemoveDependencyRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            prerequisite="ACME-2",
        ),
        TaskSubmitRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            comment="Implemented manually.",
        ),
        TaskApproveRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            comment="Verified.",
        ),
        TaskRejectRequest(
            task="ACME-1",
            expected_version=_EXPECTED_VERSION,
            reason="Evidence is incomplete.",
        ),
    )


def test_every_existing_task_mutation_requires_one_positive_exact_version() -> None:
    """No Session mutation permits missing, zero, negative, or boolean versions."""
    requests = _mutation_requests()

    assert all(request.expected_version == _EXPECTED_VERSION for request in requests)
    for request in requests:
        request_type: type[BaseModel] = type(request)
        values = request.model_dump()
        values.pop("expected_version")
        with pytest.raises(ValidationError):
            request_type.model_validate(values)
        for invalid in (0, -1, True):
            with pytest.raises(ValidationError):
                request_type.model_validate(
                    {**request.model_dump(), "expected_version": invalid}
                )


@pytest.mark.parametrize(
    "forbidden",
    [
        "actor_subject_id",
        "attempt_id",
        "event_id",
        "occurred_at",
        "request_id",
        "result_id",
        "subject_id",
    ],
)
def test_mutation_requests_never_accept_trusted_generated_identity(
    forbidden: str,
) -> None:
    """Presentation input cannot inject application-owned attribution values."""
    values: dict[str, object] = {
        "task": "ACME-1",
        "expected_version": 1,
        "reason": "Waiting for input.",
        forbidden: (
            datetime(2026, 8, 1, tzinfo=UTC)
            if forbidden == "occurred_at"
            else "injected"
        ),
    }

    with pytest.raises(ValidationError, match="Extra inputs"):
        TaskBlockRequest.model_validate(values)


def test_update_and_result_content_are_independently_revalidated() -> None:
    """Constructed nested models cannot bypass Session validation."""
    update = TaskUpdateRequest.model_validate(
        {
            "task": "ACME-1",
            "expected_version": 1,
            "patch": {"priority": 80},
        }
    )
    submission = TaskSubmitRequest.model_validate(
        {
            "task": "ACME-1",
            "expected_version": 1,
            "result": {
                "summary": "Implemented and verified.",
                "artifacts": [{"uri": "workspace://repo/report.json"}],
            },
        }
    )

    assert update.patch == TaskUpdatePatch(priority=80)
    assert submission.result == TaskResultInput(
        summary="Implemented and verified.",
        artifacts=(ArtifactReference("workspace://repo/report.json"),),
    )
    invalid_patch = TaskUpdatePatch.model_construct()
    with pytest.raises(ValidationError):
        TaskUpdateRequest(
            task="ACME-1",
            expected_version=1,
            patch=invalid_patch,
        )


def test_task_selectors_and_optional_project_remain_presentation_independent() -> None:
    """Requests carry only Human key/UID intent and an optional Project key."""
    by_key = TaskDetailsRequest(task="ACME-7", project="ACME")
    by_id = TaskEventsRequest(task=TaskId("tsk_exact"), after=4, limit=25)

    assert by_key.task == "ACME-7"
    assert by_key.project == "ACME"
    assert by_id.task == TaskId("tsk_exact")
    assert by_id.after == 4
    assert by_id.limit == 25
    with pytest.raises(ValidationError):
        TaskDetailsRequest(task="ACME-1", project="acme")


def test_view_and_event_queries_enforce_closed_bounds_and_selection() -> None:
    """View and event pages retain strict cursor and selection contracts."""
    ready = TaskListByViewRequest.model_validate(
        {"view": "ready", "project": "ACME", "limit": 50}
    )
    instance_done = TaskListByViewRequest(
        view=TaskListView.DONE,
        all_projects=True,
    )

    assert ready.view is TaskListView.READY
    assert ready.limit == 50
    assert instance_done.view is TaskListView.DONE
    for values in (
        {"project": "ACME", "all_projects": True},
        {"view": "running"},
        {"limit": 0},
        {"limit": 501},
        {"all_projects": 1},
    ):
        with pytest.raises(ValidationError):
            TaskListByViewRequest.model_validate(values)
    for values in (
        {"task": "ACME-1", "after": -1},
        {"task": "ACME-1", "after": True},
        {"task": "ACME-1", "limit": 0},
        {"task": "ACME-1", "limit": 501},
    ):
        with pytest.raises(ValidationError):
            TaskEventsRequest.model_validate(values)


def test_task_creation_rejects_loose_structured_runtime_values() -> None:
    """Task definitions accept only closed, bounded acceptance and context shapes."""
    invalid_values = (
        {"title": "Task", "approval": 1},
        {"title": "Task", "acceptance": object()},
        {"title": "Task", "acceptance": (object(),)},
        {
            "title": "Task",
            "acceptance": (
                {"id": "ac_done", "text": "Done", "required": True},
                {"id": "ac_done", "text": "Again", "required": False},
            ),
        },
        {"title": "Task", "context": object()},
        {"title": "Task", "context": (object(),)},
        {
            "title": "Task",
            "context": (
                {"uri": "workspace://repo/file", "version": None},
                {"uri": "workspace://repo/file", "version": None},
            ),
        },
        {"title": "Task", "acceptance": [object()] * 101},
    )
    for values in invalid_values:
        with pytest.raises(ValidationError):
            TaskCreateRequest.model_validate(values)


def test_task_view_rejects_non_string_runtime_value() -> None:
    """Task view parsing accepts only its enum or exact serialized string."""
    with pytest.raises(ValidationError):
        TaskListByViewRequest.model_validate({"view": 1})


def test_requests_are_frozen_and_protocol_exposes_complete_phase_three_surface() -> (
    None
):
    """Callers see immutable requests and one complete cumulative Session port."""
    request = TaskCancelRequest(task="ACME-1", expected_version=1)
    with pytest.raises(ValidationError):
        request.reason = "Changed"

    operations = (
        "update_task",
        "block_task",
        "unblock_task",
        "cancel_task",
        "add_task_dependency",
        "remove_task_dependency",
        "submit_human_result",
        "approve_result",
        "reject_result",
        "get_task_details",
        "list_tasks_by_view",
        "read_task_events",
    )
    assert all(callable(getattr(WorkaholicSession, name, None)) for name in operations)
