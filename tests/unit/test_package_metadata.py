"""Tests for installed Workaholic AI distribution metadata."""

import importlib
import importlib.util
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from workaholic import __version__
from workaholic.composition import main

_DISTRIBUTION_NAME = "workaholic-ai"
_PROJECT_ROOT = Path(__file__).parents[2]


def _project_metadata() -> dict[str, Any]:
    """Load the source project's PEP 621 metadata.

    Returns:
        The ``project`` table from ``pyproject.toml``.

    """
    pyproject = tomllib.loads(
        (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject.get("project")
    assert isinstance(project, dict), "pyproject.toml must define a project table."
    return project


def test_source_metadata_matches_foundation_decisions() -> None:
    """Source metadata matches the accepted package foundation decisions."""
    project = _project_metadata()

    assert project["name"] == _DISTRIBUTION_NAME
    assert project["version"] == "0.4.0a1"
    assert project["version"] == __version__
    assert project["readme"] == "README.md"
    assert project["requires-python"] == ">=3.14"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [
        {"name": "Pavels Gurskis", "email": "pg@ithesion.com"}
    ]
    assert project["dependencies"] == [
        "keyring>=25.7.0,<26.0.0",
        "platformdirs>=4.11.0,<4.12.0",
        "pydantic>=2.13.4,<2.14.0",
        "typer>=0.27.0,<0.28.0",
    ]
    assert project["scripts"] == {"workaholic": "workaholic.composition:main"}


def test_installed_metadata_matches_source_metadata() -> None:
    """Installed core metadata preserves the source project decisions."""
    project = _project_metadata()
    distribution = metadata.distribution(_DISTRIBUTION_NAME)
    installed = distribution.metadata
    packaged_metadata = distribution.read_text("METADATA")

    assert installed["Name"] == project["name"]
    assert installed["Version"] == project["version"]
    assert installed["Requires-Python"] == project["requires-python"]
    assert installed["License-Expression"] == project["license"]
    assert installed.get_all("License-File") == ["LICENSE"]
    assert installed["Author-email"] == ("Pavels Gurskis <pg@ithesion.com>")
    assert installed["Description-Content-Type"] == "text/markdown"
    assert installed.get_all("Requires-Dist") == [
        "keyring<26.0.0,>=25.7.0",
        "platformdirs<4.12.0,>=4.11.0",
        "pydantic<2.14.0,>=2.13.4",
        "typer<0.28.0,>=0.27.0",
    ]
    assert packaged_metadata is not None
    assert "# Workaholic AI" in packaged_metadata


def test_phase_five_trusted_process_inputs_are_safely_documented() -> None:
    """The env template distinguishes profile selectors and explicit secrets."""
    environment_example = (_PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "Do not copy this file to .workaholic.env" in environment_example
    assert "workaholic up creates" in environment_example
    assert "WORKAHOLIC_CONFIG_DIR=" in environment_example
    assert "WORKAHOLIC_PROFILE=" in environment_example
    for key in (
        "WORKAHOLIC_TOKEN=",
        "WORKAHOLIC_TOKEN_FILE=",
        "WORKAHOLIC_CREDENTIAL_BACKEND=",
    ):
        assert key in environment_example
    assert environment_example.endswith("WORKAHOLIC_CREDENTIAL_BACKEND=\n")
    assert "never copy it into .workaholic.env" in environment_example
    for unsupported in (
        "Remote URLs",
        "credentials",
        "Tokens",
        "secret references",
        "executable paths",
    ):
        assert unsupported in environment_example


def test_console_entry_point_loads_public_main() -> None:
    """The installed console entry point resolves to the public main function."""
    entry_points = [
        entry_point
        for entry_point in metadata.distribution(_DISTRIBUTION_NAME).entry_points
        if entry_point.group == "console_scripts" and entry_point.name == "workaholic"
    ]

    assert len(entry_points) == 1
    assert entry_points[0].value == "workaholic.composition:main"
    assert entry_points[0].load() is main


@pytest.mark.parametrize(
    "module_name",
    [
        "workaholic",
        "workaholic.application",
        "workaholic.auth",
        "workaholic.cli",
        "workaholic.client",
        "workaholic.context",
        "workaholic.domain",
        "workaholic.persistence",
        "workaholic.protocol",
        "workaholic.server",
        "workaholic.session",
        "workaholic.tui",
    ],
)
def test_declared_package_boundaries_are_importable(module_name: str) -> None:
    """Each declared package boundary is installed and importable.

    Args:
        module_name: Fully qualified package boundary to import.

    """
    assert importlib.import_module(module_name) is not None


def test_underscore_distribution_name_is_not_an_import_alias() -> None:
    """The distribution name does not create a second import package."""
    assert importlib.util.find_spec("workaholic_ai") is None
