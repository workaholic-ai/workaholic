"""Strict trusted-process and mounted-file credential source resolution."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth._files import (
    UnsafeDataFileError,
    read_bounded_regular_file_snapshot,
)
from workaholic.auth.errors import TokenFormatError
from workaholic.auth.models import CredentialBackend, RawToken
from workaholic.auth.tokens import parse_token

_DIRECT_TOKEN_KEY: Final = "WORKAHOLIC_TOKEN"  # noqa: S105
_TOKEN_FILE_KEY: Final = "WORKAHOLIC_TOKEN_FILE"  # noqa: S105
_CREDENTIAL_BACKEND_KEY: Final = "WORKAHOLIC_CREDENTIAL_BACKEND"
_TOKEN_FILE_MAX_BYTES: Final = 512
_UNSAFE_WRITE_MODE: Final = stat.S_IWGRP | stat.S_IWOTH


class ExplicitCredentialKind(StrEnum):
    """Explicit process credential sources, ordered by contract precedence."""

    ENVIRONMENT = "environment"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class ExplicitCredential:
    """One authoritative explicit credential selected from process input."""

    kind: ExplicitCredentialKind
    raw_token: RawToken

    def __post_init__(self) -> None:
        """Validate runtime fields without rendering the secret."""
        candidate_kind: object = self.kind
        candidate_token: object = self.raw_token
        if not isinstance(candidate_kind, ExplicitCredentialKind) or not isinstance(
            candidate_token,
            RawToken,
        ):
            raise InvalidInputError

    def __repr__(self) -> str:
        """Return source metadata with a redacted credential marker."""
        return f"ExplicitCredential(kind={self.kind!r}, raw_token=<redacted>)"


def resolve_explicit_credential(
    environment: Mapping[str, str],
) -> ExplicitCredential | None:
    """Resolve at most one authoritative process credential source.

    Args:
        environment: Trusted process environment snapshot.

    Returns:
        Selected explicit credential, or ``None`` when both sources are absent.

    Raises:
        InvalidInputError: If variables or selected credential text are invalid.
        CredentialUnavailableError: If a selected Token file cannot be read safely.

    """
    candidate_environment: object = environment
    if not isinstance(candidate_environment, Mapping):
        raise InvalidInputError
    direct = _read_optional_environment_value(
        candidate_environment,
        _DIRECT_TOKEN_KEY,
    )
    token_file = _read_optional_environment_value(
        candidate_environment,
        _TOKEN_FILE_KEY,
    )
    if direct is not None and token_file is not None:
        raise InvalidInputError
    if direct is not None:
        return ExplicitCredential(
            kind=ExplicitCredentialKind.ENVIRONMENT,
            raw_token=_parse_raw_token(direct),
        )
    if token_file is not None:
        path = resolve_token_file_path(token_file)
        return ExplicitCredential(
            kind=ExplicitCredentialKind.FILE,
            raw_token=read_token_file(path),
        )
    return None


def resolve_credential_backend(
    environment: Mapping[str, str],
) -> CredentialBackend:
    """Resolve the exact trusted Human credential backend selector.

    Args:
        environment: Trusted process environment snapshot.

    Returns:
        Explicit backend or ``auto`` when absent or empty.

    Raises:
        InvalidInputError: If the mapping or selector is malformed.

    """
    candidate_environment: object = environment
    if not isinstance(candidate_environment, Mapping):
        raise InvalidInputError
    value: object = candidate_environment.get(_CREDENTIAL_BACKEND_KEY)
    if value is None or value == "":
        return CredentialBackend.AUTO
    if not isinstance(value, str):
        raise InvalidInputError
    try:
        return CredentialBackend(value)
    except ValueError as error:
        raise InvalidInputError from error


def read_token_file(path: Path) -> RawToken:
    """Read one protected mounted-secret Token file without modifying it.

    Symlinks are resolved intentionally for orchestrator mounts. The resolved
    final target is then opened through the shared no-follow stable-snapshot
    boundary, closing the final-component race and bounding memory use.

    Args:
        path: Absolute mounted-secret path.

    Returns:
        Parsed opaque raw Token.

    Raises:
        InvalidInputError: If path syntax or file contents are malformed.
        CredentialUnavailableError: If the target is absent or unsafe.

    """
    candidate_path: object = path
    if not isinstance(candidate_path, Path) or not candidate_path.is_absolute():
        raise InvalidInputError
    try:
        resolved = candidate_path.resolve(strict=True)
        snapshot = read_bounded_regular_file_snapshot(
            resolved,
            maximum=_TOKEN_FILE_MAX_BYTES,
        )
    except (FileNotFoundError, OSError, RuntimeError, UnsafeDataFileError) as error:
        raise CredentialUnavailableError from error
    if os.name != "posix" or snapshot.metadata.st_mode & _UNSAFE_WRITE_MODE:
        raise CredentialUnavailableError
    try:
        content = snapshot.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidInputError from error
    content = content.removesuffix("\n")
    return _parse_raw_token(content)


def resolve_token_file_path(value: object) -> Path:
    """Validate one absolute trusted process Token-file path.

    Args:
        value: Candidate ``WORKAHOLIC_TOKEN_FILE`` value.

    Returns:
        Absolute path without resolving mounted-secret symlinks yet.

    Raises:
        InvalidInputError: If the candidate is not an absolute path string.

    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise InvalidInputError
    path = Path(value)
    if not path.is_absolute():
        raise InvalidInputError
    return path


def _read_optional_environment_value(
    environment: Mapping[str, object],
    key: str,
) -> str | None:
    """Return one nonempty environment value with runtime type checking.

    Args:
        environment: Validated environment mapping.
        key: Exact trusted variable name.

    Returns:
        Nonempty value or ``None`` for absent/empty.

    Raises:
        InvalidInputError: If a present value is not a string.

    """
    value = environment.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidInputError
    return value


def _parse_raw_token(value: str) -> RawToken:
    """Map strict Token parsing into the public invalid-input outcome.

    Args:
        value: Candidate canonical raw Token.

    Returns:
        Validated opaque raw Token.

    Raises:
        InvalidInputError: If the Token is malformed.

    """
    try:
        return parse_token(value).raw_token
    except TokenFormatError as error:
        raise InvalidInputError from error
