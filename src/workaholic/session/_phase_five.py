"""Authenticated embedded identity and credential orchestration.

This module owns the only Session-level escape hatches for raw bearer Tokens.
Secrets are resolved, hashed, persisted to protected sinks, and discarded here;
application services and ordinary Session operations receive only an
``AuthenticatedActor``.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from filelock import FileLock, Timeout

from workaholic.application import (
    ApplicationError,
    AuthenticationFailedError,
    AuthenticationRequiredError,
    CredentialLogoutResult,
    CredentialUnavailableError,
    CurrentIdentityResult,
    InvalidInputError,
    SubjectNotFoundError,
    TokenNotFoundError,
    TokenResult,
)
from workaholic.auth import (
    CredentialStore,
    HumanCredential,
    RawToken,
    generate_token,
    hash_token,
    parse_token,
    resolve_explicit_credential,
)
from workaholic.auth._files import (
    UnsafeDataFileError,
    read_bounded_regular_file_snapshot,
)
from workaholic.auth.errors import TokenFormatError, TokenGenerationError
from workaholic.domain import (
    AuthenticatedActor,
    InstanceId,
    Subject,
    SubjectId,
    SubjectKind,
    TokenId,
)

if TYPE_CHECKING:
    from datetime import datetime

    from workaholic.application import (
        AuditApplication,
        AuthenticationApplication,
        GrantApplication,
        IdentityIdentifierFactory,
        SubjectApplication,
        TokenApplication,
    )
    from workaholic.application.ports import Clock

_BOOTSTRAP_HANDLE = "local-operator"
_TOKEN_FILE_MAX_BYTES = 512
_TOKEN_FILE_MODE = 0o600
_UNSAFE_PARENT_MODE = stat.S_IWGRP | stat.S_IWOTH
_HUMAN_DEFAULT_LIFETIME = timedelta(days=30)
_HUMAN_MINIMUM_LIFETIME = timedelta(hours=1)
_HUMAN_MAXIMUM_LIFETIME = timedelta(days=365)
_AGENT_DEFAULT_LIFETIME = timedelta(hours=24)
_AGENT_MINIMUM_LIFETIME = timedelta(minutes=5)
_AGENT_MAXIMUM_LIFETIME = timedelta(days=30)
_CREDENTIAL_LOCK_TIMEOUT_SECONDS = 10.0


class LocalInstanceSelector(Protocol):
    """Read only the trusted singleton Instance identity before authentication."""

    def select_instance(self) -> InstanceId:
        """Return the initialized embedded Instance identity."""
        ...

    def select_bootstrap_subject(
        self,
        *,
        instance_id: InstanceId,
        handle: str,
    ) -> SubjectId:
        """Return the exact bootstrap Human only for confirmed recovery."""
        ...

    def has_tokens(self) -> bool:
        """Return whether any Token lifecycle row exists in the Instance."""
        ...


@dataclass(frozen=True, slots=True)
class ProtectedTokenFile:
    """One exclusive account-only Token output with bounded compensation."""

    path: Path
    forbidden_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """Validate absolute output and canonical forbidden-root inputs."""
        candidate_path: object = self.path
        candidate_roots: object = self.forbidden_roots
        if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
            raise InvalidInputError
        if not isinstance(candidate_roots, tuple) or any(
            not isinstance(root, Path) or not root.is_absolute()
            for root in candidate_roots
        ):
            raise InvalidInputError

    def load_retry(self) -> RawToken | None:
        """Load an existing protected output for an explicit retry.

        Returns:
            Existing canonical raw Token, or ``None`` when the target is absent.

        Raises:
            CredentialUnavailableError: If an existing target is unsafe.
            InvalidInputError: If its content is not one canonical Token.

        """
        try:
            snapshot = read_bounded_regular_file_snapshot(
                self.path,
                maximum=_TOKEN_FILE_MAX_BYTES,
            )
        except FileNotFoundError:
            return None
        except (OSError, UnsafeDataFileError) as error:
            raise CredentialUnavailableError from error
        if os.name != "posix" or stat.S_IMODE(snapshot.metadata.st_mode) != (
            _TOKEN_FILE_MODE
        ):
            raise CredentialUnavailableError
        try:
            content = snapshot.content.decode("utf-8").removesuffix("\n")
            return parse_token(content).raw_token
        except (TokenFormatError, UnicodeDecodeError) as error:
            raise InvalidInputError from error

    def create(self, raw_token: RawToken) -> os.stat_result:
        """Exclusively create and durably write one protected Token file.

        Args:
            raw_token: Canonical credential to write once.

        Returns:
            Final descriptor metadata used for safe compensation.

        Raises:
            CredentialUnavailableError: If the target or parent is unsafe.
            InvalidInputError: If runtime arguments violate the contract.

        """
        if not isinstance(raw_token, RawToken):
            raise InvalidInputError
        self._validate_location()
        if os.name != "posix":
            raise CredentialUnavailableError
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path, flags, _TOKEN_FILE_MODE)
            os.fchmod(descriptor, _TOKEN_FILE_MODE)
            payload = f"{raw_token.get_secret_value()}\n".encode("ascii")
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != _TOKEN_FILE_MODE
                or metadata.st_size != len(payload)
            ):
                raise CredentialUnavailableError
            _fsync_directory(self.path.parent)
        except FileExistsError as error:
            raise CredentialUnavailableError from error
        except (OSError, UnicodeEncodeError) as error:
            raise CredentialUnavailableError from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
        return metadata

    def compensate(self, created: os.stat_result) -> None:
        """Remove only the exact file created by this provisioning attempt.

        Args:
            created: Descriptor metadata returned by :meth:`create`.

        Raises:
            CredentialUnavailableError: If the target changed or cannot be removed.

        """
        candidate: object = created
        if not isinstance(candidate, os.stat_result):
            raise InvalidInputError
        try:
            current = self.path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or not os.path.samestat(candidate, current)
            ):
                raise CredentialUnavailableError  # noqa: TRY301 - atomic guard.
            self.path.unlink()
            _fsync_directory(self.path.parent)
        except (OSError, CredentialUnavailableError) as error:
            raise CredentialUnavailableError from error

    def _validate_location(self) -> None:
        """Reject repository-contained targets and unsafe parent directories."""
        try:
            parent = self.path.parent.resolve(strict=True)
            target = self.path.resolve(strict=False)
            metadata = parent.lstat()
        except (OSError, RuntimeError) as error:
            raise CredentialUnavailableError from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_mode & _UNSAFE_PARENT_MODE
            or target.parent != parent
        ):
            raise CredentialUnavailableError
        for root in self.forbidden_roots:
            try:
                canonical_root = root.resolve(strict=False)
            except (OSError, RuntimeError) as error:
                raise InvalidInputError from error
            if target == canonical_root or canonical_root in target.parents:
                raise InvalidInputError
        if _git_root_for(parent) is not None:
            raise InvalidInputError


@dataclass(frozen=True, slots=True)
class PhaseFiveRuntime:
    """Per-profile authentication and identity-administration capabilities."""

    profile: str
    instance: LocalInstanceSelector
    authentication: AuthenticationApplication
    subjects: SubjectApplication
    tokens: TokenApplication
    grants: GrantApplication
    audit: AuditApplication
    credentials: CredentialStore
    environment: Mapping[str, str]
    clock: Clock
    identifiers: IdentityIdentifierFactory
    credential_lock_path: Path
    forbidden_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """Validate the explicit capability surface without reading credentials."""
        for dependency, methods in (
            (
                self.instance,
                ("select_instance", "select_bootstrap_subject", "has_tokens"),
            ),
            (self.authentication, ("authenticate", "whoami")),
            (
                self.subjects,
                ("create", "list", "update", "set_enabled", "set_instance_admin"),
            ),
            (
                self.tokens,
                ("issue_pending", "activate", "list", "revoke", "recover_local"),
            ),
            (self.grants, ("assign", "list", "revoke")),
            (self.audit, ("read",)),
            (self.credentials, ("load", "replace", "delete")),
            (self.clock, ("now",)),
            (self.identifiers, ("new_token_id",)),
        ):
            for method in methods:
                if not callable(getattr(dependency, method, None)):
                    message = f"PhaseFiveRuntime dependency must provide {method}()."
                    raise TypeError(message)
        environment_value: object = self.environment
        if not isinstance(environment_value, Mapping):
            message = "PhaseFiveRuntime environment must be a mapping."
            raise TypeError(message)
        roots: object = self.forbidden_roots
        if not isinstance(roots, tuple) or any(
            not isinstance(root, Path) or not root.is_absolute() for root in roots
        ):
            message = "PhaseFiveRuntime forbidden roots must be absolute Paths."
            raise TypeError(message)
        lock_path: object = self.credential_lock_path
        if (
            not isinstance(lock_path, Path)
            or not lock_path.is_absolute()
        ):
            message = "PhaseFiveRuntime credential lock path must be absolute."
            raise TypeError(message)

    def authenticate(self) -> AuthenticatedActor:
        """Resolve exactly one credential and authenticate it for this profile."""
        instance_id = self.instance.select_instance()
        self._validate_explicit_file_location()
        explicit = resolve_explicit_credential(self.environment)
        stored: HumanCredential | None = None
        if explicit is None:
            stored = self.credentials.load(self.profile)
            if stored is None:
                raise AuthenticationRequiredError
            if stored.instance_id != instance_id:
                raise AuthenticationFailedError
            raw_token = stored.raw_token
        else:
            raw_token = explicit.raw_token
        try:
            parsed = parse_token(raw_token)
            actor = self.authentication.authenticate(
                token_id=parsed.token_id,
                token_digest=hash_token(parsed.raw_token),
                expected_instance_id=instance_id,
            )
        except TokenFormatError as error:
            raise AuthenticationFailedError from error
        if stored is not None and stored.subject_id != actor.subject_id:
            raise AuthenticationFailedError
        return actor

    def login(self, raw_token: RawToken) -> CurrentIdentityResult:
        """Authenticate and persist one explicit Human credential."""
        if not isinstance(raw_token, RawToken):
            raise InvalidInputError
        with self._credential_lock():
            instance_id = self.instance.select_instance()
            try:
                parsed = parse_token(raw_token)
                actor = self.authentication.authenticate(
                    token_id=parsed.token_id,
                    token_digest=hash_token(parsed.raw_token),
                    expected_instance_id=instance_id,
                )
            except TokenFormatError as error:
                raise InvalidInputError from error
            if actor.subject_kind is not SubjectKind.HUMAN:
                raise AuthenticationFailedError
            result = self.authentication.whoami(actor)
            self.credentials.replace(
                HumanCredential(
                    profile=self.profile,
                    instance_id=actor.instance_id,
                    subject_id=actor.subject_id,
                    raw_token=raw_token,
                )
            )
        return result

    def logout(self) -> CredentialLogoutResult:
        """Delete only this profile's local Human credential."""
        with self._credential_lock():
            self.credentials.delete(self.profile)
        return CredentialLogoutResult(profile=self.profile)

    def recover(
        self,
        *,
        instance_id: InstanceId,
        bootstrap_handle: str,
    ) -> CurrentIdentityResult:
        """Install and atomically activate one tokenless local recovery Token."""
        with self._credential_lock():
            if self.instance.select_instance() != instance_id:
                raise AuthenticationFailedError
            subject_id = self.instance.select_bootstrap_subject(
                instance_id=instance_id,
                handle=bootstrap_handle,
            )
            token_id, raw_token = self._new_token()
            expires_at = _dependency_time(self.clock) + _HUMAN_DEFAULT_LIFETIME
            previous = self.credentials.load(self.profile)
            credential = HumanCredential(
                profile=self.profile,
                instance_id=instance_id,
                subject_id=subject_id,
                raw_token=raw_token,
            )
            try:
                self.credentials.replace(credential)
                result = self.tokens.recover_local(
                    instance_id=instance_id,
                    bootstrap_handle=bootstrap_handle,
                    token_id=token_id,
                    token_digest=hash_token(raw_token),
                    expires_at=expires_at,
                )
            except ApplicationError:
                if previous is not None:
                    with suppress(ApplicationError):
                        self.credentials.replace(previous)
                else:
                    with suppress(ApplicationError):
                        self.credentials.delete(self.profile)
                raise
        if result.subject.id != subject_id:
            raise CredentialUnavailableError
        return result

    @contextmanager
    def _credential_lock(self) -> Iterator[None]:
        """Serialize credential and recovery changes across local processes."""
        lock = FileLock(
            self.credential_lock_path,
            timeout=_CREDENTIAL_LOCK_TIMEOUT_SECONDS,
            mode=_TOKEN_FILE_MODE,
            preserve_lock_file=True,
        )
        try:
            with lock:
                yield
        except (OSError, Timeout) as error:
            raise CredentialUnavailableError from error

    def provision_token(
        self,
        *,
        actor: AuthenticatedActor,
        subject: SubjectId | str,
        output: ProtectedTokenFile,
        expires_in: timedelta | None,
        idempotency_key: str | None,
    ) -> TokenResult:
        """Coordinate pending issue, protected output, activation, and cleanup."""
        existing = output.load_retry()
        if existing is not None:
            if idempotency_key is None:
                raise CredentialUnavailableError
            parsed = parse_token(existing)
            try:
                return self.tokens.activate(
                    actor=actor,
                    token_id=parsed.token_id,
                    idempotency_key=idempotency_key,
                )
            except TokenNotFoundError as error:
                raise CredentialUnavailableError from error

        target = self._resolve_subject(actor=actor, selector=subject)
        lifetime = _resolve_token_lifetime(target.kind, expires_in)
        token_id, raw_token = self._new_token()
        expires_at = _dependency_time(self.clock) + lifetime
        pending = self.tokens.issue_pending(
            actor=actor,
            token_id=token_id,
            subject=target.id,
            token_digest=hash_token(raw_token),
            expires_at=expires_at,
        )
        created: os.stat_result | None = None
        try:
            created = output.create(raw_token)
            return self.tokens.activate(
                actor=actor,
                token_id=pending.token.id,
                idempotency_key=idempotency_key,
            )
        except Exception:
            compensation_failed = False
            try:
                self.tokens.revoke(actor=actor, token_id=pending.token.id)
            except ApplicationError:
                compensation_failed = True
            if created is not None:
                try:
                    output.compensate(created)
                except ApplicationError:
                    compensation_failed = True
            if compensation_failed:
                raise CredentialUnavailableError from None
            raise

    def _resolve_subject(
        self,
        *,
        actor: AuthenticatedActor,
        selector: SubjectId | str,
    ) -> Subject:
        """Resolve one exact Token target through bounded Subject pages."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self.subjects.list(actor=actor, cursor=cursor, limit=500)
            for subject in page.subjects:
                if selector in (subject.id, subject.handle):
                    return subject
            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise CredentialUnavailableError
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        raise SubjectNotFoundError

    def _new_token(self) -> tuple[TokenId, RawToken]:
        """Allocate one public ID and canonical high-entropy raw Token."""
        token_id = self.identifiers.new_token_id()
        if not isinstance(token_id, TokenId):
            raise CredentialUnavailableError
        try:
            return token_id, generate_token(token_id)
        except (TokenGenerationError, TypeError, ValueError) as error:
            raise CredentialUnavailableError from error

    def _validate_explicit_file_location(self) -> None:
        """Validate source exclusivity before reading a mounted credential."""
        direct: object = self.environment.get("WORKAHOLIC_TOKEN")
        value: object = self.environment.get("WORKAHOLIC_TOKEN_FILE")
        if direct is not None and not isinstance(direct, str):
            raise InvalidInputError
        if value is not None and not isinstance(value, str):
            raise InvalidInputError
        if direct not in (None, "") and value not in (None, ""):
            raise InvalidInputError
        if value is None or value == "":
            return
        try:
            target = Path(value).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise CredentialUnavailableError from error
        for root in self.forbidden_roots:
            canonical_root = root.resolve(strict=False)
            if target == canonical_root or canonical_root in target.parents:
                raise InvalidInputError


def _resolve_token_lifetime(
    kind: SubjectKind,
    requested: timedelta | None,
) -> timedelta:
    """Resolve and validate the exact Human or Agent Token lifetime."""
    if kind is SubjectKind.HUMAN:
        default, minimum, maximum = (
            _HUMAN_DEFAULT_LIFETIME,
            _HUMAN_MINIMUM_LIFETIME,
            _HUMAN_MAXIMUM_LIFETIME,
        )
    else:
        default, minimum, maximum = (
            _AGENT_DEFAULT_LIFETIME,
            _AGENT_MINIMUM_LIFETIME,
            _AGENT_MAXIMUM_LIFETIME,
        )
    lifetime = default if requested is None else requested
    if not minimum <= lifetime <= maximum:
        raise InvalidInputError
    return lifetime


def _dependency_time(clock: Clock) -> datetime:
    """Read and validate one authoritative UTC credential-operation time."""
    value = clock.now()
    if (
        not hasattr(value, "tzinfo")
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise CredentialUnavailableError
    return value


def _git_root_for(path: Path) -> Path | None:
    """Return the nearest containing Git worktree/repository root, if any."""
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        try:
            if marker.exists() or marker.is_symlink():
                return candidate
        except OSError as error:
            raise CredentialUnavailableError from error
    return None


def _fsync_directory(directory: Path) -> None:
    """Durably commit an output-directory entry change."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
