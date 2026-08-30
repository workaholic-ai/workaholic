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
        TaskAttempt,
        TaskClaim,
        TaskEvent,
        TaskReadiness,
        TaskResult,
        TokenSummary,
        WorkspaceBinding,
    )
    from workaholic.session import (
        AuditEventPage,
        AuditEventResult,
        ContextResult,
        CredentialLogoutResult,
        CurrentIdentityResult,
        ProjectGrantPage,
        ProjectGrantResult,
        StatusResult,
        SubjectPage,
        SubjectResult,
        TaskClaimResult,
        TaskDetails,
        TaskEventPage,
        TaskEventResult,
        TaskMutationResult,
        TaskPage,
        TaskProgressResult,
        TaskSubmissionResult,
        TokenPage,
        TokenResult,
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


def identity_subject_data(subject: Subject) -> dict[str, JsonValue]:
    """Serialize one complete public Phase 5 Subject.

    Args:
        subject: Validated authenticated or administratively visible Subject.

    Returns:
        Closed public Subject object without credential material.

    """
    return {
        "id": str(subject.id),
        "instance_id": str(subject.instance_id),
        "kind": subject.kind.value,
        "handle": subject.handle,
        "display_name": subject.display_name,
        "enabled": subject.enabled,
        "is_instance_admin": subject.is_instance_admin,
        "version": subject.version,
        "created_by": str(subject.created_by),
        "created_at": normalize_json_value(subject.created_at),
        "updated_at": normalize_json_value(subject.updated_at),
    }


def token_summary_data(token: TokenSummary) -> dict[str, JsonValue]:
    """Serialize one non-secret public Token lifecycle projection.

    Args:
        token: Validated Token metadata at authoritative time.

    Returns:
        Closed Token object without a raw Token or digest.

    """
    return {
        "id": str(token.id),
        "subject_id": str(token.subject_id),
        "status": token.status.value,
        "created_by": str(token.created_by),
        "created_at": normalize_json_value(token.created_at),
        "activated_at": (
            None
            if token.activated_at is None
            else normalize_json_value(token.activated_at)
        ),
        "expires_at": normalize_json_value(token.expires_at),
        "revoked_at": (
            None if token.revoked_at is None else normalize_json_value(token.revoked_at)
        ),
        "revoked_by": None if token.revoked_by is None else str(token.revoked_by),
    }


def current_identity_data(result: CurrentIdentityResult) -> dict[str, JsonValue]:
    """Serialize one authenticated Subject and active Token.

    Args:
        result: Validated current identity outcome.

    Returns:
        Exact Phase 5 ``whoami`` data shape.

    """
    return {
        "subject": identity_subject_data(result.subject),
        "token": token_summary_data(result.token),
    }


def current_identity_summary(result: CurrentIdentityResult) -> str:
    """Render a concise non-secret current identity summary.

    Args:
        result: Validated current identity outcome.

    Returns:
        Stable Human-readable Subject and Token metadata.

    """
    return "\n".join(
        (
            f"Subject: {result.subject.handle} ({result.subject.id})",
            f"Kind: {result.subject.kind.value}",
            f"Token: {result.token.id}\tstatus={result.token.status.value}",
        )
    )


def credential_logout_data(
    result: CredentialLogoutResult,
) -> dict[str, JsonValue]:
    """Serialize one local Human credential-removal outcome.

    Args:
        result: Validated selected-profile logout result.

    Returns:
        Closed local credential state.

    """
    return {
        "profile": result.profile,
        "credential_stored": result.credential_stored,
    }


def subject_result_data(result: SubjectResult) -> dict[str, JsonValue]:
    """Serialize one Subject administration outcome.

    Args:
        result: Validated Subject result.

    Returns:
        Complete public Subject object.

    """
    return identity_subject_data(result.subject)


def subject_page_data(result: SubjectPage) -> dict[str, JsonValue]:
    """Serialize one handle-ordered Subject page.

    Args:
        result: Validated Subject page.

    Returns:
        Closed Subject pagination data.

    """
    return {
        "subjects": [identity_subject_data(item) for item in result.subjects],
        "next_cursor": result.next_cursor,
    }


def identity_grant_data(grant: ProjectGrant) -> dict[str, JsonValue]:
    """Serialize one complete public Phase 5 ProjectGrant.

    Args:
        grant: Validated current or revoked grant snapshot.

    Returns:
        Closed public ProjectGrant object.

    """
    return {
        "subject_id": str(grant.subject_id),
        "project_id": str(grant.project_id),
        "role": grant.role.value,
        "version": grant.version,
        "granted_by": str(grant.granted_by),
        "created_at": normalize_json_value(grant.created_at),
        "updated_at": normalize_json_value(grant.updated_at),
    }


def project_grant_result_data(
    result: ProjectGrantResult,
) -> dict[str, JsonValue]:
    """Serialize one ProjectGrant mutation result.

    Args:
        result: Validated ProjectGrant result.

    Returns:
        Complete public ProjectGrant object.

    """
    return identity_grant_data(result.grant)


def project_grant_page_data(result: ProjectGrantPage) -> dict[str, JsonValue]:
    """Serialize one Project-scoped grant page.

    Args:
        result: Validated grant page.

    Returns:
        Closed ProjectGrant pagination data.

    """
    return {
        "grants": [identity_grant_data(item) for item in result.grants],
        "next_cursor": result.next_cursor,
    }


def token_result_data(result: TokenResult) -> dict[str, JsonValue]:
    """Serialize one non-secret Token lifecycle outcome.

    Args:
        result: Validated Token result.

    Returns:
        Complete public Token metadata without credential material.

    """
    return token_summary_data(result.token)


def token_page_data(result: TokenPage) -> dict[str, JsonValue]:
    """Serialize one creation-ordered non-secret Token page.

    Args:
        result: Validated Token page.

    Returns:
        Closed Token pagination data.

    """
    return {
        "tokens": [token_summary_data(item) for item in result.tokens],
        "next_cursor": result.next_cursor,
    }


def audit_event_data(event: AuditEventResult) -> dict[str, JsonValue]:
    """Serialize one attributable administrative AuditEvent.

    Args:
        event: Validated administrative event.

    Returns:
        Closed public AuditEvent object.

    """
    payload = normalize_json_value(event.payload)
    if not isinstance(payload, dict):
        raise TypeError
    return {
        "cursor": event.cursor,
        "id": str(event.id),
        "instance_id": str(event.instance_id),
        "actor_subject_id": str(event.actor_subject_id),
        "actor_kind": event.actor_kind.value,
        "actor_token_id": (
            None if event.actor_token_id is None else str(event.actor_token_id)
        ),
        "request_id": str(event.request_id),
        "event_type": event.event_type.value,
        "occurred_at": normalize_json_value(event.occurred_at),
        "payload": payload,
    }


def audit_event_page_data(result: AuditEventPage) -> dict[str, JsonValue]:
    """Serialize one ascending administrative AuditEvent page.

    Args:
        result: Validated administrative event page.

    Returns:
        Closed polling page with its durable cursor.

    """
    return {
        "events": [audit_event_data(event) for event in result.events],
        "next_cursor": result.next_cursor,
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
    """Serialize one attributable TaskEvent.

    Args:
        event: Validated domain event returned by a mutation.

    Returns:
        Complete public TaskEvent object.

    """
    return {
        "id": str(event.id),
        "cursor": event.cursor,
        "task_uid": str(event.task_uid),
        "project_id": str(event.project_id),
        "actor_subject_id": str(event.actor_subject_id),
        "actor_kind": "agent" if event.attempt_id is not None else "human",
        "attempt_id": (None if event.attempt_id is None else str(event.attempt_id)),
        "request_id": str(event.request_id),
        "type": event.event_type.value,
        "occurred_at": normalize_json_value(event.occurred_at),
        "payload": normalize_json_value(event.payload),
    }


def task_claim_data(claim: TaskClaim) -> dict[str, JsonValue]:
    """Serialize one current exclusive Task Claim.

    Args:
        claim: Validated current Claim.

    Returns:
        Complete public Claim object.

    """
    return {
        "task_uid": str(claim.task_uid),
        "task_key": claim.task_key,
        "subject_id": str(claim.subject_id),
        "attempt_id": (None if claim.attempt_id is None else str(claim.attempt_id)),
        "claimed_at": normalize_json_value(claim.claimed_at),
        "lease_expires_at": normalize_json_value(claim.lease_expires_at),
    }


def task_attempt_data(attempt: TaskAttempt) -> dict[str, JsonValue]:
    """Serialize one Agent execution Attempt.

    Args:
        attempt: Validated active or terminal Attempt.

    Returns:
        Complete public Attempt object.

    """
    return {
        "id": str(attempt.id),
        "task_uid": str(attempt.task_uid),
        "subject_id": str(attempt.subject_id),
        "status": attempt.status.value,
        "lease_expires_at": normalize_json_value(attempt.lease_expires_at),
        "started_at": normalize_json_value(attempt.started_at),
        "ended_at": (
            None if attempt.ended_at is None else normalize_json_value(attempt.ended_at)
        ),
    }


def task_claim_result_data(result: TaskClaimResult) -> dict[str, JsonValue]:
    """Serialize one Claim acquisition, renewal, heartbeat, or release.

    Args:
        result: Validated Task ownership operation result.

    Returns:
        Closed Task, Claim, Attempt, and event result shape.

    """
    return {
        "task": task_data(result.task),
        "claim": None if result.claim is None else task_claim_data(result.claim),
        "attempt": (
            None if result.attempt is None else task_attempt_data(result.attempt)
        ),
        "events": [task_event_data(event) for event in result.events],
    }


def task_claim_summary(result: TaskClaimResult) -> str:
    """Render one concise ownership result without inventing an Attempt.

    Args:
        result: Validated Task ownership operation result.

    Returns:
        Stable multiline Human-readable ownership summary.

    """
    lines = [task_summary(result.task)]
    if result.claim is None:
        lines.append("Claim: released")
    else:
        expires_at = normalize_json_value(result.claim.lease_expires_at)
        lines.append(f"Claim: {result.claim.subject_id}\tlease_expires_at={expires_at}")
    if result.attempt is not None:
        lines.append(
            f"Attempt: {result.attempt.id}\tstatus={result.attempt.status.value}"
        )
    return "\n".join(lines)


def task_progress_data(result: TaskProgressResult) -> dict[str, JsonValue]:
    """Serialize one current Agent progress operation result.

    Args:
        result: Validated Task, ownership, and ordered progress events.

    Returns:
        Closed Task, Claim, Attempt, and events result shape.

    """
    return {
        "task": task_data(result.task),
        "claim": task_claim_data(result.claim),
        "attempt": task_attempt_data(result.attempt),
        "events": [task_event_data(event) for event in result.events],
    }


def task_progress_summary(result: TaskProgressResult) -> str:
    """Render one concise Agent progress outcome.

    Args:
        result: Validated Task progress result.

    Returns:
        Stable Human-readable ownership and event summary.

    """
    return "\n".join(
        (
            task_summary(result.task),
            f"Attempt: {result.attempt.id}\tstatus={result.attempt.status.value}",
            f"Progress events: {len(result.events)}",
        )
    )


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
        "attempt_id": (
            str(result.attempt_id) if result.attempt_id is not None else None
        ),
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


def task_submission_data(result: TaskSubmissionResult) -> dict[str, JsonValue]:
    """Serialize one Human submission or review transition outcome.

    Args:
        result: Validated Task, retained Result, and attributable event batch.

    Returns:
        Complete public submission or review result object.

    """
    return {
        "task": task_data(result.task),
        "result": task_result_data(result.result),
        "events": [task_event_data(event) for event in result.events],
    }


def agent_submission_data(result: TaskSubmissionResult) -> dict[str, JsonValue]:
    """Serialize one Attempt-backed Agent Result submission.

    Args:
        result: Validated Agent submission with a terminal Attempt.

    Returns:
        Closed Task, Result, released Claim, Attempt, and event shape.

    Raises:
        ValueError: If the result does not contain an Agent Attempt.

    """
    if result.attempt is None or result.result.attempt_id is None:
        message = "Agent submission requires terminal Attempt data."
        raise ValueError(message)
    return {
        "task": task_data(result.task),
        "result": task_result_data(result.result),
        "claim": None,
        "attempt": task_attempt_data(result.attempt),
        "events": [task_event_data(event) for event in result.events],
    }


def agent_submission_summary(result: TaskSubmissionResult) -> str:
    """Render one concise Attempt-backed Agent submission outcome.

    Args:
        result: Validated Agent submission with a terminal Attempt.

    Returns:
        Stable Human-readable Task, Result, and Attempt disposition.

    Raises:
        ValueError: If the result does not contain an Agent Attempt.

    """
    if result.attempt is None:
        message = "Agent submission requires terminal Attempt data."
        raise ValueError(message)
    return "\n".join(
        (
            task_submission_summary(result),
            f"Attempt: {result.attempt.id}\tstatus={result.attempt.status.value}",
        )
    )


def task_submission_summary(result: TaskSubmissionResult) -> str:
    """Render one concise Human submission or review outcome.

    Args:
        result: Validated Task submission or review transition outcome.

    Returns:
        Stable two-line Task and Result disposition summary.

    """
    return "\n".join(
        (
            task_summary(result.task),
            f"Result: {result.result.id}\treview={result.result.review.status.value}",
        )
    )


def task_event_result_data(event: TaskEventResult) -> dict[str, JsonValue]:
    """Serialize one flat attributable TaskEvent history record.

    Args:
        event: Session-returned event containing complete actor attribution.

    Returns:
        Complete public TaskEvent object.

    """
    return {
        "id": str(event.id),
        "cursor": event.cursor,
        "task_uid": str(event.task_uid),
        "project_id": str(event.project_id),
        "actor_subject_id": str(event.actor_subject_id),
        "actor_kind": event.actor_kind.value,
        "attempt_id": str(event.attempt_id) if event.attempt_id is not None else None,
        "request_id": str(event.request_id),
        "type": event.event_type.value,
        "occurred_at": normalize_json_value(event.occurred_at),
        "payload": normalize_json_value(event.payload),
    }


def task_event_page_data(page: TaskEventPage) -> dict[str, JsonValue]:
    """Serialize one polling-safe TaskEvent snapshot page.

    Args:
        page: Validated strictly ordered Session event page.

    Returns:
        Public event collection and resumable Instance cursor.

    """
    return {
        "events": [task_event_result_data(event) for event in page.events],
        "next_cursor": page.next_cursor,
    }


def task_event_summary(event: TaskEventResult) -> str:
    """Render one safe deterministic Human TaskEvent line.

    Args:
        event: Session-returned attributable event.

    Returns:
        Stable tab-delimited event summary with escaped JSON payload.

    """
    occurred_at = normalize_json_value(event.occurred_at)
    payload = json.dumps(
        normalize_json_value(event.payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        f"{event.cursor}\t{event.event_type.value}\t{occurred_at}"
        f"\tactor={event.actor_subject_id}\tpayload={payload}"
    )


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
