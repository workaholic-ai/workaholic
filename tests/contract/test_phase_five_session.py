"""Authenticated embedded Session conformance for the Phase 5 boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from tests.contract.test_phase_four_session import PhaseFourSessionContract
from tests.contract.test_phase_three_session import _LocalSessionFactory

if TYPE_CHECKING:
    from pathlib import Path

    from tests.contract.phase_five import PhaseFiveSessionFactory

    from workaholic.session import WorkaholicSession

from workaholic import composition
from workaholic.application import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    PermissionDeniedError,
)
from workaholic.auth import read_token_file
from workaholic.domain import ProjectRole, SubjectKind, TokenStatus
from workaholic.session import (
    AgentTaskClaimRequest,
    GrantAssignRequest,
    LoginRequest,
    LogoutRequest,
    RecoverLocalRequest,
    StatusRequest,
    SubjectCreateRequest,
    TaskCreateRequest,
    TaskGetRequest,
    TokenCreateRequest,
    TokenListRequest,
    TokenRevokeRequest,
    UpRequest,
    WhoAmIRequest,
)

pytestmark = pytest.mark.contract


@dataclass(slots=True)
class _PhaseFiveLocalSessionFactory(_LocalSessionFactory):
    """Extend the local cumulative factory with explicit Token Sessions."""

    def create_with_token(
        self,
        root: Path,
        workspace: Path,
        token_file: Path,
    ) -> WorkaholicSession:
        """Create a Session whose sole caller credential is one Token file.

        Args:
            root: Test-owned trusted data root.
            workspace: Existing Workspace bound to the configured Instance.
            token_file: Protected absolute Token file.

        Returns:
            Production LocalSession authenticated by ``token_file``.

        """
        config_directory = root.parent / f".{root.name}-config"
        return composition.create_local_session(
            cwd=workspace,
            environment={
                "WORKAHOLIC_CONFIG_DIR": str(config_directory),
                "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
                "WORKAHOLIC_DATA_DIR": str(root),
                "WORKAHOLIC_TOKEN_FILE": str(token_file),
            },
        )


class PhaseFiveSessionContract(PhaseFourSessionContract):
    """Reusable cumulative Session contract for explicit identities."""

    @pytest.fixture
    def session_factory(self) -> PhaseFiveSessionFactory:
        """Provide the Session factory under cumulative conformance."""
        message = "A concrete Phase 5 Session contract must provide its factory."
        raise NotImplementedError(message)

    def test_two_agents_use_distinct_tokens_and_project_roles(
        self,
        session_factory: PhaseFiveSessionFactory,
        tmp_path: Path,
    ) -> None:
        """Independent Agent Tokens never collapse into the local Human actor."""
        root = tmp_path / "data"
        workspace = _workspace(tmp_path)
        human = composition.create_local_session(
            cwd=workspace,
            environment={
                "WORKAHOLIC_CONFIG_DIR": str(root.parent / f".{root.name}-config"),
                "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
                "WORKAHOLIC_DATA_DIR": str(root),
            },
        )
        human.up(UpRequest(project_key="ACME"))
        first = human.create_subject(
            SubjectCreateRequest(kind=SubjectKind.AGENT, handle="first-agent")
        ).subject
        second = human.create_subject(
            SubjectCreateRequest(kind=SubjectKind.AGENT, handle="second-agent")
        ).subject
        human.assign_grant(
            GrantAssignRequest(
                subject=first.id,
                project="ACME",
                role=ProjectRole.AGENT,
            )
        )
        human.assign_grant(
            GrantAssignRequest(
                subject=second.id,
                project="ACME",
                role=ProjectRole.VIEWER,
            )
        )
        task = human.create_task(TaskCreateRequest(title="Identity-owned work"))
        first_path = _token_path(tmp_path, "first.token")
        second_path = _token_path(tmp_path, "second.token")
        first_token = human.create_token(
            TokenCreateRequest(subject=first.id, token_file=first_path)
        ).token
        human.create_token(
            TokenCreateRequest(subject=second.id, token_file=second_path)
        )

        first_session = session_factory.create_with_token(
            root,
            workspace,
            first_path,
        )
        second_session = session_factory.create_with_token(
            root,
            workspace,
            second_path,
        )
        assert first_session.whoami(WhoAmIRequest()).subject.id == first.id
        assert second_session.whoami(WhoAmIRequest()).subject.id == second.id
        assert first_session.claim_next_task(AgentTaskClaimRequest()).task == task
        with pytest.raises(PermissionDeniedError):
            second_session.claim_next_task(AgentTaskClaimRequest())

        human.revoke_token(TokenRevokeRequest(token_id=first_token.id))
        with pytest.raises(AuthenticationFailedError):
            session_factory.create_with_token(
                root,
                workspace,
                first_path,
            ).whoami(WhoAmIRequest())


class TestEmbeddedLocalPhaseFiveSession(PhaseFiveSessionContract):
    """Apply the cumulative Phase 5 Session contract to LocalSession."""

    @pytest.fixture
    def session_factory(self) -> PhaseFiveSessionFactory:
        """Provide a production local Phase 5 Session factory."""
        return _PhaseFiveLocalSessionFactory()


def _environment(tmp_path: Path) -> dict[str, str]:
    """Return isolated file-backed local configuration for one test."""
    return {
        "WORKAHOLIC_CONFIG_DIR": str(tmp_path / "config"),
        "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
        "WORKAHOLIC_DATA_DIR": str(tmp_path / "data"),
    }


def _workspace(tmp_path: Path) -> Path:
    """Create and return one isolated Workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


def _token_path(tmp_path: Path, name: str) -> Path:
    """Create one protected non-repository parent for Token output."""
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return (directory / name).resolve()


def test_distinct_human_and_agent_sessions_enforce_real_identity(
    tmp_path: Path,
) -> None:
    """A provisioned Agent uses its own Subject, role, Token, and Attempt."""
    environment = _environment(tmp_path)
    workspace = _workspace(tmp_path)
    human = composition.create_local_session(cwd=workspace, environment=environment)
    bootstrap = human.up(UpRequest(project_key="ACME"))

    current = human.whoami(WhoAmIRequest())
    assert current.subject == bootstrap.subject
    assert current.token.status is TokenStatus.ACTIVE
    assert human.status(StatusRequest()).schema_version == 5

    agent = human.create_subject(
        SubjectCreateRequest(kind=SubjectKind.AGENT, handle="build-agent")
    ).subject
    human.assign_grant(
        GrantAssignRequest(
            subject=agent.id,
            project="ACME",
            role=ProjectRole.AGENT,
        )
    )
    task = human.create_task(TaskCreateRequest(title="Authenticated work"))
    token_path = _token_path(tmp_path, "agent.token")
    agent_token = human.create_token(
        TokenCreateRequest(subject=agent.id, token_file=token_path)
    ).token

    agent_environment = {
        **environment,
        "WORKAHOLIC_TOKEN_FILE": str(token_path),
    }
    agent_session = composition.create_local_session(
        cwd=workspace,
        environment=agent_environment,
    )
    assert agent_session.whoami(WhoAmIRequest()).subject.id == agent.id
    claimed = agent_session.claim_next_task(AgentTaskClaimRequest())
    assert claimed.task.uid == task.uid
    assert claimed.attempt is not None
    assert claimed.attempt.subject_id == agent.id

    with pytest.raises(PermissionDeniedError):
        human.claim_next_task(AgentTaskClaimRequest())

    human.revoke_token(TokenRevokeRequest(token_id=agent_token.id))
    with pytest.raises(AuthenticationFailedError):
        agent_session.whoami(WhoAmIRequest())


def test_logout_login_and_confirmed_recovery_are_credential_boundaries(
    tmp_path: Path,
) -> None:
    """Local credential removal, enrollment, and recovery preserve task data."""
    environment = _environment(tmp_path)
    workspace = _workspace(tmp_path)
    session = composition.create_local_session(cwd=workspace, environment=environment)
    bootstrap = session.up(UpRequest(project_key="ACME"))
    task = session.create_task(TaskCreateRequest(title="Survive recovery"))

    assert session.logout(LogoutRequest()).credential_stored is False
    with pytest.raises(AuthenticationRequiredError):
        session.whoami(WhoAmIRequest())

    recovered = session.recover_local(
        RecoverLocalRequest(
            instance_id=bootstrap.instance.id,
            subject="local-operator",
        )
    )
    assert recovered.subject.id == bootstrap.subject.id
    assert session.status(StatusRequest()).subject.id == bootstrap.subject.id
    # The Workspace context supplies the Project scope.
    assert session.get_task(TaskGetRequest(task=task.uid)).uid == task.uid

    tokens = session.list_tokens(TokenListRequest()).tokens
    assert [item.status for item in tokens] == [
        TokenStatus.REVOKED,
        TokenStatus.ACTIVE,
    ]

    replacement_path = _token_path(tmp_path, "human.token")
    replacement = session.create_token(
        TokenCreateRequest(
            subject=bootstrap.subject.id,
            token_file=replacement_path,
        )
    ).token
    session.logout(LogoutRequest())
    enrolled = session.login(LoginRequest(raw_token=read_token_file(replacement_path)))
    assert enrolled.token.id == replacement.id

    with pytest.raises(AuthenticationFailedError):
        session.recover_local(
            RecoverLocalRequest(
                instance_id=type(bootstrap.instance.id)("ins_other"),
                subject="local-operator",
            )
        )
    assert replacement_path.stat().st_mode & 0o777 == 0o600
