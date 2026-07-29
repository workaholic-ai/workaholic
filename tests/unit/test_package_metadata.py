"""Tests for installed Workaholic AI distribution metadata."""

import importlib
import importlib.util
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from workaholic.cli.main import main

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
    assert project["version"] == "0.0.0"
    assert project["requires-python"] == ">=3.14"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [
        {"name": "Pavels Gurskis", "email": "pg@ithesion.com"}
    ]
    assert project["dependencies"] == ["typer>=0.27.0,<0.28.0"]
    assert project["scripts"] == {"workaholic": "workaholic.cli.main:main"}


def test_installed_metadata_matches_source_metadata() -> None:
    """Installed core metadata preserves the source project decisions."""
    project = _project_metadata()
    distribution = metadata.distribution(_DISTRIBUTION_NAME)
    installed = distribution.metadata

    assert installed["Name"] == project["name"]
    assert installed["Version"] == project["version"]
    assert installed["Requires-Python"] == project["requires-python"]
    assert installed["License-Expression"] == project["license"]
    assert installed.get_all("License-File") == ["LICENSE"]
    assert installed["Author-email"] == ("Pavels Gurskis <pg@ithesion.com>")


def test_console_entry_point_loads_public_main() -> None:
    """The installed console entry point resolves to the public main function."""
    entry_points = [
        entry_point
        for entry_point in metadata.distribution(_DISTRIBUTION_NAME).entry_points
        if entry_point.group == "console_scripts" and entry_point.name == "workaholic"
    ]

    assert len(entry_points) == 1
    assert entry_points[0].value == "workaholic.cli.main:main"
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
