"""Transport-neutral cumulative Phase 2 Session conformance tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from tests.contract.phase_two import (
    DeterministicClock,
    DeterministicIdentifierFactory,
    PhaseTwoSessionFactory,
)
from tests.contract.test_phase_one_session import PhaseOneSessionContract

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.composition import (
    LocalCompositionFactories,
    create_local_session,
)
from workaholic.context import CONTEXT_FILENAME
from workaholic.persistence.sqlite import (
    SQLiteLocalActorSelector,
    SQLiteRepository,
)
from workaholic.session import (
    ContextRequest,
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workaholic.session import WorkaholicSession

pytestmark = pytest.mark.contract


@dataclass(slots=True)
class _LocalSessionFactory:
    """Construct deterministic production LocalSessions over isolated profiles."""

    clock_source: DeterministicClock = field(default_factory=DeterministicClock)
    identifier_source: DeterministicIdentifierFactory = field(
        default_factory=lambda: DeterministicIdentifierFactory("contract")
    )

    def create(self, root: Path, workspace: Path) -> WorkaholicSession:
        """Construct a deterministic one-profile LocalSession.

        Args:
            root: Test-owned parent for profile storage.
            workspace: Existing exact Workspace directory.

        Returns:
            Production LocalSession isolated from operator state.

        """
        return self.create_profiled(
            root,
            workspace,
            profiles=("local",),
            default_profile="local",
        )

    def create_profiled(
        self,
        root: Path,
        workspace: Path,
        *,
        profiles: tuple[str, ...],
        default_profile: str,
        environment_profile: str | None = None,
    ) -> WorkaholicSession:
        """Construct a deterministic profile-aware LocalSession.

        Args:
            root: Test-owned parent for profile storage.
            workspace: Existing exact Workspace directory.
            profiles: Ordered configured embedded profile names.
            default_profile: Configured default profile.
            environment_profile: Optional trusted environment selection.

        Returns:
            Production LocalSession over generated trusted configuration.

        """
        config_directory = root.parent / f".{root.name}-config"
        _write_profile_registry(
            config_directory,
            root=root,
            profiles=profiles,
            default_profile=default_profile,
        )
        environment = {"WORKAHOLIC_CONFIG_DIR": str(config_directory)}
        if environment_profile is not None:
            environment["WORKAHOLIC_PROFILE"] = environment_profile
        return create_local_session(
            cwd=workspace,
            environment=environment,
            factories=LocalCompositionFactories(
                repository=SQLiteRepository,
                identity=SQLiteLocalActorSelector,
                clock=lambda: self.clock_source,
                identifiers=lambda: self.identifier_source,
            ),
        )


class PhaseTwoSessionContract(PhaseOneSessionContract):
    """Reusable cumulative observable contract for a Phase 2 Session."""

    @pytest.fixture
    def session_factory(self) -> PhaseTwoSessionFactory:
        """Provide the Session factory under cumulative conformance.

        Returns:
            Factory that owns trusted configuration, dependencies, and state.

        """
        message = "A concrete Phase 2 Session contract must provide its factory."
        raise NotImplementedError(message)

    def test_multi_project_context_selection_and_pagination_survive_restart(
        self,
        session_factory: PhaseTwoSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Bound defaults and explicit all-Project selection remain durable."""
        root = tmp_path / "data"
        acme_workspace = _workspace(tmp_path, "acme")
        docs_workspace = _workspace(tmp_path, "Documentation Ω")
        unbound_workspace = _workspace(tmp_path, "unbound")
        acme_session = session_factory.create(root, acme_workspace)
        bootstrap = acme_session.up(
            UpRequest(project_key="ACME", project_name="Acme delivery")
        )
        docs = acme_session.create_project(
            ProjectCreateRequest(
                key="DOCS",
                name="Documentation Ω",
                idempotency_key="create-docs-once",
            )
        )
        bound = acme_session.bind_project(
            ProjectBindRequest(project="DOCS", path=docs_workspace)
        )
        assert bound.project == docs.project
        assert bound.workspace_root == docs_workspace
        assert bound.context_source == docs_workspace / CONTEXT_FILENAME

        acme_task = acme_session.create_task(TaskCreateRequest(title="Acme task"))
        docs_session = session_factory.create(root, docs_workspace)
        docs_task_one = docs_session.create_task(
            TaskCreateRequest(title="Bound documentation task")
        )
        unbound_session = session_factory.create(root, unbound_workspace)
        docs_task_two = unbound_session.create_task(
            TaskCreateRequest(
                title="Explicit documentation task",
                project="DOCS",
            )
        )

        assert acme_task.key == "ACME-1"
        assert docs_task_one.key == "DOCS-1"
        assert docs_task_two.key == "DOCS-2"
        assert acme_session.list_tasks(TaskListRequest()).tasks == (acme_task,)
        assert docs_session.list_tasks(TaskListRequest()).tasks == (
            docs_task_one,
            docs_task_two,
        )
        assert (
            unbound_session.get_task(TaskGetRequest(task="DOCS-1", project="DOCS"))
            == docs_task_one
        )

        first = unbound_session.list_tasks(TaskListRequest(all_projects=True, limit=2))
        assert first.tasks == (acme_task, docs_task_one)
        assert first.next_cursor is not None
        restarted = session_factory.create(root, unbound_workspace)
        final = restarted.list_tasks(
            TaskListRequest(
                all_projects=True,
                cursor=first.next_cursor,
                limit=2,
            )
        )
        assert final.tasks == (docs_task_two,)
        assert final.next_cursor is None

        with pytest.raises(ApplicationError) as wrong_scope:
            restarted.list_tasks(
                TaskListRequest(
                    project="DOCS",
                    cursor=first.next_cursor,
                    limit=1,
                )
            )
        assert wrong_scope.value.code is ApplicationErrorCode.INVALID_INPUT

        assert restarted.context(ContextRequest(project="ACME")).workspace_root is None
        assert (
            session_factory.create(root, acme_workspace)
            .context(ContextRequest())
            .workspace_root
            == acme_workspace
        )
        assert (
            session_factory.create(root, docs_workspace)
            .context(ContextRequest())
            .workspace_root
            == docs_workspace
        )

        repeated = restarted.list_tasks(TaskListRequest(all_projects=True, limit=2))
        assert repeated == first
        docs_task_three = restarted.create_task(
            TaskCreateRequest(title="Third documentation task", project="DOCS")
        )
        assert (docs_task_three.number, docs_task_three.key) == (3, "DOCS-3")
        assert bootstrap.instance.id == bound.instance.id

    def test_binding_is_idempotent_conflict_safe_and_explicitly_replaceable(
        self,
        session_factory: PhaseTwoSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Bindings never change valid context without explicit replacement."""
        root = tmp_path / "data"
        acme_workspace = _workspace(tmp_path, "acme")
        docs_workspace = _workspace(tmp_path, "docs")
        session = session_factory.create(root, acme_workspace)
        session.up(UpRequest(project_key="ACME"))
        docs = session.create_project(
            ProjectCreateRequest(key="DOCS", name="Documentation")
        )

        first = session.bind_project(
            ProjectBindRequest(project="DOCS", path=docs_workspace)
        )
        replay = session.bind_project(
            ProjectBindRequest(project="DOCS", path=docs_workspace)
        )
        assert replay == first
        original_context = (docs_workspace / CONTEXT_FILENAME).read_bytes()

        with pytest.raises(ApplicationError) as conflict:
            session.bind_project(
                ProjectBindRequest(project="ACME", path=docs_workspace)
            )
        assert conflict.value.code is ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT
        assert (docs_workspace / CONTEXT_FILENAME).read_bytes() == original_context

        replaced = session.bind_project(
            ProjectBindRequest(
                project="ACME",
                path=docs_workspace,
                replace=True,
            )
        )
        assert replaced.project.key == "ACME"
        assert replaced.project.id != docs.project.id
        assert (
            session_factory.create(root, docs_workspace)
            .status(StatusRequest())
            .project.key
            == "ACME"
        )

    def test_nearer_hostile_context_never_falls_back_to_valid_parent(
        self,
        session_factory: PhaseTwoSessionFactory,
        tmp_path: Path,
    ) -> None:
        """A malformed nearer context blocks trusted parent discovery."""
        root = tmp_path / "data"
        parent = _workspace(tmp_path, "parent")
        child = parent / "child"
        child.mkdir()
        session_factory.create(root, parent).up(UpRequest(project_key="ACME"))
        hostile = child / CONTEXT_FILENAME
        hostile.write_text(
            "WORKAHOLIC_CONTEXT_VERSION=1\nSECRET=value\n",
            encoding="utf-8",
        )
        before = hostile.read_bytes()

        with pytest.raises(ApplicationError) as captured:
            session_factory.create(root, child).status(StatusRequest())

        assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
        assert hostile.read_bytes() == before
        assert not any(path.name == "local.db" for path in child.iterdir())

    def test_profiles_isolate_instances_while_allowing_the_same_keys(
        self,
        session_factory: PhaseTwoSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Configured profiles own unrelated Instances and Project namespaces."""
        root = tmp_path / "profiles"
        alpha_workspace = _workspace(tmp_path, "alpha")
        beta_workspace = _workspace(tmp_path, "beta")
        unbound_workspace = _workspace(tmp_path, "unbound")
        profiles = ("alpha", "beta")
        alpha = session_factory.create_profiled(
            root,
            alpha_workspace,
            profiles=profiles,
            default_profile="alpha",
        )
        beta = session_factory.create_profiled(
            root,
            beta_workspace,
            profiles=profiles,
            default_profile="alpha",
        )
        alpha_bootstrap = alpha.up(UpRequest(project_key="ACME", profile="alpha"))
        beta_bootstrap = beta.up(UpRequest(project_key="ACME", profile="beta"))
        alpha_docs = alpha.create_project(
            ProjectCreateRequest(
                key="DOCS",
                name="Alpha documentation",
                profile="alpha",
            )
        )
        beta_docs = beta.create_project(
            ProjectCreateRequest(
                key="DOCS",
                name="Beta documentation",
                profile="beta",
            )
        )
        alpha_task = alpha.create_task(TaskCreateRequest(title="Alpha task"))
        beta_task = beta.create_task(TaskCreateRequest(title="Beta task"))

        assert alpha_bootstrap.instance.id != beta_bootstrap.instance.id
        assert alpha_bootstrap.project.key == beta_bootstrap.project.key == "ACME"
        assert alpha_docs.project.key == beta_docs.project.key == "DOCS"
        assert alpha_task.key == beta_task.key == "ACME-1"
        assert alpha_task.uid != beta_task.uid

        unbound = session_factory.create_profiled(
            root,
            unbound_workspace,
            profiles=profiles,
            default_profile="alpha",
        )
        assert unbound.list_projects(ProjectListRequest(profile="alpha")) == (
            alpha_bootstrap.project,
            alpha_docs.project,
        )
        assert unbound.list_projects(ProjectListRequest(profile="beta")) == (
            beta_bootstrap.project,
            beta_docs.project,
        )

        with pytest.raises(ApplicationError) as missing_selection:
            alpha.status(StatusRequest(profile="beta"))
        assert missing_selection.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND
        explicit_beta = alpha.context(ContextRequest(profile="beta", project="ACME"))
        assert explicit_beta.instance == beta_bootstrap.instance
        assert explicit_beta.project == beta_bootstrap.project
        assert explicit_beta.workspace_root is None
        assert explicit_beta.context_source is None

    def test_context_cannot_cross_an_unrelated_instance(
        self,
        session_factory: PhaseTwoSessionFactory,
        tmp_path: Path,
    ) -> None:
        """A valid context is rejected against another profile registry's store."""
        first_root = tmp_path / "first-data"
        second_root = tmp_path / "second-data"
        first_workspace = _workspace(tmp_path, "first-workspace")
        second_workspace = _workspace(tmp_path, "second-workspace")
        first = session_factory.create(first_root, first_workspace).up(
            UpRequest(project_key="ACME")
        )
        second = session_factory.create(second_root, second_workspace).up(
            UpRequest(project_key="ACME")
        )
        assert first.instance.id != second.instance.id

        with pytest.raises(ApplicationError) as captured:
            session_factory.create(second_root, first_workspace).context(
                ContextRequest()
            )

        assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
        assert (
            session_factory.create(first_root, first_workspace)
            .status(StatusRequest())
            .instance
            == first.instance
        )
        assert (
            session_factory.create(second_root, second_workspace)
            .status(StatusRequest())
            .instance
            == second.instance
        )


class TestEmbeddedLocalPhaseTwoSession(PhaseTwoSessionContract):
    """Apply the cumulative Session contract to production LocalSession."""

    @pytest.fixture
    def session_factory(self) -> PhaseTwoSessionFactory:
        """Provide a deterministic production LocalSession factory.

        Returns:
            Isolated LocalSession factory under cumulative contract.

        """
        return _LocalSessionFactory()


def _workspace(tmp_path: Path, name: str) -> Path:
    """Create one isolated existing Workspace.

    Args:
        tmp_path: Pytest-owned temporary root.
        name: Child directory name.

    Returns:
        Absolute created Workspace path.

    """
    workspace = tmp_path / name
    workspace.mkdir()
    return workspace


def _write_profile_registry(
    config_directory: Path,
    *,
    root: Path,
    profiles: tuple[str, ...],
    default_profile: str,
) -> None:
    """Write one exact trusted profile registry for a Session scenario.

    Args:
        config_directory: Test-owned trusted configuration directory.
        root: Test-owned parent for profile data directories.
        profiles: Ordered unique profile names.
        default_profile: Required configured default.

    Raises:
        ValueError: If the requested registry is empty, duplicated, or inconsistent.

    """
    if (
        not profiles
        or len(set(profiles)) != len(profiles)
        or default_profile not in profiles
    ):
        message = "Conformance profile registry is invalid."
        raise ValueError(message)
    config_directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "version = 1",
        f"default_profile = {json.dumps(default_profile)}",
    ]
    for profile in profiles:
        lines.extend(
            (
                "",
                f"[profiles.{profile}]",
                'mode = "embedded"',
                (
                    "data_directory = "
                    f"{json.dumps(str((root / profile).resolve(strict=False)))}"
                ),
            )
        )
    (config_directory / "profiles.toml").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
