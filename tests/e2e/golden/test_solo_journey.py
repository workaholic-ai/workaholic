"""Golden specification for the complete local Human workflow."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    require_array,
    require_integer,
    require_object,
    require_string,
    require_success,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.golden import GoldenJourneyRunner, JsonObject

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
]

_TASK_FIELDS = {
    "uid",
    "project_id",
    "number",
    "key",
    "title",
    "objective",
    "state",
    "priority",
    "available_at",
    "approval",
    "acceptance",
    "context",
    "depends_on",
    "blocking_reason",
    "current_result_id",
    "version",
    "created_by",
    "created_at",
    "updated_at",
}
_TASK_VIEW_FIELDS = _TASK_FIELDS | {"views", "readiness_reasons"}
_EVENT_FIELDS = {
    "id",
    "cursor",
    "task_uid",
    "project_id",
    "actor_subject_id",
    "actor_kind",
    "attempt_id",
    "request_id",
    "type",
    "occurred_at",
    "payload",
}


# Keep the exit gate linear so its assertions preserve the exact user-visible order.
def test_human_completes_dependency_bound_reviewed_workflow(  # noqa: PLR0915
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """The Phase 3 Human lifecycle works only through fresh real CLI processes."""
    workspace = tmp_path / "solo-workspace"
    workspace.mkdir()

    up_data = _json_success(
        golden_runner,
        ("up", "--project-key", "ACME", "--idempotency-key", "solo-up"),
        workspace=workspace,
        context="bootstrap",
    )
    assert up_data.keys() == {"instance", "project", "subject", "workspace"}
    project = require_object(up_data["project"], context="bootstrap Project")
    subject = require_object(up_data["subject"], context="bootstrap Subject")
    project_id = require_string(project["id"], context="Project ID")
    subject_id = require_string(subject["id"], context="Human Subject ID")
    assert project["key"] == "ACME"
    assert subject["kind"] == "human"

    prerequisite = _created_task(
        _json_success(
            golden_runner,
            (
                "task",
                "add",
                "Prepare foundation",
                "--priority",
                "80",
                "--idempotency-key",
                "solo-prerequisite",
            ),
            workspace=workspace,
            context="prerequisite creation",
        ),
        context="prerequisite",
    )
    assert prerequisite["key"] == "ACME-1"
    assert prerequisite["project_id"] == project_id
    assert prerequisite["created_by"] == subject_id
    assert prerequisite["state"] == "open"
    assert prerequisite["version"] == 1

    definition = {
        "acceptance": [
            {
                "id": "ac_verified",
                "text": "The reviewed implementation is verified.",
                "required": True,
            }
        ],
        "context": [
            {
                "uri": "https://example.test/specification",
                "version": "v1",
            }
        ],
    }
    reviewed = _created_task(
        _json_success(
            golden_runner,
            (
                "task",
                "add",
                "Deliver reviewed change",
                "--priority",
                "90",
                "--approval",
                "human",
                "--input-file",
                "-",
                "--idempotency-key",
                "solo-reviewed",
            ),
            workspace=workspace,
            input_text=json.dumps(definition),
            context="reviewed Task creation",
        ),
        context="reviewed Task",
    )
    reviewed_uid = require_string(reviewed["uid"], context="reviewed Task UID")
    prerequisite_uid = require_string(
        prerequisite["uid"],
        context="prerequisite Task UID",
    )
    assert reviewed["key"] == "ACME-2"
    assert reviewed["approval"] == "human"
    assert reviewed["acceptance"] == definition["acceptance"]
    assert reviewed["context"] == definition["context"]
    assert reviewed["version"] == 1

    dependency_data = _json_success(
        golden_runner,
        (
            "task",
            "add-dependency",
            "ACME-2",
            "ACME-1",
            "--expected-version",
            "1",
            "--idempotency-key",
            "solo-dependency",
        ),
        workspace=workspace,
        context="dependency addition",
    )
    dependency_task = require_object(
        dependency_data["task"],
        context="dependency mutation Task",
    )
    assert dependency_task.keys() == _TASK_FIELDS
    assert dependency_task["depends_on"] == [prerequisite_uid]
    assert dependency_task["version"] == 2
    _assert_event_batch(
        dependency_data,
        expected_types=("task_updated",),
        task_uid=reviewed_uid,
        subject_id=subject_id,
    )

    update_data = _json_success(
        golden_runner,
        (
            "task",
            "update",
            "ACME-2",
            "--objective",
            "Deliver and verify the reviewed change.",
            "--expected-version",
            "2",
            "--idempotency-key",
            "solo-reviewed-update",
        ),
        workspace=workspace,
        context="reviewed Task update",
    )
    updated_task = require_object(update_data["task"], context="updated Task")
    assert updated_task["objective"] == "Deliver and verify the reviewed change."
    assert updated_task["version"] == 3
    _assert_event_batch(
        update_data,
        expected_types=("task_updated",),
        task_uid=reviewed_uid,
        subject_id=subject_id,
    )

    waiting_details = _shown_task(
        golden_runner,
        "ACME-2",
        workspace=workspace,
        context="dependency-blocked details",
    )
    waiting_task = require_object(
        waiting_details["task"],
        context="dependency-blocked Task",
    )
    assert waiting_task["views"] == {
        "ready": False,
        "running": False,
        "scheduled": False,
        "stale": False,
        "awaiting_review": False,
    }
    assert waiting_task["readiness_reasons"] == ["unsatisfied_dependency"]
    assert [
        require_object(item, context="prerequisite detail")["uid"]
        for item in require_array(
            waiting_details["prerequisites"],
            context="prerequisite details",
        )
    ] == [prerequisite_uid]

    ready_before = _task_page(
        _json_success(
            golden_runner,
            ("task", "list", "--view", "ready"),
            workspace=workspace,
            context="initial ready view",
        ),
        context="initial ready view",
    )
    assert [item["key"] for item in ready_before] == ["ACME-1"]

    blocked = golden_runner.cli(
        (
            "task",
            "block",
            "ACME-1",
            "--reason",
            "Verify the foundation manually.",
            "--expected-version",
            "1",
            "--idempotency-key",
            "solo-block",
            "--non-interactive",
        ),
        cwd=workspace,
    )
    assert blocked.returncode == 0
    assert blocked.stderr == ""
    assert blocked.stdout == ('ACME-1\tblocked\tpriority=80\t"Prepare foundation"\n')

    unblocked_data = _json_success(
        golden_runner,
        (
            "task",
            "unblock",
            "ACME-1",
            "--expected-version",
            "2",
            "--idempotency-key",
            "solo-unblock",
        ),
        workspace=workspace,
        context="prerequisite unblock",
    )
    unblocked_task = require_object(
        unblocked_data["task"],
        context="unblocked prerequisite",
    )
    assert unblocked_task["state"] == "open"
    assert unblocked_task["version"] == 3

    prerequisite_submission = _json_success(
        golden_runner,
        (
            "task",
            "submit",
            "ACME-1",
            "--comment",
            "Foundation prepared manually.",
            "--expected-version",
            "3",
            "--idempotency-key",
            "solo-prerequisite-submit",
        ),
        workspace=workspace,
        context="prerequisite submission",
    )
    completed_prerequisite = require_object(
        prerequisite_submission["task"],
        context="completed prerequisite",
    )
    prerequisite_result = require_object(
        prerequisite_submission["result"],
        context="prerequisite Result",
    )
    assert completed_prerequisite["state"] == "done"
    assert completed_prerequisite["version"] == 4
    assert prerequisite_result["attempt_id"] is None
    _assert_event_batch(
        prerequisite_submission,
        expected_types=("result_submitted", "task_completed"),
        task_uid=prerequisite_uid,
        subject_id=subject_id,
    )

    ready_details = _shown_task(
        golden_runner,
        "ACME-2",
        workspace=workspace,
        context="newly ready reviewed Task",
    )
    ready_task = require_object(ready_details["task"], context="ready reviewed Task")
    assert ready_task["version"] == 3
    ready_views = require_object(ready_task["views"], context="ready Task views")
    assert ready_views["ready"] is True
    assert ready_task["readiness_reasons"] == []
    ready_after = _task_page(
        _json_success(
            golden_runner,
            ("task", "list", "--view", "ready"),
            workspace=workspace,
            context="ready view after prerequisite completion",
        ),
        context="ready view after prerequisite completion",
    )
    assert [item["key"] for item in ready_after] == ["ACME-2"]

    submitted_content = {
        "summary": "Implemented and verified the reviewed change.",
        "criteria": [
            {
                "criterion_id": "ac_verified",
                "status": "passed",
                "evidence": "The golden and regression suites pass.",
            }
        ],
        "artifacts": [
            {
                "uri": "workspace://repo/reports/result.md",
                "media_type": "text/markdown",
                "sha256": None,
            }
        ],
        "proposed_follow_ups": [{"title": "Document the reviewed workflow"}],
    }
    reviewed_submission = _json_success(
        golden_runner,
        (
            "task",
            "submit",
            "ACME-2",
            "--comment",
            "Ready for Human review.",
            "--result-file",
            "-",
            "--expected-version",
            "3",
            "--idempotency-key",
            "solo-reviewed-submit",
        ),
        workspace=workspace,
        input_text=json.dumps(submitted_content),
        context="reviewed Result submission",
    )
    pending_task = require_object(
        reviewed_submission["task"],
        context="pending-review Task",
    )
    pending_result = require_object(
        reviewed_submission["result"],
        context="pending Result",
    )
    pending_review = require_object(
        pending_result["review"],
        context="pending Result review",
    )
    result_id = require_string(pending_result["id"], context="Result ID")
    assert pending_task["state"] == "review"
    assert pending_task["version"] == 4
    assert pending_task["current_result_id"] == result_id
    assert pending_result["attempt_id"] is None
    assert pending_result["comment"] == "Ready for Human review."
    for field, value in submitted_content.items():
        assert pending_result[field] == value
    assert pending_review == {
        "status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "comment": None,
        "reason": None,
    }
    _assert_event_batch(
        reviewed_submission,
        expected_types=("result_submitted",),
        task_uid=reviewed_uid,
        subject_id=subject_id,
    )

    review_page = _task_page(
        _json_success(
            golden_runner,
            ("task", "list", "--view", "review"),
            workspace=workspace,
            context="review view",
        ),
        context="review view",
    )
    assert [item["key"] for item in review_page] == ["ACME-2"]
    review_views = require_object(
        review_page[0]["views"],
        context="review Task views",
    )
    assert review_views["awaiting_review"] is True

    approval = _json_success(
        golden_runner,
        (
            "task",
            "approve",
            "ACME-2",
            "--comment",
            "Evidence accepted.",
            "--expected-version",
            "4",
            "--idempotency-key",
            "solo-reviewed-approve",
        ),
        workspace=workspace,
        context="Result approval",
    )
    approved_task = require_object(approval["task"], context="approved Task")
    approved_result = require_object(approval["result"], context="approved Result")
    approved_review = require_object(
        approved_result["review"],
        context="approved Result review",
    )
    assert approved_task["state"] == "done"
    assert approved_task["version"] == 5
    assert approved_task["current_result_id"] == result_id
    assert approved_result["id"] == result_id
    assert approved_result["attempt_id"] is None
    for field, value in submitted_content.items():
        assert approved_result[field] == value
    assert approved_review["status"] == "approved"
    assert approved_review["reviewed_by"] == subject_id
    assert approved_review["comment"] == "Evidence accepted."
    assert approved_review["reason"] is None
    require_string(approved_review["reviewed_at"], context="review timestamp")
    approval_events = _assert_event_batch(
        approval,
        expected_types=("review_approved", "task_completed"),
        task_uid=reviewed_uid,
        subject_id=subject_id,
    )
    assert approval_events[0]["request_id"] == approval_events[1]["request_id"]

    final_details = _shown_task(
        golden_runner,
        reviewed_uid,
        workspace=workspace,
        context="persisted approved details",
    )
    final_task = require_object(final_details["task"], context="persisted done Task")
    assert final_task["state"] == "done"
    assert final_task["version"] == 5
    assert final_details["current_result"] == approved_result

    prerequisite_events = _event_history(
        golden_runner,
        "ACME-1",
        workspace=workspace,
    )
    reviewed_events = _event_history(
        golden_runner,
        "ACME-2",
        workspace=workspace,
    )
    assert [event["type"] for event in prerequisite_events] == [
        "task_created",
        "task_blocked",
        "task_unblocked",
        "result_submitted",
        "task_completed",
    ]
    assert [event["type"] for event in reviewed_events] == [
        "task_created",
        "task_updated",
        "task_updated",
        "result_submitted",
        "review_approved",
        "task_completed",
    ]
    for history, expected_uid in (
        (prerequisite_events, prerequisite_uid),
        (reviewed_events, reviewed_uid),
    ):
        for event in history:
            assert event.keys() == _EVENT_FIELDS
            assert event["task_uid"] == expected_uid
            assert event["project_id"] == project_id
            assert event["actor_subject_id"] == subject_id
            assert event["actor_kind"] == "human"
            assert event["attempt_id"] is None
            require_string(event["request_id"], context="TaskEvent request ID")
            require_object(event["payload"], context="TaskEvent payload")
    for history in (prerequisite_events, reviewed_events):
        cursors = [
            require_integer(event["cursor"], context="TaskEvent cursor", minimum=1)
            for event in history
        ]
        assert cursors == sorted(cursors)
        assert len(cursors) == len(set(cursors))

    human_history = golden_runner.cli(
        ("task", "events", "ACME-2", "--non-interactive"),
        cwd=workspace,
    )
    assert human_history.returncode == 0
    assert human_history.stderr == ""
    for event_type in (
        "task_created",
        "task_updated",
        "result_submitted",
        "review_approved",
        "task_completed",
    ):
        assert f"\t{event_type}\t" in human_history.stdout
    assert human_history.stdout.endswith(
        f"Next cursor: {reviewed_events[-1]['cursor']}\n"
    )


def _json_success(
    runner: GoldenJourneyRunner,
    arguments: Sequence[str],
    *,
    workspace: Path,
    context: str,
    input_text: str | None = None,
) -> JsonObject:
    """Run one fresh JSON CLI process and require an object success payload.

    Args:
        runner: Real-process golden harness.
        arguments: CLI arguments excluding stable automation flags.
        workspace: Exact bound Workspace directory.
        context: Assertion label for the returned data.
        input_text: Optional explicit standard-input content.

    Returns:
        Validated command-specific success object.

    """
    result = runner.cli(
        (*arguments, "--json", "--non-interactive"),
        cwd=workspace,
        input_text=input_text,
    )
    return require_object(require_success(result), context=context)


def _created_task(data: JsonObject, *, context: str) -> JsonObject:
    """Extract and validate one Task-add response.

    Args:
        data: Validated Task-add success data.
        context: Task assertion label.

    Returns:
        Complete created Task object.

    """
    assert data.keys() == {"task"}
    task = require_object(data["task"], context=context)
    assert task.keys() == _TASK_VIEW_FIELDS
    return task


def _shown_task(
    runner: GoldenJourneyRunner,
    selector: str,
    *,
    workspace: Path,
    context: str,
) -> JsonObject:
    """Read complete Task details in a fresh process.

    Args:
        runner: Real-process golden harness.
        selector: Stable Task key or canonical UID.
        workspace: Exact bound Workspace directory.
        context: Assertion label for returned details.

    Returns:
        Validated Task-details object.

    """
    details = _json_success(
        runner,
        ("task", "show", selector),
        workspace=workspace,
        context=context,
    )
    assert details.keys() == {"task", "prerequisites", "current_result"}
    task = require_object(details["task"], context=f"{context} Task")
    assert task.keys() == _TASK_VIEW_FIELDS
    return details


def _task_page(data: JsonObject, *, context: str) -> list[JsonObject]:
    """Extract one complete Task page into validated objects.

    Args:
        data: Validated task-list success data.
        context: Assertion label for page items.

    Returns:
        Ordered Task objects.

    """
    assert data.keys() == {"tasks", "next_cursor"}
    raw_tasks = require_array(data["tasks"], context=context)
    tasks = [require_object(item, context=f"{context} Task") for item in raw_tasks]
    assert all(task.keys() == _TASK_VIEW_FIELDS for task in tasks)
    return tasks


def _assert_event_batch(
    data: JsonObject,
    *,
    expected_types: tuple[str, ...],
    task_uid: str,
    subject_id: str,
) -> list[JsonObject]:
    """Validate one mutation-owned attributable TaskEvent batch.

    Args:
        data: Mutation success object containing events.
        expected_types: Exact semantic event order.
        task_uid: Expected canonical Task identity.
        subject_id: Expected authenticated Human identity.

    Returns:
        Validated ordered event objects.

    """
    raw_events = require_array(data["events"], context="mutation events")
    events = [require_object(item, context="mutation TaskEvent") for item in raw_events]
    assert [event["type"] for event in events] == list(expected_types)
    for event in events:
        assert event.keys() == _EVENT_FIELDS
        assert event["task_uid"] == task_uid
        assert event["actor_subject_id"] == subject_id
        assert event["actor_kind"] == "human"
        assert event["attempt_id"] is None
    return events


def _event_history(
    runner: GoldenJourneyRunner,
    selector: str,
    *,
    workspace: Path,
) -> list[JsonObject]:
    """Read a complete TaskEvent history through bounded snapshot pages.

    Args:
        runner: Real-process golden harness.
        selector: Stable Task key or canonical UID.
        workspace: Exact bound Workspace directory.

    Returns:
        Complete strictly ordered TaskEvent objects.

    Raises:
        AssertionError: If pagination does not terminate within ten pages.

    """
    events: list[JsonObject] = []
    after = 0
    for _page_number in range(10):
        data = _json_success(
            runner,
            (
                "task",
                "events",
                selector,
                "--after",
                str(after),
                "--limit",
                "2",
            ),
            workspace=workspace,
            context="TaskEvent page",
        )
        assert data.keys() == {"events", "next_cursor"}
        page = require_array(data["events"], context="TaskEvent page records")
        next_cursor = require_integer(
            data["next_cursor"],
            context="TaskEvent next cursor",
            minimum=0,
        )
        if not page:
            assert next_cursor == after
            return events
        events.extend(
            require_object(item, context="TaskEvent history record") for item in page
        )
        assert next_cursor > after
        after = next_cursor
    message = "TaskEvent pagination did not terminate within ten bounded pages."
    raise AssertionError(message)
