"""Tests for the aggregate local quality-check script."""

import os
import stat
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[3]
_CHECK_SCRIPT = _PROJECT_ROOT / "scripts" / "check.sh"


def _write_fake_uv(binary_directory: Path) -> Path:
    """Create a deterministic uv test double.

    Args:
        binary_directory: Directory that will be prepended to ``PATH``.

    Returns:
        Path to the executable test double.

    """
    binary_directory.mkdir()
    fake_uv = binary_directory / "uv"
    fake_uv.write_text(
        """#!/bin/sh
set -eu
printf '%s|%s\\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_UV_LOG"
if [ "${WORKAHOLIC_TEST_UV_FAIL_ON:-}" = "$*" ]; then
  exit 17
fi
""",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)
    return fake_uv


def _run_check_script(
    tmp_path: Path,
    *,
    fail_on: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the check script with a fake uv executable.

    Args:
        tmp_path: Isolated directory for the fake executable and call log.
        fail_on: Optional exact uv argument string that should fail.

    Returns:
        Completed script process and the recorded uv calls.

    """
    binary_directory = tmp_path / "bin"
    _write_fake_uv(binary_directory)
    call_log = tmp_path / "uv-calls.log"
    caller_directory = tmp_path / "caller"
    caller_directory.mkdir()

    environment = os.environ.copy()
    environment["PATH"] = f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}"
    environment["WORKAHOLIC_TEST_UV_LOG"] = str(call_log)
    if fail_on is not None:
        environment["WORKAHOLIC_TEST_UV_FAIL_ON"] = fail_on

    result = subprocess.run(
        [str(_CHECK_SCRIPT)],
        check=False,
        cwd=caller_directory,
        env=environment,
        capture_output=True,
        text=True,
    )
    calls = (
        call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    )
    return result, calls


def test_check_script_runs_commit_and_pre_push_hooks_from_project_root(
    tmp_path: Path,
) -> None:
    """The aggregate command runs both hook stages from the repository root."""
    result, calls = _run_check_script(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert calls == [
        f"{_PROJECT_ROOT}|run --frozen pre-commit run --all-files",
        (
            f"{_PROJECT_ROOT}|run --frozen pre-commit run --all-files "
            "--hook-stage pre-push"
        ),
    ]


def test_check_script_stops_after_first_failed_stage(tmp_path: Path) -> None:
    """A failed commit-stage check prevents the pre-push stage from running."""
    failed_call = "run --frozen pre-commit run --all-files"
    result, calls = _run_check_script(tmp_path, fail_on=failed_call)

    assert result.returncode == 17
    assert calls == [f"{_PROJECT_ROOT}|{failed_call}"]
