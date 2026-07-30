"""Unit tests for strict trusted embedded-profile configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from workaholic.application import ApplicationErrorCode
from workaholic.context import (
    EmbeddedProfile,
    LocalConfigPaths,
    ProfileInvalidError,
    ProfileRegistry,
    ProfileUnsupportedError,
    load_profile_registry,
)

_VALID_HEADER = """\
version = 1
default_profile = "local"
"""


def _config_paths(tmp_path: Path) -> LocalConfigPaths:
    """Create a test-owned configuration directory and return its paths.

    Args:
        tmp_path: Isolated pytest directory.

    Returns:
        Valid trusted configuration paths.

    """
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    return LocalConfigPaths(
        config_directory=config_directory,
        profiles_file=config_directory / "profiles.toml",
    )


def _write_profiles(paths: LocalConfigPaths, content: str) -> None:
    """Write one UTF-8 test profile file before invoking the read boundary.

    Args:
        paths: Isolated configuration paths.
        content: TOML fixture contents.

    """
    paths.profiles_file.write_text(content, encoding="utf-8")


def _profile_table(name: str, data_directory: Path) -> str:
    """Render one valid embedded profile table.

    Args:
        name: Valid profile name.
        data_directory: Absolute test-owned data directory.

    Returns:
        TOML profile-table fixture.

    """
    return (
        f'[profiles.{name}]\nmode = "embedded"\ndata_directory = "{data_directory}"\n'
    )


def test_absent_configuration_preserves_built_in_local_without_writes(
    tmp_path: Path,
) -> None:
    """The built-in profile resolves the override but creates no storage."""
    paths = _config_paths(tmp_path)
    data_directory = tmp_path / "missing-data"
    before = set(tmp_path.rglob("*"))

    registry = load_profile_registry(
        paths,
        {"WORKAHOLIC_DATA_DIR": str(data_directory)},
    )

    assert registry.default_profile == "local"
    assert registry.profiles == {
        "local": EmbeddedProfile(
            name="local",
            data_directory=data_directory,
            database_path=data_directory / "local.db",
        )
    }
    assert set(tmp_path.rglob("*")) == before
    assert not data_directory.exists()


def test_absent_configuration_uses_platform_data_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The built-in profile delegates to the existing platform data rule."""
    paths = _config_paths(tmp_path)
    expected = tmp_path / "platform-data"
    monkeypatch.setattr(
        "workaholic.context.paths.platformdirs.user_data_path",
        lambda *_: expected,
    )

    registry = load_profile_registry(paths, {})

    assert registry.profiles["local"].data_directory == expected
    assert not expected.exists()


def test_valid_configuration_loads_explicit_default_and_multiple_profiles(
    tmp_path: Path,
) -> None:
    """A closed version-1 file produces an immutable multi-profile registry."""
    paths = _config_paths(tmp_path)
    local_directory = tmp_path / "local-data"
    team_directory = tmp_path / "team-parent" / ".." / "team-data"
    _write_profiles(
        paths,
        "version = 1\n"
        'default_profile = "team"\n'
        + _profile_table("local", local_directory)
        + _profile_table("team", team_directory),
    )

    registry = load_profile_registry(paths, {})

    expected_team = team_directory.resolve(strict=False)
    assert registry.default_profile == "team"
    assert tuple(registry.profiles) == ("local", "team")
    assert registry.profiles["team"] == EmbeddedProfile(
        name="team",
        data_directory=expected_team,
        database_path=expected_team / "local.db",
    )
    with pytest.raises(TypeError):
        registry.profiles["other"] = registry.profiles["local"]  # type: ignore[index]


def test_omitted_default_selects_configured_local(tmp_path: Path) -> None:
    """The optional default has the documented local fallback."""
    paths = _config_paths(tmp_path)
    data_directory = tmp_path / "data"
    _write_profiles(
        paths,
        "version = 1\n" + _profile_table("local", data_directory),
    )

    registry = load_profile_registry(paths, {})

    assert registry.default_profile == "local"


def test_configuration_directory_takes_precedence_over_data_override(
    tmp_path: Path,
) -> None:
    """A present registry ignores the built-in local data-directory override."""
    paths = _config_paths(tmp_path)
    configured_directory = tmp_path / "configured"
    override_directory = tmp_path / "override"
    _write_profiles(
        paths,
        _VALID_HEADER + _profile_table("local", configured_directory),
    )

    registry = load_profile_registry(
        paths,
        {"WORKAHOLIC_DATA_DIR": str(override_directory)},
    )

    assert registry.profiles["local"].data_directory == configured_directory
    assert not override_directory.exists()


def test_configured_data_directory_need_not_exist_and_is_not_created(
    tmp_path: Path,
) -> None:
    """Registry loading resolves a path but never opens profile storage."""
    paths = _config_paths(tmp_path)
    data_directory = tmp_path / "missing" / "data"
    _write_profiles(
        paths,
        _VALID_HEADER + _profile_table("local", data_directory),
    )
    before = set(tmp_path.rglob("*"))

    registry = load_profile_registry(paths, {})

    assert registry.profiles["local"].database_path == data_directory / "local.db"
    assert set(tmp_path.rglob("*")) == before
    assert not data_directory.exists()


def test_missing_configured_default_is_invalid(tmp_path: Path) -> None:
    """A default must identify a profile from the same trusted file."""
    paths = _config_paths(tmp_path)
    _write_profiles(
        paths,
        "version = 1\n"
        'default_profile = "missing"\n' + _profile_table("local", tmp_path / "data"),
    )

    with pytest.raises(ProfileInvalidError) as captured:
        load_profile_registry(paths, {})

    assert captured.value.code is ApplicationErrorCode.PROFILE_INVALID


def test_omitted_default_without_local_profile_is_invalid(tmp_path: Path) -> None:
    """The implicit local default must still exist in an authoritative file."""
    paths = _config_paths(tmp_path)
    _write_profiles(
        paths,
        "version = 1\n" + _profile_table("team", tmp_path / "data"),
    )

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_canonical_directory_aliases_are_invalid(tmp_path: Path) -> None:
    """Two profile names cannot select one canonical embedded Instance."""
    paths = _config_paths(tmp_path)
    data_directory = tmp_path / "data"
    alias_directory = tmp_path / "parent" / ".." / "data"
    _write_profiles(
        paths,
        _VALID_HEADER
        + _profile_table("local", data_directory)
        + _profile_table("alias", alias_directory),
    )

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_symlinked_directory_aliases_are_invalid(tmp_path: Path) -> None:
    """Canonicalization detects two names reaching one directory by symlink."""
    if os.name == "nt":
        pytest.skip("Symlink creation is not generally available on Windows.")
    paths = _config_paths(tmp_path)
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    alias_directory = tmp_path / "alias"
    alias_directory.symlink_to(data_directory, target_is_directory=True)
    _write_profiles(
        paths,
        _VALID_HEADER
        + _profile_table("local", data_directory)
        + _profile_table("alias", alias_directory),
    )

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


@pytest.mark.parametrize(
    "content",
    [
        "",
        'default_profile = "local"\n',
        "version = 1\n",
        "version = true\n[profiles]\n",
        'version = "1"\n[profiles]\n',
        "version = 1\nprofiles = 1\n",
        "version = 1\n[profiles]\n",
        'version = 1\ndefault_profile = "INVALID"\n[profiles]\n',
        'version = 1\nunknown = "value"\n[profiles]\n',
        'version = 1\n[profiles.INVALID]\nmode = "embedded"\n'
        'data_directory = "/tmp/data"\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\n',
        'version = 1\n[profiles.local]\ndata_directory = "/tmp/data"\n',
        'version = 1\n[profiles.local]\nmode = 1\ndata_directory = "/tmp/data"\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\ndata_directory = 1\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\n'
        'data_directory = "relative"\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\n'
        'data_directory = "/tmp/data"\nurl = "https://example.test"\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\n'
        'data_directory = "/tmp/data"\ncredential = "secret"\n',
        'version = 1\n[profiles.local]\nmode = "embedded"\n'
        'data_directory = "/tmp/data"\ntoken = "secret"\n',
    ],
)
def test_malformed_or_open_grammar_is_invalid(
    content: str,
    tmp_path: Path,
) -> None:
    """Malformed values and every unknown field fail closed."""
    paths = _config_paths(tmp_path)
    _write_profiles(paths, content)

    with pytest.raises(ProfileInvalidError) as captured:
        load_profile_registry(paths, {})

    assert captured.value.code is ApplicationErrorCode.PROFILE_INVALID


@pytest.mark.parametrize(
    "content",
    [
        "version = 2\n[profiles]\n",
        'version = 1\n[profiles.local]\nmode = "remote"\n'
        'data_directory = "/tmp/data"\n',
    ],
)
def test_unsupported_version_or_mode_is_explicit(
    content: str,
    tmp_path: Path,
) -> None:
    """Future versions and non-embedded modes receive their stable error."""
    paths = _config_paths(tmp_path)
    _write_profiles(paths, content)

    with pytest.raises(ProfileUnsupportedError) as captured:
        load_profile_registry(paths, {})

    assert captured.value.code is ApplicationErrorCode.PROFILE_UNSUPPORTED


def test_invalid_utf8_is_invalid(tmp_path: Path) -> None:
    """The trusted file must decode strictly as UTF-8."""
    paths = _config_paths(tmp_path)
    paths.profiles_file.write_bytes(b"\xff\xfe")

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_oversized_file_is_invalid(tmp_path: Path) -> None:
    """The profile file is rejected once it exceeds its 64 KiB bound."""
    paths = _config_paths(tmp_path)
    paths.profiles_file.write_bytes(b" " * (64 * 1_024 + 1))

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_symlinked_profile_file_is_invalid(tmp_path: Path) -> None:
    """A final-component symlink cannot redirect trusted configuration."""
    if os.name == "nt":
        pytest.skip("Symlink creation is not generally available on Windows.")
    paths = _config_paths(tmp_path)
    target = tmp_path / "target.toml"
    target.write_text(
        _VALID_HEADER + _profile_table("local", tmp_path / "data"),
        encoding="utf-8",
    )
    paths.profiles_file.symlink_to(target)

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_profile_file_directory_is_invalid(tmp_path: Path) -> None:
    """A directory at the trusted file path is not treated as absent."""
    paths = _config_paths(tmp_path)
    paths.profiles_file.mkdir()

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(paths, {})


def test_unreadable_profile_failure_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read failures map to one safe profile error without leaking details."""
    paths = _config_paths(tmp_path)
    _write_profiles(
        paths,
        _VALID_HEADER + _profile_table("local", tmp_path / "data"),
    )

    def fail_read(_path: Path, *, maximum: int) -> bytes:
        """Simulate a private operating-system read failure."""
        assert maximum == 64 * 1_024
        message = "private read failure"
        raise PermissionError(message)

    monkeypatch.setattr(
        "workaholic.context.profiles.read_bounded_regular_file",
        fail_read,
    )

    with pytest.raises(ProfileInvalidError) as captured:
        load_profile_registry(paths, {})

    assert "private read failure" not in captured.value.safe_message


def test_built_in_invalid_data_override_maps_to_profile_error(
    tmp_path: Path,
) -> None:
    """A malformed built-in override never leaks a context-specific error."""
    paths = _config_paths(tmp_path)

    with pytest.raises(ProfileInvalidError) as captured:
        load_profile_registry(paths, {"WORKAHOLIC_DATA_DIR": "relative"})

    assert captured.value.code is ApplicationErrorCode.PROFILE_INVALID


def test_loader_validates_runtime_argument_types(tmp_path: Path) -> None:
    """Public type hints are reinforced by runtime validation."""
    paths = _config_paths(tmp_path)

    with pytest.raises(ProfileInvalidError):
        load_profile_registry(
            cast("LocalConfigPaths", object()),
            {},
        )
    with pytest.raises(ProfileInvalidError):
        load_profile_registry(
            paths,
            cast("dict[str, str]", object()),
        )


@pytest.mark.parametrize(
    "profile",
    [
        ("INVALID", Path("/data"), Path("/data/local.db")),
        ("local", Path("relative"), Path("relative/local.db")),
        ("local", Path("/data"), Path("/other/local.db")),
        ("local", cast("Path", "/data"), Path("/data/local.db")),
    ],
)
def test_embedded_profile_validates_its_complete_contract(
    profile: tuple[str, Path, Path],
) -> None:
    """Direct model construction cannot bypass profile invariants."""
    with pytest.raises(ProfileInvalidError):
        EmbeddedProfile(*profile)


def test_registry_defensively_copies_and_validates_profiles(tmp_path: Path) -> None:
    """Registry input cannot mutate output or introduce mismatched aliases."""
    data_directory = tmp_path / "data"
    profile = EmbeddedProfile(
        name="local",
        data_directory=data_directory,
        database_path=data_directory / "local.db",
    )
    source = {"local": profile}
    registry = ProfileRegistry(default_profile="local", profiles=source)

    source.clear()

    assert registry.profiles == {"local": profile}
    with pytest.raises(ProfileInvalidError):
        ProfileRegistry(default_profile="missing", profiles={"local": profile})
    with pytest.raises(ProfileInvalidError):
        ProfileRegistry(default_profile="local", profiles={"other": profile})
    with pytest.raises(ProfileInvalidError):
        ProfileRegistry(
            default_profile="local",
            profiles=cast("dict[str, EmbeddedProfile]", object()),
        )
