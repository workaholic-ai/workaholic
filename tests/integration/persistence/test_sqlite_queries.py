"""Integration tests for authorized non-mutating SQLite queries."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import (
    AddTaskDependencyMutation,
    ApplicationErrorCode,
    BootstrapMutation,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    GetTaskDetails,
    InvalidInputError,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ListTasksByView,
    NotInitializedError,
    PermissionDeniedError,
    ProjectNotFoundError,
    TaskCreationMutation,
    TaskListView,
    TaskNotFoundError,
)
from workaholic.domain import (
    AcceptanceCriterion,
    ApprovalRequirement,
    ContextReference,
    InstanceId,
    ProjectId,
    ReadinessReason,
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
    open_read_connection,
    open_write_transaction,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from workaholic.application import BootstrapResult

_NOW = datetime(2026, 7, 30, 14, 0, 0, 123456, tzinfo=UTC)
_CANONICAL_NOW = "2026-07-30T14:00:00.123456Z"
_ACME_PROJECT_ID = ProjectId("prj_acme")
_VIEW_NOW = datetime(2026, 8, 1, 12, 0, 0, 444444, tzinfo=UTC)


class _Clock:
    """Fixed authoritative clock for readiness query tests."""

    def __init__(self, now: datetime) -> None:
        """Store one exact UTC query time.

        Args:
            now: Authoritative UTC time returned by every call.

        """
        self._now = now

    def now(self) -> datetime:
        """Return the fixed authoritative time."""
        return self._now


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


def _task_mutation(  # noqa: PLR0913 - explicit semantic fixture fields aid tests.
    suffix: str,
    *,
    project_id: ProjectId = _ACME_PROJECT_ID,
    title: str | None = None,
    seconds: int = 1,
    available_at: datetime | None = None,
    approval: ApprovalRequirement = ApprovalRequirement.NONE,
    acceptance: tuple[AcceptanceCriterion, ...] = (),
    context: tuple[ContextReference, ...] = (),
    priority: int = 50,
) -> TaskCreationMutation:
    """Build one deterministic attributable Task mutation.

    Args:
        suffix: Opaque identifier suffix.
        project_id: Selected Project identity.
        title: Optional normalized title and objective.
        seconds: Timestamp offset from bootstrap.
        available_at: Optional exact Task availability timestamp.
        approval: Result approval requirement.
        acceptance: Ordered acceptance criteria.
        context: Ordered inert context references.
        priority: Task scheduling priority.

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
        priority=priority,
        available_at=available_at,
        approval=approval,
        acceptance=acceptance,
        context=context,
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


def _opaque_cursor(payload: object) -> str:
    """Encode test-owned JSON into the Phase 2 cursor envelope.

    Args:
        payload: JSON-compatible value to encode canonically.

    Returns:
        Version-2 unpadded URL-safe cursor text.

    """
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = base64.urlsafe_b64encode(serialized.encode()).decode().rstrip("=")
    return f"v2.{encoded}"


def _phase_three_cursor(payload: object) -> str:
    """Encode test-owned JSON into a version-3 cursor envelope.

    Args:
        payload: JSON-compatible cursor payload.

    Returns:
        Version-3 unpadded URL-safe cursor text.

    """
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = base64.urlsafe_b64encode(serialized.encode()).decode().rstrip("=")
    return f"v3.{encoded}"


def _decode_opaque_cursor(cursor: str) -> object:
    """Decode one emitted cursor for white-box contract assertions.

    Args:
        cursor: Version-2 unpadded URL-safe cursor.

    Returns:
        Decoded JSON-compatible payload.

    """
    encoded = cursor.removeprefix("v2.")
    padding = "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}"))


def _project_cursor_payload(  # noqa: PLR0913
    *,
    after: object = 1,
    profile: object = "local",
    instance_id: object = "ins_local",
    subject_id: object = "sub_local",
    project_id: object = "prj_acme",
    selection: object = "project",
    version: object = 2,
) -> dict[str, object]:
    """Build one closed Phase 2 Project-cursor payload.

    Args:
        after: Last Project-local Task number.
        profile: Trusted profile binding.
        instance_id: Selected Instance identity.
        subject_id: Selected Subject identity.
        project_id: Selected Project identity.
        selection: Cursor selection kind.
        version: Cursor payload version.

    Returns:
        Complete mutable payload for malformed-input tests.

    """
    return {
        "after": after,
        "instance_id": instance_id,
        "profile": profile,
        "project_id": project_id,
        "selection": selection,
        "subject_id": subject_id,
        "v": version,
    }


def test_project_lookup_is_exact_authorized_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Immutable-key lookup never crosses Instance or authorization scope."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)

    beta = _read_without_mutation(
        repository,
        lambda: repository.get_project_by_key(
            GetProjectByKey(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                project_key="BETA",
            )
        ),
    )

    assert beta.id == beta_id
    assert beta.instance_id == bootstrap.instance.id
    assert beta.key == "BETA"
    assert beta.name == "Beta"
    for missing_key in ("DOCS", "OTHER"):
        missing_command = GetProjectByKey(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            project_key=missing_key,
        )
        with pytest.raises(ProjectNotFoundError) as captured:
            _read_without_mutation(
                repository,
                partial(repository.get_project_by_key, missing_command),
            )
        assert captured.value.code is ApplicationErrorCode.PROJECT_NOT_FOUND

    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO subjects (
                id, kind, display_name, enabled, is_instance_admin
            ) VALUES ('sub_other', 'human', 'Other operator', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO project_grants (subject_id, project_id, role)
            VALUES ('sub_other', 'prj_beta', 'owner')
            """
        )
    with pytest.raises(ProjectNotFoundError):
        _read_without_mutation(
            repository,
            lambda: repository.get_project_by_key(
                GetProjectByKey(
                    instance_id=bootstrap.instance.id,
                    subject_id=SubjectId("sub_other"),
                    project_key="ACME",
                )
            ),
        )
    assert _read_without_mutation(
        repository,
        lambda: repository.list_projects(
            ListProjects(
                instance_id=bootstrap.instance.id,
                subject_id=SubjectId("sub_other"),
            )
        ),
    ) == (beta,)


def test_all_project_pagination_is_stable_gap_tolerant_and_complete(
    tmp_path: Path,
) -> None:
    """Instance traversal follows Project key and number without omissions."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (
                'prj_empty', 'ins_local', 'EMPTY', 'Empty', 1, ?
            )
            """,
            (_CANONICAL_NOW,),
        )
        connection.execute(
            """
            INSERT INTO project_grants (subject_id, project_id, role)
            VALUES ('sub_local', 'prj_empty', 'owner')
            """
        )
    acme_tasks = tuple(
        repository.create_task(_task_mutation(f"acme_{number}", seconds=number))
        for number in range(1, 3)
    )
    beta_tasks = tuple(
        repository.create_task(
            _task_mutation(
                f"beta_{number}",
                project_id=beta_id,
                seconds=number + 2,
            )
        )
        for number in range(1, 4)
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "DELETE FROM task_events WHERE task_uid = ?",
            (str(beta_tasks[1].uid),),
        )
        connection.execute(
            "DELETE FROM tasks WHERE uid = ?",
            (str(beta_tasks[1].uid),),
        )
    expected = (acme_tasks[0], acme_tasks[1], beta_tasks[0], beta_tasks[2])

    collected: list[Task] = []
    emitted_cursors: list[str] = []
    cursor: str | None = None
    while True:
        command = ListInstanceTasks(
            profile="alpha",
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            cursor=cursor,
            limit=2,
        )
        page = _read_without_mutation(
            repository,
            partial(repository.list_tasks_for_instance, command),
        )
        collected.extend(page.tasks)
        if page.next_cursor is None:
            break
        assert page.next_cursor.startswith("v2.")
        emitted_cursors.append(page.next_cursor)
        cursor = page.next_cursor

    assert tuple(collected) == expected
    assert tuple(task.key for task in collected) == (
        "ACME-1",
        "ACME-2",
        "BETA-1",
        "BETA-3",
    )
    assert len({task.uid for task in collected}) == len(expected)
    assert _decode_opaque_cursor(emitted_cursors[0]) == {
        "after": ["ACME", 2],
        "instance_id": "ins_local",
        "profile": "alpha",
        "project_id": None,
        "selection": "all_projects",
        "subject_id": "sub_local",
        "v": 2,
    }
    reopened = SQLiteRepository(repository.database_path)
    assert (
        _read_without_mutation(
            reopened,
            lambda: reopened.list_tasks_for_instance(
                ListInstanceTasks(
                    profile="alpha",
                    instance_id=bootstrap.instance.id,
                    subject_id=bootstrap.subject.id,
                    limit=500,
                )
            ),
        ).tasks
        == expected
    )


def test_all_project_listing_filters_unauthorized_projects(
    tmp_path: Path,
) -> None:
    """Instance pages expose Tasks only through active Project grants."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    acme_task = repository.create_task(_task_mutation("acme"))
    repository.create_task(_task_mutation("beta", project_id=beta_id, seconds=2))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            DELETE FROM project_grants
            WHERE subject_id = 'sub_local' AND project_id = 'prj_beta'
            """
        )

    page = _read_without_mutation(
        repository,
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )

    assert page.tasks == (acme_task,)
    projects = _read_without_mutation(
        repository,
        lambda: repository.list_projects(
            ListProjects(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )
    assert projects == (bootstrap.project,)


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

    assert status.mode == "embedded"
    assert status.profile == "local"
    assert status.schema_version == 4
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


def test_task_reads_hydrate_complete_definition_and_ordered_dependencies(
    tmp_path: Path,
) -> None:
    """Detail and list paths expose one equal complete Task after restart."""
    repository, bootstrap = _repository(tmp_path)
    first = repository.create_task(_task_mutation("first", seconds=1))
    second = repository.create_task(_task_mutation("second", seconds=2))
    acceptance = (
        AcceptanceCriterion(
            id="ac_evidence",
            text="Attach categorized evidence.",
            required=True,
        ),
    )
    context = (
        ContextReference(uri="workspace://repo/data.csv", version="git:8f31c12"),
    )
    available_at = _NOW + timedelta(days=2, microseconds=333333)
    created = repository.create_task(
        _task_mutation(
            "dependent",
            title="Complete definition",
            seconds=3,
            available_at=available_at,
            approval=ApprovalRequirement.HUMAN,
            acceptance=acceptance,
            context=context,
        )
    )
    with open_write_transaction(repository.database_path) as connection:
        # Insert reverse key order to prove reads own deterministic ordering.
        connection.executemany(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (
                (str(created.uid), str(second.uid), str(created.project_id)),
                (str(created.uid), str(first.uid), str(created.project_id)),
            ),
        )
    expected = replace(created, depends_on=(first.uid, second.uid))
    command = GetTask(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        task=created.uid,
    )

    assert (
        _read_without_mutation(
            repository,
            partial(repository.get_task, command),
        )
        == expected
    )
    project_page = _read_without_mutation(
        repository,
        lambda: repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )
    assert project_page.tasks[-1] == expected
    instance_page = _read_without_mutation(
        repository,
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
    )
    assert instance_page.tasks[-1] == expected
    reopened = SQLiteRepository(repository.database_path)
    assert (
        _read_without_mutation(
            reopened,
            partial(reopened.get_task, command),
        )
        == expected
    )


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
    assert first.next_cursor.startswith("v2.")
    assert _decode_opaque_cursor(first.next_cursor) == {
        "after": 2,
        "instance_id": "ins_local",
        "profile": "local",
        "project_id": "prj_acme",
        "selection": "project",
        "subject_id": "sub_local",
        "v": 2,
    }

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
    noncanonical_payload = json.dumps(
        _project_cursor_payload(),
        sort_keys=True,
    ).encode()
    noncanonical_cursor = "v2." + base64.urlsafe_b64encode(
        noncanonical_payload
    ).decode().rstrip("=")
    invalid_cursors = (
        "broken",
        "v2.***",
        "v2.e30",
        noncanonical_cursor,
        _opaque_cursor({"wrong": "shape"}),
        _opaque_cursor(_project_cursor_payload(after=True)),
        _opaque_cursor(_project_cursor_payload(version=3)),
        _opaque_cursor(_project_cursor_payload(after=9_223_372_036_854_775_808)),
        f"{beta_first.next_cursor}=",
        beta_first.next_cursor.replace("v2.", "v1.", 1),
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


def test_cursors_reject_cross_binding_and_cross_selection_reuse(
    tmp_path: Path,
) -> None:
    """Every profile, Instance, Subject, Project, and selection is bound."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    for number in range(1, 3):
        repository.create_task(_task_mutation(f"acme_{number}", seconds=number))
        repository.create_task(
            _task_mutation(
                f"beta_{number}",
                project_id=beta_id,
                seconds=number + 2,
            )
        )
    project_page = repository.list_tasks(
        ListTasks(
            profile="alpha",
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=1,
        )
    )
    all_page = repository.list_tasks_for_instance(
        ListInstanceTasks(
            profile="alpha",
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            limit=1,
        )
    )
    project_cursor = project_page.next_cursor
    all_cursor = all_page.next_cursor
    assert project_cursor is not None
    assert all_cursor is not None

    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO subjects (
                id, kind, display_name, enabled, is_instance_admin
            ) VALUES ('sub_other', 'human', 'Other operator', 1, 1)
            """
        )
        connection.executemany(
            """
            INSERT INTO project_grants (subject_id, project_id, role)
            VALUES ('sub_other', ?, 'owner')
            """,
            (("prj_acme",), ("prj_beta",)),
        )

    invalid_operations: tuple[Callable[[], object], ...] = (
        lambda: repository.list_tasks(
            ListTasks(
                profile="beta",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                cursor=project_cursor,
                limit=1,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                profile="alpha",
                project_id=beta_id,
                subject_id=bootstrap.subject.id,
                cursor=project_cursor,
                limit=1,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                profile="alpha",
                project_id=bootstrap.project.id,
                subject_id=SubjectId("sub_other"),
                cursor=project_cursor,
                limit=1,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                cursor=project_cursor,
                limit=1,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                profile="alpha",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                cursor=all_cursor,
                limit=1,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                profile="alpha",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                cursor=_opaque_cursor(_project_cursor_payload(instance_id="ins_other")),
                limit=1,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                cursor=_opaque_cursor(
                    _project_cursor_payload(
                        after=1,
                        project_id=None,
                        selection="all_projects",
                    )
                ),
                limit=1,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                cursor=_opaque_cursor(
                    _project_cursor_payload(
                        after=[1, 1],
                        project_id=None,
                        selection="all_projects",
                    )
                ),
                limit=1,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                cursor=_opaque_cursor(
                    _project_cursor_payload(
                        after=["lowercase", 1],
                        project_id=None,
                        selection="all_projects",
                    )
                ),
                limit=1,
            )
        ),
    )

    for operation in invalid_operations:
        with pytest.raises(InvalidInputError) as captured:
            _read_without_mutation(repository, operation)
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
        lambda: repository.get_project_by_key(
            GetProjectByKey(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                project_key=bootstrap.project.key,
            )
        ),
        lambda: repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                instance_id=bootstrap.instance.id,
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
            lambda: repository.get_project_by_key(
                GetProjectByKey(
                    instance_id=InstanceId("ins_missing"),
                    subject_id=SubjectId("sub_missing"),
                    project_key="ACME",
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
    with pytest.raises(NotInitializedError):
        _read_without_mutation(
            repository,
            lambda: repository.list_tasks_for_instance(
                ListInstanceTasks(
                    instance_id=InstanceId("ins_missing"),
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
        lambda: repository.get_project_by_key(cast("GetProjectByKey", object())),
        lambda: repository.list_tasks(cast("ListTasks", object())),
        lambda: repository.list_tasks_for_instance(cast("ListInstanceTasks", object())),
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
    project_command = GetProjectByKey(
        instance_id=bootstrap.instance.id,
        subject_id=bootstrap.subject.id,
        project_key=bootstrap.project.key,
    )
    with open_write_transaction(repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE projects SET name = ' invalid ' WHERE id = ?",
            (str(bootstrap.project.id),),
        )

    with pytest.raises(StorageUnavailableError):
        _read_without_mutation(
            repository,
            partial(repository.get_project_by_key, project_command),
        )

    with open_write_transaction(repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE projects
            SET key = 'lowercase', name = 'ACME'
            WHERE id = ?
            """,
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
            "UPDATE tasks SET key = 'BETA-1' WHERE uid = ?",
            (str(task.uid),),
        )
    cross_key_commands: tuple[Callable[[], object], ...] = (
        lambda: repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                instance_id=bootstrap.instance.id,
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
    for operation in cross_key_commands:
        with pytest.raises(StorageUnavailableError):
            _read_without_mutation(repository, operation)

    with open_write_transaction(repository.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE tasks
            SET key = 'ACME-1', state = 'invalid'
            WHERE uid = ?
            """,
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
        lambda: repository.list_tasks_for_instance(
            ListInstanceTasks(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ),
        lambda: repository.get_task(task_get_command),
    ):
        with pytest.raises(StorageUnavailableError):
            _read_without_mutation(repository, task_operation)


def test_task_details_derive_dependency_readiness_without_rewriting_dependant(
    tmp_path: Path,
) -> None:
    """Prerequisite state changes affect reads but never dependant storage rows."""
    repository, bootstrap = _repository(tmp_path)
    prerequisite = repository.create_task(_task_mutation("prerequisite", seconds=1))
    dependant = repository.create_task(_task_mutation("dependant", seconds=2))
    repository.add_task_dependency(
        AddTaskDependencyMutation(
            task_uid=dependant.uid,
            prerequisite_uid=prerequisite.uid,
            project_id=dependant.project_id,
            actor_subject_id=bootstrap.subject.id,
            event_id=TaskEventId("evt_dependency"),
            request_id=RequestId("req_dependency"),
            occurred_at=_NOW + timedelta(seconds=3),
            expected_version=1,
        )
    )
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )
    command = GetTaskDetails(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        task=dependant.uid,
    )
    before_bytes = repository.database_path.read_bytes()

    waiting = query_repository.get_task_details(command)

    assert waiting.task.depends_on == (prerequisite.uid,)
    assert waiting.prerequisites == (prerequisite,)
    assert waiting.readiness.ready is False
    assert waiting.readiness.reasons == (ReadinessReason.UNSATISFIED_DEPENDENCY,)
    assert waiting.current_result is None
    assert repository.database_path.read_bytes() == before_bytes
    with open_read_connection(repository.database_path) as connection:
        dependant_row = connection.execute(
            "SELECT version, updated_at FROM tasks WHERE uid = ?",
            (str(dependant.uid),),
        ).fetchone()
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            "UPDATE tasks SET state = 'done' WHERE uid = ?",
            (str(prerequisite.uid),),
        )

    ready = query_repository.get_task_details(command)

    assert ready.readiness.ready is True
    assert ready.readiness.reasons == ()
    with open_read_connection(repository.database_path) as connection:
        assert (
            connection.execute(
                "SELECT version, updated_at FROM tasks WHERE uid = ?",
                (str(dependant.uid),),
            ).fetchone()
            == dependant_row
        )


def test_cancelled_prerequisite_has_distinct_unsatisfiable_reason(
    tmp_path: Path,
) -> None:
    """Cancelled prerequisites remain edges and surface a stable reason code."""
    repository, bootstrap = _repository(tmp_path)
    prerequisite = repository.create_task(_task_mutation("prerequisite", seconds=1))
    dependant = repository.create_task(_task_mutation("dependant", seconds=2))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO task_dependencies (task_uid, prerequisite_uid, project_id)
            VALUES (?, ?, ?)
            """,
            (str(dependant.uid), str(prerequisite.uid), str(dependant.project_id)),
        )
        connection.execute(
            "UPDATE tasks SET state = 'cancelled' WHERE uid = ?",
            (str(prerequisite.uid),),
        )
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )

    details = query_repository.get_task_details(
        GetTaskDetails(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            task=dependant.key,
        )
    )

    assert details.readiness.reasons == (ReadinessReason.UNSATISFIABLE_DEPENDENCY,)
    assert details.task.version == dependant.version


def test_availability_boundary_uses_injected_authoritative_clock(
    tmp_path: Path,
) -> None:
    """A Task becomes ready exactly at available_at without stored mutation."""
    repository, bootstrap = _repository(tmp_path)
    available_at = _VIEW_NOW + timedelta(seconds=1)
    task = repository.create_task(
        _task_mutation("future", available_at=available_at, seconds=1)
    )
    command = GetTaskDetails(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        task=task.uid,
    )

    before = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    ).get_task_details(command)
    at_boundary = SQLiteRepository(
        repository.database_path,
        clock=_Clock(available_at),
    ).get_task_details(command)

    assert before.readiness.scheduled is True
    assert before.readiness.reasons == (ReadinessReason.NOT_YET_AVAILABLE,)
    assert at_boundary.readiness.ready is True
    assert at_boundary.readiness.scheduled is False


def test_view_pages_filter_stored_and_derived_states_with_aligned_readiness(
    tmp_path: Path,
) -> None:
    """Every Phase 3 view has one authoritative selection meaning."""
    repository, bootstrap = _repository(tmp_path)
    ready = repository.create_task(_task_mutation("ready", seconds=1))
    scheduled = repository.create_task(
        _task_mutation(
            "scheduled",
            seconds=2,
            available_at=_VIEW_NOW + timedelta(hours=1),
        )
    )
    blocked = repository.create_task(_task_mutation("blocked", seconds=3))
    review = repository.create_task(_task_mutation("review", seconds=4))
    done = repository.create_task(_task_mutation("done", seconds=5))
    cancelled = repository.create_task(_task_mutation("cancelled", seconds=6))
    with open_write_transaction(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO task_results (
                id, task_uid, submitted_by, submitted_at, review_status
            ) VALUES ('res_review', ?, 'sub_local', ?, 'pending')
            """,
            (str(review.uid), "2026-08-01T11:00:00.123456Z"),
        )
        connection.execute(
            """
            UPDATE tasks
            SET state = 'blocked', blocking_reason = 'Waiting.'
            WHERE uid = ?
            """,
            (str(blocked.uid),),
        )
        connection.execute(
            """
            UPDATE tasks SET state = 'review', current_result_id = 'res_review'
            WHERE uid = ?
            """,
            (str(review.uid),),
        )
        connection.execute(
            "UPDATE tasks SET state = 'done' WHERE uid = ?",
            (str(done.uid),),
        )
        connection.execute(
            "UPDATE tasks SET state = 'cancelled' WHERE uid = ?",
            (str(cancelled.uid),),
        )
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )
    expected = {
        TaskListView.ALL: (
            ready.uid,
            scheduled.uid,
            blocked.uid,
            review.uid,
            done.uid,
            cancelled.uid,
        ),
        TaskListView.READY: (ready.uid,),
        TaskListView.SCHEDULED: (scheduled.uid,),
        TaskListView.BLOCKED: (blocked.uid,),
        TaskListView.REVIEW: (review.uid,),
        TaskListView.DONE: (done.uid,),
        TaskListView.CANCELLED: (cancelled.uid,),
    }

    for view, identities in expected.items():
        page = query_repository.list_tasks_by_view(
            ListTasksByView(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                view=view,
            )
        )
        assert tuple(task.uid for task in page.tasks) == identities
        assert len(page.readiness) == len(page.tasks)
        assert page.view is view
    review_details = query_repository.get_task_details(
        GetTaskDetails(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            task=review.uid,
        )
    )
    assert review_details.current_result is not None
    assert str(review_details.current_result.id) == "res_review"
    assert review_details.readiness.awaiting_review is True


def test_ready_order_and_v3_keyset_pagination_are_deterministic(
    tmp_path: Path,
) -> None:
    """Ready pagination preserves priority, null-first availability, and number."""
    repository, bootstrap = _repository(tmp_path)
    past = _VIEW_NOW - timedelta(hours=1)
    tasks = (
        repository.create_task(_task_mutation("low", seconds=1, priority=50)),
        repository.create_task(_task_mutation("high_null", seconds=2, priority=90)),
        repository.create_task(
            _task_mutation(
                "high_past",
                seconds=3,
                priority=90,
                available_at=past,
            )
        ),
        repository.create_task(
            _task_mutation("high_null_later", seconds=4, priority=90)
        ),
    )
    expected = (tasks[1], tasks[3], tasks[2], tasks[0])
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )
    collected: list[Task] = []
    cursor: str | None = None
    emitted: list[str] = []
    while True:
        page = query_repository.list_tasks_by_view(
            ListTasksByView(
                profile="alpha",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                view=TaskListView.READY,
                cursor=cursor,
                limit=2,
            )
        )
        collected.extend(page.tasks)
        if page.next_cursor is None:
            break
        emitted.append(page.next_cursor)
        cursor = page.next_cursor

    assert tuple(collected) == expected
    assert emitted[0].startswith("v3.")
    encoded = emitted[0].removeprefix("v3.")
    payload = json.loads(
        base64.urlsafe_b64decode(f"{encoded}{'=' * (-len(encoded) % 4)}")
    )
    assert payload == {
        "after": [90, None, 4],
        "instance_id": "ins_local",
        "profile": "alpha",
        "project_id": "prj_acme",
        "selection": "project",
        "subject_id": "sub_local",
        "v": 3,
        "view": "ready",
    }
    with pytest.raises(InvalidInputError):
        query_repository.list_tasks_by_view(
            ListTasksByView(
                profile="alpha",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                view=TaskListView.ALL,
                cursor=emitted[0],
            )
        )
    with pytest.raises(InvalidInputError):
        query_repository.list_tasks_by_view(
            ListTasksByView(
                profile="other",
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                view=TaskListView.READY,
                cursor=emitted[0],
            )
        )


def test_all_project_ready_order_inserts_project_key_before_task_number(
    tmp_path: Path,
) -> None:
    """Equal ready Tasks across Projects use immutable Project key as tie-breaker."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    acme = repository.create_task(_task_mutation("acme", seconds=1, priority=80))
    beta = repository.create_task(
        _task_mutation(
            "beta",
            project_id=beta_id,
            seconds=2,
            priority=80,
        )
    )
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )

    page = query_repository.list_tasks_by_view(
        ListTasksByView(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.READY,
            limit=1,
        )
    )

    assert page.tasks == (acme,)
    assert page.next_cursor is not None
    second = query_repository.list_tasks_by_view(
        ListTasksByView(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            view=TaskListView.READY,
            cursor=page.next_cursor,
            limit=1,
        )
    )
    assert second.tasks == (beta,)
    assert tuple(item.ready for item in (*page.readiness, *second.readiness)) == (
        True,
        True,
    )


def test_all_view_v3_pagination_uses_scope_specific_keysets(tmp_path: Path) -> None:
    """Stored-state pages resume by number or Project-key/number exactly once."""
    repository, bootstrap = _repository(tmp_path)
    beta_id = _add_second_project(repository)
    acme = repository.create_task(_task_mutation("acme", seconds=1))
    acme_second = repository.create_task(_task_mutation("acme_second", seconds=2))
    beta = repository.create_task(_task_mutation("beta", project_id=beta_id, seconds=3))
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )
    project_first = query_repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            limit=1,
        )
    )
    assert project_first.tasks == (acme,)
    assert project_first.next_cursor is not None
    project_second = query_repository.list_tasks_by_view(
        ListTasksByView(
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
            cursor=project_first.next_cursor,
            limit=1,
        )
    )
    assert project_second.tasks == (acme_second,)
    assert project_second.next_cursor is None
    instance_first = query_repository.list_tasks_by_view(
        ListTasksByView(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            limit=1,
        )
    )
    assert instance_first.tasks == (acme,)
    assert instance_first.next_cursor is not None

    instance_second = query_repository.list_tasks_by_view(
        ListTasksByView(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            cursor=instance_first.next_cursor,
            limit=1,
        )
    )

    assert instance_second.tasks == (acme_second,)
    assert instance_second.next_cursor is not None
    instance_third = query_repository.list_tasks_by_view(
        ListTasksByView(
            instance_id=bootstrap.instance.id,
            subject_id=bootstrap.subject.id,
            cursor=instance_second.next_cursor,
            limit=1,
        )
    )
    assert instance_third.tasks == (beta,)
    assert instance_third.next_cursor is None


def test_malformed_and_noncanonical_v3_cursors_are_safe_input_errors(
    tmp_path: Path,
) -> None:
    """Cursor parsing rejects malformed shape, bounds, version, and encoding."""
    repository, bootstrap = _repository(tmp_path)
    repository.create_task(_task_mutation("one", seconds=1))
    query_repository = SQLiteRepository(
        repository.database_path,
        clock=_Clock(_VIEW_NOW),
    )
    base = {
        "after": 1,
        "instance_id": "ins_local",
        "profile": "local",
        "project_id": "prj_acme",
        "selection": "project",
        "subject_id": "sub_local",
        "v": 3,
        "view": "all",
    }
    malformed = (
        "v3.not_base64!",
        _phase_three_cursor({**base, "v": 2}),
        _phase_three_cursor({**base, "after": True}),
        _phase_three_cursor(
            {key: value for key, value in base.items() if key != "view"}
        ),
        f"{_phase_three_cursor(base)}=",
    )

    for cursor in malformed:
        with pytest.raises(InvalidInputError):
            query_repository.list_tasks_by_view(
                ListTasksByView(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    cursor=cursor,
                )
            )


def test_task_detail_missing_selector_and_invalid_clock_are_safe_failures(
    tmp_path: Path,
) -> None:
    """Detail queries preserve missing-Task meaning and reject invalid clock data."""
    repository, bootstrap = _repository(tmp_path)
    command = GetTaskDetails(
        project_id=bootstrap.project.id,
        subject_id=bootstrap.subject.id,
        task=TaskId("tsk_missing"),
    )
    with pytest.raises(TaskNotFoundError):
        SQLiteRepository(
            repository.database_path,
            clock=_Clock(_VIEW_NOW),
        ).get_task_details(command)
    with pytest.raises(StorageUnavailableError):
        SQLiteRepository(
            repository.database_path,
            clock=_Clock(_VIEW_NOW.replace(tzinfo=None)),
        ).get_task_details(command)
