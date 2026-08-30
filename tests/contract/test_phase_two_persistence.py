"""Backend-neutral cumulative Phase 2 repository conformance tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from typing import TYPE_CHECKING, cast

import pytest
from tests.contract.phase_one import bootstrap_mutation, task_mutation
from tests.contract.phase_two import (
    DeterministicClock,
    DeterministicIdentifierFactory,
    PhaseTwoRepositoryFactory,
    phase_two_time,
    project_mutation,
    project_task_mutation,
)
from tests.contract.test_phase_one_persistence import PhaseOnePersistenceContract

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    GetLocalStatus,
    GetProjectByKey,
    GetTask,
    ListInstanceTasks,
    ListProjects,
    ListTasks,
)
from workaholic.domain import SubjectId
from workaholic.persistence.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.contract.phase_two import PhaseTwoIdentifierFactory

    from workaholic.application import (
        BootstrapResult,
        Clock,
        ProjectCreationResult,
        WorkaholicRepository,
    )

pytestmark = pytest.mark.contract


@dataclass(frozen=True, slots=True)
class _SQLiteRepositoryFactory:
    """Construct isolated SQLite repositories and deterministic dependencies."""

    def create(self, root: Path) -> WorkaholicRepository:
        """Construct one exact-version SQLite repository.

        Args:
            root: Test-owned persistence root.

        Returns:
            SQLite repository without initializing its store.

        """
        return cast("WorkaholicRepository", SQLiteRepository(root / "local.db"))

    def clock(self, *, offset: int = 0) -> Clock:
        """Construct a deterministic advancing clock.

        Args:
            offset: Nonnegative offset from the Phase 2 fixture time.

        Returns:
            Deterministic clock at the requested initial instant.

        """
        return DeterministicClock(current=phase_two_time(offset))

    def identifiers(self, namespace: str) -> PhaseTwoIdentifierFactory:
        """Construct a deterministic identifier sequence.

        Args:
            namespace: Scenario-specific identifier namespace.

        Returns:
            Thread-safe deterministic identifier factory.

        """
        return DeterministicIdentifierFactory(namespace)


class PhaseTwoPersistenceContract(PhaseOnePersistenceContract):
    """Reusable cumulative observable contract for a Phase 2 repository."""

    @pytest.fixture
    def repository_factory(self) -> PhaseTwoRepositoryFactory:
        """Provide the adapter factory under cumulative conformance.

        Returns:
            Factory with exact-version repository and deterministic dependencies.

        """
        message = "A concrete Phase 2 repository contract must provide its factory."
        raise NotImplementedError(message)

    def test_factory_dependencies_are_deterministic_and_isolated(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
    ) -> None:
        """Factory clocks and identifiers are reproducible per scenario."""
        first_clock = repository_factory.clock(offset=5)
        second_clock = repository_factory.clock(offset=5)
        assert first_clock.now() == phase_two_time(5)
        assert first_clock.now() == phase_two_time(6)
        assert second_clock.now() == phase_two_time(5)

        first_ids = repository_factory.identifiers("alpha")
        second_ids = repository_factory.identifiers("alpha")
        assert str(first_ids.new_instance_id()) == "ins_alpha_1"
        assert str(first_ids.new_project_id()) == "prj_alpha_1"
        assert str(first_ids.new_project_id()) == "prj_alpha_2"
        assert str(second_ids.new_instance_id()) == "ins_alpha_1"

    def test_projects_keep_independent_numbers_keys_and_restart_state(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Each immutable Project owns an independent durable Task sequence."""
        root = tmp_path / "store"
        repository, bootstrap = _bootstrapped(repository_factory, root, "base")
        docs = repository.create_project(project_mutation(bootstrap, "docs"))

        acme_tasks = (
            repository.create_task(task_mutation(bootstrap, "acme_one")),
            repository.create_task(task_mutation(bootstrap, "acme_two")),
        )
        docs_tasks = (
            repository.create_task(
                project_task_mutation(
                    docs.project,
                    bootstrap.subject.id,
                    "docs_one",
                )
            ),
            repository.create_task(
                project_task_mutation(
                    docs.project,
                    bootstrap.subject.id,
                    "docs_two",
                    occurred_at=phase_two_time(2),
                )
            ),
        )

        assert tuple((task.number, task.key) for task in acme_tasks) == (
            (1, "ACME-1"),
            (2, "ACME-2"),
        )
        assert tuple((task.number, task.key) for task in docs_tasks) == (
            (1, "DOCS-1"),
            (2, "DOCS-2"),
        )
        assert repository.list_projects(
            ListProjects(
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
            )
        ) == (bootstrap.project, docs.project)

        with pytest.raises(ApplicationError) as duplicate:
            repository.create_project(
                project_mutation(
                    bootstrap,
                    "renamed",
                    name="Renamed documentation",
                )
            )
        assert duplicate.value.code is ApplicationErrorCode.PROJECT_KEY_CONFLICT

        reopened = repository_factory.create(root)
        assert (
            reopened.list_tasks(
                ListTasks(
                    project_id=bootstrap.project.id,
                    subject_id=bootstrap.subject.id,
                )
            ).tasks
            == acme_tasks
        )
        assert (
            reopened.list_tasks(
                ListTasks(
                    project_id=docs.project.id,
                    subject_id=bootstrap.subject.id,
                )
            ).tasks
            == docs_tasks
        )
        assert (
            reopened.get_project_by_key(
                GetProjectByKey(
                    instance_id=bootstrap.instance.id,
                    subject_id=bootstrap.subject.id,
                    project_key="DOCS",
                )
            )
            == docs.project
        )

    def test_project_duplicate_and_idempotency_races_are_atomic(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Concurrent equivalent replay converges and duplicate keys have one winner."""
        replay_root = tmp_path / "replay"
        _, replay_bootstrap = _bootstrapped(
            repository_factory,
            replay_root,
            "replay_base",
        )
        replay_barrier = Barrier(2)

        def replay(label: str) -> ProjectCreationResult | ApplicationError:
            """Race one equivalent idempotent Project request."""
            repository = repository_factory.create(replay_root)
            replay_barrier.wait()
            try:
                return repository.create_project(
                    project_mutation(
                        replay_bootstrap,
                        label,
                        idempotency_key="create-docs-once",
                    )
                )
            except ApplicationError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            replay_results = tuple(executor.map(replay, ("replay_one", "replay_two")))

        assert all(
            not isinstance(result, ApplicationError) for result in replay_results
        )
        first_replay = cast("ProjectCreationResult", replay_results[0])
        second_replay = cast("ProjectCreationResult", replay_results[1])
        assert second_replay == first_replay
        replay_projects = repository_factory.create(replay_root).list_projects(
            ListProjects(
                instance_id=replay_bootstrap.instance.id,
                subject_id=replay_bootstrap.subject.id,
            )
        )
        assert tuple(project.key for project in replay_projects) == ("ACME", "DOCS")

        duplicate_root = tmp_path / "duplicate"
        _, duplicate_bootstrap = _bootstrapped(
            repository_factory,
            duplicate_root,
            "duplicate_base",
        )
        duplicate_barrier = Barrier(2)

        def duplicate(label: str) -> ProjectCreationResult | ApplicationError:
            """Race one non-idempotent duplicate Project key."""
            repository = repository_factory.create(duplicate_root)
            duplicate_barrier.wait()
            try:
                return repository.create_project(
                    project_mutation(
                        duplicate_bootstrap,
                        label,
                        key="OPS",
                        name="Operations",
                    )
                )
            except ApplicationError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            duplicate_results = tuple(
                executor.map(duplicate, ("duplicate_one", "duplicate_two"))
            )

        successes = tuple(
            result
            for result in duplicate_results
            if not isinstance(result, ApplicationError)
        )
        failures = tuple(
            result
            for result in duplicate_results
            if isinstance(result, ApplicationError)
        )
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code is ApplicationErrorCode.PROJECT_KEY_CONFLICT

    def test_instances_are_isolated_and_allow_the_same_project_keys(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """Unrelated stores retain independent identity and key namespaces."""
        first, first_bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "first",
            "first",
        )
        second, second_bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "second",
            "second",
        )
        first_docs = first.create_project(
            project_mutation(first_bootstrap, "first_docs")
        )
        second_docs = second.create_project(
            project_mutation(second_bootstrap, "second_docs")
        )
        first_task = first.create_task(
            project_task_mutation(
                first_docs.project,
                first_bootstrap.subject.id,
                "first_task",
            )
        )
        second_task = second.create_task(
            project_task_mutation(
                second_docs.project,
                second_bootstrap.subject.id,
                "second_task",
            )
        )

        assert first_bootstrap.instance.id != second_bootstrap.instance.id
        assert first_docs.project.key == second_docs.project.key == "DOCS"
        assert first_task.key == second_task.key == "DOCS-1"
        assert first_task.uid != second_task.uid
        assert first.list_projects(
            ListProjects(
                instance_id=first_bootstrap.instance.id,
                subject_id=first_bootstrap.subject.id,
            )
        ) == (first_bootstrap.project, first_docs.project)
        assert second.list_projects(
            ListProjects(
                instance_id=second_bootstrap.instance.id,
                subject_id=second_bootstrap.subject.id,
            )
        ) == (second_bootstrap.project, second_docs.project)

    def test_all_project_order_pagination_and_cursor_scope_are_strict(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """All-Project pages are ordered and cursors bind every selection scope."""
        root = tmp_path / "store"
        repository, bootstrap = _bootstrapped(repository_factory, root, "base")
        docs = repository.create_project(project_mutation(bootstrap, "docs"))
        expected = (
            repository.create_task(task_mutation(bootstrap, "acme_one")),
            repository.create_task(
                project_task_mutation(
                    docs.project,
                    bootstrap.subject.id,
                    "docs_one",
                )
            ),
            repository.create_task(
                project_task_mutation(
                    docs.project,
                    bootstrap.subject.id,
                    "docs_two",
                    occurred_at=phase_two_time(2),
                )
            ),
        )
        first = repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                limit=2,
            )
        )
        assert first.tasks == expected[:2]
        assert first.next_cursor is not None
        assert first.next_cursor.startswith("v2.")
        final = repository_factory.create(root).list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                cursor=first.next_cursor,
                limit=2,
            )
        )
        assert final.tasks == expected[2:]
        assert final.next_cursor is None

        project_page = repository.list_tasks(
            ListTasks(
                profile="alpha",
                project_id=docs.project.id,
                subject_id=bootstrap.subject.id,
                limit=1,
            )
        )
        assert project_page.next_cursor is not None
        invalid_reads: tuple[Callable[[], object], ...] = (
            lambda: repository.list_tasks(
                ListTasks(
                    profile="alpha",
                    project_id=docs.project.id,
                    subject_id=bootstrap.subject.id,
                    cursor=first.next_cursor,
                    limit=1,
                )
            ),
            lambda: repository.list_tasks_for_instance(
                ListInstanceTasks(
                    profile="alpha",
                    instance_id=bootstrap.instance.id,
                    subject_id=bootstrap.subject.id,
                    cursor=project_page.next_cursor,
                    limit=1,
                )
            ),
            lambda: repository.list_tasks_for_instance(
                ListInstanceTasks(
                    profile="beta",
                    instance_id=bootstrap.instance.id,
                    subject_id=bootstrap.subject.id,
                    cursor=first.next_cursor,
                    limit=1,
                )
            ),
        )
        for read in invalid_reads:
            with pytest.raises(ApplicationError) as captured:
                read()
            assert captured.value.code is ApplicationErrorCode.INVALID_INPUT

        repeated = repository.list_tasks_for_instance(
            ListInstanceTasks(
                profile="alpha",
                instance_id=bootstrap.instance.id,
                subject_id=bootstrap.subject.id,
                limit=2,
            )
        )
        assert repeated == first
        next_docs = repository.create_task(
            project_task_mutation(
                docs.project,
                bootstrap.subject.id,
                "docs_three",
                occurred_at=phase_two_time(3),
            )
        )
        assert (next_docs.number, next_docs.key) == (3, "DOCS-3")

    def test_every_phase_two_operation_revalidates_authorization(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """An unknown Subject cannot administer or read any Project scope."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "base",
        )
        docs = repository.create_project(project_mutation(bootstrap, "docs"))
        created = repository.create_task(
            project_task_mutation(
                docs.project,
                bootstrap.subject.id,
                "docs_task",
            )
        )
        unknown = SubjectId("sub_unknown")
        forbidden_project = project_mutation(
            bootstrap,
            "forbidden",
            key="OPS",
            name="Operations",
        ).model_copy(update={"actor_subject_id": unknown})
        operations: tuple[Callable[[], object], ...] = (
            lambda: repository.create_project(forbidden_project),
            lambda: repository.list_projects(
                ListProjects(
                    instance_id=bootstrap.instance.id,
                    subject_id=unknown,
                )
            ),
            lambda: repository.get_project_by_key(
                GetProjectByKey(
                    instance_id=bootstrap.instance.id,
                    subject_id=unknown,
                    project_key="DOCS",
                )
            ),
            lambda: repository.list_tasks_for_instance(
                ListInstanceTasks(
                    instance_id=bootstrap.instance.id,
                    subject_id=unknown,
                )
            ),
            lambda: repository.get_task(
                GetTask(
                    project_id=docs.project.id,
                    subject_id=unknown,
                    task=created.uid,
                )
            ),
        )

        for operation in operations:
            with pytest.raises(ApplicationError) as captured:
                operation()
            assert captured.value.code is ApplicationErrorCode.PERMISSION_DENIED

    def test_cross_project_event_failure_rolls_back_allocation(
        self,
        repository_factory: PhaseTwoRepositoryFactory,
        tmp_path: Path,
    ) -> None:
        """A global event collision rolls back another Project's Task number."""
        repository, bootstrap = _bootstrapped(
            repository_factory,
            tmp_path / "store",
            "base",
        )
        docs = repository.create_project(project_mutation(bootstrap, "docs"))
        repository.create_task(
            project_task_mutation(
                bootstrap.project,
                bootstrap.subject.id,
                "acme",
                event_label="shared",
            )
        )

        with pytest.raises(ApplicationError) as captured:
            repository.create_task(
                project_task_mutation(
                    docs.project,
                    bootstrap.subject.id,
                    "rejected",
                    event_label="shared",
                    occurred_at=phase_two_time(1),
                )
            )
        assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
        assert (
            repository.list_tasks(
                ListTasks(
                    project_id=docs.project.id,
                    subject_id=bootstrap.subject.id,
                )
            ).tasks
            == ()
        )

        accepted = repository.create_task(
            project_task_mutation(
                docs.project,
                bootstrap.subject.id,
                "accepted",
                occurred_at=phase_two_time(2),
            )
        )
        assert (accepted.number, accepted.key) == (1, "DOCS-1")


class TestSQLitePhaseTwoPersistence(PhaseTwoPersistenceContract):
    """Apply the cumulative repository contract to production SQLite."""

    @pytest.fixture
    def repository_factory(self) -> PhaseTwoRepositoryFactory:
        """Provide the production SQLite factory.

        Returns:
            SQLite factory under cumulative contract.

        """
        return _SQLiteRepositoryFactory()


def _bootstrapped(
    factory: PhaseTwoRepositoryFactory,
    root: Path,
    label: str,
) -> tuple[WorkaholicRepository, BootstrapResult]:
    """Create one repository with a committed ACME bootstrap graph.

    Args:
        factory: Adapter factory under cumulative contract.
        root: Test-owned persistence root.
        label: Stable bootstrap identity suffix.

    Returns:
        Repository and committed bootstrap result.

    """
    repository = factory.create(root)
    bootstrap = repository.bootstrap_local_project(
        bootstrap_mutation(
            label,
            occurred_at=phase_two_time(),
        )
    )
    status = repository.get_local_status(
        GetLocalStatus(
            instance_id=bootstrap.instance.id,
            project_id=bootstrap.project.id,
            subject_id=bootstrap.subject.id,
        )
    )
    assert status.schema_version == 5
    return repository, bootstrap
