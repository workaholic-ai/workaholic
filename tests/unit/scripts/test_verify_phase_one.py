"""Tests for the fail-fast Phase 1 clean-state acceptance orchestrator."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).parents[3]
_VERIFY_SCRIPT = _PROJECT_ROOT / "scripts" / "verify-phase-1.sh"
_PRE_COMMIT_CONFIG = _PROJECT_ROOT / ".pre-commit-config.yaml"


@dataclass(frozen=True, slots=True)
class _GateScenario:
    """Configure one deterministic Phase 1 gate scenario."""

    arguments: tuple[str, ...] = ()
    active_environment: bool = False
    git_status: str = ""
    uv_fail_on: str | None = None
    smoke_install_fails: bool = False
    smoke_phase_one_fails: bool = False
    generated_path: str | None = None


_DEFAULT_SCENARIO = _GateScenario()


def _write_executable(path: Path, source: str) -> None:
    """Write one executable boundary fixture.

    Args:
        path: Destination path.
        source: Complete POSIX shell source.

    """
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _phase_one_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create an isolated project with deterministic external boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Project root, fake executable directory, and gate temporary root.

    """
    project_root = tmp_path / "project"
    scripts_directory = project_root / "scripts"
    binary_directory = tmp_path / "bin"
    runtime_directory = tmp_path / "runtime"
    scripts_directory.mkdir(parents=True)
    binary_directory.mkdir()
    runtime_directory.mkdir()
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
  printf '%s\n' "wheel fixture" > dist/workaholic_ai-0.2.0a1-py3-none-any.whl
fi
""",
    )
    _write_executable(
        scripts_directory / "smoke-install.sh",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_SMOKE_INSTALL_LOG"
if [ "${WORKAHOLIC_TEST_SMOKE_INSTALL_FAIL:-0}" = "1" ]; then
  exit 65
fi
""",
    )
    _write_executable(
        scripts_directory / "smoke-phase-1-wheel.sh",
        """#!/bin/sh
set -eu
printf '%s|%s|%s\n' \
  "$PWD" "$*" "$WORKAHOLIC_DATA_DIR" \
  >> "$WORKAHOLIC_TEST_SMOKE_PHASE_ONE_LOG"
if [ "${WORKAHOLIC_TEST_SMOKE_PHASE_ONE_FAIL:-0}" = "1" ]; then
  exit 65
fi
""",
    )
    return project_root, binary_directory, runtime_directory


def _read_calls(path: Path) -> list[str]:
    """Return newline-delimited boundary calls when present.

    Args:
        path: Test log path.

    Returns:
        Recorded lines or an empty list.

    """
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _run_gate(
    tmp_path: Path,
    scenario: _GateScenario = _DEFAULT_SCENARIO,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[str],
    list[str],
    list[str],
    list[str],
    tuple[Path, ...],
]:
    """Run the gate against deterministic external-command boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.
        scenario: Boundary state for this invocation.

    Returns:
        Result, Git/uv/smoke calls, and remaining gate temporary paths.

    """
    project_root, binary_directory, runtime_directory = _phase_one_fixture(tmp_path)
    if scenario.generated_path is not None:
        (project_root / scenario.generated_path).mkdir()

    git_log = tmp_path / "git.log"
    uv_log = tmp_path / "uv.log"
    smoke_install_log = tmp_path / "smoke-install.log"
    smoke_phase_one_log = tmp_path / "smoke-phase-one.log"
    caller_directory = tmp_path / "caller"
    caller_directory.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}",
            "TMPDIR": str(runtime_directory),
            "WORKAHOLIC_TEST_GIT_LOG": str(git_log),
            "WORKAHOLIC_TEST_GIT_STATUS": scenario.git_status,
            "WORKAHOLIC_TEST_PROJECT_ROOT": str(project_root),
            "WORKAHOLIC_TEST_SMOKE_INSTALL_LOG": str(smoke_install_log),
            "WORKAHOLIC_TEST_SMOKE_PHASE_ONE_LOG": str(smoke_phase_one_log),
            "WORKAHOLIC_TEST_UV_LOG": str(uv_log),
        }
    )
    environment.pop("VIRTUAL_ENV", None)
    if scenario.active_environment:
        environment["VIRTUAL_ENV"] = str(tmp_path / "active")
    if scenario.uv_fail_on is not None:
        environment["WORKAHOLIC_TEST_UV_FAIL_ON"] = scenario.uv_fail_on
    if scenario.smoke_install_fails:
        environment["WORKAHOLIC_TEST_SMOKE_INSTALL_FAIL"] = "1"
    if scenario.smoke_phase_one_fails:
        environment["WORKAHOLIC_TEST_SMOKE_PHASE_ONE_FAIL"] = "1"

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
    return (
        result,
        _read_calls(git_log),
        _read_calls(uv_log),
        _read_calls(smoke_install_log),
        _read_calls(smoke_phase_one_log),
        tuple(runtime_directory.iterdir()),
    )


def test_gate_runs_the_exact_phase_one_journey_and_cleans_runtime(
    tmp_path: Path,
) -> None:
    """The public command sequences existing checks without hidden substitutes."""
    result, git_calls, uv_calls, install_calls, phase_one_calls, remaining = _run_gate(
        tmp_path
    )
    project_root = tmp_path / "project"

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.endswith("Phase 1 clean-state acceptance gate passed.\n")
    assert uv_calls == [
        f"{project_root}|sync --frozen",
        f"{project_root}|run pre-commit run --all-files",
        f"{project_root}|run pytest",
        f"{project_root}|build",
    ]
    wheel_argument = f"{project_root}|dist/workaholic_ai-0.2.0a1-py3-none-any.whl"
    assert install_calls == [wheel_argument]
    assert len(phase_one_calls) == 1
    assert phase_one_calls[0].startswith(f"{wheel_argument}|")
    assert "workaholic-phase-one-gate." in phase_one_calls[0]
    assert git_calls[0] == f"{project_root}|rev-parse --show-toplevel"
    assert (
        git_calls.count(f"{project_root}|status --porcelain=v1 --untracked-files=all")
        == 3
    )
    assert remaining == ()


def test_gate_rejects_arguments_and_active_environment(tmp_path: Path) -> None:
    """Usage and inherited-environment failures happen before Git or uv."""
    unexpected = _run_gate(
        tmp_path / "arguments",
        _GateScenario(arguments=("--unexpected",)),
    )
    active = _run_gate(
        tmp_path / "active",
        _GateScenario(active_environment=True),
    )

    assert unexpected[0].returncode == 64
    assert "usage: scripts/verify-phase-1.sh" in unexpected[0].stderr
    assert unexpected[1:5] == ([], [], [], [])
    assert active[0].returncode == 69
    assert "deactivate the active virtual environment" in active[0].stderr
    assert active[1:5] == ([], [], [], [])


def test_gate_rejects_dirty_or_untracked_input(tmp_path: Path) -> None:
    """A dirty checkout cannot masquerade as clean-state evidence."""
    result, _git, uv_calls, install_calls, phase_one_calls, remaining = _run_gate(
        tmp_path,
        _GateScenario(git_status="?? local-only.txt\n"),
    )

    assert result.returncode == 69
    assert "verification requires a clean Git worktree" in result.stderr
    assert "?? local-only.txt" in result.stderr
    assert uv_calls == install_calls == phase_one_calls == []
    assert remaining == ()


@pytest.mark.parametrize("generated_path", [".venv", "dist"])
def test_gate_rejects_preexisting_generated_state(
    tmp_path: Path,
    generated_path: str,
) -> None:
    """Existing environments or artifacts invalidate clean-state proof."""
    result, _git, uv_calls, install_calls, phase_one_calls, remaining = _run_gate(
        tmp_path,
        _GateScenario(generated_path=generated_path),
    )

    assert result.returncode == 69
    assert f"remove pre-existing {generated_path}" in result.stderr
    assert uv_calls == install_calls == phase_one_calls == []
    assert remaining == ()


@pytest.mark.parametrize(
    ("failed_command", "expected_call_count"),
    [
        ("sync --frozen", 1),
        ("run pre-commit run --all-files", 2),
        ("run pytest", 3),
        ("build", 4),
    ],
)
def test_gate_stops_at_the_first_failed_source_command(
    tmp_path: Path,
    failed_command: str,
    expected_call_count: int,
) -> None:
    """A source-stage failure propagates without running later boundaries."""
    result, _git, uv_calls, install_calls, phase_one_calls, remaining = _run_gate(
        tmp_path,
        _GateScenario(uv_fail_on=failed_command),
    )

    assert result.returncode == 17
    assert len(uv_calls) == expected_call_count
    assert uv_calls[-1].endswith(f"|{failed_command}")
    assert install_calls == phase_one_calls == []
    assert remaining == ()


def test_gate_propagates_each_wheel_smoke_failure(tmp_path: Path) -> None:
    """Installation and persistent-journey failures stop the aggregate gate."""
    install = _run_gate(
        tmp_path / "install",
        _GateScenario(smoke_install_fails=True),
    )
    phase_one = _run_gate(
        tmp_path / "phase-one",
        _GateScenario(smoke_phase_one_fails=True),
    )

    assert install[0].returncode == 65
    assert len(install[3]) == 1
    assert install[4] == []
    assert phase_one[0].returncode == 65
    assert len(phase_one[3]) == 1
    assert len(phase_one[4]) == 1
    assert install[5] == phase_one[5] == ()


def test_gate_is_an_executable_posix_entry_point() -> None:
    """The Phase 1 acceptance command is directly executable."""
    mode = _VERIFY_SCRIPT.stat().st_mode

    assert _VERIFY_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR


def test_pre_commit_runs_phase_one_contracts_for_every_gate_input() -> None:
    """Relevant acceptance changes run deterministic boundary tests at commit."""
    configuration = yaml.safe_load(_PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    local_repository = next(
        repository
        for repository in configuration["repos"]
        if repository["repo"] == "local"
    )
    hooks = [
        hook
        for hook in local_repository["hooks"]
        if hook.get("id") == "phase-one-contracts"
    ]

    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["entry"] == (
        "uv run --frozen pytest --no-cov "
        "tests/unit/scripts/test_smoke_phase_one_wheel.py "
        "tests/unit/scripts/test_verify_phase_one.py"
    )
    assert hook["language"] == "system"
    assert hook["pass_filenames"] is False
    path_pattern = re.compile(hook["files"])
    for required_path in (
        "README.md",
        "scripts/smoke-phase-1-wheel.sh",
        "scripts/verify-phase-1.sh",
        "tests/e2e/test_phase_1_distribution.py",
        "tests/unit/scripts/test_smoke_phase_one_wheel.py",
        "tests/unit/scripts/test_verify_phase_one.py",
    ):
        assert path_pattern.fullmatch(required_path)
