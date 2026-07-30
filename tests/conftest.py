"""Shared pytest fixtures for real-boundary acceptance specifications."""

from __future__ import annotations

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
    """Provide a fresh-process runner pinned to isolated local state.

    Args:
        tmp_path: Pytest-owned root unique to the current golden journey.

    Returns:
        Phase 1 subprocess runner over an isolated trusted data directory.

    """
    return SubprocessGoldenJourneyRunner(tmp_path / "golden-data")
