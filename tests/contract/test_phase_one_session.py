"""Transport-neutral Phase 1 Session conformance tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.composition import create_local_session
from workaholic.context import CONTEXT_FILENAME
from workaholic.session import (
    ProjectListRequest,
    StatusRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
    UpRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.contract.phase_one import PhaseOneSessionFactory

    from workaholic.session import TaskSession

pytestmark = pytest.mark.contract


@dataclass(frozen=True, slots=True)
class _LocalSessionFactory:
    """Construct production embedded Sessions for the shared contract."""

    def create(self, root: Path, workspace: Path) -> TaskSession:
        """Construct a LocalSession without invoking an operation.

        Args:
            root: Test-owned trusted local data root.
            workspace: Existing exact Workspace directory.

        Returns:
            Production-composed embedded Session.

        """
        return create_local_session(
            cwd=workspace,
            environment={
                "WORKAHOLIC_CONFIG_DIR": str(root.parent / "config"),
                "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
                "WORKAHOLIC_DATA_DIR": str(root),
            },
        )


class PhaseOneSessionContract:
    """Reusable observable contract for a Phase 1 Session implementation."""

    @pytest.fixture
    def session_factory(self) -> PhaseOneSessionFactory:
        """Provide the Session factory under conformance.

        Returns:
            Factory that constructs a Session over test-owned state.

        """
        message = "A concrete Session contract must provide its factory."
        raise NotImplementedError(message)

    def test_construction_and_missing_context_reads_are_side_effect_free(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Constructing and reading without context writes no local state."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)

        with pytest.raises(ApplicationError) as captured:
            session.status(StatusRequest())

        assert captured.value.code is ApplicationErrorCode.CONTEXT_NOT_FOUND
        assert not root.exists()
        assert tuple(workspace.iterdir()) == ()

    def test_complete_solo_journey_survives_session_restart(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Bootstrap, creation, listing, and lookup survive a new Session."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        first_session = session_factory.create(root, workspace)

        bootstrap = first_session.up(UpRequest(project_key="ACME"))
        status = first_session.status(StatusRequest())
        created = first_session.create_task(
            TaskCreateRequest(title="Ship the local alpha")
        )

        assert status.instance == bootstrap.instance
        assert status.project == bootstrap.project
        assert status.subject == bootstrap.subject
        assert created.key == "ACME-1"
        assert created.objective == created.title
        assert created.priority == 50
        assert created.version == 1

        restarted = session_factory.create(root, workspace)
        assert restarted.status(StatusRequest()) == status
        assert restarted.list_projects(ProjectListRequest()) == (bootstrap.project,)
        page = restarted.list_tasks(TaskListRequest())
        assert page.tasks == (created,)
        assert page.next_cursor is None
        assert restarted.get_task(TaskGetRequest(task=created.uid)) == created
        assert restarted.get_task(TaskGetRequest(task=created.key)) == created

    def test_nearest_parent_context_is_discovered_without_child_write(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """A child inherits the nearest valid binding without copying context."""
        root = tmp_path / "data"
        parent = _workspace(tmp_path, "workspace")
        child = parent / "child"
        child.mkdir()
        bootstrap = session_factory.create(root, parent).up(
            UpRequest(project_key="ACME")
        )
        child_session = session_factory.create(root, child)

        status = child_session.status(StatusRequest())

        assert status.instance == bootstrap.instance
        assert status.project == bootstrap.project
        assert status.subject == bootstrap.subject
        assert not (child / CONTEXT_FILENAME).exists()

    def test_malformed_context_is_rejected_without_fallback(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Malformed exact-directory context fails as CONTEXT_INVALID."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        (workspace / CONTEXT_FILENAME).write_text(
            "WORKAHOLIC_CONTEXT_VERSION=1\nUNEXPECTED=value\n",
            encoding="utf-8",
        )
        session = session_factory.create(root, workspace)

        with pytest.raises(ApplicationError) as captured:
            session.status(StatusRequest())

        assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
        assert not root.exists()

    def test_bootstrap_enforces_project_uniqueness_and_idempotency(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Session bootstrap replays matching calls and rejects another key."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)
        first = session.up(
            UpRequest(
                project_key="ACME",
                idempotency_key="bootstrap-once",
            )
        )

        replayed = session_factory.create(root, workspace).up(
            UpRequest(
                project_key="ACME",
                idempotency_key="bootstrap-once",
            )
        )
        assert replayed == first

        with pytest.raises(ApplicationError) as captured:
            session.up(UpRequest(project_key="OTHER"))
        assert captured.value.code is ApplicationErrorCode.PROJECT_KEY_CONFLICT
        assert session.status(StatusRequest()).project == first.project

    def test_task_idempotency_is_stable_across_sessions(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Matching Task retries replay and changed semantic input conflicts."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)
        session.up(UpRequest(project_key="ACME"))
        first = session.create_task(
            TaskCreateRequest(
                title="Stable task",
                idempotency_key="task-once",
            )
        )

        restarted = session_factory.create(root, workspace)
        replayed = restarted.create_task(
            TaskCreateRequest(
                title="Stable task",
                idempotency_key="task-once",
            )
        )
        assert replayed == first

        with pytest.raises(ApplicationError) as captured:
            restarted.create_task(
                TaskCreateRequest(
                    title="Changed task",
                    idempotency_key="task-once",
                )
            )
        assert captured.value.code is ApplicationErrorCode.IDEMPOTENCY_CONFLICT
        assert restarted.list_tasks(TaskListRequest()).tasks == (first,)

    @pytest.mark.parametrize(
        ("operation", "expected_code"),
        [
            ("up", ApplicationErrorCode.INVALID_INPUT),
            ("task_add", ApplicationErrorCode.INVALID_INPUT),
            ("task_list", ApplicationErrorCode.INVALID_INPUT),
            ("task_show", ApplicationErrorCode.INVALID_INPUT),
        ],
    )
    def test_semantic_input_failures_use_public_errors(
        self,
        session_factory: PhaseOneSessionFactory,
        tmp_path: Path,
        operation: str,
        expected_code: ApplicationErrorCode,
    ) -> None:
        """Invalid semantic inputs do not leak Pydantic or adapter failures."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path, "workspace")
        session = session_factory.create(root, workspace)
        if operation != "up":
            session.up(UpRequest(project_key="ACME"))

        with pytest.raises(ApplicationError) as captured:
            _invoke_invalid_operation(session, operation)

        assert captured.value.code is expected_code


class TestEmbeddedLocalSession(PhaseOneSessionContract):
    """Apply the shared Phase 1 Session contract to LocalSession."""

    @pytest.fixture
    def session_factory(self) -> PhaseOneSessionFactory:
        """Provide the production embedded Session factory.

        Returns:
            Local Session factory under contract.

        """
        return _LocalSessionFactory()


def _workspace(tmp_path: Path, name: str) -> Path:
    """Create one isolated existing Workspace directory.

    Args:
        tmp_path: Pytest-owned temporary root.
        name: Child directory name.

    Returns:
        Absolute created Workspace path.

    """
    workspace = tmp_path / name
    workspace.mkdir()
    return workspace


def _invoke_invalid_operation(
    session: TaskSession,
    operation: str,
) -> object:
    """Invoke one deliberately invalid Session operation.

    Args:
        session: Session under contract.
        operation: Test scenario selector.

    Returns:
        Operation result if the Session incorrectly accepts the input.

    Raises:
        ApplicationError: Expected public semantic-input failure.
        AssertionError: If the test scenario is unsupported.

    """
    if operation == "up":
        return session.up(UpRequest.model_construct(project_key="lowercase"))
    if operation == "task_add":
        return session.create_task(TaskCreateRequest(title="   "))
    if operation == "task_list":
        return session.list_tasks(TaskListRequest(cursor="invalid"))
    if operation == "task_show":
        return session.get_task(TaskGetRequest(task="not-a-task"))
    message = f"Unsupported invalid-operation scenario: {operation}"
    raise AssertionError(message)
