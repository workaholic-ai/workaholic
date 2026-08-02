"""Tests for the installed-wheel Phase 3 Human lifecycle smoke boundary."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_SMOKE_SCRIPT = _PROJECT_ROOT / "scripts" / "smoke-phase-3-wheel.sh"


def _run_smoke(
    tmp_path: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run the smoke script with an isolated temporary root.

    Args:
        tmp_path: Pytest-owned temporary directory.
        arguments: Arguments supplied after the script path.

    Returns:
        Completed smoke process.

    """
    temporary_directory = tmp_path / "temporary"
    temporary_directory.mkdir(parents=True)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(temporary_directory)
    return subprocess.run(
        [str(_SMOKE_SCRIPT), *arguments],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


def _wheel(tmp_path: Path, *, suffix: str = ".whl") -> Path:
    """Create one inert wheel-path fixture.

    Args:
        tmp_path: Pytest-owned temporary directory.
        suffix: Candidate artifact suffix.

    Returns:
        Created fixture path.

    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / f"workaholic_ai-0.3.0a1-py3-none-any{suffix}"
    wheel.touch()
    return wheel


@pytest.mark.parametrize("arguments", [[], ["one.whl", "two.whl"]])
def test_smoke_requires_exactly_one_wheel(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Missing or ambiguous wheel selection fails before temporary state."""
    result = _run_smoke(tmp_path, arguments)

    assert result.returncode == 64
    assert "usage: scripts/smoke-phase-3-wheel.sh <wheel-path>" in result.stderr
    assert tuple((tmp_path / "temporary").iterdir()) == ()


def test_smoke_rejects_missing_and_nonwheel_artifacts(tmp_path: Path) -> None:
    """Artifact validation happens before uv or temporary-state creation."""
    missing = _run_smoke(
        tmp_path / "missing",
        [str(tmp_path / "missing.whl")],
    )
    malformed_path = _wheel(tmp_path / "malformed", suffix=".zip")
    malformed = _run_smoke(
        tmp_path / "malformed-run",
        [str(malformed_path)],
    )

    assert missing.returncode == 66
    assert "wheel file does not exist" in missing.stderr
    assert malformed.returncode == 65
    assert "expected a .whl file" in malformed.stderr
    assert tuple((tmp_path / "missing" / "temporary").iterdir()) == ()
    assert tuple((tmp_path / "malformed-run" / "temporary").iterdir()) == ()


def test_smoke_declares_complete_isolated_lifecycle_and_failure_contracts() -> None:
    """The script owns state and proves every Phase 3 acceptance boundary."""
    source = _SMOKE_SCRIPT.read_text(encoding="utf-8")

    for required_contract in (
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_DATA_DIR",
        "unset PYTHONHOME PYTHONPATH VIRTUAL_ENV WORKAHOLIC_PROFILE",
        "phase-three-update",
        "phase-three-block",
        "phase-three-dependency",
        '"--view", "ready"',
        "phase-three-reviewed-submit",
        "phase-three-approve",
        '"events", "ACME-2"',
        '"attempt_id"] is not None',
        "VERSION_CONFLICT",
        "INVALID_TRANSITION",
        "DEPENDENCY_CYCLE",
        "UNSATISFIABLE_DEPENDENCY",
        "RESULT_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "SCHEMA_UNSUPPORTED",
        "schema_two_database.read_bytes() != schema_two_bytes",
        "process restart changed approved state",
    ):
        assert required_contract in source


def test_smoke_uses_only_the_installed_cli_for_product_operations() -> None:
    """The wheel gate never imports source or private Workaholic modules."""
    source = _SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "\nfrom workaholic" not in source
    assert "\nimport workaholic" not in source
    assert "phase_three_command=$phase_three_environment/bin/workaholic" in source
    assert "subprocess.run(" in source
    assert '"--json", "--non-interactive"' in source
    assert '"PYTHONNOUSERSITE": "1"' in source


def test_smoke_is_an_executable_posix_entry_point() -> None:
    """The wheel journey can run directly on supported acceptance hosts."""
    mode = _SMOKE_SCRIPT.stat().st_mode

    assert _SMOKE_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR
