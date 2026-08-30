"""Unit and real-composition tests for authenticated local Session orchestration."""

from __future__ import annotations

import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
from filelock import Timeout
from pydantic import ValidationError

if TYPE_CHECKING:
    import os
    from collections.abc import Callable

    from workaholic.application import (
        AuditApplication,
        AuthenticationApplication,
        GrantApplication,
        IdentityIdentifierFactory,
        SubjectApplication,
        TokenApplication,
    )
    from workaholic.application.ports import Clock
    from workaholic.auth import CredentialStore
    from workaholic.session._phase_five import LocalInstanceSelector

from workaholic import composition
from workaholic.application import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    CredentialUnavailableError,
    CurrentIdentityResult,
    InvalidInputError,
    SubjectNotFoundError,
    SubjectPage,
    TokenNotFoundError,
    TokenResult,
)
from workaholic.auth import (
    FileCredentialStore,
    HumanCredential,
    RawToken,
    generate_token,
)
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
    TokenStatus,
    TokenSummary,
)
from workaholic.session import (
    LoginRequest,
    LogoutRequest,
    SubjectCreateRequest,
    TokenCreateRequest,
    TokenListRequest,
    UpRequest,
    WhoAmIRequest,
)
from workaholic.session._phase_five import (
    PhaseFiveRuntime,
    ProtectedTokenFile,
    _dependency_time,
    _git_root_for,
    _resolve_token_lifetime,
)

_NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


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


def _subject(*, kind: SubjectKind = SubjectKind.HUMAN) -> Subject:
    """Return one valid runtime Subject of the requested kind.

    Args:
        kind: Immutable Human or Agent kind.

    Returns:
        Enabled Subject owned by the local Instance.

    """
    handle = "local-operator" if kind is SubjectKind.HUMAN else "build-agent"
    identifier = "sub_local" if kind is SubjectKind.HUMAN else "sub_agent"
    return Subject(
        id=SubjectId(identifier),
        instance_id=InstanceId("ins_local"),
        kind=kind,
        handle=handle,
        display_name=handle.replace("-", " ").title(),
        enabled=True,
        is_instance_admin=kind is SubjectKind.HUMAN,
        version=1,
        created_by=SubjectId("sub_local"),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _token_result(
    *,
    token_id: TokenId | None = None,
    status: TokenStatus = TokenStatus.PENDING,
) -> TokenResult:
    """Return one valid non-secret Token lifecycle result.

    Args:
        token_id: Public Token identifier.
        status: Pending or active lifecycle projection.

    Returns:
        Validated Token application result.

    """
    resolved_token_id = TokenId("tok_runtime") if token_id is None else token_id
    activated_at = _NOW if status is TokenStatus.ACTIVE else None
    return TokenResult(
        token=TokenSummary(
            id=resolved_token_id,
            subject_id=SubjectId("sub_local"),
            status=status,
            created_by=SubjectId("sub_local"),
            created_at=_NOW,
            activated_at=activated_at,
            expires_at=_NOW + timedelta(days=30),
            revoked_at=None,
            revoked_by=None,
        )
    )


@dataclass(slots=True)
class _RuntimeHarness:
    """PhaseFiveRuntime paired with explicit recording dependencies."""

    runtime: PhaseFiveRuntime
    instance: Mock
    authentication: Mock
    subjects: Mock
    tokens: Mock
    credentials: Mock
    clock: Mock
    identifiers: Mock
    actor: AuthenticatedActor
    raw_token: RawToken


def _runtime_harness(
    tmp_path: Path,
    *,
    environment: dict[str, str] | None = None,
    forbidden_roots: tuple[Path, ...] = (),
) -> _RuntimeHarness:
    """Build a valid runtime with independently configurable dependencies.

    Args:
        tmp_path: Test-owned root for the credential lock.
        environment: Optional explicit credential environment.
        forbidden_roots: Canonical roots excluded from Token files.

    Returns:
        Runtime plus each recording dependency.

    """
    raw_token = generate_token(
        TokenId("tok_runtime"),
        random_bytes=lambda count: b"r" * count,
    )
    actor = AuthenticatedActor(
        instance_id=InstanceId("ins_local"),
        subject_id=SubjectId("sub_local"),
        subject_kind=SubjectKind.HUMAN,
        token_id=TokenId("tok_runtime"),
    )
    instance = Mock(spec=["select_instance", "select_bootstrap_subject", "has_tokens"])
    instance.select_instance.return_value = actor.instance_id
    instance.select_bootstrap_subject.return_value = actor.subject_id
    instance.has_tokens.return_value = True
    authentication = Mock(spec=["authenticate", "whoami"])
    authentication.authenticate.return_value = actor
    subjects = Mock(
        spec=["create", "list", "update", "set_enabled", "set_instance_admin"]
    )
    subjects.list.return_value = SubjectPage(subjects=(_subject(),), next_cursor=None)
    tokens = Mock(spec=["issue_pending", "activate", "list", "revoke", "recover_local"])
    tokens.issue_pending.return_value = _token_result()
    tokens.activate.return_value = _token_result(status=TokenStatus.ACTIVE)
    grants = Mock(spec=["assign", "list", "revoke"])
    audit = Mock(spec=["read"])
    credentials = Mock(spec=["load", "replace", "delete"])
    credentials.load.return_value = HumanCredential(
        profile="local",
        instance_id=actor.instance_id,
        subject_id=actor.subject_id,
        raw_token=raw_token,
    )
    clock = Mock(spec=["now"])
    clock.now.return_value = _NOW
    identifiers = Mock(spec=["new_token_id"])
    identifiers.new_token_id.return_value = TokenId("tok_runtime")
    runtime = PhaseFiveRuntime(
        profile="local",
        instance=cast("LocalInstanceSelector", instance),
        authentication=cast("AuthenticationApplication", authentication),
        subjects=cast("SubjectApplication", subjects),
        tokens=cast("TokenApplication", tokens),
        grants=cast("GrantApplication", grants),
        audit=cast("AuditApplication", audit),
        credentials=cast("CredentialStore", credentials),
        environment={} if environment is None else environment,
        clock=cast("Clock", clock),
        identifiers=cast("IdentityIdentifierFactory", identifiers),
        credential_lock_path=(tmp_path / "credential.lock").resolve(),
        forbidden_roots=forbidden_roots,
    )
    return _RuntimeHarness(
        runtime=runtime,
        instance=instance,
        authentication=authentication,
        subjects=subjects,
        tokens=tokens,
        credentials=credentials,
        clock=clock,
        identifiers=identifiers,
        actor=actor,
        raw_token=raw_token,
    )


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


@pytest.mark.parametrize(
    "constructor",
    [
        pytest.param(
            lambda _tmp_path: ProtectedTokenFile(Path("relative")),
            id="relative-target",
        ),
        pytest.param(
            lambda tmp_path: ProtectedTokenFile(
                (tmp_path / "token").resolve(),
                cast("tuple[Path, ...]", [tmp_path.resolve()]),
            ),
            id="non-tuple-roots",
        ),
        pytest.param(
            lambda tmp_path: ProtectedTokenFile(
                (tmp_path / "token").resolve(),
                (Path("relative"),),
            ),
            id="relative-root",
        ),
    ],
)
def test_protected_token_file_rejects_invalid_construction(
    tmp_path: Path,
    constructor: Callable[[Path], ProtectedTokenFile],
) -> None:
    """Runtime validation rejects ambiguous Token destinations immediately."""
    with pytest.raises(InvalidInputError):
        constructor(tmp_path)


def test_protected_token_file_rejects_malformed_or_unprotected_retry(
    tmp_path: Path,
) -> None:
    """Existing output is accepted only at mode 0600 with one canonical Token."""
    path = _token_path(tmp_path, "retry.token")
    output = ProtectedTokenFile(path)

    assert output.load_retry() is None
    path.write_bytes(b"\xff")
    path.chmod(0o600)
    with pytest.raises(InvalidInputError):
        output.load_retry()
    path.write_text("not-a-token\n", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        output.load_retry()
    path.chmod(0o644)
    with pytest.raises(CredentialUnavailableError):
        output.load_retry()


def test_protected_token_file_rejects_invalid_create_and_changed_compensation(
    tmp_path: Path,
) -> None:
    """Creation and compensation cannot act on untyped or replaced state."""
    path = _token_path(tmp_path, "replace.token")
    output = ProtectedTokenFile(path)

    with pytest.raises(InvalidInputError):
        output.create(cast("RawToken", object()))
    with pytest.raises(InvalidInputError):
        output.compensate(cast("os.stat_result", object()))

    raw_token = generate_token(TokenId("tok_replace"), random_bytes=lambda n: b"z" * n)
    created = output.create(raw_token)
    path.unlink()
    path.write_text(raw_token.get_secret_value(), encoding="ascii")
    path.chmod(0o600)
    with pytest.raises(CredentialUnavailableError):
        output.compensate(created)


def test_protected_token_file_maps_snapshot_and_existing_target_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Protected output maps read failures and rejects exclusive-create reuse."""
    path = _token_path(tmp_path, "exclusive.token")
    output = ProtectedTokenFile(path)

    def fail_snapshot(_path: Path, *, maximum: int) -> object:
        """Raise a simulated private snapshot failure."""
        del maximum
        message = "private snapshot failure"
        raise PermissionError(message)

    monkeypatch.setattr(
        "workaholic.session._phase_five.read_bounded_regular_file_snapshot",
        fail_snapshot,
    )
    with pytest.raises(CredentialUnavailableError):
        output.load_retry()

    monkeypatch.undo()
    raw_token = generate_token(
        TokenId("tok_exclusive"), random_bytes=lambda n: b"e" * n
    )
    output.create(raw_token)
    with pytest.raises(CredentialUnavailableError):
        output.create(raw_token)


def test_protected_token_file_rejects_missing_parent_and_forbidden_root(
    tmp_path: Path,
) -> None:
    """Token output remains within one safe existing non-repository directory."""
    raw_token = generate_token(TokenId("tok_location"), random_bytes=lambda n: b"l" * n)
    missing = ProtectedTokenFile((tmp_path / "missing" / "token").resolve())
    with pytest.raises(CredentialUnavailableError):
        missing.create(raw_token)

    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    forbidden = ProtectedTokenFile(
        (safe / "token").resolve(),
        forbidden_roots=(safe.resolve(),),
    )
    with pytest.raises(InvalidInputError):
        forbidden.create(raw_token)


def test_phase_five_runtime_validates_explicit_dependency_surface(
    tmp_path: Path,
) -> None:
    """Miswired composition fails before reading state or credentials."""
    harness = _runtime_harness(tmp_path)

    with pytest.raises(TypeError, match=r"must provide read\(\)"):
        replace(
            harness.runtime,
            audit=cast("AuditApplication", object()),
        )
    with pytest.raises(TypeError, match="environment must be a mapping"):
        replace(harness.runtime, environment=cast("dict[str, str]", object()))
    with pytest.raises(TypeError, match="forbidden roots must be absolute"):
        replace(harness.runtime, forbidden_roots=(Path("relative"),))
    with pytest.raises(TypeError, match="credential lock path must be absolute"):
        replace(harness.runtime, credential_lock_path=Path("relative.lock"))


def test_runtime_authentication_fails_closed_for_missing_or_redirected_identity(
    tmp_path: Path,
) -> None:
    """Stored credential absence and identity disagreement never authenticate."""
    harness = _runtime_harness(tmp_path)
    harness.credentials.load.return_value = None
    with pytest.raises(AuthenticationRequiredError):
        harness.runtime.authenticate()

    harness = _runtime_harness(tmp_path / "instance")
    stored = cast("HumanCredential", harness.credentials.load.return_value)
    harness.credentials.load.return_value = HumanCredential(
        profile=stored.profile,
        instance_id=InstanceId("ins_other"),
        subject_id=stored.subject_id,
        raw_token=stored.raw_token,
    )
    with pytest.raises(AuthenticationFailedError):
        harness.runtime.authenticate()

    harness = _runtime_harness(tmp_path / "subject")
    stored = cast("HumanCredential", harness.credentials.load.return_value)
    harness.credentials.load.return_value = HumanCredential(
        profile=stored.profile,
        instance_id=stored.instance_id,
        subject_id=SubjectId("sub_other"),
        raw_token=stored.raw_token,
    )
    with pytest.raises(AuthenticationFailedError):
        harness.runtime.authenticate()


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param(
            cast("dict[str, str]", {"WORKAHOLIC_TOKEN": object()}),
            id="non-string-direct",
        ),
        pytest.param(
            cast("dict[str, str]", {"WORKAHOLIC_TOKEN_FILE": object()}),
            id="non-string-file",
        ),
        pytest.param(
            {"WORKAHOLIC_TOKEN": "token", "WORKAHOLIC_TOKEN_FILE": "/token"},
            id="ambiguous",
        ),
        pytest.param(
            {"WORKAHOLIC_TOKEN_FILE": "/definitely/missing/workaholic.token"},
            id="missing-file",
        ),
    ],
)
def test_runtime_rejects_invalid_explicit_credential_sources(
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    """Explicit Agent credential configuration is typed and fail-closed."""
    harness = _runtime_harness(tmp_path, environment=environment)

    with pytest.raises((InvalidInputError, CredentialUnavailableError)):
        harness.runtime.authenticate()


def test_runtime_rejects_token_file_within_forbidden_root(tmp_path: Path) -> None:
    """Repository-owned paths cannot redirect the explicit Agent credential."""
    forbidden = tmp_path / "workspace"
    forbidden.mkdir()
    token_file = forbidden / "agent.token"
    token_file.write_text("not-used", encoding="utf-8")
    harness = _runtime_harness(
        tmp_path,
        environment={"WORKAHOLIC_TOKEN_FILE": str(token_file.resolve())},
        forbidden_roots=(forbidden.resolve(),),
    )

    with pytest.raises(InvalidInputError):
        harness.runtime.authenticate()


def test_runtime_login_rejects_untyped_and_agent_credentials(tmp_path: Path) -> None:
    """Human credential enrollment rejects non-Tokens and Agent identities."""
    harness = _runtime_harness(tmp_path)
    with pytest.raises(InvalidInputError):
        harness.runtime.login(cast("RawToken", object()))

    harness.authentication.authenticate.return_value = replace(
        harness.actor,
        subject_kind=SubjectKind.AGENT,
    )
    with pytest.raises(AuthenticationFailedError):
        harness.runtime.login(harness.raw_token)
    harness.credentials.replace.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        {"subject": "local-operator", "token_file": "token.txt"},
        {"subject": "local-operator", "token_file": Path("relative.token")},
        {
            "subject": "local-operator",
            "token_file": Path.cwd() / "token.txt",
            "expires_in": "one day",
        },
        {
            "subject": "local-operator",
            "token_file": Path.cwd() / "token.txt",
            "expires_in": timedelta(0),
        },
        {
            "subject": "local-operator",
            "token_file": Path.cwd() / "token.txt",
            "expires_in": timedelta(days=366),
        },
    ],
)
def test_token_create_request_rejects_unsafe_output_and_expiry(
    values: dict[str, object],
) -> None:
    """Token provisioning input requires an absolute Path and bounded duration."""
    with pytest.raises(ValidationError):
        TokenCreateRequest.model_validate(values)


def test_runtime_logout_deletes_only_selected_profile(tmp_path: Path) -> None:
    """Logout uses the serialized credential boundary and returns safe metadata."""
    harness = _runtime_harness(tmp_path)

    result = harness.runtime.logout()

    assert result.profile == "local"
    harness.credentials.delete.assert_called_once_with("local")


def test_runtime_recovery_restores_previous_credential_after_failure(
    tmp_path: Path,
) -> None:
    """A rejected recovery restores the exact previous Human credential."""
    harness = _runtime_harness(tmp_path)
    previous = cast("HumanCredential", harness.credentials.load.return_value)
    harness.tokens.recover_local.side_effect = InvalidInputError

    with pytest.raises(InvalidInputError):
        harness.runtime.recover(
            instance_id=harness.actor.instance_id,
            bootstrap_handle="local-operator",
        )

    assert harness.credentials.replace.call_count == 2
    assert harness.credentials.replace.call_args_list[-1].args == (previous,)


def test_runtime_recovery_deletes_new_credential_when_no_previous_value(
    tmp_path: Path,
) -> None:
    """Failed first recovery removes only its newly written credential."""
    harness = _runtime_harness(tmp_path)
    harness.credentials.load.return_value = None
    harness.tokens.recover_local.side_effect = InvalidInputError

    with pytest.raises(InvalidInputError):
        harness.runtime.recover(
            instance_id=harness.actor.instance_id,
            bootstrap_handle="local-operator",
        )

    harness.credentials.delete.assert_called_once_with("local")


def test_runtime_recovery_rejects_instance_and_subject_mismatch(tmp_path: Path) -> None:
    """Recovery confirmation and application result must bind one identity."""
    harness = _runtime_harness(tmp_path)
    with pytest.raises(AuthenticationFailedError):
        harness.runtime.recover(
            instance_id=InstanceId("ins_other"),
            bootstrap_handle="local-operator",
        )

    harness = _runtime_harness(tmp_path / "result")
    other = _subject(kind=SubjectKind.AGENT)
    active = _token_result(status=TokenStatus.ACTIVE).token
    active = replace(active, subject_id=other.id)
    harness.tokens.recover_local.return_value = CurrentIdentityResult(
        subject=other,
        token=active,
    )
    with pytest.raises(CredentialUnavailableError):
        harness.runtime.recover(
            instance_id=harness.actor.instance_id,
            bootstrap_handle="local-operator",
        )


def test_existing_token_retry_requires_idempotency_and_known_pending_token(
    tmp_path: Path,
) -> None:
    """Existing secret output can resume only its exact pending operation."""
    harness = _runtime_harness(tmp_path)
    output = ProtectedTokenFile(_token_path(tmp_path, "existing.token"))
    output.create(harness.raw_token)

    with pytest.raises(CredentialUnavailableError):
        harness.runtime.provision_token(
            actor=harness.actor,
            subject=harness.actor.subject_id,
            output=output,
            expires_in=None,
            idempotency_key=None,
        )

    harness.tokens.activate.side_effect = TokenNotFoundError
    with pytest.raises(CredentialUnavailableError):
        harness.runtime.provision_token(
            actor=harness.actor,
            subject=harness.actor.subject_id,
            output=output,
            expires_in=None,
            idempotency_key="retry",
        )


def test_token_provisioning_fails_closed_when_compensation_fails(
    tmp_path: Path,
) -> None:
    """Provisioning surfaces a safe error if database or file rollback fails."""
    harness = _runtime_harness(tmp_path)
    output = Mock(spec=ProtectedTokenFile)
    output.load_retry.return_value = None
    output.create.return_value = cast("os.stat_result", object())
    output.compensate.side_effect = InvalidInputError
    harness.tokens.activate.side_effect = InvalidInputError

    with pytest.raises(CredentialUnavailableError):
        harness.runtime.provision_token(
            actor=harness.actor,
            subject=harness.actor.subject_id,
            output=cast("ProtectedTokenFile", output),
            expires_in=None,
            idempotency_key="create-token",
        )

    harness.tokens.revoke.assert_called_once()
    output.compensate.assert_called_once()


def test_token_provisioning_fails_closed_when_database_rollback_fails(
    tmp_path: Path,
) -> None:
    """A failed pending-Token revocation cannot be mistaken for clean rollback."""
    harness = _runtime_harness(tmp_path)
    output = Mock(spec=ProtectedTokenFile)
    output.load_retry.return_value = None
    output.create.side_effect = InvalidInputError
    harness.tokens.revoke.side_effect = InvalidInputError

    with pytest.raises(CredentialUnavailableError):
        harness.runtime.provision_token(
            actor=harness.actor,
            subject=harness.actor.subject_id,
            output=cast("ProtectedTokenFile", output),
            expires_in=None,
            idempotency_key="create-token",
        )


def test_runtime_token_generation_rejects_invalid_identifier(tmp_path: Path) -> None:
    """Credential provisioning requires an exact public Token identifier."""
    harness = _runtime_harness(tmp_path)
    harness.identifiers.new_token_id.return_value = "tok_invalid"

    with pytest.raises(CredentialUnavailableError):
        harness.runtime._new_token()


def test_credential_lock_timeout_maps_to_safe_storage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Credential operations expose one safe error when lock acquisition times out."""
    harness = _runtime_harness(tmp_path)

    def fail_lock(_lock: object) -> object:
        """Raise the simulated cross-process lock timeout."""
        raise Timeout(str(harness.runtime.credential_lock_path))

    monkeypatch.setattr("workaholic.session._phase_five.FileLock.__enter__", fail_lock)
    with pytest.raises(CredentialUnavailableError):
        harness.runtime.logout()


def test_subject_resolution_rejects_missing_and_cyclic_pages(tmp_path: Path) -> None:
    """Bounded Subject lookup terminates on absence or a repeated cursor."""
    harness = _runtime_harness(tmp_path)
    harness.subjects.list.return_value = SubjectPage(subjects=(), next_cursor=None)
    with pytest.raises(SubjectNotFoundError):
        harness.runtime._resolve_subject(
            actor=harness.actor,
            selector="missing",
        )

    harness.subjects.list.side_effect = (
        SubjectPage(subjects=(), next_cursor="v5.repeat"),
        SubjectPage(subjects=(), next_cursor="v5.repeat"),
    )
    with pytest.raises(CredentialUnavailableError):
        harness.runtime._resolve_subject(
            actor=harness.actor,
            selector="missing",
        )


@pytest.mark.parametrize(
    ("kind", "requested", "expected"),
    [
        (SubjectKind.HUMAN, None, timedelta(days=30)),
        (SubjectKind.AGENT, None, timedelta(hours=24)),
        (SubjectKind.HUMAN, timedelta(hours=1), timedelta(hours=1)),
        (SubjectKind.AGENT, timedelta(minutes=5), timedelta(minutes=5)),
    ],
)
def test_token_lifetime_resolves_kind_specific_defaults_and_bounds(
    kind: SubjectKind,
    requested: timedelta | None,
    expected: timedelta,
) -> None:
    """Token lifetime uses the exact Human and Agent policy windows."""
    assert _resolve_token_lifetime(kind, requested) == expected


@pytest.mark.parametrize(
    ("kind", "requested"),
    [
        (SubjectKind.HUMAN, timedelta(minutes=59)),
        (SubjectKind.HUMAN, timedelta(days=366)),
        (SubjectKind.AGENT, timedelta(minutes=4)),
        (SubjectKind.AGENT, timedelta(days=31)),
    ],
)
def test_token_lifetime_rejects_values_outside_kind_specific_bounds(
    kind: SubjectKind,
    requested: timedelta,
) -> None:
    """No caller can bypass the closed lifetime ranges."""
    with pytest.raises(InvalidInputError):
        _resolve_token_lifetime(kind, requested)


@pytest.mark.parametrize(
    "value",
    [_NOW.replace(tzinfo=None), object()],
)
def test_dependency_time_requires_an_aware_utc_datetime(value: object) -> None:
    """Credential expiry derives only from an authoritative UTC clock."""
    clock = Mock(spec=["now"])
    clock.now.return_value = value

    with pytest.raises(CredentialUnavailableError):
        _dependency_time(cast("Clock", clock))


def test_git_root_detection_returns_nearest_repository(tmp_path: Path) -> None:
    """Token path screening finds a containing worktree and clean absence."""
    outside = tmp_path / "outside"
    outside.mkdir()
    assert _git_root_for(outside) is None

    repository = tmp_path / "repository"
    child = repository / "child"
    child.mkdir(parents=True)
    (repository / ".git").mkdir()
    assert _git_root_for(child) == repository


def test_git_root_detection_maps_filesystem_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repository screening never leaks a raw filesystem inspection error."""

    def fail_exists(_path: Path) -> bool:
        """Raise the simulated private marker inspection failure."""
        message = "private marker failure"
        raise PermissionError(message)

    monkeypatch.setattr(Path, "exists", fail_exists)
    with pytest.raises(CredentialUnavailableError):
        _git_root_for(tmp_path)
