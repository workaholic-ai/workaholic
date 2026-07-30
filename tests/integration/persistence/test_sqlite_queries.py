"""Integration tests for authorized non-mutating SQLite queries."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    ApplicationErrorCode,
    BootstrapMutation,
    GetLocalStatus,
    GetTask,
    InvalidInputError,
    ListProjects,
    ListTasks,
    NotInitializedError,
    PermissionDeniedError,
    TaskCreationMutation,
    TaskNotFoundError,
)
from workaholic.domain import (
    InstanceId,
    ProjectId,
    RequestId,
    SubjectId,
    Task,
    TaskEventId,
    TaskId,
)
from workaholic.persistence.sqlite import (
    SQLiteRepository,
    StorageUnavailableError,
    initialize_empty_store,
    open_write_transaction,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from workaholic.application import BootstrapResult

_NOW = datetime(2026, 7, 30, 14, 0, 0, 123456, tzinfo=UTC)
_CANONICAL_NOW = "2026-07-30T14:00:00.123456Z"
_ACME_PROJECT_ID = ProjectId("prj_acme")


def _repository(
    tmp_path: Path,
) -> tuple[SQLiteRepository, BootstrapResult]:
    """Create one initialized, bootstrapped SQLite repository.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Repository and its committed local bootstrap result.

    """
    database_path = tmp_path / "local.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)
    result = repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=InstanceId("ins_local"),
            project_id=ProjectId("prj_acme"),
            subject_id=SubjectId("sub_local"),
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW,
            project_key="ACME",
        )
    )
    return repository, result


def _task_mutation(
    suffix: str,
    *,
    project_id: ProjectId = _ACME_PROJECT_ID,
    title: str | None = None,
    seconds: int = 1,
) -> TaskCreationMutation:
    """Build one deterministic attributable Task mutation.

    Args:
        suffix: Opaque identifier suffix.
        project_id: Selected Project identity.
        title: Optional normalized title and objective.
        seconds: Timestamp offset from bootstrap.

    Returns:
        Validated Task creation mutation.

    """
    task_title = title if title is not None else f"Task {suffix}"
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{suffix}"),
        event_id=TaskEventId(f"evt_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        project_id=project_id,
        actor_subject_id=SubjectId("sub_local"),
        occurred_at=_NOW + timedelta(seconds=seconds),
        title=task_title,
        objective=task_title,
        priority=50,
    )


def _read_without_mutation[T](
    repository: SQLiteRepository,
    operation: Callable[[], T],
) -> T:
    """Execute one query and prove its database file remains byte-identical.

    Args:
        repository: Repository whose SQLite file is observed.
        operation: Query operation, including expected failing queries.

    Returns:
        Query return value.

    Raises:
        Exception: Re-raises the query failure after verifying non-mutation.

    """
    before = repository.database_path.read_bytes()
    try:
        return operation()
    finally:
        assert repository.database_path.read_bytes() == before


def _add_second_project(repository: SQLiteRepository) -> ProjectId:
    """Add one authorized Project for isolation and ordering tests.

    Args:
        repository: Bootstrapped repository to extend as test setup.

    Returns:
        Identity of the inserted Project.

    """
    project_id = ProjectId("prj_beta")
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(project_id),
                "ins_local",
                "BETA",
                "Beta",
                1,
                _CANONICAL_NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO project_grants (subject_id, project_id, role)
            VALUES (?, ?, ?)
            """,
            ("sub_local", str(project_id), "owner"),
        )
    return project_id


def _opaque_cursor(payload: str) -> str:
    """Encode test-owned JSON into the public cursor envelope.

    Args:
        payload: UTF-8 JSON text to encode.

    Returns:
        Version-1 unpadded URL-safe cursor text.

    """
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"v1.{encoded}"


def test_status_and_project_listing_are_stable_after_reopen(tmp_path: Path) -> None:
    """Status is safe and a later repository returns equivalent read models."""
    repository, bootstrap = _repository(tmp_path)
    status_command = GetLocalStatus(
        instance_id=bootstrap.instance.id,
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
    )
    projects_command = ListProjects(
        instance_id=bootstrap.instance.id,
        subject_id=bootstrap.subject.id,
    )

    status = _read_without_mutation(
        repository,
        lambda: repository.get_local_status(status_command),
    )
    projects = _read_without_mutation(
        repository,
        lambda: repository.list_projects(projects_command),
    )

    assert status.mode == "local"
    assert status.schema_version == 1
    assert status.instance == bootstrap.instance
    assert status.project == bootstrap.project
    assert status.subject == bootstrap.subject
    assert status.grant == bootstrap.grant
    assert projects == (bootstrap.project,)
    reopened = SQLiteRepository(repository.database_path)
    assert (
        _read_without_mutation(
            reopened,
            lambda: reopened.get_local_status(status_command),
        )
        == status
    )
    assert (
        _read_without_mutation(
            reopened,
            lambda: reopened.list_projects(projects_command),
        )
        == projects
    )


def test_task_lookup_is_exact_and_project_isolated(tmp_path: Path) -> None:
    """UID and Human-key lookups work without crossing Project boundaries."""
    repository, bootstrap = _repository(tmp_path)
    acme_task = repository.create_task(_task_mutation("acme"))
    beta_id = _add_second_project(repository)
    beta_task = repository.create_task(
        _task_mutation(
            "beta",
            project_id=beta_id,
            seconds=2,
        )
    )

    by_uid = _read_without_mutation(
        repository,
        lambda: repository.get_task(
            GetTask(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                task=acme_task.uid,
            )
        ),
    )
    by_key = _read_without_mutation(
        repository,
        lambda: repository.get_task(
            GetTask(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                task=acme_task.key,
            )
        ),
    )

    assert by_uid == acme_task
    assert by_key == acme_task
    with pytest.raises(TaskNotFoundError) as isolated:
        _read_without_mutation(
            repository,
            lambda: repository.get_task(
                GetTask(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    task=beta_task.uid,
                )
            ),
        )
    assert isolated.value.code is ApplicationErrorCode.TASK_NOT_FOUND
    with pytest.raises(TaskNotFoundError):
        _read_without_mutation(
            repository,
            lambda: repository.get_task(
                GetTask(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    task="ACME-404",
                )
            ),
        )
    projects = _read_without_mutation(
        repository,
        lambda: repository.list_projects(
            ListProjects(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )
    assert tuple(project.key for project in projects) == ("ACME", "BETA")
    beta_page = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(
            ListTasks(
                project_id=beta_id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )
    assert beta_page.tasks == (beta_task,)


def test_task_pagination_is_empty_then_complete_stable_and_reopenable(
    tmp_path: Path,
) -> None:
    """Pagination is ascending, gap-tolerant, deterministic, and durable."""
    repository, bootstrap = _repository(tmp_path)
    base_command = ListTasks(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        limit=2,
    )
    empty = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(base_command),
    )
    assert empty.tasks == ()
    assert empty.next_cursor is None
    expected = tuple(
        repository.create_task(_task_mutation(str(number), seconds=number))
        for number in range(1, 6)
    )

    first = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(base_command),
    )
    repeated = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(base_command),
    )
    assert first == repeated
    assert tuple(task.number for task in first.tasks) == (1, 2)
    assert first.next_cursor is not None

    collected: list[Task] = []
    cursor: str | None = None
    while True:
        page_command = ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            cursor=cursor,
            limit=2,
        )
        page = _read_without_mutation(
            repository,
            partial(repository.list_tasks, page_command),
        )
        collected.extend(page.tasks)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert tuple(collected) == expected
    assert tuple(task.number for task in collected) == (1, 2, 3, 4, 5)
    reopened = SQLiteRepository(repository.database_path)
    assert (
        _read_without_mutation(
            reopened,
            lambda: reopened.list_tasks(base_command),
        )
        == first
    )


def test_malformed_versioned_and_cross_project_cursors_are_invalid(
    tmp_path: Path,
) -> None:
    """Only canonical cursors emitted for the selected Project are accepted."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    repository.create_task(_task_mutation("beta_one", project_id=beta_id, seconds=1))
    repository.create_task(_task_mutation("beta_two", project_id=beta_id, seconds=2))
    beta_first = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(
            ListTasks(
                project_id=beta_id,
                subject_id=bootstrap.subject.id,
                limit=1,
            )
        ),
    )
    assert beta_first.next_cursor is not None
    invalid_cursors = (
        "broken",
        "v1.***",
        "v1.e30",
        _opaque_cursor('{"after":true,"project_id":"prj_acme","v":1}'),
        _opaque_cursor('{"after":1,"project_id":"prj_acme","v":2}'),
        _opaque_cursor('{"after":9223372036854775808,"project_id":"prj_acme","v":1}'),
        f"{beta_first.next_cursor}=",
        beta_first.next_cursor.replace("v1.", "v2.", 1),
        beta_first.next_cursor,
    )

    for cursor in invalid_cursors:
        invalid_command = ListTasks(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            cursor=cursor,
            limit=1,
        )
        with pytest.raises(InvalidInputError) as captured:
            _read_without_mutation(
                repository,
                partial(repository.list_tasks, invalid_command),
            )
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_disabled_subject_cannot_execute_any_query(tmp_path: Path) -> None:
    """Every query revalidates the selected local Human's enabled state."""
    repository, bootstrap = _repository(tmp_path)
    task = repository.create_task(_task_mutation("first"))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "UPDATE subjects SET enabled = 0 WHERE id = ?",
            (str(bootstrap.subject.id),),
        )
    operations: tuple[Callable[[], object], ...] = (
        lambda: repository.get_local_status(
            GetLocalStatus(
                instance_id=bootstrap.instance.id,
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.list_projects(
            ListProjects(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.get_task(
            GetTask(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                task=task.uid,
            )
        ),
    )

    for operation in operations:
        with pytest.raises(PermissionDeniedError) as captured:
            _read_without_mutation(repository, operation)
        assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED


def test_valid_store_maps_missing_identity_state_to_typed_errors(
    tmp_path: Path,
) -> None:
    """Valid stores distinguish absent initialization and scoped Task lookup."""
    database_path = tmp_path / "empty.db"
    initialize_empty_store(database_path)
    repository = SQLiteRepository(database_path)

    with pytest.raises(NotInitializedError) as status_error:
        _read_without_mutation(
            repository,
            lambda: repository.get_local_status(
                GetLocalStatus(
                    instance_id=InstanceId("ins_missing"),
                    project_id=ProjectId("prj_missing"),
                    subject_id=SubjectId("sub_missing"),
                )
            ),
        )
    assert status_error.value.code is ApplicationErrorCode.NOT_INITIALIZED
    with pytest.raises(NotInitializedError):
        _read_without_mutation(
            repository,
            lambda: repository.list_projects(
                ListProjects(
                    instance_id=InstanceId("ins_missing"),
                    subject_id=SubjectId("sub_missing"),
                )
            ),
        )
    with pytest.raises(NotInitializedError):
        _read_without_mutation(
            repository,
            lambda: repository.list_tasks(
                ListTasks(
                    project_id=ProjectId("prj_missing"),
                    subject_id=SubjectId("sub_missing"),
                )
            ),
        )


def test_repository_rejects_unvalidated_query_objects(tmp_path: Path) -> None:
    """Direct adapter use cannot bypass any validated query command boundary."""
    repository, _bootstrap = _repository(tmp_path)
    operations: tuple[Callable[[], object], ...] = (
        lambda: repository.get_local_status(cast("GetLocalStatus", object())),
        lambda: repository.list_projects(cast("ListProjects", object())),
        lambda: repository.list_tasks(cast("ListTasks", object())),
        lambda: repository.get_task(cast("GetTask", object())),
    )

    for operation in operations:
        with pytest.raises(InvalidInputError) as captured:
            _read_without_mutation(repository, operation)
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT


def test_malformed_persisted_rows_map_to_safe_storage_failure(
    tmp_path: Path,
) -> None:
    """Queries never expose domain validation details from malformed durable rows."""
    repository, bootstrap = _repository(tmp_path)
    task = repository.create_task(_task_mutation("first"))
    status_command = GetLocalStatus(
        instance_id=bootstrap.instance.id,
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
    )
    projects_command = ListProjects(
        instance_id=bootstrap.instance.id,
        subject_id=bootstrap.subject.id,
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE projects SET key = 'lowercase' WHERE id = ?",
            (str(bootstrap.project.id),),
        )

    for identity_operation in (
        lambda: repository.get_local_status(status_command),
        lambda: repository.list_projects(projects_command),
    ):
        with pytest.raises(StorageUnavailableError):
            _read_without_mutation(repository, identity_operation)

    with open_write_transaction(repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE projects SET key = 'ACME' WHERE id = ?",
            (str(bootstrap.project.id),),
        )
        connection.execute(
            "UPDATE tasks SET state = 'invalid' WHERE uid = ?",
            (str(task.uid),),
        )
    task_list_command = ListTasks(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
    )
    task_get_command = GetTask(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        task=task.uid,
    )

    for task_operation in (
        lambda: repository.list_tasks(task_list_command),
        lambda: repository.get_task(task_get_command),
    ):
        with pytest.raises(StorageUnavailableError):
            _read_without_mutation(repository, task_operation)
