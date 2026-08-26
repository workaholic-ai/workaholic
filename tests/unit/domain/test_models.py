"""Unit tests for immutable cumulative domain entities."""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ArtifactReference,
    AttemptId,
    ContextReference,
    CriterionOutcome,
    CriterionStatus,
    DomainValidationError,
    Instance,
    InstanceId,
    JsonScalar,
    JsonValue,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    ProposedFollowUp,
    RequestId,
    ResultId,
    ResultReview,
    ResultReviewStatus,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskResult,
    TaskState,
    WorkspaceBinding,
    build_task_key,
    require_phase_one_owner,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_DOMAIN_DIRECTORY = Path(__file__).parents[3] / "src" / "workaholic" / "domain"
_NOW = datetime(2026, 7, 30, 10, 30, tzinfo=UTC)


def _instance() -> Instance:
    """Build a valid Instance test entity.

    Returns:
        A valid immutable Instance.

    """
    return Instance(id=InstanceId("ins_local"), created_at=_NOW)


def _subject() -> Subject:
    """Build the valid Phase 1 bootstrap Subject.

    Returns:
        A valid enabled Human administrator.

    """
    return Subject(
        id=SubjectId("sub_local"),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=True,
        is_instance_admin=True,
    )


def _project() -> Project:
    """Build a valid Project test entity.

    Returns:
        A valid ACME Project.

    """
    return Project(
        id=ProjectId("prj_acme"),
        instance_id=InstanceId("ins_local"),
        key="ACME",
        name="Acme",
        created_at=_NOW,
    )


def _task() -> Task:
    """Build a valid Task test entity.

    Returns:
        A valid initial ACME Task.

    """
    return Task(
        uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        number=1,
        key="ACME-1",
        title="First task",
        objective="Complete the first task.",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _event(payload: Mapping[str, JsonValue] | None = None) -> TaskEvent:
    """Build a valid task-created event.

    Args:
        payload: Optional payload override.

    Returns:
        A valid immutable TaskEvent.

    """
    return TaskEvent(
        id=TaskEventId("evt_first"),
        cursor=1,
        task_uid=TaskId("tsk_first"),
        project_id=ProjectId("prj_acme"),
        actor_subject_id=SubjectId("sub_local"),
        request_id=RequestId("req_first"),
        event_type=TaskEventType.TASK_CREATED,
        occurred_at=_NOW,
        payload={"title": "First task"} if payload is None else payload,
    )


@pytest.mark.parametrize(
    ("entity", "attribute_name"),
    [
        (_instance(), "id"),
        (_subject(), "id"),
        (_project(), "id"),
        (
            ProjectGrant(
                subject_id=SubjectId("sub_local"),
                project_id=ProjectId("prj_acme"),
                role=ProjectRole.OWNER,
            ),
            "subject_id",
        ),
        (
            WorkspaceBinding(
                context_version=1,
                profile="local",
                instance_id=InstanceId("ins_local"),
                project_id=ProjectId("prj_acme"),
                project_key="ACME",
                workspace_root=".",
            ),
            "context_version",
        ),
        (_task(), "uid"),
        (_event(), "id"),
    ],
)
def test_domain_entities_are_frozen(entity: object, attribute_name: str) -> None:
    """All public domain entities reject attribute reassignment."""
    with pytest.raises(FrozenInstanceError):
        setattr(entity, attribute_name, None)


def test_instance_validates_identifier_and_utc_timestamp() -> None:
    """Instance construction rejects type-hint violations and non-UTC time."""
    with pytest.raises(DomainValidationError, match="Instance id"):
        Instance(id="ins_local", created_at=_NOW)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="timezone-aware UTC"):
        Instance(
            id=InstanceId("ins_local"),
            created_at=_NOW.replace(tzinfo=None),
        )


def test_subject_normalizes_name_and_validates_runtime_fields() -> None:
    """Subject construction trims names and rejects invalid enum and bool values."""
    subject = replace(_subject(), display_name="  Local operator  ")

    assert subject.display_name == "Local operator"
    with pytest.raises(DomainValidationError, match="Subject kind"):
        replace(_subject(), kind="human")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="must be a boolean"):
        replace(_subject(), enabled=1)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="display_name"):
        replace(_subject(), display_name=" ")
    with pytest.raises(DomainValidationError, match="must be a string"):
        replace(_subject(), display_name=123)  # type: ignore[arg-type]


def test_project_and_grant_validate_keys_types_and_roles() -> None:
    """Project and ProjectGrant constructors enforce identity and role categories."""
    project = replace(_project(), name="  Cafe\u0301  ")
    grant = ProjectGrant(
        subject_id=_subject().id,
        project_id=project.id,
        role=ProjectRole.OWNER,
    )

    assert project.name == "Café"
    assert grant.role is ProjectRole.OWNER
    with pytest.raises(DomainValidationError, match="Project key"):
        replace(project, key="acme")
    with pytest.raises(DomainValidationError, match="Project name"):
        replace(project, name="")
    with pytest.raises(DomainValidationError, match="Project name"):
        replace(project, name=True)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="ProjectGrant role"):
        replace(grant, role="owner")  # type: ignore[arg-type]


def test_real_subject_and_grant_satisfy_owner_authorization_contract() -> None:
    """Concrete domain entities implement the pure authorization rule boundary."""
    subject = _subject()
    project = _project()
    grant = ProjectGrant(
        subject_id=subject.id,
        project_id=project.id,
        role=ProjectRole.OWNER,
    )

    require_phase_one_owner(
        subject=subject,
        grant=grant,
        target_project_id=project.id,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("context_version", 2, "context_version"),
        ("context_version", True, "context_version"),
        ("profile", "Team", "Profile name"),
        ("project_key", "acme", "Project key"),
        ("workspace_root", "", "Workspace root"),
        ("workspace_root", "bad\x00path", "null character"),
        ("workspace_root", "../escape", "context directory"),
    ],
)
def test_workspace_binding_rejects_unsupported_phase_one_values(
    field: str,
    value: object,
    message: str,
) -> None:
    """Workspace bindings reject invalid versions, identities, and paths."""
    binding = WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        project_key="ACME",
        workspace_root=".",
    )

    with pytest.raises(DomainValidationError, match=message):
        replace(binding, **{field: value})  # type: ignore[arg-type]


def test_workspace_binding_accepts_named_profile_and_normalizes_root() -> None:
    """Phase 2 bindings hold validated profiles and portable relative roots."""
    binding = WorkspaceBinding(
        context_version=1,
        profile="team_1",
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        project_key="ACME",
        workspace_root="repo/./agents/../worker",
    )

    assert binding.profile == "team_1"
    assert binding.workspace_root == "repo/worker"


def test_task_normalizes_text_and_preserves_stable_identity() -> None:
    """Task construction trims text without changing stable identity fields."""
    project = _project()
    task = replace(
        _task(),
        title="  First task  ",
        objective="  Complete the first task.  ",
    )

    assert task.title == "First task"
    assert task.objective == "Complete the first task."
    assert build_task_key(project.key, task.number) == task.key


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("uid", "tsk_first", "Task uid"),
        ("number", 0, "Task number"),
        ("key", "ACME-2", "Task key"),
        ("title", " ", "Task title"),
        ("objective", " ", "Task objective"),
        ("state", "open", "Task state"),
        ("priority", 101, "Task priority"),
        ("version", 0, "Task version"),
        ("created_by", "sub_local", "Task created_by"),
    ],
)
def test_task_rejects_invalid_runtime_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    """Task construction validates every field rather than trusting type hints."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_task(), **{field: value})  # type: ignore[arg-type]


def test_task_rejects_non_utc_and_reverse_timestamp_order() -> None:
    """Task timestamps must be UTC and updated_at cannot precede creation."""
    with pytest.raises(DomainValidationError, match="timezone-aware UTC"):
        replace(_task(), updated_at=_NOW.replace(tzinfo=None))
    with pytest.raises(DomainValidationError, match="must not precede"):
        replace(_task(), updated_at=_NOW - timedelta(seconds=1))


def test_task_event_defensively_copies_and_freezes_payload() -> None:
    """TaskEvent payload aliases cannot mutate an accepted audit record."""
    source: dict[str, JsonScalar] = {"title": "First task", "priority": 50}
    event = _event(source)
    source["title"] = "Changed elsewhere"

    assert dict(event.payload) == {"title": "First task", "priority": 50}
    with pytest.raises(TypeError):
        event.payload["title"] = "Changed"  # type: ignore[index]


def test_result_and_event_use_typed_nullable_attempt_identity() -> None:
    """Agent attribution accepts AttemptId and rejects untyped strings."""
    attempt_id = AttemptId("atm_current")

    assert replace(_result(), attempt_id=attempt_id).attempt_id == attempt_id
    assert replace(_event(), attempt_id=attempt_id).attempt_id == attempt_id
    with pytest.raises(DomainValidationError, match="Result attempt_id"):
        replace(_result(), attempt_id="atm_current")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError, match="TaskEvent attempt_id"):
        replace(_event(), attempt_id="atm_current")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "evt_first", "TaskEvent id"),
        ("cursor", 0, "TaskEvent cursor"),
        ("task_uid", "tsk_first", "TaskEvent task_uid"),
        ("project_id", "prj_acme", "TaskEvent project_id"),
        ("actor_subject_id", "sub_local", "actor_subject_id"),
        ("request_id", "req_first", "request_id"),
        ("event_type", "task_created", "event_type"),
    ],
)
def test_task_event_rejects_invalid_runtime_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    """TaskEvent construction validates identity, cursor, and enum fields."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_event(), **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {" nested": "value"},
        {"nested": {"not": float("inf")}},
        {"number": float("nan")},
    ],
)
def test_task_event_rejects_invalid_payload(payload: dict[str, object]) -> None:
    """TaskEvent payloads reject invalid keys and non-finite values."""
    with pytest.raises(DomainValidationError, match="payload"):
        _event(cast("dict[str, JsonScalar]", payload))


def test_task_event_rejects_non_mapping_payload() -> None:
    """TaskEvent payload validation does not trust the Mapping annotation."""
    with pytest.raises(DomainValidationError, match="must be a mapping"):
        replace(_event(), payload=[])  # type: ignore[arg-type]


def test_domain_modules_import_only_standard_library_and_domain_modules() -> None:
    """The domain dependency boundary remains enforceable without site packages."""
    for path in sorted(_DOMAIN_DIRECTORY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots = {node.module.partition(".")[0]}
            else:
                continue

            assert imported_roots <= sys.stdlib_module_names | {"workaholic"}, path


def _result(*, review: ResultReview | None = None) -> TaskResult:
    """Build one valid Human Result for Phase 3 model tests.

    Args:
        review: Optional review disposition override.

    Returns:
        A valid immutable Result for the first Task.

    """
    return TaskResult(
        id=ResultId("res_first"),
        task_uid=TaskId("tsk_first"),
        submitted_by=SubjectId("sub_local"),
        attempt_id=None,
        submitted_at=_NOW,
        comment="Implemented manually.",
        summary="Acceptance checked.",
        criteria=(
            CriterionOutcome(
                criterion_id="ac_done",
                status=CriterionStatus.PASSED,
                evidence="Verified locally.",
            ),
        ),
        artifacts=(
            ArtifactReference(
                uri="workspace://repo/report.md",
                media_type="text/markdown",
                sha256="a" * 64,
            ),
        ),
        proposed_follow_ups=(ProposedFollowUp(title="  Add regression coverage  "),),
        review=review or ResultReview(status=ResultReviewStatus.NOT_REQUIRED),
    )


def test_phase_three_task_defaults_preserve_phase_two_construction() -> None:
    """Existing Task construction receives safe immutable Phase 3 defaults."""
    task = _task()

    assert task.available_at is None
    assert task.approval is ApprovalRequirement.NONE
    assert task.acceptance == ()
    assert task.context == ()
    assert task.depends_on == ()
    assert task.blocking_reason is None
    assert task.current_result_id is None


def test_phase_three_task_defensively_copies_ordered_definition_values() -> None:
    """Mutable caller collections cannot alter an accepted Task definition."""
    criterion = AcceptanceCriterion(
        id="ac_done",
        text="  Cafe\u0301 output is complete.  ",
        required=True,
    )
    context = ContextReference(
        uri="workspace://repo/spec.md",
        version="  git:abc123  ",
    )
    acceptance = [criterion]
    references = [context]
    dependencies = [TaskId("tsk_prerequisite")]
    task = replace(
        _task(),
        available_at=_NOW + timedelta(days=1),
        approval=ApprovalRequirement.HUMAN,
        acceptance=acceptance,  # type: ignore[arg-type]
        context=references,  # type: ignore[arg-type]
        depends_on=dependencies,  # type: ignore[arg-type]
        current_result_id=ResultId("res_current"),
    )
    acceptance.clear()
    references.clear()
    dependencies.clear()

    assert task.acceptance == (criterion,)
    assert task.acceptance[0].text == "Café output is complete."
    assert task.context == (context,)
    assert task.context[0].version == "git:abc123"
    assert task.depends_on == (TaskId("tsk_prerequisite"),)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"available_at": _NOW.replace(tzinfo=None)}, "available_at"),
        ({"approval": "none"}, "Task approval"),
        (
            {
                "acceptance": (
                    AcceptanceCriterion("ac_same", "First", required=True),
                    AcceptanceCriterion("ac_same", "Second", required=False),
                )
            },
            "criterion IDs",
        ),
        (
            {
                "context": (
                    ContextReference("workspace://repo/spec.md"),
                    ContextReference("workspace://repo/spec.md"),
                )
            },
            "unique",
        ),
        (
            {"depends_on": (TaskId("tsk_other"), TaskId("tsk_other"))},
            "dependencies",
        ),
        ({"depends_on": (TaskId("tsk_first"),)}, "itself"),
        ({"blocking_reason": "Paused"}, "Only blocked"),
        ({"state": TaskState.BLOCKED}, "blocking_reason"),
        ({"current_result_id": "res_current"}, "current_result_id"),
    ],
)
def test_phase_three_task_rejects_invalid_definition_combinations(
    changes: dict[str, object],
    message: str,
) -> None:
    """Task construction rejects malformed or contradictory Phase 3 fields."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_task(), **changes)  # type: ignore[arg-type]


def test_blocked_task_requires_and_normalizes_one_reason() -> None:
    """Blocking state and its bounded reason remain one invariant."""
    task = replace(
        _task(),
        state=TaskState.BLOCKED,
        blocking_reason="  Waiting on procurement  ",
    )

    assert task.blocking_reason == "Waiting on procurement"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AcceptanceCriterion("bad", "Criterion", required=True),
        lambda: AcceptanceCriterion("ac_ok", "line\nbreak", required=True),
        lambda: AcceptanceCriterion("ac_ok", "Criterion", required=1),  # type: ignore[arg-type]
        lambda: ContextReference("relative/path"),
        lambda: ContextReference("workspace://repo/spec", " "),
        lambda: CriterionOutcome("bad", CriterionStatus.PASSED),
        lambda: CriterionOutcome("ac_ok", "passed"),  # type: ignore[arg-type]
        lambda: CriterionOutcome("ac_ok", CriterionStatus.PASSED, "line\nbreak"),
        lambda: ArtifactReference("relative/path"),
        lambda: ArtifactReference("workspace://repo/a", "Text/Markdown"),
        lambda: ArtifactReference("workspace://repo/a", sha256="A" * 64),
        lambda: ProposedFollowUp(" "),
    ],
)
def test_phase_three_value_objects_reject_invalid_runtime_values(
    factory: Callable[[], object],
) -> None:
    """Every structured definition and Result value validates at construction."""
    with pytest.raises(DomainValidationError):
        factory()


@pytest.mark.parametrize(
    "review",
    [
        ResultReview(status=ResultReviewStatus.NOT_REQUIRED),
        ResultReview(status=ResultReviewStatus.PENDING),
        ResultReview(
            status=ResultReviewStatus.APPROVED,
            reviewed_by=SubjectId("sub_reviewer"),
            reviewed_at=_NOW,
            comment="  Looks good.  ",
        ),
        ResultReview(
            status=ResultReviewStatus.REJECTED,
            reviewed_by=SubjectId("sub_reviewer"),
            reviewed_at=_NOW,
            reason="  Missing evidence.  ",
        ),
    ],
)
def test_result_review_accepts_each_valid_disposition(review: ResultReview) -> None:
    """Review attribution and note fields follow their status contracts."""
    assert review.status in ResultReviewStatus


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "pending"},
        {"reviewed_by": "sub_reviewer"},
        {"reviewed_at": _NOW.replace(tzinfo=None)},
        {"comment": "line\nbreak"},
        {"reason": "line\nbreak"},
        {"status": ResultReviewStatus.APPROVED},
        {
            "status": ResultReviewStatus.APPROVED,
            "reviewed_by": SubjectId("sub_reviewer"),
            "reviewed_at": _NOW,
            "reason": "No",
        },
        {
            "status": ResultReviewStatus.REJECTED,
            "reviewed_by": SubjectId("sub_reviewer"),
            "reviewed_at": _NOW,
        },
        {"status": ResultReviewStatus.PENDING, "comment": "Early"},
    ],
)
def test_result_review_rejects_inconsistent_fields(changes: dict[str, object]) -> None:
    """Review status cannot disagree with attribution or note semantics."""
    with pytest.raises(DomainValidationError):
        replace(ResultReview(status=ResultReviewStatus.PENDING), **changes)  # type: ignore[arg-type]


def test_task_result_normalizes_and_defensively_copies_content() -> None:
    """Result collections and optional text are immutable normalized values."""
    result = _result()

    assert result.comment == "Implemented manually."
    assert result.proposed_follow_ups[0].title == "Add regression coverage"
    assert result.review.status is ResultReviewStatus.NOT_REQUIRED
    with pytest.raises(FrozenInstanceError):
        result.summary = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "res_first"}, "Result id"),
        ({"task_uid": "tsk_first"}, "task_uid"),
        ({"submitted_by": "sub_local"}, "submitted_by"),
        ({"attempt_id": "bad"}, "attempt_id"),
        ({"submitted_at": _NOW.replace(tzinfo=None)}, "submitted_at"),
        ({"comment": "line\nbreak"}, "comment"),
        ({"summary": "line\nbreak"}, "summary"),
        (
            {
                "criteria": (
                    CriterionOutcome("ac_done", CriterionStatus.PASSED),
                    CriterionOutcome("ac_done", CriterionStatus.FAILED),
                )
            },
            "unique criterion",
        ),
        ({"artifacts": ("artifact",)}, "ArtifactReference"),
        ({"proposed_follow_ups": ("follow-up",)}, "ProposedFollowUp"),
        ({"review": "pending"}, "Result review"),
    ],
)
def test_task_result_rejects_invalid_runtime_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    """Result construction enforces identity, content, and collection types."""
    with pytest.raises(DomainValidationError, match=message):
        replace(_result(), **changes)  # type: ignore[arg-type]


def test_task_event_recursively_copies_and_freezes_bounded_json() -> None:
    """Nested event arrays and objects cannot be changed through caller aliases."""
    nested = {"changes": [{"field": "title", "old": None}], "ok": True}
    event = _event(cast("dict[str, JsonValue]", nested))
    cast("list[object]", nested["changes"]).clear()

    frozen_changes = cast(
        "tuple[Mapping[str, JsonValue], ...]",
        event.payload["changes"],
    )
    assert frozen_changes[0]["field"] == "title"
    with pytest.raises(TypeError):
        frozen_changes[0]["field"] = "objective"  # type: ignore[index]
