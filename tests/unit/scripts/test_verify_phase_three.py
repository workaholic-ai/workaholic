"""Tests for the fail-fast Phase 3 clean-state acceptance orchestrator."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).parents[3]
_VERIFY_SCRIPT = _PROJECT_ROOT / "scripts" / "verify-phase-3.sh"
_PRE_COMMIT_CONFIG = _PROJECT_ROOT / ".pre-commit-config.yaml"


@dataclass(frozen=True, slots=True)
class _GateScenario:
    """Configure one deterministic Phase 3 gate scenario."""

    arguments: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    git_status: str = ""
    uv_fail_on: str | None = None
    smoke_install_fails: bool = False
    smoke_phase_three_fails: bool = False
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


def _phase_three_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
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
  printf '%s\n' "wheel fixture" > dist/workaholic_ai-0.4.0a1-py3-none-any.whl
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
        scripts_directory / "smoke-phase-3-wheel.sh",
        """#!/bin/sh
set -eu
printf '%s|%s|%s|%s\n' \
  "$PWD" "$*" "$WORKAHOLIC_CONFIG_DIR" "$WORKAHOLIC_DATA_DIR" \
  >> "$WORKAHOLIC_TEST_SMOKE_PHASE_THREE_LOG"
if [ "${WORKAHOLIC_TEST_SMOKE_PHASE_THREE_FAIL:-0}" = "1" ]; then
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
    project_root, binary_directory, runtime_directory = _phase_three_fixture(tmp_path)
    if scenario.generated_path is not None:
        (project_root / scenario.generated_path).mkdir()

    git_log = tmp_path / "git.log"
    uv_log = tmp_path / "uv.log"
    smoke_install_log = tmp_path / "smoke-install.log"
    smoke_phase_three_log = tmp_path / "smoke-phase-three.log"
    environment = os.environ.copy()
    for key in (
        "VIRTUAL_ENV",
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_DATA_DIR",
        "WORKAHOLIC_PROFILE",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment['PATH']}",
            "TMPDIR": str(runtime_directory),
            "WORKAHOLIC_TEST_GIT_LOG": str(git_log),
            "WORKAHOLIC_TEST_GIT_STATUS": scenario.git_status,
            "WORKAHOLIC_TEST_PROJECT_ROOT": str(project_root),
            "WORKAHOLIC_TEST_SMOKE_INSTALL_FAIL": (
                "1" if scenario.smoke_install_fails else "0"
            ),
            "WORKAHOLIC_TEST_SMOKE_INSTALL_LOG": str(smoke_install_log),
            "WORKAHOLIC_TEST_SMOKE_PHASE_THREE_FAIL": (
                "1" if scenario.smoke_phase_three_fails else "0"
            ),
            "WORKAHOLIC_TEST_SMOKE_PHASE_THREE_LOG": str(smoke_phase_three_log),
            "WORKAHOLIC_TEST_UV_FAIL_ON": scenario.uv_fail_on or "",
            "WORKAHOLIC_TEST_UV_LOG": str(uv_log),
        }
    )
    environment.update(scenario.environment)

    result = subprocess.run(
        [str(project_root / "scripts" / "verify-phase-3.sh"), *scenario.arguments],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    remaining = tuple(runtime_directory.glob("workaholic-phase-three-gate.*"))
    return (
        result,
        _read_calls(git_log),
        _read_calls(uv_log),
        _read_calls(smoke_install_log),
        _read_calls(smoke_phase_three_log),
        remaining,
    )


def test_gate_runs_the_exact_phase_three_journey_in_owned_roots(
    tmp_path: Path,
) -> None:
    """The success path is ordered, isolated, and cleans runtime state."""
    result, git_calls, uv_calls, install_calls, phase_three_calls, remaining = (
        _run_gate(tmp_path)
    )
    project_root = tmp_path / "project"
    wheel = "dist/workaholic_ai-0.4.0a1-py3-none-any.whl"

    assert result.returncode == 0
    assert result.stdout.endswith("Phase 3 clean-state acceptance gate passed.\n")
    for step in range(1, 7):
        assert f"[{step}/6]" in result.stdout
    assert uv_calls == [
        f"{project_root}|sync --frozen",
        f"{project_root}|run pre-commit run --all-files",
        f"{project_root}|run pytest",
        f"{project_root}|build",
    ]
    assert install_calls == [f"{project_root}|{wheel}"]
    assert len(phase_three_calls) == 1
    phase_three_root, phase_three_wheel, config_path, data_path = phase_three_calls[
        0
    ].split("|")
    assert phase_three_root == str(project_root)
    assert phase_three_wheel == wheel
    runtime_root = (tmp_path / "runtime").resolve()
    for owned_path in (Path(config_path), Path(data_path)):
        assert owned_path.is_absolute()
        assert owned_path.is_relative_to(runtime_root)
    assert Path(config_path) != Path(data_path)
    assert (
        git_calls.count(f"{project_root}|status --porcelain=v1 --untracked-files=all")
        == 3
    )
    assert remaining == ()


@pytest.mark.parametrize(
    ("scenario", "expected_message"),
    [
        pytest.param(
            _GateScenario(arguments=("unexpected",)),
            "usage: scripts/verify-phase-3.sh",
            id="arguments",
        ),
        pytest.param(
            _GateScenario(environment={"VIRTUAL_ENV": "/active"}),
            "deactivate the active virtual environment",
            id="active-venv",
        ),
        pytest.param(
            _GateScenario(environment={"WORKAHOLIC_CONFIG_DIR": "/outside"}),
            "unset WORKAHOLIC_CONFIG_DIR",
            id="config-selector",
        ),
        pytest.param(
            _GateScenario(environment={"WORKAHOLIC_DATA_DIR": "/outside"}),
            "unset WORKAHOLIC_DATA_DIR",
            id="data-selector",
        ),
        pytest.param(
            _GateScenario(environment={"WORKAHOLIC_PROFILE": "operator"}),
            "unset WORKAHOLIC_PROFILE",
            id="profile-selector",
        ),
    ],
)
def test_gate_rejects_arguments_and_unowned_environment_before_external_calls(
    tmp_path: Path,
    scenario: _GateScenario,
    expected_message: str,
) -> None:
    """Caller-controlled inputs cannot redirect the clean-state boundary."""
    result, git_calls, uv_calls, install_calls, phase_three_calls, remaining = (
        _run_gate(tmp_path, scenario)
    )

    assert result.returncode in {64, 69}
    assert expected_message in result.stderr
    assert git_calls == []
    assert uv_calls == []
    assert install_calls == []
    assert phase_three_calls == []
    assert remaining == ()


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(_GateScenario(git_status=" M README.md\n"), id="dirty"),
        pytest.param(_GateScenario(generated_path=".venv"), id="venv"),
        pytest.param(_GateScenario(generated_path="dist"), id="dist"),
    ],
)
def test_gate_rejects_dirty_or_preexisting_generated_state(
    tmp_path: Path,
    scenario: _GateScenario,
) -> None:
    """The gate accepts only a clean checkout without build artifacts."""
    result, _git_calls, uv_calls, install_calls, phase_three_calls, remaining = (
        _run_gate(tmp_path, scenario)
    )

    assert result.returncode == 69
    assert "phase-three:" in result.stderr
    assert uv_calls == []
    assert install_calls == []
    assert phase_three_calls == []
    assert remaining == ()


@pytest.mark.parametrize(
    "failed_call",
    [
        "sync --frozen",
        "run pre-commit run --all-files",
        "run pytest",
        "build",
    ],
)
def test_gate_stops_at_the_first_failed_source_command(
    tmp_path: Path,
    failed_call: str,
) -> None:
    """Every locked source gate is fail-fast and suppresses later smoke work."""
    result, _git_calls, uv_calls, install_calls, phase_three_calls, remaining = (
        _run_gate(tmp_path, _GateScenario(uv_fail_on=failed_call))
    )

    assert result.returncode == 17
    assert uv_calls[-1].endswith(f"|{failed_call}")
    assert install_calls == []
    assert phase_three_calls == []
    assert remaining == ()


@pytest.mark.parametrize(
    ("scenario", "expected_install_calls", "expected_phase_three_calls"),
    [
        pytest.param(_GateScenario(smoke_install_fails=True), 1, 0, id="install"),
        pytest.param(
            _GateScenario(smoke_phase_three_fails=True),
            1,
            1,
            id="phase-three",
        ),
    ],
)
def test_gate_propagates_each_wheel_smoke_failure(
    tmp_path: Path,
    scenario: _GateScenario,
    expected_install_calls: int,
    expected_phase_three_calls: int,
) -> None:
    """Malformed installation or lifecycle wheels fail the aggregate gate."""
    result, _git_calls, uv_calls, install_calls, phase_three_calls, remaining = (
        _run_gate(tmp_path, scenario)
    )

    assert result.returncode == 65
    assert uv_calls[-1].endswith("|build")
    assert len(install_calls) == expected_install_calls
    assert len(phase_three_calls) == expected_phase_three_calls
    assert remaining == ()


def test_gate_is_an_executable_posix_entry_point() -> None:
    """The aggregate gate can run directly on supported acceptance hosts."""
    mode = _VERIFY_SCRIPT.stat().st_mode

    assert _VERIFY_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR


def test_pre_commit_runs_phase_three_contracts_for_every_gate_input() -> None:
    """Every Phase 3 gate boundary change triggers focused local contracts."""
    configuration = yaml.safe_load(_PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks = [
        hook
        for repository in configuration["repos"]
        if repository["repo"] == "local"
        for hook in repository["hooks"]
        if hook["id"] == "phase-three-contracts"
    ]

    assert len(hooks) == 1
    hook = hooks[0]
    assert "tests/unit/scripts/test_smoke_phase_three_wheel.py" in hook["entry"]
    assert "tests/unit/scripts/test_verify_phase_three.py" in hook["entry"]
    pattern = re.compile(hook["files"])
    for path in (
        "CHANGELOG.md",
        "README.md",
        "scripts/smoke-phase-3-wheel.sh",
        "scripts/verify-phase-3.sh",
        "tests/e2e/test_phase_3_distribution.py",
        "tests/unit/scripts/test_smoke_phase_three_wheel.py",
        "tests/unit/scripts/test_verify_phase_three.py",
    ):
        assert pattern.search(path), path
