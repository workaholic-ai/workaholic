"""Trusted local data-path resolution for embedded operation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import platformdirs

from workaholic.context.errors import ContextInvalidError, ContextStorageError
from workaholic.context.models import LocalConfigPaths, LocalDataPaths

_CONFIG_DIRECTORY_ENVIRONMENT_KEY = "WORKAHOLIC_CONFIG_DIR"
_DATA_DIRECTORY_ENVIRONMENT_KEY = "WORKAHOLIC_DATA_DIR"
_DATABASE_FILENAME = "local.db"
_PROFILES_FILENAME = "profiles.toml"


def resolve_local_config_paths(
    environment: Mapping[str, str],
) -> LocalConfigPaths:
    """Resolve the trusted user configuration and profile-file paths.

    Args:
        environment: Trusted process environment. Only
            ``WORKAHOLIC_CONFIG_DIR`` is inspected.

    Returns:
        Absolute configuration directory and ``profiles.toml`` path. No
        directory or file is created.

    Raises:
        ContextInvalidError: If the environment or override is malformed.
        ContextStorageError: If the platform default cannot be determined.

    """
    candidate_environment: object = environment
    if not isinstance(candidate_environment, Mapping):
        message = "The process environment must be a mapping."
        raise ContextInvalidError(message)
    override: object = candidate_environment.get(_CONFIG_DIRECTORY_ENVIRONMENT_KEY)
    if override is None or override == "":
        try:
            config_directory = Path(
                platformdirs.user_config_path("workaholic", "workaholic-ai")
            )
        except (OSError, RuntimeError) as error:
            message = (
                "The operating-system user configuration directory is unavailable."
            )
            raise ContextStorageError(message) from error
    else:
        if not isinstance(override, str):
            message = "WORKAHOLIC_CONFIG_DIR must be an absolute path string."
            raise ContextInvalidError(message)
        if "\x00" in override:
            message = "WORKAHOLIC_CONFIG_DIR must not contain a null character."
            raise ContextInvalidError(message)
        config_directory = Path(override)

    if not config_directory.is_absolute():
        message = "WORKAHOLIC_CONFIG_DIR must resolve to an absolute path."
        raise ContextInvalidError(message)
    return LocalConfigPaths(
        config_directory=config_directory,
        profiles_file=config_directory / _PROFILES_FILENAME,
    )


def resolve_local_data_paths(
    environment: Mapping[str, str],
) -> LocalDataPaths:
    """Resolve the default database location from trusted process input.

    Args:
        environment: Trusted process environment. Only
            ``WORKAHOLIC_DATA_DIR`` is inspected.

    Returns:
        Absolute local data and SQLite paths. No directory is created.

    Raises:
        ContextInvalidError: If the environment or override is malformed.
        ContextStorageError: If the platform default cannot be determined.

    """
    candidate_environment: object = environment
    if not isinstance(candidate_environment, Mapping):
        message = "The process environment must be a mapping."
        raise ContextInvalidError(message)
    override: object = candidate_environment.get(_DATA_DIRECTORY_ENVIRONMENT_KEY)
    if override is None or override == "":
        try:
            data_directory = Path(
                platformdirs.user_data_path("workaholic", "workaholic-ai")
            )
        except (OSError, RuntimeError) as error:
            message = "The operating-system user data directory is unavailable."
            raise ContextStorageError(message) from error
    else:
        if not isinstance(override, str):
            message = "WORKAHOLIC_DATA_DIR must be an absolute path string."
            raise ContextInvalidError(message)
        if "\x00" in override:
            message = "WORKAHOLIC_DATA_DIR must not contain a null character."
            raise ContextInvalidError(message)
        try:
            data_directory = Path(override).expanduser()
        except (OSError, RuntimeError) as error:
            message = "WORKAHOLIC_DATA_DIR could not be expanded safely."
            raise ContextInvalidError(message) from error

    if not data_directory.is_absolute():
        message = "WORKAHOLIC_DATA_DIR must resolve to an absolute path."
        raise ContextInvalidError(message)
    return LocalDataPaths(
        data_directory=data_directory,
        database_path=data_directory / _DATABASE_FILENAME,
    )
