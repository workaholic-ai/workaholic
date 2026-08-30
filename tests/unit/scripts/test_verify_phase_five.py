"""Tests for the fail-fast Phase 5 clean-state acceptance orchestrator."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_VERIFY_SCRIPT = _PROJECT_ROOT / "scripts" / "verify-phase-5.sh"


@dataclass(frozen=True, slots=True)
class _GateScenario:
    """Configure one deterministic Phase 5 gate boundary scenario."""

    arguments: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    git_status: str = ""
    uv_fail_on: str | None = None
    smoke_install_fails: bool = False
    smoke_phase_five_fails: bool = False
    generated_path: str | None = None


_DEFAULT_SCENARIO = _GateScenario()


def _write_executable(path: Path, source: str) -> None:
    """Write an executable POSIX fixture at ``path``.

    Args:
        path: Fixture destination.
        source: Complete shell source.

    """
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a fake checkout and deterministic command boundaries.

    Args:
        tmp_path: Pytest-owned root.

    Returns:
        Fake checkout, executable directory, and temporary runtime parent.

    """
    project = tmp_path / "project"
    scripts = project / "scripts"
    binaries = tmp_path / "bin"
    runtime = tmp_path / "runtime"
    scripts.mkdir(parents=True)
    binaries.mkdir()
    runtime.mkdir()
    shutil.copy2(_VERIFY_SCRIPT, scripts / _VERIFY_SCRIPT.name)
    _write_executable(
        binaries / "git",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_GIT_LOG"
case "$*" in
  "rev-parse --show-toplevel") printf '%s\n' "$WORKAHOLIC_TEST_PROJECT_ROOT" ;;
  "status --porcelain=v1 --untracked-files=all")
    printf '%s' "${WORKAHOLIC_TEST_GIT_STATUS:-}"
    ;;
  *) exit 91 ;;
esac
""",
    )
    _write_executable(
        binaries / "uv",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_UV_LOG"
if [ "${WORKAHOLIC_TEST_UV_FAIL_ON:-}" = "$*" ]; then exit 17; fi
if [ "$*" = "build --no-progress" ]; then
  mkdir -p dist
  printf '%s\n' wheel > dist/workaholic_ai-0.5.0a1-py3-none-any.whl
fi
""",
    )
    _write_executable(
        scripts / "smoke-install.sh",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_INSTALL_LOG"
if [ "${WORKAHOLIC_TEST_INSTALL_FAIL:-0}" = 1 ]; then exit 65; fi
""",
    )
    _write_executable(
        scripts / "smoke-phase-5-wheel.sh",
        """#!/bin/sh
set -eu
printf '%s|%s|%s|%s|%s|%s\n' \
  "$PWD" "$*" "$WORKAHOLIC_CONFIG_DIR" "$WORKAHOLIC_DATA_DIR" \
  "$WORKAHOLIC_CREDENTIAL_BACKEND" "${WORKAHOLIC_TOKEN_FILE:-}" \
  >> "$WORKAHOLIC_TEST_PHASE_FIVE_LOG"
if [ "${WORKAHOLIC_TEST_PHASE_FIVE_FAIL:-0}" = 1 ]; then exit 65; fi
""",
    )
    return project, binaries, runtime


def _read(path: Path) -> list[str]:
    """Read a newline-delimited call log if it exists.

    Args:
        path: Log path.

    Returns:
        Recorded lines.

    """
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _run(
    tmp_path: Path,
    scenario: _GateScenario = _DEFAULT_SCENARIO,
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]], tuple[Path, ...]]:
    """Run one isolated fake acceptance gate.

    Args:
        tmp_path: Pytest-owned root.
        scenario: Requested external boundary behavior.

    Returns:
        Process result, call logs, and remaining gate roots.

    """
    project, binaries, runtime = _fixture(tmp_path)
    if scenario.generated_path is not None:
        (project / scenario.generated_path).mkdir()
    logs = {
        name: tmp_path / f"{name}.log"
        for name in ("git", "uv", "install", "phase-five")
    }
    environment = os.environ.copy()
    for key in (
        "VIRTUAL_ENV",
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_CREDENTIAL_BACKEND",
        "WORKAHOLIC_DATA_DIR",
        "WORKAHOLIC_PROFILE",
        "WORKAHOLIC_TOKEN",
        "WORKAHOLIC_TOKEN_FILE",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "PATH": f"{binaries}{os.pathsep}{environment['PATH']}",
            "TMPDIR": str(runtime),
            "WORKAHOLIC_TEST_GIT_LOG": str(logs["git"]),
            "WORKAHOLIC_TEST_GIT_STATUS": scenario.git_status,
            "WORKAHOLIC_TEST_PROJECT_ROOT": str(project),
            "WORKAHOLIC_TEST_UV_LOG": str(logs["uv"]),
            "WORKAHOLIC_TEST_UV_FAIL_ON": scenario.uv_fail_on or "",
            "WORKAHOLIC_TEST_INSTALL_LOG": str(logs["install"]),
            "WORKAHOLIC_TEST_INSTALL_FAIL": (
                "1" if scenario.smoke_install_fails else "0"
            ),
            "WORKAHOLIC_TEST_PHASE_FIVE_LOG": str(logs["phase-five"]),
            "WORKAHOLIC_TEST_PHASE_FIVE_FAIL": (
                "1" if scenario.smoke_phase_five_fails else "0"
            ),
        }
    )
    environment.update(scenario.environment)
    result = subprocess.run(
        [str(project / "scripts" / "verify-phase-5.sh"), *scenario.arguments],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    calls = {name: _read(path) for name, path in logs.items()}
    remaining = tuple(runtime.glob("workaholic-phase-five-gate.*"))
    return result, calls, remaining


def test_gate_runs_exact_sequence_in_owned_identity_roots(tmp_path: Path) -> None:
    """The successful gate is ordered, isolated, and cleans its runtime root."""
    result, calls, remaining = _run(tmp_path)
    project = tmp_path / "project"
    wheel = "dist/workaholic_ai-0.5.0a1-py3-none-any.whl"

    assert result.returncode == 0
    assert result.stdout.endswith("Phase 5 clean-state acceptance gate passed.\n")
    assert calls["uv"] == [
        f"{project}|sync --frozen",
        f"{project}|run pre-commit run --all-files",
        f"{project}|run pytest",
        f"{project}|build --no-progress",
    ]
    assert calls["install"] == [f"{project}|{wheel}"]
    fields = calls["phase-five"][0].split("|")
    assert fields[0:2] == [str(project), wheel]
    runtime = (tmp_path / "runtime").resolve()
    for owned in map(Path, fields[2:4]):
        assert owned.is_absolute()
        assert owned.is_relative_to(runtime)
    assert fields[4:] == ["file", ""]
    assert (
        calls["git"].count(f"{project}|status --porcelain=v1 --untracked-files=all")
        == 3
    )
    assert remaining == ()


@pytest.mark.parametrize(
    ("selector", "value"),
    [
        ("VIRTUAL_ENV", "/active"),
        ("WORKAHOLIC_CONFIG_DIR", "/outside"),
        ("WORKAHOLIC_CREDENTIAL_BACKEND", "keyring"),
        ("WORKAHOLIC_DATA_DIR", "/outside"),
        ("WORKAHOLIC_PROFILE", "operator"),
        ("WORKAHOLIC_TOKEN", "secret"),
        ("WORKAHOLIC_TOKEN_FILE", "/outside/token"),
    ],
)
def test_gate_rejects_inherited_identity_selectors_before_calls(
    tmp_path: Path,
    selector: str,
    value: str,
) -> None:
    """Caller-selected identity and state cannot redirect acceptance."""
    result, calls, remaining = _run(
        tmp_path,
        _GateScenario(environment={selector: value}),
    )

    assert result.returncode == 69
    assert selector in result.stderr or selector == "VIRTUAL_ENV"
    assert all(not entries for entries in calls.values())
    assert remaining == ()


def test_gate_rejects_arguments_dirty_state_and_generated_output(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Arguments, dirty work, virtualenvs, and dist output fail before sync."""
    scenarios = (
        _GateScenario(arguments=("unexpected",)),
        _GateScenario(git_status=" M README.md\n"),
        _GateScenario(generated_path=".venv"),
        _GateScenario(generated_path="dist"),
    )
    for index, scenario in enumerate(scenarios):
        root = tmp_path_factory.mktemp(f"phase-five-reject-{index}")
        result, calls, remaining = _run(root, scenario)
        assert result.returncode in {64, 69}
        assert calls["uv"] == []
        assert calls["install"] == []
        assert calls["phase-five"] == []
        assert remaining == ()


@pytest.mark.parametrize(
    "failed_call",
    [
        "sync --frozen",
        "run pre-commit run --all-files",
        "run pytest",
        "build --no-progress",
    ],
)
def test_gate_stops_at_first_failed_source_command(
    tmp_path: Path,
    failed_call: str,
) -> None:
    """A failed source gate suppresses every later artifact boundary."""
    result, calls, remaining = _run(
        tmp_path,
        _GateScenario(uv_fail_on=failed_call),
    )

    assert result.returncode == 17
    assert calls["uv"][-1].endswith(f"|{failed_call}")
    assert calls["install"] == []
    assert calls["phase-five"] == []
    assert remaining == ()


@pytest.mark.parametrize(
    "scenario",
    [
        _GateScenario(smoke_install_fails=True),
        _GateScenario(smoke_phase_five_fails=True),
    ],
)
def test_gate_propagates_each_wheel_failure(
    tmp_path: Path,
    scenario: _GateScenario,
) -> None:
    """Base installation and Phase 5 behavior are both fail-fast boundaries."""
    result, calls, remaining = _run(tmp_path, scenario)

    assert result.returncode == 65
    assert len(calls["install"]) == 1
    assert len(calls["phase-five"]) == int(not scenario.smoke_install_fails)
    assert remaining == ()
