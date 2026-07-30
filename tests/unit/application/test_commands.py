"""Unit tests for strict Phase 1 application boundary models."""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from workaholic.application import (
    BootstrapLocalProjectInput,
    BootstrapMutation,
    BootstrapResult,
    CreateTaskInput,
    GetLocalStatus,
    GetTask,
    ListProjects,
    ListTasks,
    StatusResult,
    TaskCreationMutation,
    TaskPage,
)
from workaholic.domain import (
    Instance,
    InstanceId,
    Project,
    ProjectGrant,
    ProjectId,
    ProjectRole,
    RequestId,
    Subject,
    SubjectId,
    SubjectKind,
    Task,
    TaskEventId,
    TaskId,
    TaskState,
    WorkspaceBinding,
)

_APPLICATION_DIRECTORY = (
    Path(__file__).parents[3] / "src" / "workaholic" / "application"
)
_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _instance(value: str = "ins_local") -> Instance:
    """Build a valid application-test Instance.

    Args:
        value: Serialized Instance ID.

    Returns:
        A valid domain Instance.

    """
    return Instance(id=InstanceId(value), created_at=_NOW)


def _project(
    *,
    value: str = "prj_acme",
    instance_id: InstanceId | None = None,
    key: str = "ACME",
) -> Project:
    """Build a valid application-test Project.

    Args:
        value: Serialized Project ID.
        instance_id: Optional owning Instance ID.
        key: Immutable Project key.

    Returns:
        A valid domain Project.

    """
    return Project(
        id=ProjectId(value),
        instance_id=instance_id or InstanceId("ins_local"),
        key=key,
        name=key,
        created_at=_NOW,
    )


def _subject(
    *,
    value: str = "sub_local",
    enabled: bool = True,
    is_instance_admin: bool = True,
) -> Subject:
    """Build a valid application-test Subject.

    Args:
        value: Serialized Subject ID.
        enabled: Whether the Subject may act.
        is_instance_admin: Whether the Subject administers the Instance.

    Returns:
        A valid Human Subject.

    """
    return Subject(
        id=SubjectId(value),
        kind=SubjectKind.HUMAN,
        display_name="Local operator",
        enabled=enabled,
        is_instance_admin=is_instance_admin,
    )


def _grant(
    *,
    subject_id: SubjectId | None = None,
    project_id: ProjectId | None = None,
) -> ProjectGrant:
    """Build a valid application-test Owner grant.

    Args:
        subject_id: Optional granted Subject ID.
        project_id: Optional target Project ID.

    Returns:
        A valid Owner ProjectGrant.

    """
    return ProjectGrant(
        subject_id=subject_id or SubjectId("sub_local"),
        project_id=project_id or ProjectId("prj_acme"),
        role=ProjectRole.OWNER,
    )


def _workspace(
    *,
    instance_id: InstanceId | None = None,
    project_id: ProjectId | None = None,
    project_key: str = "ACME",
) -> WorkspaceBinding:
    """Build a valid application-test Workspace binding.

    Args:
        instance_id: Optional bound Instance ID.
        project_id: Optional bound Project ID.
        project_key: Bound immutable Project key.

    Returns:
        A valid local WorkspaceBinding.

    """
    return WorkspaceBinding(
        context_version=1,
        profile="local",
        instance_id=instance_id or InstanceId("ins_local"),
        project_id=project_id or ProjectId("prj_acme"),
        project_key=project_key,
        workspace_root=".",
    )


def _task(
    number: int,
    *,
    project_id: ProjectId | None = None,
    project_key: str = "ACME",
) -> Task:
    """Build a valid application-test Task.

    Args:
        number: Project-local Task number.
        project_id: Optional owning Project ID.
        project_key: Stable human-key prefix.

    Returns:
        A valid initial Task.

    """
    return Task(
        uid=TaskId(f"tsk_{number}"),
        project_id=project_id or ProjectId("prj_acme"),
        number=number,
        key=f"{project_key}-{number}",
        title=f"Task {number}",
        objective=f"Complete task {number}.",
        state=TaskState.OPEN,
        priority=50,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _bootstrap_result() -> BootstrapResult:
    """Build a consistent bootstrap result.

    Returns:
        A fully consistent Phase 1 BootstrapResult.

    """
    return BootstrapResult(
        instance=_instance(),
        project=_project(),
        subject=_subject(),
        grant=_grant(),
        workspace=_workspace(),
    )


def test_bootstrap_input_validates_project_and_idempotency_key() -> None:
    """Bootstrap input accepts a strict key and optional opaque retry token."""
    command = BootstrapLocalProjectInput(
        project_key="ACME",
        idempotency_key="bootstrap:acme.1",
    )

    assert command.project_key == "ACME"
    assert command.idempotency_key == "bootstrap:acme.1"


@pytest.mark.parametrize(
    "idempotency_key",
    ["", " ", "has whitespace", "line\nbreak", "delete\x7f", "x" * 129, 123],
)
def test_bootstrap_input_rejects_malformed_idempotency_key(
    idempotency_key: object,
) -> None:
    """Idempotency keys reject blank, unsafe, oversized, and coerced values."""
    with pytest.raises(ValidationError):
        BootstrapLocalProjectInput.model_validate(
            {
                "project_key": "ACME",
                "idempotency_key": idempotency_key,
            }
        )


def test_all_command_models_forbid_extra_fields_and_are_frozen() -> None:
    """Commands reject undeclared data and cannot change after validation."""
    command = BootstrapLocalProjectInput(project_key="ACME")

    with pytest.raises(ValidationError, match="Extra inputs"):
        BootstrapLocalProjectInput.model_validate(
            {"project_key": "ACME", "unexpected": True}
        )
    with pytest.raises(ValidationError, match="frozen"):
        command.project_key = "OTHER"


def test_query_commands_require_typed_domain_identifiers() -> None:
    """Query models never coerce strings or mappings into domain identifiers."""
    status = GetLocalStatus(
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )
    projects = ListProjects(
        instance_id=status.instance_id,
        subject_id=status.subject_id,
    )

    assert projects.instance_id == status.instance_id
    with pytest.raises(ValidationError):
        GetLocalStatus.model_validate(
            {
                "instance_id": "ins_local",
                "project_id": "prj_acme",
                "subject_id": "sub_local",
            }
        )


def test_create_task_defaults_and_normalizes_fields() -> None:
    """Task input applies documented defaults after trimming Human text."""
    command = CreateTaskInput(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        title="  First task  ",
    )
    explicit_none = CreateTaskInput.model_validate(
        {
            "project_id": ProjectId("prj_acme"),
            "subject_id": SubjectId("sub_local"),
            "title": "  First task  ",
            "objective": None,
        }
    )

    assert command.title == "First task"
    assert command.objective == "First task"
    assert command.priority == 50
    assert explicit_none.objective == "First task"


def test_create_task_rejects_non_mapping_model_input() -> None:
    """Task input rejects an entire scalar before field validation."""
    with pytest.raises(ValidationError):
        CreateTaskInput.model_validate("not-a-command")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", "x" * 201),
        ("objective", ""),
        ("objective", "x" * 4_001),
        ("priority", -1),
        ("priority", 101),
        ("priority", True),
        ("priority", "50"),
        ("idempotency_key", "bad key"),
    ],
)
def test_create_task_rejects_invalid_field_boundaries(
    field: str,
    value: object,
) -> None:
    """Task input rejects every documented invalid text and priority boundary."""
    data: dict[str, object] = {
        "project_id": ProjectId("prj_acme"),
        "subject_id": SubjectId("sub_local"),
        "title": "First task",
        "objective": "Complete it.",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        CreateTaskInput.model_validate(data)


@pytest.mark.parametrize("limit", [1, 100, 500])
def test_list_tasks_accepts_page_boundaries(limit: int) -> None:
    """Task listing accepts the inclusive documented page-size bounds."""
    command = ListTasks(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        cursor="v1:opaque",
        limit=limit,
    )

    assert command.limit == limit
    assert command.cursor == "v1:opaque"


def test_list_tasks_defaults_page_size() -> None:
    """Task listing defaults to 100 records without a cursor."""
    command = ListTasks(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )

    assert command.limit == 100
    assert command.cursor is None


@pytest.mark.parametrize("limit", [0, 501, True, 100.0, "100"])
def test_list_tasks_rejects_invalid_page_size(limit: object) -> None:
    """Task listing rejects out-of-range and implicitly coerced limits."""
    with pytest.raises(ValidationError):
        ListTasks.model_validate(
            {
                "project_id": ProjectId("prj_acme"),
                "subject_id": SubjectId("sub_local"),
                "limit": limit,
            }
        )


@pytest.mark.parametrize(
    "cursor",
    ["", " ", "has space", "line\nbreak", "delete\x7f", "x" * 2_049],
)
def test_list_tasks_rejects_malformed_cursor(cursor: str) -> None:
    """Task listing rejects blank, unsafe, and oversized opaque cursors."""
    with pytest.raises(ValidationError):
        ListTasks(
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            cursor=cursor,
        )


def test_get_task_disambiguates_uid_and_human_key() -> None:
    """Task lookup converts UID strings and preserves validated human keys."""
    by_id = GetTask(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task="tsk_019c0d91-7b8a-7000-8000-0123456789ab",
    )
    by_value = GetTask(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task=TaskId("tsk_first"),
    )
    by_key = GetTask(
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
        task="ACME-42",
    )

    assert isinstance(by_id.task, TaskId)
    assert by_value.task == TaskId("tsk_first")
    assert by_key.task == "ACME-42"


@pytest.mark.parametrize(
    "selector",
    ["", "ACME", "ACME-x", "ACME-01", "tsk_bad!", "x" * 257, 42],
)
def test_get_task_rejects_ambiguous_or_malformed_selector(
    selector: object,
) -> None:
    """Task lookup rejects values that cannot map to exactly one identity kind."""
    with pytest.raises(ValidationError):
        GetTask.model_validate(
            {
                "project_id": ProjectId("prj_acme"),
                "subject_id": SubjectId("sub_local"),
                "task": selector,
            }
        )


def test_bootstrap_mutation_requires_typed_ids_and_utc_time() -> None:
    """Bootstrap mutations preserve candidate identities and authoritative UTC."""
    mutation = BootstrapMutation(
        instance_id=InstanceId("ins_candidate"),
        project_id=ProjectId("prj_candidate"),
        subject_id=SubjectId("sub_candidate"),
        request_id=RequestId("req_bootstrap"),
        occurred_at=_NOW,
        project_key="ACME",
        idempotency_key="bootstrap-1",
    )

    assert mutation.occurred_at is _NOW
    with pytest.raises(ValidationError):
        BootstrapMutation.model_validate(
            {
                "instance_id": mutation.instance_id,
                "project_id": mutation.project_id,
                "subject_id": mutation.subject_id,
                "request_id": mutation.request_id,
                "occurred_at": _NOW.replace(tzinfo=None),
                "project_key": mutation.project_key,
                "idempotency_key": mutation.idempotency_key,
            }
        )


def test_task_creation_mutation_normalizes_and_validates_fields() -> None:
    """Task mutations validate allocated identities, UTC time, and Task input."""
    mutation = TaskCreationMutation(
        task_id=TaskId("tsk_candidate"),
        event_id=TaskEventId("evt_candidate"),
        request_id=RequestId("req_create"),
        project_id=ProjectId("prj_acme"),
        actor_subject_id=SubjectId("sub_local"),
        occurred_at=_NOW,
        title="  First task  ",
        objective="  Complete it.  ",
        priority=100,
        idempotency_key=None,
    )

    assert mutation.title == "First task"
    assert mutation.objective == "Complete it."
    assert mutation.priority == 100
    with pytest.raises(ValidationError):
        TaskCreationMutation.model_validate(
            {
                "task_id": mutation.task_id,
                "event_id": mutation.event_id,
                "request_id": mutation.request_id,
                "project_id": mutation.project_id,
                "actor_subject_id": mutation.actor_subject_id,
                "occurred_at": mutation.occurred_at,
                "title": mutation.title,
                "objective": mutation.objective,
                "priority": "100",
                "idempotency_key": mutation.idempotency_key,
            }
        )


def test_result_models_validate_consistent_bootstrap_and_status() -> None:
    """Bootstrap and status results accept one real Human Owner relationship."""
    bootstrap = _bootstrap_result()
    status = StatusResult(
        instance=bootstrap.instance,
        project=bootstrap.project,
        subject=bootstrap.subject,
        grant=bootstrap.grant,
    )

    assert bootstrap.workspace.project_id == bootstrap.project.id
    assert status.mode == "local"
    assert status.schema_version == 1


@pytest.mark.parametrize(
    ("instance", "project", "subject", "grant", "workspace"),
    [
        (
            _instance(),
            _project(instance_id=InstanceId("ins_other")),
            _subject(),
            _grant(),
            _workspace(),
        ),
        (
            _instance(),
            _project(),
            _subject(enabled=False),
            _grant(),
            _workspace(),
        ),
        (
            _instance(),
            _project(),
            _subject(),
            _grant(subject_id=SubjectId("sub_other")),
            _workspace(),
        ),
        (
            _instance(),
            _project(),
            _subject(),
            _grant(),
            _workspace(project_id=ProjectId("prj_other")),
        ),
    ],
)
def test_bootstrap_result_rejects_cross_entity_inconsistency(
    instance: Instance,
    project: Project,
    subject: Subject,
    grant: ProjectGrant,
    workspace: WorkspaceBinding,
) -> None:
    """Bootstrap results reject unrelated entities and invalid authorization."""
    with pytest.raises(ValidationError):
        BootstrapResult(
            instance=instance,
            project=project,
            subject=subject,
            grant=grant,
            workspace=workspace,
        )


def test_task_page_requires_tuple_project_consistency_and_ascending_order() -> None:
    """Task pages enforce deterministic ordering and one Project boundary."""
    empty_page = TaskPage(tasks=(), next_cursor=None)
    page = TaskPage(tasks=(_task(1), _task(3)), next_cursor="v1:3")

    assert empty_page.tasks == ()
    assert tuple(task.number for task in page.tasks) == (1, 3)
    with pytest.raises(ValidationError):
        TaskPage.model_validate({"tasks": [_task(1)], "next_cursor": None})
    with pytest.raises(ValidationError, match="ordered"):
        TaskPage(tasks=(_task(2), _task(1)), next_cursor=None)
    with pytest.raises(ValidationError, match="combine Projects"):
        TaskPage(
            tasks=(
                _task(1),
                _task(
                    2,
                    project_id=ProjectId("prj_other"),
                    project_key="OTHER",
                ),
            ),
            next_cursor=None,
        )


@pytest.mark.parametrize(
    "cursor",
    ["", "bad cursor", "line\nbreak", "delete\x7f", "x" * 2_049, 1],
)
def test_task_page_rejects_invalid_next_cursor(cursor: object) -> None:
    """Result cursors retain the same opaque-token safety boundary as requests."""
    with pytest.raises(ValidationError):
        TaskPage.model_validate({"tasks": (), "next_cursor": cursor})


def test_application_modules_import_only_owned_and_declared_boundaries() -> None:
    """Application code never imports concrete adapters, context, CLI, or Typer."""
    prohibited_modules = {
        "sqlite3",
        "typer",
        "workaholic.cli",
        "workaholic.context",
        "workaholic.persistence",
    }

    for path in sorted(_APPLICATION_DIRECTORY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)

        for prohibited in prohibited_modules:
            assert all(
                module != prohibited and not module.startswith(f"{prohibited}.")
                for module in imported_modules
            ), path
        imported_roots = {module.partition(".")[0] for module in imported_modules}
        assert imported_roots <= sys.stdlib_module_names | {"pydantic", "workaholic"}


def test_application_models_reject_non_utc_offsets() -> None:
    """Mutation timestamps reject aware datetimes outside UTC."""
    non_utc = _NOW.astimezone(timezone(timedelta(hours=2)))
    data = {
        "instance_id": InstanceId("ins_candidate"),
        "project_id": ProjectId("prj_candidate"),
        "subject_id": SubjectId("sub_candidate"),
        "request_id": RequestId("req_bootstrap"),
        "occurred_at": non_utc,
        "project_key": "ACME",
    }

    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        BootstrapMutation.model_validate(data)


def test_result_models_forbid_extra_fields() -> None:
    """Application results reject accidental expansion at construction boundaries."""
    bootstrap = _bootstrap_result()
    data = bootstrap.model_dump()
    data["database_path"] = "/unsafe/local.db"

    with pytest.raises(ValidationError, match="Extra inputs"):
        BootstrapResult.model_validate(data)


def test_model_fields_retain_typed_domain_identifiers() -> None:
    """Validated commands retain explicit domain identifier value objects."""
    command = GetLocalStatus(
        instance_id=InstanceId("ins_local"),
        project_id=ProjectId("prj_acme"),
        subject_id=SubjectId("sub_local"),
    )

    assert command.instance_id == InstanceId("ins_local")
    assert command.project_id == ProjectId("prj_acme")
