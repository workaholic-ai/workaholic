"""Tests for the fail-fast Phase 0 acceptance orchestrator."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_VERIFY_SCRIPT = _PROJECT_ROOT / "scripts" / "verify-phase-0.sh"


@dataclass(frozen=True)
class _GateScenario:
    """Configure one deterministic acceptance-gate test scenario."""

    arguments: tuple[str, ...] = ()
    active_environment: bool = False
    git_status: str = ""
    uv_fail_on: str | None = None
    smoke_fails: bool = False
    generated_path: str | None = None


_DEFAULT_SCENARIO = _GateScenario()


def _write_executable(path: Path, source: str) -> None:
    """Write an executable boundary fixture.

    Args:
        path: Destination path.
        source: Complete POSIX shell source.

    """
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _phase_zero_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create an isolated project with deterministic Git and uv boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Fake project root and fake executable directory.

    """
    project_root = tmp_path / "project"
    scripts_directory = project_root / "scripts"
    binary_directory = tmp_path / "bin"
    scripts_directory.mkdir(parents=True)
    binary_directory.mkdir()
    shutil.copy2(_VERIFY_SCRIPT, scripts_directory / _VERIFY_SCRIPT.name)
    _write_executable(
        binary_directory / "git",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_GIT_LOG"
case "$*" in
  "rev-parse --show-toplevel")
    printf '%s\n' "$WORKAHOLIC_TEST_PROJECT_ROOT"
    ;;
  "status --porcelain=v1 --untracked-files=all")
    printf '%s' "${WORKAHOLIC_TEST_GIT_STATUS:-}"
    ;;
  *)
    exit 91
    ;;
esac
""",
    )
    _write_executable(
        binary_directory / "uv",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_UV_LOG"
if [ "${WORKAHOLIC_TEST_UV_FAIL_ON:-}" = "$*" ]; then
  exit 17
fi
if [ "$*" = "build" ]; then
  mkdir -p dist
  printf '%s\n' "wheel fixture" > dist/workaholic_ai-0.3.0a1-py3-none-any.whl
fi
""",
    )
    _write_executable(
        scripts_directory / "smoke-install.sh",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_SMOKE_LOG"
if [ "${WORKAHOLIC_TEST_SMOKE_FAIL:-0}" = "1" ]; then
  exit 65
fi
""",
    )
    return project_root, binary_directory


def _run_gate(
    tmp_path: Path,
    scenario: _GateScenario = _DEFAULT_SCENARIO,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[str],
    list[str],
    list[str],
]:
    """Run the gate against deterministic external-command boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.
        scenario: External-boundary state for this invocation.

    Returns:
        Process result plus recorded Git, uv, and smoke calls.

    """
    project_root, binary_directory = _phase_zero_fixture(tmp_path)
    if scenario.generated_path is not None:
        (project_root / scenario.generated_path).mkdir()

    git_log = tmp_path / "git.log"
    uv_log = tmp_path / "uv.log"
    smoke_log = tmp_path / "smoke.log"
    caller_directory = tmp_path / "caller"
    caller_directory.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}",
            "WORKAHOLIC_TEST_GIT_LOG": str(git_log),
            "WORKAHOLIC_TEST_GIT_STATUS": scenario.git_status,
            "WORKAHOLIC_TEST_PROJECT_ROOT": str(project_root),
            "WORKAHOLIC_TEST_SMOKE_LOG": str(smoke_log),
            "WORKAHOLIC_TEST_UV_LOG": str(uv_log),
        }
    )
    environment.pop("VIRTUAL_ENV", None)
    if scenario.active_environment:
        environment["VIRTUAL_ENV"] = str(tmp_path / "active")
    if scenario.uv_fail_on is not None:
        environment["WORKAHOLIC_TEST_UV_FAIL_ON"] = scenario.uv_fail_on
    if scenario.smoke_fails:
        environment["WORKAHOLIC_TEST_SMOKE_FAIL"] = "1"

    result = subprocess.run(
        [
            str(project_root / "scripts" / _VERIFY_SCRIPT.name),
            *scenario.arguments,
        ],
        check=False,
        cwd=caller_directory,
        env=environment,
        capture_output=True,
        text=True,
    )

    def read_calls(path: Path) -> list[str]:
        """Return newline-delimited calls when the boundary was invoked."""
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return result, read_calls(git_log), read_calls(uv_log), read_calls(smoke_log)


def test_gate_runs_the_exact_phase_zero_journey_from_the_project_root(
    tmp_path: Path,
) -> None:
    """The public command sequences existing checks without hidden substitutes."""
    result, git_calls, uv_calls, smoke_calls = _run_gate(tmp_path)
    project_root = tmp_path / "project"

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.endswith("Phase 0 clean-checkout acceptance gate passed.\n")
    assert uv_calls == [
        f"{project_root}|sync --frozen",
        f"{project_root}|run pre-commit run --all-files",
        f"{project_root}|run workaholic --version",
        f"{project_root}|run pytest",
        f"{project_root}|build",
    ]
    assert smoke_calls == [
        (f"{project_root}|dist/workaholic_ai-0.3.0a1-py3-none-any.whl")
    ]
    assert git_calls[0] == f"{project_root}|rev-parse --show-toplevel"
    assert (
        git_calls.count(f"{project_root}|status --porcelain=v1 --untracked-files=all")
        == 3
    )


def test_gate_rejects_arguments_before_invoking_external_commands(
    tmp_path: Path,
) -> None:
    """Unexpected arguments fail with a stable usage status."""
    result, git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(arguments=("--unexpected",)),
    )

    assert result.returncode == 64
    assert "usage: scripts/verify-phase-0.sh" in result.stderr
    assert git_calls == []
    assert uv_calls == []
    assert smoke_calls == []


def test_gate_rejects_an_active_virtual_environment(tmp_path: Path) -> None:
    """Acceptance cannot inherit dependencies from an activated environment."""
    result, git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(active_environment=True),
    )

    assert result.returncode == 69
    assert "deactivate the active virtual environment" in result.stderr
    assert git_calls == []
    assert uv_calls == []
    assert smoke_calls == []


def test_gate_rejects_dirty_or_untracked_input_before_sync(tmp_path: Path) -> None:
    """A non-clean checkout cannot masquerade as clean-clone evidence."""
    result, _git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(git_status="?? local-only.txt\n"),
    )

    assert result.returncode == 69
    assert "verification requires a clean Git worktree" in result.stderr
    assert "?? local-only.txt" in result.stderr
    assert uv_calls == []
    assert smoke_calls == []


@pytest.mark.parametrize("generated_path", [".venv", "dist"])
def test_gate_rejects_preexisting_generated_state(
    tmp_path: Path,
    generated_path: str,
) -> None:
    """An existing environment or distribution invalidates clean-clone proof."""
    result, _git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(generated_path=generated_path),
    )

    assert result.returncode == 69
    assert f"remove pre-existing {generated_path}" in result.stderr
    assert uv_calls == []
    assert smoke_calls == []


@pytest.mark.parametrize(
    ("failed_command", "expected_call_count"),
    [
        ("sync --frozen", 1),
        ("run pre-commit run --all-files", 2),
        ("run workaholic --version", 3),
        ("run pytest", 4),
        ("build", 5),
    ],
)
def test_gate_stops_at_the_first_failed_existing_command(
    tmp_path: Path,
    failed_command: str,
    expected_call_count: int,
) -> None:
    """Every source-stage failure propagates without running later stages."""
    result, _git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(uv_fail_on=failed_command),
    )

    assert result.returncode == 17
    assert len(uv_calls) == expected_call_count
    assert uv_calls[-1].endswith(f"|{failed_command}")
    assert smoke_calls == []


def test_gate_propagates_a_malformed_wheel_failure(tmp_path: Path) -> None:
    """A wheel rejected by the existing smoke boundary fails the whole gate."""
    result, _git_calls, uv_calls, smoke_calls = _run_gate(
        tmp_path,
        _GateScenario(smoke_fails=True),
    )

    assert result.returncode == 65
    assert len(uv_calls) == 5
    assert len(smoke_calls) == 1


def test_gate_is_an_executable_posix_entry_point() -> None:
    """The acceptance command can run directly on supported development hosts."""
    mode = _VERIFY_SCRIPT.stat().st_mode

    assert _VERIFY_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR
