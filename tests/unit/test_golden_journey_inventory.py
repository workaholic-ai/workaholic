"""Validate the canonical golden-journey inventory and pytest taxonomy."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parents[2]
_GOLDEN_DIRECTORY = _PROJECT_ROOT / "tests" / "e2e" / "golden"
_GOLDEN_README = _GOLDEN_DIRECTORY / "README.md"
_PHASE_REASON_PATTERN = re.compile(r"^Phase (?P<phase>[1-9][0-9]*): missing .+\.$")
_REQUIRED_MARKERS = frozenset(
    {
        "contract",
        "e2e",
        "golden",
        "integration",
        "requires_network",
        "requires_postgres",
        "requires_uv",
    }
)


@dataclass(frozen=True, slots=True)
class JourneyExpectation:
    """Static contract for one canonical golden specification."""

    test_name: str
    enabling_phase: int
    skip_reason: str
    resource_markers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MarkerUse:
    """One marker declared in a module-level `pytestmark` list."""

    name: str
    reason: str | None


_EXPECTED_JOURNEYS = {
    "test_agent_journey.py": JourneyExpectation(
        test_name=(
            "test_agent_completes_current_attempt_but_cannot_renew_an_expired_attempt"
        ),
        enabling_phase=4,
        skip_reason=(
            "Phase 4: missing agent claims, leases, heartbeats, and result submission."
        ),
    ),
    "test_backend_conformance_journey.py": JourneyExpectation(
        test_name="test_supported_backends_expose_the_same_task_behavior",
        enabling_phase=7,
        skip_reason=(
            "Phase 7: missing JSON, SQLite, and PostgreSQL adapter conformance."
        ),
        resource_markers=frozenset({"requires_network", "requires_postgres"}),
    ),
    "test_clean_install_journey.py": JourneyExpectation(
        test_name="test_published_package_runs_through_uvx_in_a_clean_environment",
        enabling_phase=9,
        skip_reason=(
            "Phase 9: missing release-candidate publication and clean uvx acceptance."
        ),
        resource_markers=frozenset({"requires_network", "requires_uv"}),
    ),
    "test_multi_project_journey.py": JourneyExpectation(
        test_name="test_each_working_directory_selects_its_bound_project",
        enabling_phase=2,
        skip_reason=(
            "Phase 2: missing project binding, context discovery, and stable "
            "project task keys."
        ),
    ),
    "test_solo_journey.py": JourneyExpectation(
        test_name="test_solo_tasks_remain_visible_after_reopening_the_project",
        enabling_phase=1,
        skip_reason=(
            "Phase 1: missing LocalSession, SQLite persistence, and task commands."
        ),
    ),
    "test_team_journey.py": JourneyExpectation(
        test_name="test_two_remote_users_and_one_agent_share_one_server",
        enabling_phase=6,
        skip_reason=(
            "Phase 6: missing authenticated server, RemoteSession, and shared-team "
            "workflow."
        ),
        resource_markers=frozenset({"requires_network"}),
    ),
}


def _parse(path: Path) -> ast.Module:
    """Parse one Python specification.

    Args:
        path: Python source path.

    Returns:
        Parsed abstract syntax tree.

    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _marker_name(expression: ast.expr) -> str | None:
    """Extract a marker name from a `pytest.mark` attribute.

    Args:
        expression: Candidate marker expression.

    Returns:
        Marker name, or `None` when the expression is not a pytest marker.

    """
    if not isinstance(expression, ast.Attribute):
        return None
    mark_attribute = expression.value
    if (
        not isinstance(mark_attribute, ast.Attribute)
        or mark_attribute.attr != "mark"
        or not isinstance(mark_attribute.value, ast.Name)
        or mark_attribute.value.id != "pytest"
    ):
        return None
    return expression.attr


def _marker_use(expression: ast.expr) -> MarkerUse:
    """Parse one element of a module-level `pytestmark` sequence.

    Args:
        expression: Marker attribute or marker call.

    Returns:
        Parsed marker declaration.

    Raises:
        AssertionError: If the expression is not a supported pytest marker.

    """
    marker_expression = (
        expression.func if isinstance(expression, ast.Call) else expression
    )
    marker_name = _marker_name(marker_expression)
    if marker_name is None:
        message = "Golden pytestmark entries must be direct pytest markers."
        raise AssertionError(message)

    reason: str | None = None
    if isinstance(expression, ast.Call):
        reason_values = [
            keyword.value for keyword in expression.keywords if keyword.arg == "reason"
        ]
        if reason_values:
            reason_value = reason_values[0]
            if not isinstance(reason_value, ast.Constant) or not isinstance(
                reason_value.value, str
            ):
                message = "Golden skip reasons must be literal strings."
                raise AssertionError(message)
            reason = reason_value.value
    return MarkerUse(name=marker_name, reason=reason)


def _module_markers(module: ast.Module) -> tuple[MarkerUse, ...]:
    """Read the single module-level `pytestmark` sequence.

    Args:
        module: Parsed Python module.

    Returns:
        Marker declarations in source order.

    Raises:
        AssertionError: If the module does not have one explicit marker list.

    """
    assignments = [
        statement
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        )
    ]
    if len(assignments) != 1:
        message = "Each golden specification must define one module-level pytestmark."
        raise AssertionError(message)

    value = assignments[0].value
    if not isinstance(value, ast.List | ast.Tuple):
        message = "Golden pytestmark must be an explicit list or tuple."
        raise AssertionError(message)  # noqa: TRY004 - source contract assertion
    return tuple(_marker_use(element) for element in value.elts)


def _all_marker_names(module: ast.Module) -> frozenset[str]:
    """Return every pytest marker name referenced anywhere in a module.

    Args:
        module: Parsed Python module.

    Returns:
        All syntactically referenced pytest marker names.

    """
    return frozenset(
        marker_name
        for node in ast.walk(module)
        if isinstance(node, ast.Attribute)
        if (marker_name := _marker_name(node)) is not None
    )


def _pytest_configuration() -> dict[str, Any]:
    """Load the pytest table from project configuration.

    Returns:
        Parsed `tool.pytest.ini_options` table.

    """
    configuration = tomllib.loads(
        (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    pytest_configuration = configuration["tool"]["pytest"]["ini_options"]
    assert isinstance(pytest_configuration, dict)
    return pytest_configuration


def test_exactly_one_canonical_specification_exists_for_each_journey() -> None:
    """The golden directory contains exactly the six roadmap specifications."""
    actual_paths = {
        path.name: path
        for path in _GOLDEN_DIRECTORY.glob("test_*_journey.py")
        if path.is_file()
    }

    assert actual_paths.keys() == _EXPECTED_JOURNEYS.keys()

    for filename, expectation in _EXPECTED_JOURNEYS.items():
        module = _parse(actual_paths[filename])
        test_names = [
            statement.name
            for statement in module.body
            if isinstance(statement, ast.FunctionDef)
            and statement.name.startswith("test_")
        ]

        assert test_names == [expectation.test_name], filename


def test_golden_specs_have_phase_specific_skips_and_no_xfail() -> None:
    """Every blocked journey declares its phase and can never silently xpass."""
    for filename, expectation in _EXPECTED_JOURNEYS.items():
        module = _parse(_GOLDEN_DIRECTORY / filename)
        marker_uses = _module_markers(module)
        markers = {marker.name for marker in marker_uses}
        expected_markers = {
            "e2e",
            "golden",
            "skip",
            *expectation.resource_markers,
        }
        skip_markers = [marker for marker in marker_uses if marker.name == "skip"]

        assert markers == expected_markers, filename
        assert len(marker_uses) == len(markers), filename
        assert len(skip_markers) == 1, filename
        assert skip_markers[0].reason == expectation.skip_reason, filename
        assert "xfail" not in _all_marker_names(module), filename

        reason_match = _PHASE_REASON_PATTERN.fullmatch(expectation.skip_reason)
        assert reason_match is not None, filename
        assert int(reason_match.group("phase")) == expectation.enabling_phase, filename


def test_golden_readme_maps_every_journey_to_its_unskip_condition() -> None:
    """The operator guide maps every canonical file and enabling phase."""
    readme = _GOLDEN_README.read_text(encoding="utf-8")

    for filename, expectation in _EXPECTED_JOURNEYS.items():
        assert f"]({filename})" in readme
        assert f"Phase {expectation.enabling_phase}" in readme
    assert "never use `xfail`" in readme
    assert "real CLI" in readme


def test_pytest_marker_contract_is_strict_and_complete() -> None:
    """Unknown markers fail collection and every public marker is registered."""
    configuration = _pytest_configuration()
    addopts = configuration["addopts"]
    marker_declarations = configuration["markers"]

    assert isinstance(addopts, list)
    assert "--strict-markers" in addopts
    assert isinstance(marker_declarations, list)

    marker_names = {
        declaration.partition(":")[0].strip()
        for declaration in marker_declarations
        if isinstance(declaration, str)
    }
    assert marker_names == _REQUIRED_MARKERS


def test_unknown_marker_fails_collection(tmp_path: Path) -> None:
    """A misspelled marker is rejected by a real isolated pytest collection."""
    invalid_specification = tmp_path / "test_invalid_marker.py"
    invalid_specification.write_text(
        """import pytest


@pytest.mark.goldden
def test_marker_typo() -> None:
    pass
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--no-cov",
            "--collect-only",
            "-c",
            str(_PROJECT_ROOT / "pyproject.toml"),
            str(invalid_specification),
        ],
        check=False,
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    collection_output = result.stdout + result.stderr
    assert "'goldden' not found in `markers` configuration option" in collection_output
