"""Strict trusted embedded-profile configuration loading."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from workaholic.context._files import (
    UnsafeDataFileError,
    read_bounded_regular_file,
)
from workaholic.context.errors import (
    ContextInvalidError,
    ProfileInvalidError,
    ProfileUnsupportedError,
)
from workaholic.context.models import (
    EmbeddedProfile,
    LocalConfigPaths,
    ProfileRegistry,
)
from workaholic.context.paths import resolve_local_data_paths
from workaholic.domain import validate_profile_name

_PROFILE_CONFIG_VERSION = 1
_PROFILES_MAX_BYTES = 64 * 1_024
_DATABASE_FILENAME = "local.db"
_TOP_LEVEL_KEYS = frozenset({"version", "default_profile", "profiles"})
_REQUIRED_TOP_LEVEL_KEYS = frozenset({"version", "profiles"})
_PROFILE_KEYS = frozenset({"mode", "data_directory"})


def load_profile_registry(
    paths: LocalConfigPaths,
    environment: Mapping[str, str],
) -> ProfileRegistry:
    """Load immutable embedded profiles without opening or creating storage.

    An absent file preserves the built-in ``local`` profile. A present file is
    authoritative and must use the exact closed version-1 grammar.

    Args:
        paths: Validated trusted configuration paths.
        environment: Trusted process environment used only by the absent-file
            built-in ``local`` profile.

    Returns:
        Immutable validated profile registry.

    Raises:
        ProfileInvalidError: If the paths, environment, file, or values are
            malformed or unsafe.
        ProfileUnsupportedError: If the version or mode is unsupported.

    """
    candidate_paths: object = paths
    candidate_environment: object = environment
    if not isinstance(candidate_paths, LocalConfigPaths) or not isinstance(
        candidate_environment,
        Mapping,
    ):
        raise ProfileInvalidError
    try:
        content = read_bounded_regular_file(
            candidate_paths.profiles_file,
            maximum=_PROFILES_MAX_BYTES,
        )
    except FileNotFoundError:
        return _built_in_registry(candidate_environment)
    except (OSError, UnsafeDataFileError) as error:
        raise ProfileInvalidError from error

    try:
        decoded_text = content.decode("utf-8")
        decoded: object = tomllib.loads(decoded_text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ProfileInvalidError from error
    return _parse_registry(decoded)


def _built_in_registry(environment: Mapping[str, str]) -> ProfileRegistry:
    """Build the backward-compatible trusted ``local`` profile.

    Args:
        environment: Trusted process environment for local data selection.

    Returns:
        A one-profile immutable registry.

    Raises:
        ProfileInvalidError: If the trusted data override is malformed.

    """
    try:
        data_paths = resolve_local_data_paths(environment)
    except ContextInvalidError as error:
        raise ProfileInvalidError from error
    try:
        data_directory = data_paths.data_directory.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ProfileInvalidError from error
    profile = EmbeddedProfile(
        name="local",
        data_directory=data_directory,
        database_path=data_directory / _DATABASE_FILENAME,
    )
    return ProfileRegistry(default_profile="local", profiles={"local": profile})


def _parse_registry(value: object) -> ProfileRegistry:
    """Parse one decoded closed profile configuration.

    Args:
        value: Candidate TOML root object.

    Returns:
        Immutable validated profile registry.

    Raises:
        ProfileInvalidError: If the object violates the closed grammar.
        ProfileUnsupportedError: If the version or mode is unsupported.

    """
    if not isinstance(value, dict):
        raise ProfileInvalidError
    keys = set(value)
    if not keys >= _REQUIRED_TOP_LEVEL_KEYS or not keys <= _TOP_LEVEL_KEYS:
        raise ProfileInvalidError

    version: object = value["version"]
    if type(version) is not int:
        raise ProfileInvalidError
    if version != _PROFILE_CONFIG_VERSION:
        raise ProfileUnsupportedError

    raw_profiles: object = value["profiles"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ProfileInvalidError

    profiles: dict[str, EmbeddedProfile] = {}
    for raw_name, raw_profile in raw_profiles.items():
        try:
            name = validate_profile_name(raw_name)
        except ValueError as error:
            raise ProfileInvalidError from error
        profiles[name] = _parse_profile(name, raw_profile)

    raw_default: object = value.get("default_profile", "local")
    try:
        default_profile = validate_profile_name(raw_default)
    except ValueError as error:
        raise ProfileInvalidError from error
    return ProfileRegistry(default_profile=default_profile, profiles=profiles)


def _parse_profile(name: str, value: object) -> EmbeddedProfile:
    """Parse one exact embedded profile table.

    Args:
        name: Validated profile name.
        value: Candidate profile table.

    Returns:
        Validated canonical embedded profile.

    Raises:
        ProfileInvalidError: If fields or the data directory are malformed.
        ProfileUnsupportedError: If the mode is not ``embedded``.

    """
    if not isinstance(value, dict) or set(value) != _PROFILE_KEYS:
        raise ProfileInvalidError
    mode: object = value["mode"]
    if not isinstance(mode, str):
        raise ProfileInvalidError
    if mode != "embedded":
        raise ProfileUnsupportedError

    raw_data_directory: object = value["data_directory"]
    if (
        not isinstance(raw_data_directory, str)
        or not raw_data_directory
        or "\x00" in raw_data_directory
    ):
        raise ProfileInvalidError
    data_directory = Path(raw_data_directory)
    if not data_directory.is_absolute():
        raise ProfileInvalidError
    try:
        canonical_directory = data_directory.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ProfileInvalidError from error
    return EmbeddedProfile(
        name=name,
        data_directory=canonical_directory,
        database_path=canonical_directory / _DATABASE_FILENAME,
    )
