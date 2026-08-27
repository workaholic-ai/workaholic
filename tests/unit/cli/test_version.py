"""Tests for the Workaholic AI CLI entry points."""

from __future__ import annotations

import importlib
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from typer.testing import CliRunner

from workaholic.cli.main import app as cli_app
from workaholic.cli.main import main as uncomposed_cli_main
from workaholic.composition import main as cli_entrypoint

if TYPE_CHECKING:
    from collections.abc import Sequence

_CLI_RUNNER = CliRunner()
_EXPECTED_VERSION_OUTPUT = "workaholic 0.4.0a1\n"


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


def test_cli_application_reports_exact_version_in_process() -> None:
    """The Typer application handles its eager version option in process."""
    result = _CLI_RUNNER.invoke(cli_app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == _EXPECTED_VERSION_OUTPUT


def test_module_entry_point_reports_exact_version(tmp_path: Path) -> None:
    """The module entry point reports the same exact distribution version."""
    result = _run_command(
        [sys.executable, "-m", "workaholic", "--version"],
        working_directory=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == _EXPECTED_VERSION_OUTPUT
    assert result.stderr == ""


def test_cli_application_without_command_prints_help_in_process() -> None:
    """The Typer application renders root help in process."""
    result = _CLI_RUNNER.invoke(cli_app)

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "Coordinate work between human operators" in result.stdout


def test_console_script_without_command_prints_help(
    console_script: str,
    tmp_path: Path,
) -> None:
    """A commandless invocation prints help, does not prompt, and succeeds."""
    result = _run_command([console_script], working_directory=tmp_path)
    output = unstyle(result.stdout)

    assert result.returncode == 0
    assert "Usage: workaholic [OPTIONS] COMMAND [ARGS]..." in output
    assert "Coordinate work between human operators" in output
    assert result.stderr == ""


def test_public_main_invokes_the_named_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public composition entry point invokes Typer with the stable name."""
    program_names: list[str] = []

    def fake_app(*, prog_name: str) -> None:
        """Record the program name passed across the CLI boundary."""
        program_names.append(prog_name)

    def fake_create_app(_provider: object) -> object:
        """Return the recording command application."""
        return fake_app

    composition = importlib.import_module("workaholic.composition")
    monkeypatch.setattr(composition, "create_app", fake_create_app)

    cli_entrypoint()

    assert program_names == ["workaholic"]


def test_uncomposed_cli_main_retains_named_factory_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The internal CLI factory entry remains a stable named wrapper."""
    program_names: list[str] = []

    def fake_app(*, prog_name: str) -> None:
        """Record the program name passed to the bare factory app."""
        program_names.append(prog_name)

    cli_module = importlib.import_module("workaholic.cli.main")
    monkeypatch.setattr(cli_module, "app", fake_app)

    uncomposed_cli_main()

    assert program_names == ["workaholic"]


def test_module_package_is_importable_without_running_the_cli() -> None:
    """Importing the module entry point does not execute its main guard."""
    module = importlib.import_module("workaholic.__main__")

    assert module is not None


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
