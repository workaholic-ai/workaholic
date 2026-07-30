"""Contract tests for the production package dependency graph."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

_PROJECT_ROOT = Path(__file__).parents[2]
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"
_VIOLATION_FIXTURE = (
    _PROJECT_ROOT / "tests" / "contract" / "fixtures" / "import_boundaries"
)
_EXPECTED_CONTRACTS = frozenset({"package-layers", "cli-session-boundary"})


def _linter_executable() -> str:
    """Resolve the Import Linter console script from the active environment.

    Returns:
        Absolute or PATH-resolved `lint-imports` executable.

    Raises:
        AssertionError: If the locked development tool is not installed.

    """
    executable = shutil.which("lint-imports")
    if executable is None:
        script_name = "lint-imports.exe" if os.name == "nt" else "lint-imports"
        adjacent_script = Path(sys.executable).with_name(script_name)
        if adjacent_script.is_file():
            executable = str(adjacent_script)
    if executable is None:
        message = "The locked import-linter development dependency is unavailable."
        raise AssertionError(message)
    return executable


def _run_linter(
    *,
    config: Path,
    cwd: Path,
    contract_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Import Linter without a persistent cache.

    Args:
        config: Import Linter TOML configuration.
        cwd: Directory containing the package under analysis.
        contract_id: Optional single contract to execute.

    Returns:
        Completed linter process with captured text output.

    """
    arguments = [
        _linter_executable(),
        "--config",
        str(config),
        "--no-cache",
    ]
    if contract_id is not None:
        arguments.extend(("--contract", contract_id))

    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    environment["TERM"] = "dumb"
    return subprocess.run(
        arguments,
        check=False,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
    )


def _import_linter_configuration() -> dict[str, Any]:
    """Load the production Import Linter configuration.

    Returns:
        Parsed `tool.importlinter` table.

    """
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    configuration = pyproject["tool"]["importlinter"]
    assert isinstance(configuration, dict)
    return configuration


def test_production_import_contracts_are_kept() -> None:
    """The current production import graph satisfies every configured contract."""
    result = _run_linter(config=_PYPROJECT, cwd=_PROJECT_ROOT)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Contracts: 2 kept, 0 broken." in output


def test_import_contracts_are_exhaustive_and_have_no_ignored_edges() -> None:
    """Every production package is classified without a package-wide escape."""
    configuration = _import_linter_configuration()
    contracts = configuration["contracts"]

    assert configuration["root_package"] == "workaholic"
    assert configuration["exclude_type_checking_imports"] is False
    assert isinstance(contracts, list)
    assert {contract["id"] for contract in contracts} == _EXPECTED_CONTRACTS
    assert all("ignore_imports" not in contract for contract in contracts)

    layer_contract = next(
        contract for contract in contracts if contract["id"] == "package-layers"
    )
    assert layer_contract["exhaustive"] is True
    assert layer_contract["exhaustive_ignores"] == ["__main__"]
    assert layer_contract["layers"] == [
        "composition",
        "cli : tui : server : session : persistence : protocol : client : context",
        "application : auth",
        "domain",
    ]


@pytest.mark.parametrize(
    ("contract_id", "prohibited_importer", "imported_module"),
    [
        pytest.param(
            "package-layers",
            "boundary_sample.domain",
            "boundary_sample.application",
            id="domain-to-application",
        ),
        pytest.param(
            "package-layers",
            "boundary_sample.application",
            "boundary_sample.persistence",
            id="application-to-persistence",
        ),
        pytest.param(
            "cli-session-boundary",
            "boundary_sample.cli",
            "boundary_sample.persistence",
            id="cli-to-persistence",
        ),
    ],
)
def test_boundary_contract_reports_both_sides_of_isolated_violation(
    contract_id: str,
    prohibited_importer: str,
    imported_module: str,
) -> None:
    """Each architectural rule fails with an actionable illegal import path."""
    result = _run_linter(
        config=_VIOLATION_FIXTURE / "pyproject.toml",
        cwd=_VIOLATION_FIXTURE,
        contract_id=contract_id,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert prohibited_importer in output
    assert imported_module in output
    assert "1 broken." in output
