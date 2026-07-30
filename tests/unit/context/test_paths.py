"""Unit tests for deterministic local application data paths."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from workaholic.application import ApplicationErrorCode
from workaholic.context import (
    ContextInvalidError,
    ContextStorageError,
    LocalDataPaths,
    resolve_local_data_paths,
)


def test_default_data_path_uses_platformdirs_without_creating_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The absent override delegates to the documented platform directory."""
    expected = tmp_path / "platform-data"

    def fake_user_data_path(appname: str, appauthor: str) -> Path:
        """Return an isolated default while asserting the public identifiers."""
        assert appname == "workaholic"
        assert appauthor == "workaholic-ai"
        return expected

    monkeypatch.setattr(
        "workaholic.context.paths.platformdirs.user_data_path",
        fake_user_data_path,
    )

    paths = resolve_local_data_paths({})

    assert paths == LocalDataPaths(expected, expected / "local.db")
    assert not expected.exists()


@pytest.mark.parametrize("environment", [{}, {"WORKAHOLIC_DATA_DIR": ""}])
def test_missing_or_empty_override_uses_platform_default(
    environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing and empty overrides have the same deterministic semantics."""
    expected = tmp_path / "default"
    monkeypatch.setattr(
        "workaholic.context.paths.platformdirs.user_data_path",
        lambda *_: expected,
    )

    assert resolve_local_data_paths(environment).data_directory == expected


def test_absolute_override_is_used_without_creating_directories(tmp_path: Path) -> None:
    """An absolute trusted override determines both returned paths only."""
    expected = tmp_path / "isolated" / "data"

    paths = resolve_local_data_paths({"WORKAHOLIC_DATA_DIR": str(expected)})

    assert paths.data_directory == expected
    assert paths.database_path == expected / "local.db"
    assert not expected.exists()


def test_override_expands_the_current_users_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A leading user marker is expanded before the absolute-path check."""
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = resolve_local_data_paths({"WORKAHOLIC_DATA_DIR": "~/workaholic"})

    assert paths.data_directory == tmp_path / "workaholic"


@pytest.mark.parametrize(
    "environment",
    [
        {"WORKAHOLIC_DATA_DIR": "relative/data"},
        {"WORKAHOLIC_DATA_DIR": "\x00invalid"},
        cast("dict[str, str]", {"WORKAHOLIC_DATA_DIR": 42}),
    ],
)
def test_invalid_overrides_are_rejected(
    environment: dict[str, str],
) -> None:
    """Relative, null-containing, and non-string overrides are invalid."""
    with pytest.raises(ContextInvalidError) as captured:
        resolve_local_data_paths(environment)

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID


def test_non_mapping_environment_is_rejected() -> None:
    """Runtime validation does not rely on the Mapping type hint."""
    with pytest.raises(ContextInvalidError):
        resolve_local_data_paths(cast("dict[str, str]", object()))


def test_platform_directory_failure_is_a_safe_storage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platform lookup failures do not leak an implementation exception."""

    def fail_lookup(*_arguments: object) -> Path:
        """Simulate an unavailable operating-system profile."""
        message = "private platform failure"
        raise OSError(message)

    monkeypatch.setattr(
        "workaholic.context.paths.platformdirs.user_data_path",
        fail_lookup,
    )

    with pytest.raises(ContextStorageError) as captured:
        resolve_local_data_paths({})

    assert captured.value.code is ApplicationErrorCode.STORAGE_UNAVAILABLE
    assert "private platform failure" not in captured.value.safe_message


def test_user_expansion_failure_is_a_safe_invalid_context_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed user expansion is classified as invalid trusted input."""

    def fail_expansion(_path: Path) -> Path:
        """Simulate an operating-system home lookup failure."""
        message = "private home failure"
        raise RuntimeError(message)

    monkeypatch.setattr(Path, "expanduser", fail_expansion)

    with pytest.raises(ContextInvalidError) as captured:
        resolve_local_data_paths({"WORKAHOLIC_DATA_DIR": "~/data"})

    assert captured.value.code is ApplicationErrorCode.CONTEXT_INVALID
    assert "private home failure" not in captured.value.safe_message


@pytest.mark.parametrize(
    ("data_directory", "database_path"),
    [
        (Path("relative"), Path("relative/local.db")),
        (Path("/absolute"), Path("relative/local.db")),
        (Path("/absolute"), Path("/somewhere-else/local.db")),
        (cast("Path", "/absolute"), Path("/absolute/local.db")),
    ],
)
def test_local_data_paths_validate_types_and_relationship(
    data_directory: Path,
    database_path: Path,
) -> None:
    """The value object accepts only absolute, internally consistent Paths."""
    with pytest.raises(ContextInvalidError):
        LocalDataPaths(data_directory, database_path)
