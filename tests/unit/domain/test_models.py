"""Unit tests for immutable Phase 1 domain entities."""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.domain import (
    DomainValidationError,
    Instance,
    InstanceId,
    JsonScalar,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskState,
    WorkspaceBinding,
    build_task_key,
    require_phase_one_owner,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def _event(payload: Mapping[str, JsonScalar] | None = None) -> TaskEvent:
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
    project = _project()
    grant = ProjectGrant(
        subject_id=_subject().id,
        project_id=project.id,
        role=ProjectRole.OWNER,
    )

    assert grant.role is ProjectRole.OWNER
    with pytest.raises(DomainValidationError, match="Project key"):
        replace(project, key="acme")
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
        ("profile", "team", "profile"),
        ("project_key", "acme", "Project key"),
        ("workspace_root", "", "Workspace root"),
        ("workspace_root", "bad\x00path", "null character"),
    ],
)
def test_workspace_binding_rejects_unsupported_phase_one_values(
    field: str,
    value: object,
    message: str,
) -> None:
    """Workspace bindings accept only safe, local Phase 1 context values."""
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
        {"nested": {"not": "a scalar"}},
        {"number": float("nan")},
    ],
)
def test_task_event_rejects_invalid_payload(payload: dict[str, object]) -> None:
    """TaskEvent payloads accept trimmed keys and finite scalar values only."""
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
