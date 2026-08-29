"""Unit and real-composition tests for authenticated local Session orchestration."""

from __future__ import annotations

import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from workaholic import composition
from workaholic.application import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    CredentialUnavailableError,
    InvalidInputError,
)
from workaholic.auth import (
    FileCredentialStore,
    HumanCredential,
    generate_token,
)
from workaholic.domain import InstanceId, SubjectKind, TokenId, TokenStatus
from workaholic.session import (
    LoginRequest,
    LogoutRequest,
    SubjectCreateRequest,
    TokenCreateRequest,
    TokenListRequest,
    UpRequest,
    WhoAmIRequest,
)
from workaholic.session._phase_five import ProtectedTokenFile


def _environment(tmp_path: Path) -> dict[str, str]:
    """Return one isolated file-backed local process environment."""
    return {
        "WORKAHOLIC_CONFIG_DIR": str(tmp_path / "config"),
        "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
        "WORKAHOLIC_DATA_DIR": str(tmp_path / "data"),
    }


def _workspace(tmp_path: Path, name: str = "workspace") -> Path:
    """Create one isolated Workspace directory."""
    workspace = tmp_path / name
    workspace.mkdir()
    return workspace


def _token_path(tmp_path: Path, name: str) -> Path:
    """Return a Token path beneath a protected non-Workspace directory."""
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return (directory / name).resolve()


def test_protected_token_file_is_exclusive_account_only_and_compensable(
    tmp_path: Path,
) -> None:
    """Provisioning writes once at 0600 and removes only its exact inode."""
    output = ProtectedTokenFile(_token_path(tmp_path, "agent.token"))
    raw_token = generate_token(
        TokenId("tok_protected"),
        random_bytes=lambda n: b"x" * n,
    )

    created = output.create(raw_token)

    assert stat.S_IMODE(output.path.stat().st_mode) == 0o600
    assert output.load_retry() == raw_token
    with pytest.raises(CredentialUnavailableError):
        output.create(raw_token)
    output.compensate(created)
    assert not output.path.exists()


def test_protected_token_file_rejects_repository_and_unsafe_parent(
    tmp_path: Path,
) -> None:
    """Token output cannot enter a repository or broadly writable directory."""
    raw_token = generate_token(TokenId("tok_unsafe"), random_bytes=lambda n: b"y" * n)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    with pytest.raises(InvalidInputError):
        ProtectedTokenFile((repository / "token").resolve()).create(raw_token)

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(CredentialUnavailableError):
        ProtectedTokenFile((unsafe / "token").resolve()).create(raw_token)


def test_initialized_up_requires_auth_and_explicit_source_never_falls_back(
    tmp_path: Path,
) -> None:
    """Initialized profiles require auth and malformed explicit input is final."""
    environment = _environment(tmp_path)
    workspace = _workspace(tmp_path)
    session = composition.create_local_session(cwd=workspace, environment=environment)
    session.up(UpRequest(project_key="ACME"))

    invalid_direct = composition.create_local_session(
        cwd=workspace,
        environment={**environment, "WORKAHOLIC_TOKEN": "not-a-token"},
    )
    with pytest.raises(InvalidInputError):
        invalid_direct.whoami(WhoAmIRequest())

    duplicate_sources = composition.create_local_session(
        cwd=workspace,
        environment={
            **environment,
            "WORKAHOLIC_TOKEN": "not-a-token",
            "WORKAHOLIC_TOKEN_FILE": str(tmp_path / "missing.token"),
        },
    )
    with pytest.raises(InvalidInputError):
        duplicate_sources.whoami(WhoAmIRequest())

    session.logout(LogoutRequest())
    with pytest.raises(AuthenticationRequiredError):
        session.up(UpRequest(project_key="ACME"))


def test_stored_expected_instance_mismatch_fails_before_authentication(
    tmp_path: Path,
) -> None:
    """Profile credentials remain bound to their originally enrolled Instance."""
    environment = _environment(tmp_path)
    workspace = _workspace(tmp_path)
    session = composition.create_local_session(cwd=workspace, environment=environment)
    session.up(UpRequest(project_key="ACME"))
    store = FileCredentialStore(
        (tmp_path / "config" / "credentials").resolve(),
        (tmp_path / "config" / "credentials" / "credentials.toml").resolve(),
        forbidden_roots=(workspace.resolve(),),
    )
    stored = store.load("local")
    assert stored is not None
    raw_value = stored.raw_token.get_secret_value()
    store.replace(
        HumanCredential(
            profile=stored.profile,
            instance_id=InstanceId("ins_wrong"),
            subject_id=stored.subject_id,
            raw_token=stored.raw_token,
        )
    )

    with pytest.raises(AuthenticationFailedError) as captured:
        session.whoami(WhoAmIRequest())
    assert raw_value not in repr(captured.value)
    assert raw_value not in repr(LoginRequest(raw_token=stored.raw_token))


def test_failed_token_sink_revokes_pending_token_and_leaves_no_secret(
    tmp_path: Path,
) -> None:
    """A failed protected sink compensates the pending database lifecycle."""
    environment = _environment(tmp_path)
    workspace = _workspace(tmp_path)
    session = composition.create_local_session(cwd=workspace, environment=environment)
    session.up(UpRequest(project_key="ACME"))
    agent = session.create_subject(
        SubjectCreateRequest(kind=SubjectKind.AGENT, handle="sink-agent")
    ).subject
    unsafe = tmp_path / "unsafe-output"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    token_file = (unsafe / "agent.token").resolve()

    with pytest.raises(CredentialUnavailableError):
        session.create_token(
            TokenCreateRequest(subject=agent.id, token_file=token_file)
        )

    page = session.list_tokens(TokenListRequest(subject=agent.id))
    assert len(page.tokens) == 1
    assert page.tokens[0].status is TokenStatus.REVOKED
    assert not token_file.exists()


def test_concurrent_first_up_has_one_authenticatable_final_credential(
    tmp_path: Path,
) -> None:
    """Concurrent first runs serialize credential and Token recovery outcomes."""
    environment = _environment(tmp_path)
    workspaces = (_workspace(tmp_path, "one"), _workspace(tmp_path, "two"))
    barrier = Barrier(len(workspaces))

    def bootstrap(workspace: Path) -> object:
        """Run one synchronized first-up request in an independent Session."""
        session = composition.create_local_session(
            cwd=workspace,
            environment=environment,
        )
        barrier.wait()
        return session.up(
            UpRequest(project_key="ACME", idempotency_key="concurrent-up")
        )

    with ThreadPoolExecutor(max_workers=len(workspaces)) as executor:
        results = tuple(executor.map(bootstrap, workspaces))

    assert len(results) == 2
    final = composition.create_local_session(
        cwd=workspaces[0],
        environment=environment,
    )
    identity = final.whoami(WhoAmIRequest())
    assert identity.subject.handle == "local-operator"
    tokens = final.list_tokens(TokenListRequest()).tokens
    assert sum(token.status is TokenStatus.ACTIVE for token in tokens) == 1
