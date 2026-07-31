"""Backend-neutral Phase 1 repository conformance tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from tests.contract.phase_one import (
    PhaseOneRepositoryFactory,
    bootstrap_mutation,
    later_timestamp,
    task_mutation,
)

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetLocalStatus,
    GetTask,
    ListProjects,
    ListTasks,
)
from workaholic.domain import SubjectId, TaskId
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from workaholic.application import BootstrapResult, WorkaholicRepository

pytestmark = pytest.mark.contract


@dataclass(frozen=True, slots=True)
class _SQLiteRepositoryFactory:
    """Construct isolated SQLite repositories for the shared contract."""

    def create(self, root: Path) -> WorkaholicRepository:
        """Construct a repository over one test-owned SQLite path.

        Args:
            root: Test-owned persistence root.

        Returns:
            SQLite repository without initializing its store.

        """
        return cast(
            "WorkaholicRepository",
            SQLiteRepository(root / "local.db"),
        )


class PhaseOnePersistenceContract:
    """Reusable observable contract for a Phase 1 repository adapter."""

    @pytest.fixture
    def repository_factory(self) -> PhaseOneRepositoryFactory:
        """Provide the adapter factory under conformance.

        Returns:
            Factory that constructs independent connections to one backend.

        """
        message = "A concrete repository contract must provide its factory."
        raise NotImplementedError(message)

    def test_construction_and_failed_read_do_not_initialize_storage(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Repository construction and rejected reads leave storage absent."""
        root = tmp_path / "missing"
        repository = repository_factory.create(root)
        command = GetLocalStatus(
            instance_id=bootstrap_mutation("missing").instance_id,
            project_id=bootstrap_mutation("missing").project_id,
            subject_id=bootstrap_mutation("missing").subject_id,
        )

        with pytest.raises(ApplicationError) as captured:
            repository.get_local_status(command)

        assert captured.value.code is ApplicationErrorCode.SCHEMA_UNSUPPORTED
        assert not root.exists()

    def test_bootstrap_commits_one_durable_authorized_graph(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Bootstrap persists stable identity and Owner authorization."""
        root = tmp_path / "store"
        repository = repository_factory.create(root)
        mutation = bootstrap_mutation("first")

        result = repository.bootstrap_local_project(mutation)
        reopened = repository_factory.create(root)
        status = reopened.get_local_status(
            GetLocalStatus(
                instance_id=result.instance.id,
                project_id=result.project.id,
                subject_id=result.subject.id,
            )
        )

        assert status.instance == result.instance
        assert status.project == result.project
        assert status.subject == result.subject
        assert status.grant == result.grant
        assert reopened.list_projects(
            ListProjects(
                instance_id=result.instance.id,
                subject_id=result.subject.id,
            )
        ) == (result.project,)

    def test_bootstrap_replay_and_conflicts_are_stable(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Bootstrap retries replay, while changed requests remain conflicts."""
        root = tmp_path / "store"
        repository = repository_factory.create(root)
        first = repository.bootstrap_local_project(
            bootstrap_mutation(
                "first",
                idempotency_key="bootstrap-once",
            )
        )

        replayed = repository.bootstrap_local_project(
            bootstrap_mutation(
                "replay",
                idempotency_key="bootstrap-once",
                occurred_at=later_timestamp(1),
            )
        )
        assert replayed == first

        with pytest.raises(ApplicationError) as idempotency_error:
            repository.bootstrap_local_project(
                bootstrap_mutation(
                    "changed",
                    project_key="OTHER",
                    idempotency_key="bootstrap-once",
                )
            )
        assert idempotency_error.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT

        with pytest.raises(ApplicationError) as project_error:
            repository.bootstrap_local_project(
                bootstrap_mutation("second", project_key="OTHER")
            )
        assert project_error.value.code is ApplicationErrorCode.PROJECT_KEY_CONFLICT

    def test_task_creation_allocates_stable_keys_and_attribution(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Created Tasks expose durable identity, defaults, and attribution."""
        root = tmp_path / "store"
        repository, bootstrap = _bootstrapped(repository_factory, root)

        first = repository.create_task(task_mutation(bootstrap, "first"))
        second = repository.create_task(
            task_mutation(
                bootstrap,
                "second",
                title="Second task",
                objective="An explicit outcome",
                priority=80,
                occurred_at=later_timestamp(2),
            )
        )
        reopened = repository_factory.create(root)

        assert (first.number, first.key, first.version) == (1, "ACME-1", 1)
        assert (first.state.value, first.priority) == ("open", 50)
        assert first.created_by == bootstrap.subject.id
        assert first.created_at == first.updated_at
        assert (second.number, second.key, second.version) == (2, "ACME-2", 1)
        assert second.objective == "An explicit outcome"
        assert second.priority == 80
        assert (
            reopened.get_task(
                GetTask(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    task=first.uid,
                )
            )
            == first
        )
        assert (
            reopened.get_task(
                GetTask(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    task=second.key,
                )
            )
            == second
        )

    def test_task_idempotency_replays_without_allocating_again(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Matching retries replay exactly and changed semantic input conflicts."""
        repository, bootstrap = _bootstrapped(repository_factory, tmp_path / "store")
        first = repository.create_task(
            task_mutation(
                bootstrap,
                "first",
                idempotency_key="task-once",
            )
        )

        replayed = repository.create_task(
            task_mutation(
                bootstrap,
                "replay",
                title="Task first",
                idempotency_key="task-once",
                occurred_at=later_timestamp(10),
            )
        )

        assert replayed == first
        with pytest.raises(ApplicationError) as captured:
            repository.create_task(
                task_mutation(
                    bootstrap,
                    "changed",
                    title="Changed task",
                    idempotency_key="task-once",
                )
            )
        assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT

        second = repository.create_task(task_mutation(bootstrap, "second"))
        assert (second.number, second.key) == (2, "ACME-2")

    def test_every_operation_revalidates_authorization(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """An unknown local Subject cannot read or mutate Project state."""
        repository, bootstrap = _bootstrapped(repository_factory, tmp_path / "store")
        unknown = SubjectId("sub_unknown")
        forbidden_mutation = task_mutation(bootstrap, "forbidden").model_copy(
            update={"actor_subject_id": unknown}
        )
        operations: tuple[Callable[[], object], ...] = (
            lambda: repository.get_local_status(
                GetLocalStatus(
                    instance_id=bootstrap.instance.id,
                    project_id=bootstrap.project.id,
                    subject_id=unknown,
                )
            ),
            lambda: repository.list_projects(
                ListProjects(
                    instance_id=bootstrap.instance.id,
                    subject_id=unknown,
                )
            ),
            lambda: repository.list_tasks(
                ListTasks(
                    project_id=bootstrap.project.id,
                    subject_id=unknown,
                )
            ),
            lambda: repository.get_task(
                GetTask(
                    project_id=bootstrap.project.id,
                    subject_id=unknown,
                    task=TaskId("tsk_unknown"),
                )
            ),
            lambda: repository.create_task(forbidden_mutation),
        )

        for operation in operations:
            with pytest.raises(ApplicationError) as captured:
                operation()
            assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED

    def test_pagination_is_opaque_stable_and_ascending(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Pagination yields every Task once in deterministic number order."""
        repository, bootstrap = _bootstrapped(repository_factory, tmp_path / "store")
        expected = tuple(
            repository.create_task(
                task_mutation(
                    bootstrap,
                    f"task{number}",
                    occurred_at=later_timestamp(number),
                )
            )
            for number in range(1, 6)
        )

        first = repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                limit=2,
            )
        )
        assert first.tasks == expected[:2]
        assert first.next_cursor is not None
        second = repository_factory.create(tmp_path / "store").list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                cursor=first.next_cursor,
                limit=2,
            )
        )
        assert second.tasks == expected[2:4]
        assert second.next_cursor is not None
        final = repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
                cursor=second.next_cursor,
                limit=2,
            )
        )
        assert final.tasks == expected[4:]
        assert final.next_cursor is None

        with pytest.raises(ApplicationError) as captured:
            repository.list_tasks(
                ListTasks(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                    cursor="not-a-supported-cursor",
                )
            )
        assert captured.value.code is ApplicationErrorCode.INVALID_INPUT

    def test_event_failure_rolls_back_task_and_allocation(
        self,
        repository_factory: PhaseOneRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """A rejected event commits neither its Task nor its allocated number."""
        repository, bootstrap = _bootstrapped(repository_factory, tmp_path / "store")
        first = repository.create_task(
            task_mutation(
                bootstrap,
                "first",
                event_label="shared",
            )
        )
        duplicate_event = task_mutation(
            bootstrap,
            "rejected",
            event_label="shared",
            occurred_at=later_timestamp(2),
        )

        with pytest.raises(ApplicationError) as captured:
            repository.create_task(duplicate_event)
        assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE

        page = repository.list_tasks(
            ListTasks(
                project_id=bootstrap.project.id,
                subject_id=bootstrap.subject.id,
            )
        )
        assert page.tasks == (first,)
        next_task = repository.create_task(
            task_mutation(
                bootstrap,
                "after",
                occurred_at=later_timestamp(3),
            )
        )
        assert (next_task.number, next_task.key) == (2, "ACME-2")


class TestSQLitePhaseOnePersistence(PhaseOnePersistenceContract):
    """Apply the shared Phase 1 repository contract to SQLite."""

    @pytest.fixture
    def repository_factory(self) -> PhaseOneRepositoryFactory:
        """Provide the production SQLite repository factory.

        Returns:
            SQLite factory under contract.

        """
        return _SQLiteRepositoryFactory()


def _bootstrapped(
    factory: PhaseOneRepositoryFactory,
    root: Path,
) -> tuple[WorkaholicRepository, BootstrapResult]:
    """Create one repository with a committed ACME bootstrap graph.

    Args:
        factory: Adapter factory under contract.
        root: Test-owned persistence root.

    Returns:
        Repository and its committed bootstrap result.

    """
    repository = factory.create(root)
    return repository, repository.bootstrap_local_project(
        bootstrap_mutation("bootstrap")
    )
