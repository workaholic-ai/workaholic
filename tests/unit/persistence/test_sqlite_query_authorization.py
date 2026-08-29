"""Phase 5 Viewer authorization tests for every SQLite read surface."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from workaholic.application import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    BootstrapMutation,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    GetTaskDetails,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
    ListTasksByView,
    PermissionDeniedError,
    ReadTaskEvents,
    TaskCreationMutation,
    TaskListView,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    ProjectId,
    ProjectRole,
    RequestId,
    SubjectId,
    SubjectKind,
    TaskEventId,
    TaskId,
    TokenId,
)
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_NOW: Final = datetime(2026, 8, 29, 10, tzinfo=UTC)
_INSTANCE_ID: Final = InstanceId("ins_local")
_LOCAL_PROJECT_ID: Final = ProjectId("prj_local")
_SECOND_PROJECT_ID: Final = ProjectId("prj_second")
_OWNER_ID: Final = SubjectId("sub_owner")


@dataclass(frozen=True, slots=True)
class _FixedClock:
    """Supply one authoritative read time."""

    value: datetime

    def now(self) -> datetime:
        """Return the fixed timezone-aware UTC timestamp."""
        return self.value


def _repository(tmp_path: Path) -> SQLiteRepository:
    """Create a two-Project store with every cumulative role and real Tokens."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = SQLiteRepository(
        (tmp_path / "local.db").resolve(),
        clock=_FixedClock(_NOW + timedelta(minutes=30)),
    )
    repository.bootstrap_local_project(
        BootstrapMutation(
            instance_id=_INSTANCE_ID,
            project_id=_LOCAL_PROJECT_ID,
            subject_id=_OWNER_ID,
            request_id=RequestId("req_bootstrap"),
            occurred_at=_NOW,
            project_key="LOCAL",
            project_name="Local",
        )
    )
    connection = sqlite3.connect(repository.database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    timestamp = _timestamp(_NOW)
    try:
        connection.execute(
            """
            INSERT INTO projects (
                id, instance_id, key, name, next_task_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(_SECOND_PROJECT_ID),
                str(_INSTANCE_ID),
                "SECOND",
                "Second",
                1,
                timestamp,
            ),
        )
        identities = (
            (_OWNER_ID, SubjectKind.HUMAN, True),
            (SubjectId("sub_viewer"), SubjectKind.HUMAN, False),
            (SubjectId("sub_agent"), SubjectKind.AGENT, False),
            (SubjectId("sub_operator"), SubjectKind.HUMAN, False),
            (SubjectId("sub_ungranted"), SubjectKind.HUMAN, True),
        )
        for subject_id, kind, administrator in identities:
            if subject_id != _OWNER_ID:
                connection.execute(
                    """
                    INSERT INTO subjects (
                        id, instance_id, kind, handle, display_name, enabled,
                        is_instance_admin, version, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(subject_id),
                        str(_INSTANCE_ID),
                        kind.value,
                        subject_id.value.removeprefix("sub_"),
                        subject_id.value,
                        1,
                        int(administrator),
                        1,
                        str(_OWNER_ID),
                        timestamp,
                        timestamp,
                    ),
                )
            _insert_token(connection, subject_id=subject_id, timestamp=timestamp)
        for subject_id, project_id, role in (
            (_OWNER_ID, _SECOND_PROJECT_ID, ProjectRole.OWNER),
            (SubjectId("sub_viewer"), _LOCAL_PROJECT_ID, ProjectRole.VIEWER),
            (SubjectId("sub_agent"), _LOCAL_PROJECT_ID, ProjectRole.AGENT),
            (SubjectId("sub_operator"), _LOCAL_PROJECT_ID, ProjectRole.OPERATOR),
            (SubjectId("sub_operator"), _SECOND_PROJECT_ID, ProjectRole.OPERATOR),
        ):
            connection.execute(
                """
                INSERT INTO project_grants (
                    instance_id, subject_id, project_id, role, version,
                    granted_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(_INSTANCE_ID),
                    str(subject_id),
                    str(project_id),
                    role.value,
                    1,
                    str(_OWNER_ID),
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    repository.create_task(_task_mutation("local", _LOCAL_PROJECT_ID, seconds=1))
    repository.create_task(_task_mutation("second", _SECOND_PROJECT_ID, seconds=2))
    return repository


def _insert_token(
    connection: sqlite3.Connection,
    *,
    subject_id: SubjectId,
    timestamp: str,
) -> None:
    """Insert one active hash-only Token for a query actor fixture."""
    token_id = _token_id(subject_id)
    connection.execute(
        """
        INSERT INTO tokens (
            id, instance_id, subject_id, token_hash, created_by,
            created_at, activated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(token_id),
            str(_INSTANCE_ID),
            str(subject_id),
            hashlib.sha256(str(token_id).encode("ascii")).hexdigest(),
            str(_OWNER_ID),
            timestamp,
            timestamp,
            _timestamp(_NOW + timedelta(days=1)),
        ),
    )


def _task_mutation(
    suffix: str,
    project_id: ProjectId,
    *,
    seconds: int,
) -> TaskCreationMutation:
    """Build one attributable Task for read authorization tests."""
    return TaskCreationMutation(
        task_id=TaskId(f"tsk_{suffix}"),
        event_id=TaskEventId(f"evt_{suffix}"),
        request_id=RequestId(f"req_{suffix}"),
        project_id=project_id,
        actor_subject_id=_OWNER_ID,
        occurred_at=_NOW + timedelta(seconds=seconds),
        title=f"Task {suffix}",
        objective=f"Read {suffix}",
        priority=50,
    )


def _timestamp(value: datetime) -> str:
    """Serialize one fixed timestamp in canonical SQLite form."""
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _token_id(subject_id: SubjectId) -> TokenId:
    """Derive the fixture's non-secret Token identity."""
    return TokenId(f"tok_{subject_id.value.removeprefix('sub_')}")


def _actor(
    suffix: str,
    *,
    kind: SubjectKind = SubjectKind.HUMAN,
) -> AuthenticatedActor:
    """Build one actor matching a persisted role fixture."""
    subject_id = SubjectId(f"sub_{suffix}")
    return AuthenticatedActor(
        instance_id=_INSTANCE_ID,
        subject_id=subject_id,
        subject_kind=kind,
        token_id=_token_id(subject_id),
    )


def _assert_read_only[T](repository: SQLiteRepository, operation: Callable[[], T]) -> T:
    """Run one query and require byte-identical durable state afterward."""
    before = repository.database_path.read_bytes()
    try:
        return operation()
    finally:
        assert repository.database_path.read_bytes() == before


@pytest.mark.parametrize(
    ("suffix", "kind"),
    [
        ("viewer", SubjectKind.HUMAN),
        ("agent", SubjectKind.AGENT),
        ("operator", SubjectKind.HUMAN),
        ("owner", SubjectKind.HUMAN),
    ],
)
def test_every_cumulative_role_can_use_all_project_read_paths(
    tmp_path: Path,
    suffix: str,
    kind: SubjectKind,
) -> None:
    """Viewer is the sufficient permission for Project data and Task history."""
    repository = _repository(tmp_path)
    actor = _actor(suffix, kind=kind)
    subject_id = actor.subject_id

    status = _assert_read_only(
        repository,
        lambda: repository.get_local_status(
            GetLocalStatus(
                instance_id=_INSTANCE_ID,
                project_id=_LOCAL_PROJECT_ID,
                subject_id=subject_id,
                actor=actor,
            )
        ),
    )
    assert status.subject.id == subject_id
    assert status.grant.role.value == suffix
    assert (
        repository.get_project_by_key(
            GetProjectByKey(
                instance_id=_INSTANCE_ID,
                subject_id=subject_id,
                project_key="LOCAL",
                actor=actor,
            )
        ).id
        == _LOCAL_PROJECT_ID
    )
    assert repository.list_tasks(
        ListTasks(
            project_id=_LOCAL_PROJECT_ID,
            subject_id=subject_id,
            actor=actor,
        )
    ).tasks[0].uid == TaskId("tsk_local")
    assert repository.get_task(
        GetTask(
            project_id=_LOCAL_PROJECT_ID,
            subject_id=subject_id,
            task="LOCAL-1",
            actor=actor,
        )
    ).uid == TaskId("tsk_local")
    assert repository.get_task_details(
        GetTaskDetails(
            project_id=_LOCAL_PROJECT_ID,
            subject_id=subject_id,
            task=TaskId("tsk_local"),
            actor=actor,
        )
    ).task.uid == TaskId("tsk_local")
    assert repository.list_tasks_by_view(
        ListTasksByView(
            subject_id=subject_id,
            project_id=_LOCAL_PROJECT_ID,
            view=TaskListView.ALL,
            actor=actor,
        )
    ).tasks[0].uid == TaskId("tsk_local")
    assert repository.read_task_events_after(
        ReadTaskEvents(
            project_id=_LOCAL_PROJECT_ID,
            subject_id=subject_id,
            task=TaskId("tsk_local"),
            actor=actor,
        )
    ).events[0].task_uid == TaskId("tsk_local")


def test_project_and_instance_lists_filter_to_current_grants(tmp_path: Path) -> None:
    """Listing reveals only currently granted Projects with stable pagination."""
    repository = _repository(tmp_path)
    viewer = _actor("viewer")
    operator = _actor("operator")

    assert tuple(
        project.key
        for project in repository.list_projects(
            ListProjects(
                instance_id=_INSTANCE_ID,
                subject_id=viewer.subject_id,
                actor=viewer,
            )
        )
    ) == ("LOCAL",)
    first = repository.list_tasks_for_instance(
        ListInstanceTasks(
            instance_id=_INSTANCE_ID,
            subject_id=operator.subject_id,
            actor=operator,
            limit=1,
        )
    )
    assert tuple(task.key for task in first.tasks) == ("LOCAL-1",)
    assert first.next_cursor is not None
    second = repository.list_tasks_for_instance(
        ListInstanceTasks(
            instance_id=_INSTANCE_ID,
            subject_id=operator.subject_id,
            actor=operator,
            cursor=first.next_cursor,
            limit=1,
        )
    )
    assert tuple(task.key for task in second.tasks) == ("SECOND-1",)
    assert tuple(
        task.key
        for task in repository.list_tasks_by_view(
            ListTasksByView(
                subject_id=viewer.subject_id,
                instance_id=_INSTANCE_ID,
                actor=viewer,
            )
        ).tasks
    ) == ("LOCAL-1",)


def test_fresh_token_subject_grant_and_context_state_is_required(
    tmp_path: Path,
) -> None:
    """Each read rejects stale credentials, state, grants, and actor mismatches."""
    repository = _repository(tmp_path)
    viewer = _actor("viewer")
    command = ListTasks(
        project_id=_LOCAL_PROJECT_ID,
        subject_id=viewer.subject_id,
        actor=viewer,
    )

    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(
            "UPDATE tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?",
            (
                _timestamp(_NOW + timedelta(minutes=2)),
                str(_OWNER_ID),
                str(viewer.token_id),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AuthenticationFailedError):
        repository.list_tasks(command)

    repository = _repository(tmp_path / "disabled")
    viewer = _actor("viewer")
    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(
            "UPDATE subjects SET enabled = 0 WHERE id = ?",
            (str(viewer.subject_id),),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AuthenticationFailedError):
        repository.list_tasks(
            ListTasks(
                project_id=_LOCAL_PROJECT_ID,
                subject_id=viewer.subject_id,
                actor=viewer,
            )
        )

    repository = _repository(tmp_path / "grant")
    viewer = _actor("viewer")
    connection = sqlite3.connect(repository.database_path)
    try:
        connection.execute(
            "DELETE FROM project_grants WHERE project_id = ? AND subject_id = ?",
            (str(_LOCAL_PROJECT_ID), str(viewer.subject_id)),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PermissionDeniedError):
        repository.get_task(
            GetTask(
                project_id=_LOCAL_PROJECT_ID,
                subject_id=viewer.subject_id,
                task=TaskId("tsk_local"),
                actor=viewer,
            )
        )

    operator = _actor("operator")
    with pytest.raises(PermissionDeniedError):
        repository.list_tasks(
            ListTasks(
                project_id=_LOCAL_PROJECT_ID,
                subject_id=viewer.subject_id,
                actor=operator,
            )
        )


def test_ungranted_and_unknown_projects_share_non_disclosing_failure(
    tmp_path: Path,
) -> None:
    """Project-scoped reads do not reveal whether an inaccessible ID exists."""
    repository = _repository(tmp_path)
    viewer = _actor("viewer")
    for project_id in (_SECOND_PROJECT_ID, ProjectId("prj_unknown")):
        with pytest.raises(PermissionDeniedError):
            repository.list_tasks(
                ListTasks(
                    project_id=project_id,
                    subject_id=viewer.subject_id,
                    actor=viewer,
                )
            )


def test_instance_administrator_without_grant_has_no_project_data_access(
    tmp_path: Path,
) -> None:
    """Instance administration never implies Viewer permission."""
    repository = _repository(tmp_path)
    administrator = _actor("ungranted")
    assert (
        repository.list_projects(
            ListProjects(
                instance_id=_INSTANCE_ID,
                subject_id=administrator.subject_id,
                actor=administrator,
            )
        )
        == ()
    )
    with pytest.raises(PermissionDeniedError):
        repository.get_task_details(
            GetTaskDetails(
                project_id=_LOCAL_PROJECT_ID,
                subject_id=administrator.subject_id,
                task=TaskId("tsk_local"),
                actor=administrator,
            )
        )


def test_initialized_identity_store_rejects_legacy_unauthenticated_reads(
    tmp_path: Path,
) -> None:
    """Presence of Phase 5 Tokens closes the temporary bootstrap read path."""
    repository = _repository(tmp_path)
    with pytest.raises(AuthenticationRequiredError):
        repository.list_projects(
            ListProjects(instance_id=_INSTANCE_ID, subject_id=_OWNER_ID)
        )
