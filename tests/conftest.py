"""Shared pytest fixtures for real-boundary acceptance specifications."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from tests.golden import SubprocessGoldenJourneyRunner

if TYPE_CHECKING:
    from pathlib import Path

    from tests.golden import GoldenJourneyRunner


class _CoverageOptions(Protocol):
    """pytest-cov option surface needed for a targeted journey run."""

    cov_fail_under: float | None


class _CoveragePlugin(Protocol):
    """pytest-cov plugin surface needed for a targeted journey run."""

    options: _CoverageOptions


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep targeted golden execution independent of whole-suite coverage.

    The permanent 95 percent gate still applies to an unfiltered test run in
    CI and pre-push. A golden-only run is an executable journey selection, so
    its success must depend on journey outcomes rather than unrelated modules.

    Args:
        config: Active pytest configuration.

    """
    marker_expression: object = config.getoption("markexpr")
    if marker_expression == "golden":
        config.option.cov_fail_under = 0
        coverage_plugin = config.pluginmanager.get_plugin("_cov")
        if coverage_plugin is not None:
            cast("_CoveragePlugin", coverage_plugin).options.cov_fail_under = 0


@pytest.fixture
def golden_runner(tmp_path: Path) -> GoldenJourneyRunner:
    """Provide a fresh-process runner pinned to two isolated embedded profiles.

    Args:
        tmp_path: Pytest-owned root unique to the current golden journey.

    Returns:
        Phase 2 subprocess runner over isolated trusted configuration and data.

    """
    data_directory = tmp_path / "golden-data"
    config_directory = tmp_path / "golden-config"
    _write_golden_profiles(
        config_directory,
        profiles={
            "local": data_directory,
            "isolated": tmp_path / "isolated-profile-data",
        },
    )
    return SubprocessGoldenJourneyRunner(
        data_directory=data_directory,
        config_directory=config_directory,
    )


def _write_golden_profiles(
    config_directory: Path,
    *,
    profiles: dict[str, Path],
) -> None:
    """Write the exact trusted embedded-profile registry used by golden tests.

    Args:
        config_directory: Pytest-owned configuration directory.
        profiles: Profile names mapped to distinct pytest-owned data directories.

    Raises:
        ValueError: If the required default profile is absent or paths overlap.

    """
    if "local" not in profiles or len(set(profiles.values())) != len(profiles):
        message = "Golden profiles require one distinct default local data path."
        raise ValueError(message)

    config_directory.mkdir(parents=True)
    lines = [
        "version = 1",
        'default_profile = "local"',
    ]
    for name, data_directory in profiles.items():
        lines.extend(
            (
                "",
                f"[profiles.{name}]",
                'mode = "embedded"',
                f"data_directory = {json.dumps(str(data_directory))}",
            )
        )
    (config_directory / "profiles.toml").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
