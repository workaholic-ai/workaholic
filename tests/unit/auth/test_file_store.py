"""Unit tests for the crash-safe protected-TOML credential fallback."""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from tests.unit.auth.test_credentials import _credential

if TYPE_CHECKING:
    from os import PathLike

    from workaholic.auth import HumanCredential
from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth import FileCredentialStore
from workaholic.auth.file_store import (
    _ensure_protected_directory,
    _optional_safe_file_metadata,
    _require_same_directory,
    _safe_lstat,
    _serialize_document,
)


def _store(tmp_path: Path) -> FileCredentialStore:
    """Build one isolated fallback store without creating it."""
    directory = tmp_path / "config" / "credentials"
    return FileCredentialStore(directory, directory / "credentials.toml")


def test_file_store_profile_isolation_replacement_modes_and_restart(
    tmp_path: Path,
) -> None:
    """Protected TOML persists independent profiles with exact account-only modes."""
    store = _store(tmp_path)
    local = _credential("local")
    staging = _credential("staging")

    assert store.load("local") is None
    store.replace(local)
    store.replace(staging)
    assert stat.S_IMODE(store.credentials_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.credentials_file.stat().st_mode) == 0o600
    assert store.load("local") == local
    assert store.load("staging") == staging

    reopened = _store(tmp_path)
    assert reopened.load("local") == local
    reopened.delete("local")
    reopened.delete("local")
    assert reopened.load("local") is None
    assert reopened.load("staging") == staging
    assert not tuple(store.credentials_file.parent.glob(".credentials.*"))


def test_file_store_replaces_one_profile_without_rendering_secret(
    tmp_path: Path,
) -> None:
    """Replacement changes only the selected entry and public repr stays redacted."""
    store = _store(tmp_path)
    original = _credential("local")
    store.replace(original)
    raw_text = original.raw_token.get_secret_value()

    replacement = _credential("local")
    store.replace(replacement)

    assert store.load("local") == replacement
    assert raw_text in store.credentials_file.read_text(encoding="utf-8")
    assert raw_text not in repr(store.load("local"))


@pytest.mark.parametrize(
    "content",
    [
        b"not toml",
        b"version = 2\n[credentials]\n",
        b"version = 1\nunknown = 1\n[credentials]\n",
        b"\xff\xfe",
        b"x" * 1_048_577,
    ],
)
def test_malformed_encoding_version_shape_and_size_fail_closed(
    content: bytes,
    tmp_path: Path,
) -> None:
    """The fallback reader accepts one bounded exact versioned TOML grammar."""
    store = _store(tmp_path)
    store.credentials_file.parent.mkdir(parents=True, mode=0o700)
    store.credentials_file.write_bytes(content)
    store.credentials_file.chmod(0o600)

    with pytest.raises(CredentialUnavailableError):
        store.load("local")


@pytest.mark.parametrize("mode", [0o640, 0o660, 0o666])
def test_unsafe_existing_file_modes_are_rejected(mode: int, tmp_path: Path) -> None:
    """Existing fallback files must retain exact mode ``0600``."""
    store = _store(tmp_path)
    store.credentials_file.parent.mkdir(parents=True, mode=0o700)
    store.credentials_file.write_text(
        "version = 1\n\n[credentials]\n",
        encoding="utf-8",
    )
    store.credentials_file.chmod(mode)

    with pytest.raises(CredentialUnavailableError):
        store.load("local")


def test_symlink_file_and_directory_are_rejected(tmp_path: Path) -> None:
    """Fallback storage never follows a repository-controlled symlink."""
    store = _store(tmp_path)
    target = tmp_path / "target.toml"
    target.write_text("version = 1\n[credentials]\n", encoding="utf-8")
    target.chmod(0o600)
    store.credentials_file.parent.mkdir(parents=True, mode=0o700)
    store.credentials_file.symlink_to(target)
    with pytest.raises(CredentialUnavailableError):
        store.load("local")

    symlink_directory = tmp_path / "linked" / "credentials"
    symlink_directory.parent.mkdir()
    real_directory = tmp_path / "real-credentials"
    real_directory.mkdir(mode=0o700)
    symlink_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(InvalidInputError):
        FileCredentialStore(
            symlink_directory,
            symlink_directory / "credentials.toml",
        )


def test_forbidden_workspace_or_git_root_is_rejected(tmp_path: Path) -> None:
    """Protected credentials cannot be placed beneath a caller-supplied unsafe root."""
    root = tmp_path / "workspace"
    directory = root / "config" / "credentials"
    with pytest.raises(InvalidInputError):
        FileCredentialStore(
            directory,
            directory / "credentials.toml",
            forbidden_roots=(root,),
        )


def test_replace_failure_preserves_original_and_cleans_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic replace retains the old credential registry."""
    store = _store(tmp_path)
    original = _credential("local")
    store.replace(original)
    before = store.credentials_file.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> Path:
        """Simulate an operating-system atomic replacement failure."""
        message = "private replace failure"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(CredentialUnavailableError):
        store.replace(_credential("staging"))

    assert store.credentials_file.read_bytes() == before
    assert not tuple(store.credentials_file.parent.glob(".credentials.*"))


def test_fsync_failure_before_replace_preserves_original(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failure to durably flush the temporary file never replaces live state."""
    store = _store(tmp_path)
    store.replace(_credential("local"))
    before = store.credentials_file.read_bytes()

    def fail_fsync(_descriptor: int) -> None:
        """Simulate a private durable-write failure."""
        message = "private fsync failure"
        raise OSError(message)

    monkeypatch.setattr("workaholic.auth.file_store.os.fsync", fail_fsync)
    with pytest.raises(CredentialUnavailableError):
        store.replace(_credential("staging"))
    assert store.credentials_file.read_bytes() == before
    assert not tuple(store.credentials_file.parent.glob(".credentials.*"))


def test_concurrent_target_change_is_rejected_before_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A target changed after initial validation is never overwritten."""
    store = _store(tmp_path)
    store.replace(_credential("local"))
    original_mkstemp = tempfile.mkstemp

    def mutate_then_create(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | PathLike[str] | None = None,  # noqa: A002
        text: bool = False,  # noqa: FBT001, FBT002
    ) -> tuple[int, str]:
        """Change live state after initial metadata capture, then create temp."""
        store.credentials_file.write_text("attacker change", encoding="utf-8")
        store.credentials_file.chmod(0o600)
        return original_mkstemp(suffix=suffix, prefix=prefix, dir=dir, text=text)

    monkeypatch.setattr(
        "workaholic.auth.file_store.tempfile.mkstemp",
        mutate_then_create,
    )
    with pytest.raises(CredentialUnavailableError):
        store.replace(_credential("staging"))
    assert store.credentials_file.read_text(encoding="utf-8") == "attacker change"
    assert not tuple(store.credentials_file.parent.glob(".credentials.*"))


@pytest.mark.parametrize(
    ("directory", "credential_file"),
    [
        ("relative", "relative/credentials.toml"),
        (Path("relative"), Path("relative/credentials.toml")),
        (Path.cwd() / "credentials", Path.cwd() / "other.toml"),
    ],
)
def test_file_store_constructor_rejects_invalid_path_contract(
    directory: object,
    credential_file: object,
) -> None:
    """The protected file location requires exact absolute Path operands."""
    with pytest.raises(InvalidInputError):
        FileCredentialStore(
            cast("Path", directory),
            cast("Path", credential_file),
        )


@pytest.mark.parametrize("forbidden_root", ["not-a-path", Path("relative")])
def test_file_store_rejects_invalid_forbidden_roots(
    forbidden_root: object,
    tmp_path: Path,
) -> None:
    """Forbidden roots themselves must be trusted absolute Path values."""
    directory = tmp_path / "credentials"
    with pytest.raises(InvalidInputError):
        FileCredentialStore(
            directory,
            directory / "credentials.toml",
            forbidden_roots=(cast("Path", forbidden_root),),
        )


def test_file_store_replace_rejects_malformed_credential(tmp_path: Path) -> None:
    """The file adapter does not accept a credential-shaped arbitrary object."""
    with pytest.raises(InvalidInputError):
        _store(tmp_path).replace(cast("HumanCredential", object()))


def test_file_store_rejects_non_directory_and_unsafe_directory_mode(
    tmp_path: Path,
) -> None:
    """The protected root must remain a real account-only directory."""
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("content", encoding="utf-8")
    with pytest.raises(CredentialUnavailableError):
        _ensure_protected_directory(file_path)

    directory = tmp_path / "unsafe-directory"
    directory.mkdir(mode=0o700)
    directory.chmod(0o755)
    with pytest.raises(CredentialUnavailableError):
        _ensure_protected_directory(directory)


def test_file_store_internal_metadata_errors_are_mapped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Filesystem inspection failures never escape as raw operating-system errors."""
    missing = tmp_path / "missing"

    def fail_lstat(_path: Path) -> object:
        """Raise a simulated private metadata failure."""
        message = "private metadata failure"
        raise PermissionError(message)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    with pytest.raises(CredentialUnavailableError):
        _safe_lstat(missing)
    with pytest.raises(CredentialUnavailableError):
        _optional_safe_file_metadata(missing)


def test_file_store_detects_changed_directory_identity(tmp_path: Path) -> None:
    """Atomic writes reject a directory whose identity changes mid-operation."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)

    with pytest.raises(CredentialUnavailableError):
        _require_same_directory(first.stat(), second.stat())


def test_file_store_serializer_rejects_profile_key_mismatch() -> None:
    """Registry serialization binds each entry to its exact profile key."""
    with pytest.raises(CredentialUnavailableError):
        _serialize_document({"staging": _credential("local")})


@pytest.mark.parametrize(
    "content",
    [
        b'version = 1\ncredentials = "invalid"\n',
        b'version = 1\n[credentials.local]\ninstance_id = "ins_local"\n',
    ],
)
def test_file_store_rejects_malformed_credential_entries(
    content: bytes,
    tmp_path: Path,
) -> None:
    """Every persisted profile entry must match the exact closed schema."""
    store = _store(tmp_path)
    store.credentials_file.parent.mkdir(parents=True, mode=0o700)
    store.credentials_file.write_bytes(content)
    store.credentials_file.chmod(0o600)

    with pytest.raises(CredentialUnavailableError):
        store.load("local")
