"""Command-specific CLI object serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from workaholic.cli.envelopes import normalize_json_value
from workaholic.domain import derive_task_readiness

if TYPE_CHECKING:
    from workaholic.cli.envelopes import JsonValue
    from workaholic.domain import (
        Instance,
        Project,
        ProjectGrant,
        Subject,
        Task,
        TaskEvent,
        TaskReadiness,
        TaskResult,
        WorkspaceBinding,
    )
    from workaholic.session import (
        ContextResult,
        StatusResult,
        TaskDetails,
        TaskMutationResult,
        TaskPage,
    )

_CONTEXT_FILENAME = ".workaholic.env"


def instance_data(instance: Instance) -> dict[str, JsonValue]:
    """Serialize one Instance using the closed Phase 1 CLI shape.

    Args:
        instance: Validated domain Instance.

    Returns:
        Public Instance data.

    """
    return {"id": str(instance.id)}


def project_data(project: Project) -> dict[str, JsonValue]:
    """Serialize one Project using the closed Phase 2 CLI shape.

    Args:
        project: Validated domain Project.

    Returns:
        Public Project data.

    """
    return {
        "id": str(project.id),
        "key": project.key,
        "name": project.name,
    }


def grant_data(grant: ProjectGrant) -> dict[str, JsonValue]:
    """Serialize one Project grant returned by Project creation.

    Args:
        grant: Validated Project grant.

    Returns:
        Public grant data.

    """
    return {
        "subject_id": str(grant.subject_id),
        "project_id": str(grant.project_id),
        "role": grant.role.value,
    }


def subject_data(
    subject: Subject,
    grant: ProjectGrant,
) -> dict[str, JsonValue]:
    """Serialize one Subject and its selected-Project role.

    Args:
        subject: Validated selected Subject.
        grant: Subject's validated Project grant.

    Returns:
        Public Subject data with authorization context.

    """
    return {
        "id": str(subject.id),
        "kind": subject.kind.value,
        "display_name": subject.display_name,
        "is_instance_admin": subject.is_instance_admin,
        "project_role": grant.role.value,
    }


def status_data(result: StatusResult) -> dict[str, JsonValue]:
    """Serialize authoritative embedded status.

    Args:
        result: Validated status result.

    Returns:
        Closed Phase 2 status object.

    """
    return {
        "mode": result.mode,
        "profile": result.profile,
        "schema_version": result.schema_version,
        "instance": instance_data(result.instance),
        "project": project_data(result.project),
        "subject": subject_data(result.subject, result.grant),
    }


def context_data(result: ContextResult) -> dict[str, JsonValue]:
    """Serialize effective context without private profile or storage details.

    Args:
        result: Validated effective-context result.

    Returns:
        Closed Phase 2 effective-context object.

    """
    return {
        "mode": result.mode,
        "profile": result.profile,
        "schema_version": result.schema_version,
        "instance": instance_data(result.instance),
        "project": project_data(result.project),
        "workspace_root": (
            None if result.workspace_root is None else str(result.workspace_root)
        ),
        "subject": subject_data(result.subject, result.grant),
        "context_source": (
            None if result.context_source is None else str(result.context_source)
        ),
    }


def task_data(task: Task) -> dict[str, JsonValue]:
    """Serialize one Task's complete stored Phase 3 definition.

    Args:
        task: Validated domain Task.

    Returns:
        Public Task data without a derived readiness projection.

    """
    return {
        "uid": str(task.uid),
        "project_id": str(task.project_id),
        "number": task.number,
        "key": task.key,
        "title": task.title,
        "objective": task.objective,
        "state": task.state.value,
        "priority": task.priority,
        "available_at": (
            None
            if task.available_at is None
            else normalize_json_value(task.available_at)
        ),
        "approval": task.approval.value,
        "acceptance": [
            {
                "id": criterion.id,
                "text": criterion.text,
                "required": criterion.required,
            }
            for criterion in task.acceptance
        ],
        "context": [
            {
                "uri": reference.uri,
                "version": reference.version,
            }
            for reference in task.context
        ],
        "depends_on": [str(identifier) for identifier in task.depends_on],
        "blocking_reason": task.blocking_reason,
        "current_result_id": (
            None if task.current_result_id is None else str(task.current_result_id)
        ),
        "version": task.version,
        "created_by": str(task.created_by),
        "created_at": normalize_json_value(task.created_at),
        "updated_at": normalize_json_value(task.updated_at),
    }


def task_with_readiness_data(
    task: Task,
    readiness: TaskReadiness,
) -> dict[str, JsonValue]:
    """Serialize one Task definition with its authoritative derived views.

    Args:
        task: Validated stored Task.
        readiness: Session-returned readiness projection for that Task.

    Returns:
        Complete shared Phase 3 Task object.

    """
    return {
        **task_data(task),
        "views": {
            "ready": readiness.ready,
            "running": readiness.running,
            "scheduled": readiness.scheduled,
            "stale": readiness.stale,
            "awaiting_review": readiness.awaiting_review,
        },
        "readiness_reasons": [reason.value for reason in readiness.reasons],
    }


def created_task_data(task: Task) -> dict[str, JsonValue]:
    """Serialize one newly committed dependency-free Task with readiness.

    Creation has no prerequisites or Attempts, and ``updated_at`` is the
    authoritative creation time, so this is the one mutation result whose
    readiness can be derived without another Session read or invented state.

    Args:
        task: Newly created Task returned by the Session.

    Returns:
        Complete shared Phase 3 Task object.

    """
    readiness = derive_task_readiness(
        task=task,
        prerequisites=(),
        now=task.updated_at,
    )
    return task_with_readiness_data(task, readiness)


def task_event_data(event: TaskEvent) -> dict[str, JsonValue]:
    """Serialize one attributable Phase 3 Human TaskEvent.

    Args:
        event: Validated domain event returned by a mutation.

    Returns:
        Complete public Human TaskEvent object.

    """
    return {
        "id": str(event.id),
        "cursor": event.cursor,
        "task_uid": str(event.task_uid),
        "project_id": str(event.project_id),
        "actor_subject_id": str(event.actor_subject_id),
        "actor_kind": "human",
        "attempt_id": None,
        "request_id": str(event.request_id),
        "type": event.event_type.value,
        "occurred_at": normalize_json_value(event.occurred_at),
        "payload": normalize_json_value(event.payload),
    }


def task_result_data(result: TaskResult) -> dict[str, JsonValue]:
    """Serialize one structured Task Result and its review disposition.

    Args:
        result: Validated retained Task Result.

    Returns:
        Complete public Result object.

    """
    review = result.review
    return {
        "id": str(result.id),
        "task_uid": str(result.task_uid),
        "submitted_by": str(result.submitted_by),
        "attempt_id": result.attempt_id,
        "submitted_at": normalize_json_value(result.submitted_at),
        "comment": result.comment,
        "summary": result.summary,
        "criteria": [
            {
                "criterion_id": outcome.criterion_id,
                "status": outcome.status.value,
                "evidence": outcome.evidence,
            }
            for outcome in result.criteria
        ],
        "artifacts": [
            {
                "uri": artifact.uri,
                "media_type": artifact.media_type,
                "sha256": artifact.sha256,
            }
            for artifact in result.artifacts
        ],
        "proposed_follow_ups": [
            {"title": follow_up.title} for follow_up in result.proposed_follow_ups
        ],
        "review": {
            "status": review.status.value,
            "reviewed_by": (
                None if review.reviewed_by is None else str(review.reviewed_by)
            ),
            "reviewed_at": (
                None
                if review.reviewed_at is None
                else normalize_json_value(review.reviewed_at)
            ),
            "comment": review.comment,
            "reason": review.reason,
        },
    }


def task_mutation_data(result: TaskMutationResult) -> dict[str, JsonValue]:
    """Serialize one optimistic Task mutation outcome.

    Args:
        result: Validated committed Task and event batch.

    Returns:
        Public mutation result object.

    """
    return {
        "task": task_data(result.task),
        "events": [task_event_data(event) for event in result.events],
    }


def task_page_data(page: TaskPage) -> dict[str, JsonValue]:
    """Serialize one view-bound Task page with aligned readiness.

    Args:
        page: Validated Session Task page.

    Returns:
        Public paginated Task collection.

    Raises:
        ValueError: If a caller supplies a page without aligned readiness.

    """
    if len(page.readiness) != len(page.tasks):
        message = "Phase 3 Task pages require aligned readiness."
        raise ValueError(message)
    return {
        "tasks": [
            task_with_readiness_data(task, readiness)
            for task, readiness in zip(page.tasks, page.readiness, strict=True)
        ],
        "next_cursor": page.next_cursor,
    }


def task_details_data(details: TaskDetails) -> dict[str, JsonValue]:
    """Serialize complete Task definition, readiness, and related records.

    Args:
        details: Validated Session Task details.

    Returns:
        Public complete Task detail object.

    """
    return {
        "task": task_with_readiness_data(details.task, details.readiness),
        "prerequisites": [task_data(task) for task in details.prerequisites],
        "current_result": (
            None
            if details.current_result is None
            else task_result_data(details.current_result)
        ),
    }


def task_summary(task: Task) -> str:
    """Render one safe deterministic Human Task summary.

    Args:
        task: Validated domain Task returned by a Session.

    Returns:
        Stable single-line Task summary with JSON-escaped title text.

    """
    rendered_title = json.dumps(task.title, ensure_ascii=False)
    return f"{task.key}\t{task.state.value}\tpriority={task.priority}\t{rendered_title}"


def task_details_summary(details: TaskDetails) -> str:
    """Render one compact Human Task definition and readiness summary.

    Args:
        details: Complete validated Task details.

    Returns:
        Stable multiline Human-readable summary.

    """
    readiness = "ready" if details.readiness.ready else "not-ready"
    reasons = ",".join(reason.value for reason in details.readiness.reasons) or "none"
    prerequisites = ",".join(task.key for task in details.prerequisites) or "none"
    return "\n".join(
        (
            task_summary(details.task),
            f"Version: {details.task.version}",
            f"Readiness: {readiness} ({reasons})",
            f"Prerequisites: {prerequisites}",
        )
    )


def workspace_data(
    binding: WorkspaceBinding,
    *,
    current_directory: Path,
) -> dict[str, JsonValue]:
    """Serialize the exact-directory Workspace as absolute paths.

    The durable Phase 1 binding stores ``.`` so it remains location-safe. The
    CLI resolves that marker against the command's current directory only
    after bootstrap has durably completed.

    Args:
        binding: Validated Workspace binding returned by the Session.
        current_directory: Exact directory in which the command is running.

    Returns:
        Public absolute Workspace and context-file paths.

    Raises:
        TypeError: If ``current_directory`` is not a Path.

    """
    candidate_directory: object = current_directory
    if not isinstance(candidate_directory, Path):
        message = "CLI current directory must be a Path."
        raise TypeError(message)
    root = (candidate_directory / binding.workspace_root).resolve()
    return {
        "root": str(root),
        "context_file": str(root / _CONTEXT_FILENAME),
    }
