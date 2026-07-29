"""Tests for the Workaholic AI CLI entry points."""

import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

import pytest

_EXPECTED_VERSION_OUTPUT = "workaholic 0.0.0\n"


def _run_command(
    command: Sequence[str],
    *,
    working_directory: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a CLI command without raising on a nonzero exit.

    Args:
        command: Executable and arguments to invoke.
        working_directory: Directory from which to run the process.

    Returns:
        The completed process with decoded stdout and stderr.
    """
    return subprocess.run(
        command,
        check=False,
        cwd=working_directory,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def console_script() -> str:
    """Return the console script installed in the active test environment."""
    script_name = "workaholic.exe" if sys.platform == "win32" else "workaholic"
    script = Path(sysconfig.get_path("scripts")) / script_name
    if not script.is_file():
        pytest.fail(f"The workaholic console script is not installed at {script}.")
    return str(script)


def test_console_script_reports_exact_version(
    console_script: str,
    tmp_path: Path,
) -> None:
    """The installed console script reports its exact distribution version."""
    result = _run_command(
        [console_script, "--version"],
        working_directory=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == _EXPECTED_VERSION_OUTPUT
    assert result.stderr == ""


def test_module_entry_point_reports_exact_version(tmp_path: Path) -> None:
    """The module entry point reports the same exact distribution version."""
    result = _run_command(
        [sys.executable, "-m", "workaholic", "--version"],
        working_directory=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == _EXPECTED_VERSION_OUTPUT
    assert result.stderr == ""


def test_console_script_without_command_prints_help(
    console_script: str,
    tmp_path: Path,
) -> None:
    """A commandless invocation prints help, does not prompt, and succeeds."""
    result = _run_command([console_script], working_directory=tmp_path)

    assert result.returncode == 0
    assert "Usage: workaholic [OPTIONS] COMMAND [ARGS]..." in result.stdout
    assert "Coordinate work between human operators" in result.stdout
    assert result.stderr == ""


def test_import_is_silent_and_does_not_modify_working_directory(
    tmp_path: Path,
) -> None:
    """Importing the package has no output or filesystem side effects."""
    result = _run_command(
        [sys.executable, "-c", "import workaholic"],
        working_directory=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
