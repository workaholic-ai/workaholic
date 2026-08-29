"""Crash-safe protected-TOML fallback for Human profile credentials."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Final

from workaholic.application import CredentialUnavailableError, InvalidInputError
from workaholic.auth._files import (
    UnsafeDataFileError,
    read_bounded_regular_file_snapshot,
)
from workaholic.auth.credentials import (
    HumanCredential,
    credential_from_mapping,
    credential_to_mapping,
    validate_credential_profile,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_CREDENTIAL_FILE_VERSION: Final = 1
_CREDENTIAL_FILE_MAX_BYTES: Final = 1_048_576
_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_ROOT_KEYS: Final = frozenset(("credentials", "version"))
_ENTRY_KEYS: Final = frozenset(("instance_id", "subject_id", "token"))


class FileCredentialStore:
    """CredentialStore using one dedicated account-only TOML file."""

    def __init__(
        self,
        credentials_directory: Path,
        credentials_file: Path,
        *,
        forbidden_roots: Sequence[Path] = (),
    ) -> None:
        """Validate stable path ownership without creating filesystem state.

        Args:
            credentials_directory: Dedicated directory required at mode ``0700``.
            credentials_file: Exact ``credentials.toml`` child path.
            forbidden_roots: Canonical Workspace or Git roots that must not contain
                protected credentials.

        Raises:
            InvalidInputError: If paths are relative, inconsistent, or forbidden.

        """
        candidate_directory: object = credentials_directory
        candidate_file: object = credentials_file
        if not isinstance(candidate_directory, Path) or not isinstance(
            candidate_file,
            Path,
        ):
            raise InvalidInputError
        if (
            not candidate_directory.is_absolute()
            or not candidate_file.is_absolute()
            or candidate_file != candidate_directory / "credentials.toml"
        ):
            raise InvalidInputError
        try:
            supplied_directory_metadata = candidate_directory.lstat()
        except FileNotFoundError:
            supplied_directory_metadata = None
        except OSError as error:
            raise InvalidInputError from error
        if supplied_directory_metadata is not None and stat.S_ISLNK(
            supplied_directory_metadata.st_mode
        ):
            raise InvalidInputError
        try:
            canonical_directory = candidate_directory.resolve(strict=False)
            canonical_file = candidate_file.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise InvalidInputError from error
        if canonical_file != canonical_directory / "credentials.toml":
            raise InvalidInputError
        for root in forbidden_roots:
            candidate_root: object = root
            if not isinstance(candidate_root, Path) or not candidate_root.is_absolute():
                raise InvalidInputError
            try:
                canonical_root = root.resolve(strict=False)
            except (OSError, RuntimeError) as error:
                raise InvalidInputError from error
            if canonical_directory == canonical_root or canonical_root in (
                canonical_directory.parents
            ):
                raise InvalidInputError
        self._directory = canonical_directory
        self._path = canonical_file

    @property
    def credentials_file(self) -> Path:
        """Return the exact protected fallback file path."""
        return self._path

    def load(self, profile: str) -> HumanCredential | None:
        """Load one profile credential from protected TOML.

        Args:
            profile: Trusted selected profile name.

        Returns:
            Stored credential or ``None``.

        Raises:
            CredentialUnavailableError: If protected storage is unsafe or malformed.

        """
        name = validate_credential_profile(profile)
        entries = self._read_entries()
        return entries.get(name)

    def replace(self, credential: HumanCredential) -> None:
        """Atomically create or replace one profile credential.

        Args:
            credential: Validated profile credential.

        Raises:
            CredentialUnavailableError: If protected storage cannot commit safely.
            InvalidInputError: If the credential is malformed.

        """
        if not isinstance(credential, HumanCredential):
            raise InvalidInputError
        entries = self._read_entries()
        entries[credential.profile] = credential
        self._write_entries(entries)

    def delete(self, profile: str) -> None:
        """Atomically remove one profile credential if present.

        Args:
            profile: Trusted selected profile name.

        Raises:
            CredentialUnavailableError: If protected storage cannot commit safely.

        """
        name = validate_credential_profile(profile)
        entries = self._read_entries()
        if name not in entries:
            return
        del entries[name]
        self._write_entries(entries)

    def _read_entries(self) -> dict[str, HumanCredential]:
        """Read and parse the complete protected credential registry."""
        try:
            snapshot = read_bounded_regular_file_snapshot(
                self._path,
                maximum=_CREDENTIAL_FILE_MAX_BYTES,
            )
        except FileNotFoundError:
            return {}
        except (OSError, UnsafeDataFileError) as error:
            raise CredentialUnavailableError from error
        _require_mode(snapshot.metadata, expected=_FILE_MODE)
        try:
            decoded = snapshot.content.decode("utf-8")
            document: object = tomllib.loads(decoded)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise CredentialUnavailableError from error
        return _parse_document(document)

    def _write_entries(self, entries: Mapping[str, HumanCredential]) -> None:
        """Serialize and atomically commit a complete credential registry."""
        content = _serialize_document(entries).encode("utf-8")
        if len(content) > _CREDENTIAL_FILE_MAX_BYTES:
            raise CredentialUnavailableError
        _ensure_protected_directory(self._directory)
        initial_directory = _safe_lstat(self._directory)
        initial_file = _optional_safe_file_metadata(self._path)
        temporary_path: Path | None = None
        descriptor: int | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".credentials.",
                dir=self._directory,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, _FILE_MODE)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _require_same_directory(initial_directory, _safe_lstat(self._directory))
            current_file = _optional_safe_file_metadata(self._path)
            _require_same_optional_metadata(initial_file, current_file)
            temporary_path.replace(self._path)
            temporary_path = None
            _require_mode(_safe_lstat(self._path), expected=_FILE_MODE)
            _fsync_directory(self._directory)
        except (OSError, CredentialUnavailableError) as error:
            raise CredentialUnavailableError from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)


def _ensure_protected_directory(directory: Path) -> None:
    """Create or validate one exact account-only credential directory."""
    if os.name != "posix":
        raise CredentialUnavailableError
    try:
        directory.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
        metadata = directory.lstat()
    except OSError as error:
        raise CredentialUnavailableError from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CredentialUnavailableError
    _require_mode(metadata, expected=_DIRECTORY_MODE)


def _safe_lstat(path: Path) -> os.stat_result:
    """Return metadata or map an unsafe filesystem outcome."""
    try:
        return path.lstat()
    except OSError as error:
        raise CredentialUnavailableError from error


def _optional_safe_file_metadata(path: Path) -> os.stat_result | None:
    """Return safe existing-file metadata or ``None`` without following links."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CredentialUnavailableError from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CredentialUnavailableError
    _require_mode(metadata, expected=_FILE_MODE)
    return metadata


def _require_mode(metadata: os.stat_result, *, expected: int) -> None:
    """Require exact POSIX mode bits for one protected filesystem object."""
    if os.name != "posix" or stat.S_IMODE(metadata.st_mode) != expected:
        raise CredentialUnavailableError


def _require_same_optional_metadata(
    expected: os.stat_result | None,
    actual: os.stat_result | None,
) -> None:
    """Require a target path to remain absent or retain the same identity."""
    if expected is None or actual is None:
        if expected is not actual:
            raise CredentialUnavailableError
        return
    _require_same_metadata(expected, actual)


def _require_same_metadata(
    expected: os.stat_result,
    actual: os.stat_result,
) -> None:
    """Require stable identity, size, and content-change timestamps."""
    if not (
        os.path.samestat(expected, actual)
        and expected.st_size == actual.st_size
        and expected.st_mtime_ns == actual.st_mtime_ns
        and expected.st_ctime_ns == actual.st_ctime_ns
    ):
        raise CredentialUnavailableError


def _require_same_directory(
    expected: os.stat_result,
    actual: os.stat_result,
) -> None:
    """Require the same protected directory despite expected entry changes."""
    if (
        not os.path.samestat(expected, actual)
        or not stat.S_ISDIR(actual.st_mode)
        or stat.S_IMODE(actual.st_mode) != _DIRECTORY_MODE
    ):
        raise CredentialUnavailableError


def _fsync_directory(directory: Path) -> None:
    """Durably commit one atomic directory-entry replacement."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _serialize_document(entries: Mapping[str, HumanCredential]) -> str:
    """Serialize one stable closed TOML credential registry."""
    lines = [f"version = {_CREDENTIAL_FILE_VERSION}", "", "[credentials]"]
    for profile in sorted(entries):
        credential = entries[profile]
        if credential.profile != profile:
            raise CredentialUnavailableError
        values = credential_to_mapping(credential)
        lines.extend(
            (
                "",
                f"[credentials.{json.dumps(profile, ensure_ascii=True)}]",
                f"instance_id = {json.dumps(values['instance_id'])}",
                f"subject_id = {json.dumps(values['subject_id'])}",
                f"token = {json.dumps(values['token'])}",
            )
        )
    return "\n".join(lines) + "\n"


def _parse_document(value: object) -> dict[str, HumanCredential]:
    """Parse one exact versioned closed TOML credential registry."""
    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise CredentialUnavailableError
    if (
        type(value["version"]) is not int
        or value["version"] != _CREDENTIAL_FILE_VERSION
    ):
        raise CredentialUnavailableError
    raw_entries: object = value["credentials"]
    if not isinstance(raw_entries, dict):
        raise CredentialUnavailableError
    entries: dict[str, HumanCredential] = {}
    for profile, raw_entry in raw_entries.items():
        if (
            not isinstance(profile, str)
            or not isinstance(raw_entry, dict)
            or set(raw_entry) != _ENTRY_KEYS
        ):
            raise CredentialUnavailableError
        mapping = {"profile": profile, **raw_entry}
        credential = credential_from_mapping(mapping)
        if credential.profile in entries:
            raise CredentialUnavailableError
        entries[credential.profile] = credential
    return entries
