"""Validated local filesystem locations owned by the context boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from workaholic.context.errors import ContextInvalidError, ProfileInvalidError
from workaholic.domain import WorkspaceBinding, validate_profile_name

_DATABASE_FILENAME = "local.db"


@dataclass(frozen=True, slots=True)
class LocalConfigPaths:
    """Absolute trusted configuration directory and profile-file path."""

    config_directory: Path
    profiles_file: Path

    def __post_init__(self) -> None:
        """Validate absolute and internally consistent configuration paths."""
        config_directory: object = self.config_directory
        profiles_file: object = self.profiles_file
        if not isinstance(config_directory, Path) or not isinstance(
            profiles_file, Path
        ):
            message = "Local configuration paths must be pathlib Paths."
            raise ContextInvalidError(message)
        if not config_directory.is_absolute() or not profiles_file.is_absolute():
            message = "Local configuration paths must be absolute."
            raise ContextInvalidError(message)
        if profiles_file != config_directory / "profiles.toml":
            message = "The trusted profile file must be named profiles.toml."
            raise ContextInvalidError(message)


@dataclass(frozen=True, slots=True)
class LocalDataPaths:
    """Absolute user data and default SQLite paths for local operation."""

    data_directory: Path
    database_path: Path

    def __post_init__(self) -> None:
        """Validate absolute and internally consistent local paths."""
        data_directory: object = self.data_directory
        database_path: object = self.database_path
        if not isinstance(data_directory, Path) or not isinstance(database_path, Path):
            message = "Local data paths must be pathlib Paths."
            raise ContextInvalidError(message)
        if not data_directory.is_absolute() or not database_path.is_absolute():
            message = "Local data paths must be absolute."
            raise ContextInvalidError(message)
        if database_path != data_directory / _DATABASE_FILENAME:
            message = "The local database must be named local.db in the data directory."
            raise ContextInvalidError(message)


@dataclass(frozen=True, slots=True)
class EmbeddedProfile:
    """One trusted profile selecting an exact embedded SQLite database."""

    name: str
    data_directory: Path
    database_path: Path

    def __post_init__(self) -> None:
        """Validate the profile name and absolute storage relationship."""
        try:
            name = validate_profile_name(self.name)
        except ValueError as error:
            raise ProfileInvalidError from error
        data_directory: object = self.data_directory
        database_path: object = self.database_path
        if not isinstance(data_directory, Path) or not isinstance(database_path, Path):
            raise ProfileInvalidError
        if not data_directory.is_absolute() or not database_path.is_absolute():
            raise ProfileInvalidError
        if database_path != data_directory / _DATABASE_FILENAME:
            raise ProfileInvalidError
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    """Immutable trusted profile definitions and configured default."""

    default_profile: str
    profiles: Mapping[str, EmbeddedProfile]

    def __post_init__(self) -> None:
        """Validate keys, default ownership, aliases, and defensive immutability."""
        try:
            default_profile = validate_profile_name(self.default_profile)
        except ValueError as error:
            raise ProfileInvalidError from error
        candidate_profiles: object = self.profiles
        if not isinstance(candidate_profiles, Mapping) or not candidate_profiles:
            raise ProfileInvalidError

        copied: dict[str, EmbeddedProfile] = {}
        data_directories: set[Path] = set()
        for name, profile in candidate_profiles.items():
            try:
                validated_name = validate_profile_name(name)
            except ValueError as error:
                raise ProfileInvalidError from error
            if not isinstance(profile, EmbeddedProfile) or (
                profile.name != validated_name
            ):
                raise ProfileInvalidError
            if profile.data_directory in data_directories:
                raise ProfileInvalidError
            data_directories.add(profile.data_directory)
            copied[validated_name] = profile
        if default_profile not in copied:
            raise ProfileInvalidError

        object.__setattr__(self, "default_profile", default_profile)
        object.__setattr__(self, "profiles", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class DiscoveredWorkspace:
    """Validated binding, source file, and contained physical Workspace root."""

    binding: WorkspaceBinding
    context_file: Path
    workspace_root: Path

    def __post_init__(self) -> None:
        """Validate the complete discovery-result relationship."""
        binding: object = self.binding
        context_file: object = self.context_file
        workspace_root: object = self.workspace_root
        if not isinstance(binding, WorkspaceBinding):
            raise ContextInvalidError
        if not isinstance(context_file, Path) or not isinstance(
            workspace_root,
            Path,
        ):
            raise ContextInvalidError
        if not context_file.is_absolute() or not workspace_root.is_absolute():
            raise ContextInvalidError
        if context_file.name != ".workaholic.env":
            raise ContextInvalidError
        context_directory = context_file.parent
        if workspace_root != context_directory and (
            context_directory not in workspace_root.parents
        ):
            raise ContextInvalidError
