"""Unit tests for explicit process and mounted-secret credential sources."""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING, cast

import pytest

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth import (
    CredentialBackend,
    ExplicitCredentialKind,
    read_token_file,
    resolve_credential_backend,
    resolve_explicit_credential,
)

if TYPE_CHECKING:
    from pathlib import Path


def _raw_text(label: str = "source") -> str:
    """Build deterministic canonical Token text for one test label.

    Args:
        label: Opaque public Token ID suffix.

    Returns:
        Canonical raw Token string.

    """
    encoded = base64.urlsafe_b64encode(label.encode().ljust(32, b"x")[:32])
    return f"tok_{label}.{encoded.decode('ascii').rstrip('=')}"


def _write_token_file(
    path: Path,
    *,
    content: str | bytes | None = None,
    mode: int = 0o600,
) -> None:
    """Create one test Token file with explicit bytes and mode.

    Args:
        path: Target file path.
        content: Optional text or bytes; defaults to canonical Token text.
        mode: POSIX permission bits applied after creation.

    """
    value = _raw_text() if content is None else content
    data = value.encode("utf-8") if isinstance(value, str) else value
    path.write_bytes(data)
    path.chmod(mode)


def test_direct_environment_token_has_precedence_and_environment_is_unchanged() -> None:
    """A direct nonempty Token is selected without mutating process input."""
    text = _raw_text("direct")
    environment = {"WORKAHOLIC_TOKEN": text}
    before = environment.copy()

    credential = resolve_explicit_credential(environment)

    assert credential is not None
    assert credential.kind is ExplicitCredentialKind.ENVIRONMENT
    assert credential.raw_token.get_secret_value() == text
    assert text not in repr(credential)
    assert environment == before


def test_file_environment_token_resolves_one_protected_file(tmp_path: Path) -> None:
    """An absent direct value permits the mounted-file source."""
    token_file = tmp_path / "agent.token"
    _write_token_file(token_file, content=f"{_raw_text('file')}\n")

    credential = resolve_explicit_credential(
        {
            "WORKAHOLIC_TOKEN": "",
            "WORKAHOLIC_TOKEN_FILE": str(token_file),
        }
    )

    assert credential is not None
    assert credential.kind is ExplicitCredentialKind.FILE
    assert credential.raw_token.get_secret_value() == _raw_text("file")


def test_absent_and_empty_explicit_sources_return_none() -> None:
    """Empty process variables count as absent and allow Human-store fallback."""
    assert resolve_explicit_credential({}) is None
    assert (
        resolve_explicit_credential(
            {"WORKAHOLIC_TOKEN": "", "WORKAHOLIC_TOKEN_FILE": ""}
        )
        is None
    )


def test_two_nonempty_process_sources_are_rejected_before_parsing() -> None:
    """Mutually exclusive explicit inputs never establish ambiguous identity."""
    with pytest.raises(InvalidInputError):
        resolve_explicit_credential(
            {
                "WORKAHOLIC_TOKEN": "malformed-private-value",
                "WORKAHOLIC_TOKEN_FILE": "/private/missing-token",
            }
        )


@pytest.mark.parametrize(
    "environment",
    [
        cast("dict[str, str]", object()),
        cast("dict[str, str]", {"WORKAHOLIC_TOKEN": 7}),
        cast("dict[str, str]", {"WORKAHOLIC_TOKEN_FILE": 7}),
        {"WORKAHOLIC_TOKEN": " malformed"},
        {"WORKAHOLIC_TOKEN_FILE": "relative/token"},
        {"WORKAHOLIC_TOKEN_FILE": f"{os.sep}invalid\x00token"},
    ],
)
def test_malformed_explicit_source_input_is_invalid(
    environment: dict[str, str],
) -> None:
    """Mapping, type, Token, and absolute-path syntax are runtime validated."""
    with pytest.raises(InvalidInputError):
        resolve_explicit_credential(environment)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, CredentialBackend.AUTO),
        ("", CredentialBackend.AUTO),
        ("auto", CredentialBackend.AUTO),
        ("keyring", CredentialBackend.KEYRING),
        ("file", CredentialBackend.FILE),
    ],
)
def test_credential_backend_selection_is_closed_and_deterministic(
    value: str | None,
    expected: CredentialBackend,
) -> None:
    """Backend selection accepts only the documented exact values."""
    environment = {} if value is None else {"WORKAHOLIC_CREDENTIAL_BACKEND": value}
    assert resolve_credential_backend(environment) is expected


@pytest.mark.parametrize("value", ["AUTO", "fallback", " file ", "0"])
def test_invalid_backend_selection_is_rejected(value: str) -> None:
    """Backend values are not normalized or treated as fallback hints."""
    with pytest.raises(InvalidInputError):
        resolve_credential_backend({"WORKAHOLIC_CREDENTIAL_BACKEND": value})


def test_mounted_secret_symlink_resolves_to_protected_regular_target(
    tmp_path: Path,
) -> None:
    """Orchestrator-style symlinks intentionally resolve before a no-follow read."""
    versioned = tmp_path / "..2026_08_29"
    versioned.mkdir()
    target = versioned / "token"
    _write_token_file(target, content=_raw_text("mounted"), mode=0o400)
    mounted = tmp_path / "token"
    mounted.symlink_to(target)

    raw_token = read_token_file(mounted)

    assert raw_token.get_secret_value() == _raw_text("mounted")
    assert mounted.is_symlink()
    assert target.read_text(encoding="utf-8") == _raw_text("mounted")


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_group_or_world_writable_token_file_is_unavailable(
    mode: int,
    tmp_path: Path,
) -> None:
    """A writable mounted-secret target fails closed before parsing."""
    token_file = tmp_path / "unsafe.token"
    _write_token_file(token_file, mode=mode)

    with pytest.raises(CredentialUnavailableError):
        read_token_file(token_file)


def test_directory_missing_broken_link_and_fifo_are_unavailable(tmp_path: Path) -> None:
    """Only an existing stable regular final target can supply a credential."""
    candidates = [tmp_path / "missing", tmp_path]
    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "absent-target")
    candidates.append(broken)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        candidates.append(fifo)

    for candidate in candidates:
        with pytest.raises(CredentialUnavailableError):
            read_token_file(candidate)


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe",
        b"x" * 513,
        b"",
        (_raw_text() + "\n\n").encode(),
        (_raw_text() + "\r\n").encode(),
        b"not-a-token\n",
    ],
)
def test_invalid_file_encoding_size_or_content_is_rejected(
    content: bytes,
    tmp_path: Path,
) -> None:
    """File bytes are bounded, UTF-8, and exactly one optionally terminated Token."""
    token_file = tmp_path / "invalid.token"
    _write_token_file(token_file, content=content)

    expected_error = (
        CredentialUnavailableError if len(content) > 512 else InvalidInputError
    )
    with pytest.raises(expected_error):
        read_token_file(token_file)


def test_selected_file_failure_does_not_fall_through_to_any_other_source(
    tmp_path: Path,
) -> None:
    """An explicit file remains authoritative when its contents are malformed."""
    token_file = tmp_path / "malformed.token"
    _write_token_file(token_file, content="malformed")
    with pytest.raises(InvalidInputError):
        resolve_explicit_credential({"WORKAHOLIC_TOKEN_FILE": str(token_file)})
