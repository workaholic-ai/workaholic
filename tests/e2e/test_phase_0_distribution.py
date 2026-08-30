"""End-to-end acceptance for the Phase 0 clean-checkout distribution."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
_GATE_ENVIRONMENT_KEYS = (
    "WORKAHOLIC_PHASE_0_GATE_RUNNING",
    "WORKAHOLIC_PHASE_1_GATE_RUNNING",
    "WORKAHOLIC_PHASE_2_GATE_RUNNING",
    "WORKAHOLIC_PHASE_3_GATE_RUNNING",
    "WORKAHOLIC_PHASE_4_GATE_RUNNING",
)
_COMMAND_TIMEOUT_SECONDS = 600

pytestmark = [
    pytest.mark.distribution,
    pytest.mark.e2e,
    pytest.mark.requires_network,
    pytest.mark.requires_uv,
    pytest.mark.skipif(
        any(os.environ.get(key) == "1" for key in _GATE_ENVIRONMENT_KEYS),
        reason="The outer clean-checkout test owns Phase 0 gate recursion.",
    ),
]


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded external command.

    Args:
        command: Argument vector to invoke.
        cwd: Working directory for the child process.
        environment: Optional complete child environment.

    Returns:
        Completed process with decoded output.

    """
    return subprocess.run(
        command,
        check=False,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def _require_success(
    result: subprocess.CompletedProcess[str],
    *,
    context: str,
) -> None:
    """Assert that an external acceptance command succeeded.

    Args:
        result: Completed child process.
        context: Human-readable operation name for failure output.

    """
    assert result.returncode == 0, (
        f"{context} failed with status {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _clone_current_revision(tmp_path: Path) -> Path:
    """Clone the committed revision under test without local file sharing.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Path to a clean detached checkout.

    """
    revision_result = _run(
        ["git", "rev-parse", "HEAD"],
        cwd=_PROJECT_ROOT,
    )
    _require_success(revision_result, context="reading the source revision")
    revision = revision_result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", revision)

    clone = tmp_path / "clone"
    clone_result = _run(
        [
            "git",
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--quiet",
            str(_PROJECT_ROOT),
            str(clone),
        ],
        cwd=tmp_path,
    )
    _require_success(clone_result, context="creating the clean clone")
    checkout_result = _run(
        ["git", "checkout", "--detach", "--quiet", revision],
        cwd=clone,
    )
    _require_success(checkout_result, context="checking out the tested revision")

    status_result = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=clone,
    )
    _require_success(status_result, context="checking clone cleanliness")
    assert status_result.stdout == ""
    assert not (clone / ".venv").exists()
    assert not (clone / "dist").exists()
    return clone


def _clean_environment(tmp_path: Path) -> dict[str, str]:
    """Return an environment isolated from the developer's active project.

    Args:
        tmp_path: Pytest-owned directory for external tool caches.

    Returns:
        Complete environment mapping for clean-clone commands.

    """
    environment = os.environ.copy()
    for variable in (
        "CONDA_PREFIX",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "NO_COLOR": "1",
            "PRE_COMMIT_HOME": str(tmp_path.parent / "phase-zero-pre-commit"),
            "UV_CACHE_DIR": str(tmp_path.parent / "phase-zero-uv"),
            "UV_LINK_MODE": "copy",
            "WORKAHOLIC_CREDENTIAL_BACKEND": "file",
            "WORKAHOLIC_DATA_DIR": str(tmp_path / "workaholic-data"),
        }
    )
    return environment


def _commit_fixture(clone: Path, relative_path: str, source: str) -> None:
    """Commit one intentional negative fixture to an otherwise clean clone.

    Args:
        clone: Temporary repository root.
        relative_path: Repository-relative fixture path.
        source: Complete UTF-8 fixture contents.

    """
    fixture_path = clone / relative_path
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(source, encoding="utf-8")
    for command in (
        ["git", "config", "user.name", "Phase 0 Test"],
        ["git", "config", "user.email", "phase-zero-test@example.invalid"],
        ["git", "add", relative_path],
        ["git", "commit", "--quiet", "-m", "Add Phase 0 negative fixture"],
    ):
        result = _run(command, cwd=clone)
        _require_success(result, context=f"preparing fixture with {' '.join(command)}")


def _run_gate(clone: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the public acceptance command in a clean child environment.

    Args:
        clone: Temporary repository root.
        tmp_path: Pytest-owned directory for external tool state.

    Returns:
        Completed gate process.

    """
    return _run(
        [str(clone / "scripts" / "verify-phase-0.sh")],
        cwd=clone,
        environment=_clean_environment(tmp_path),
    )


def test_phase_zero_gate_passes_from_a_fresh_clone(tmp_path: Path) -> None:
    """The aggregate command proves the complete source-to-wheel journey."""
    clone = _clone_current_revision(tmp_path)

    result = _run_gate(clone, tmp_path)

    _require_success(result, context="Phase 0 clean-checkout gate")
    assert "workaholic 0.5.0a1" in result.stdout
    assert result.stdout.endswith("Phase 0 clean-checkout acceptance gate passed.\n")


def test_phase_zero_gate_rejects_a_stale_lockfile(tmp_path: Path) -> None:
    """Uncommitted dependency resolution cannot pass clean-clone acceptance."""
    clone = _clone_current_revision(tmp_path)
    pyproject = clone / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8").replace(
        "dev = [\n",
        'dev = [\n  "idna==3.11",\n',
        1,
    )
    _commit_fixture(clone, "pyproject.toml", source)

    result = _run_gate(clone, tmp_path)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "verification requires a clean Git worktree" in output
    assert "uv.lock" in output


def test_phase_zero_gate_rejects_dirty_formatting(tmp_path: Path) -> None:
    """A committed formatting defect is stopped by repository quality hooks."""
    clone = _clone_current_revision(tmp_path)
    _commit_fixture(
        clone,
        "tests/phase_zero_dirty_format_fixture.py",
        '"""Intentional formatting failure fixture."""\n\n\n'
        "def badly_formatted( ) -> int:\n"
        " return 1\n",
    )

    result = _run_gate(clone, tmp_path)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Ruff lint" in output
    assert "files were modified by this hook" in output


def test_phase_zero_gate_rejects_a_failing_test(tmp_path: Path) -> None:
    """A cleanly formatted but failing test prevents artifact construction."""
    clone = _clone_current_revision(tmp_path)
    _commit_fixture(
        clone,
        "tests/test_phase_zero_injected_failure.py",
        '"""Intentional test failure fixture."""\n\n\n'
        "def test_injected_phase_zero_failure() -> None:\n"
        '    """Prove that the acceptance gate propagates pytest failures."""\n'
        '    raise AssertionError("injected Phase 0 failure")\n',
    )

    result = _run_gate(clone, tmp_path)

    assert result.returncode != 0
    assert "injected Phase 0 failure" in result.stdout + result.stderr
    assert not (clone / "dist").exists()


def test_phase_zero_gate_rejects_a_malformed_built_wheel(tmp_path: Path) -> None:
    """The final smoke boundary rejects a corrupt artifact from the build step."""
    clone = _clone_current_revision(tmp_path)
    real_uv = shutil.which("uv")
    assert real_uv is not None
    binary_directory = tmp_path / "malformed-build-bin"
    binary_directory.mkdir()
    uv_wrapper = binary_directory / "uv"
    uv_wrapper.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" = "build" ]; then
  mkdir -p dist
  printf '%s\n' "not a wheel archive" > \
    dist/workaholic_ai-0.5.0a1-py3-none-any.whl
  exit 0
fi
exec "$WORKAHOLIC_TEST_REAL_UV" "$@"
""",
        encoding="utf-8",
    )
    uv_wrapper.chmod(
        uv_wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    environment = _clean_environment(tmp_path)
    environment["WORKAHOLIC_PHASE_0_GATE_RUNNING"] = "1"
    environment["PATH"] = f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}"
    environment["WORKAHOLIC_TEST_REAL_UV"] = real_uv

    result = _run(
        [str(clone / "scripts" / "verify-phase-0.sh")],
        cwd=clone,
        environment=environment,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "[6/6] Installing and running the built wheel" in output
    assert "workaholic_ai-0.5.0a1-py3-none-any.whl" in output
