"""Tests for isolated built-wheel smoke installation."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_SMOKE_SCRIPT = _PROJECT_ROOT / "scripts" / "smoke-install.sh"
_EXPECTED_VERSION = "0.5.0a1"


def _write_executable(path: Path, source: str) -> None:
    """Write one executable test helper.

    Args:
        path: Destination path.
        source: Complete POSIX shell source.

    """
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_uv(binary_directory: Path, template_directory: Path) -> None:
    """Create a deterministic uv test double and installed executables.

    Args:
        binary_directory: Directory prepended to the subprocess PATH.
        template_directory: Directory holding executables copied into the fake
            virtual environment.

    """
    binary_directory.mkdir()
    template_directory.mkdir()
    _write_executable(
        template_directory / "python",
        """#!/bin/sh
set -eu
test "${1:-}" = "-c"
printf '%s\\n' "$WORKAHOLIC_TEST_INSTALLED_VERSION"
""",
    )
    _write_executable(
        template_directory / "workaholic",
        """#!/bin/sh
set -eu
test "${1:-}" = "--version"
printf '%s\\n' "$PWD" > "$WORKAHOLIC_TEST_CLI_CWD_LOG"
printf 'workaholic %s\\n' "$WORKAHOLIC_TEST_INSTALLED_VERSION"
""",
    )
    _write_executable(
        binary_directory / "uv",
        """#!/bin/sh
set -eu
printf '%s|%s\\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_UV_LOG"
case "${1:-}" in
  version)
    test "${2:-}" = "--short"
    printf '%s\\n' "$WORKAHOLIC_TEST_PROJECT_VERSION"
    ;;
  venv)
    for workaholic_last_argument in "$@"; do :; done
    mkdir -p "$workaholic_last_argument/bin"
    cp "$WORKAHOLIC_TEST_TEMPLATE_DIR/python" "$workaholic_last_argument/bin/python"
    cp \\
      "$WORKAHOLIC_TEST_TEMPLATE_DIR/workaholic" \\
      "$workaholic_last_argument/bin/workaholic"
    ;;
  pip)
    if [ "${WORKAHOLIC_TEST_INSTALL_FAIL:-0}" = "1" ]; then
      exit 17
    fi
    ;;
  *)
    exit 99
    ;;
esac
""",
    )


def _run_smoke_script(
    tmp_path: Path,
    arguments: list[str],
    *,
    installed_version: str = _EXPECTED_VERSION,
    install_fails: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], str | None, tuple[Path, ...]]:
    """Run the smoke script with deterministic uv and CLI boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.
        arguments: Arguments passed after the smoke-script path.
        installed_version: Version reported by the isolated fake installation.
        install_fails: Whether the fake installer exits with a failure.

    Returns:
        Process result, uv calls, CLI working directory, and remaining smoke
        temporary paths.

    """
    binary_directory = tmp_path / "bin"
    template_directory = tmp_path / "templates"
    smoke_temp_directory = tmp_path / "smoke-temp"
    caller_directory = tmp_path / "caller"
    smoke_temp_directory.mkdir()
    caller_directory.mkdir()
    _write_fake_uv(binary_directory, template_directory)

    uv_log = tmp_path / "uv.log"
    cli_cwd_log = tmp_path / "cli-cwd.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}",
            "TMPDIR": str(smoke_temp_directory),
            "WORKAHOLIC_TEST_CLI_CWD_LOG": str(cli_cwd_log),
            "WORKAHOLIC_TEST_INSTALLED_VERSION": installed_version,
            "WORKAHOLIC_TEST_PROJECT_VERSION": _EXPECTED_VERSION,
            "WORKAHOLIC_TEST_TEMPLATE_DIR": str(template_directory),
            "WORKAHOLIC_TEST_UV_LOG": str(uv_log),
        }
    )
    if install_fails:
        environment["WORKAHOLIC_TEST_INSTALL_FAIL"] = "1"

    result = subprocess.run(
        [str(_SMOKE_SCRIPT), *arguments],
        check=False,
        cwd=caller_directory,
        env=environment,
        capture_output=True,
        text=True,
    )
    uv_calls = (
        uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
    )
    cli_cwd = (
        cli_cwd_log.read_text(encoding="utf-8").strip()
        if cli_cwd_log.exists()
        else None
    )
    remaining_temp_paths = tuple(smoke_temp_directory.iterdir())
    return result, uv_calls, cli_cwd, remaining_temp_paths


def _wheel(tmp_path: Path, *, suffix: str = ".whl") -> Path:
    """Create an inert wheel-path fixture accepted by the fake installer.

    Args:
        tmp_path: Pytest-owned temporary directory.
        suffix: Filename suffix for boundary tests.

    Returns:
        Created fixture path.

    """
    wheel = tmp_path / f"workaholic_ai-{_EXPECTED_VERSION}-py3-none-any{suffix}"
    wheel.touch()
    return wheel


def test_smoke_script_installs_and_runs_wheel_outside_checkout(tmp_path: Path) -> None:
    """The success path verifies metadata and CLI output in isolation."""
    wheel = _wheel(tmp_path)

    result, uv_calls, cli_cwd, remaining = _run_smoke_script(
        tmp_path,
        [str(wheel)],
    )

    assert result.returncode == 0
    assert result.stdout == (
        f"Verified workaholic {_EXPECTED_VERSION} from an isolated wheel install.\n"
    )
    assert result.stderr == ""
    assert len(uv_calls) == 3
    assert uv_calls[0] == f"{_PROJECT_ROOT}|version --short"
    assert "|venv --no-project --python 3.14 " in uv_calls[1]
    assert "|pip install --python " in uv_calls[2]
    assert f" --strict {wheel}" in uv_calls[2]
    assert cli_cwd is not None
    assert Path(cli_cwd).name == "outside"
    assert not Path(cli_cwd).is_relative_to(_PROJECT_ROOT)
    assert remaining == ()


@pytest.mark.parametrize("arguments", [[], ["one.whl", "two.whl"]])
def test_smoke_script_requires_exactly_one_wheel_argument(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Missing or ambiguous wheel selection fails with usage status."""
    result, uv_calls, cli_cwd, remaining = _run_smoke_script(tmp_path, arguments)

    assert result.returncode == 64
    assert "usage: scripts/smoke-install.sh <wheel-path>" in result.stderr
    assert uv_calls == []
    assert cli_cwd is None
    assert remaining == ()


def test_smoke_script_rejects_a_missing_wheel(tmp_path: Path) -> None:
    """A nonexistent artifact fails before creating an environment."""
    missing_wheel = tmp_path / f"workaholic_ai-{_EXPECTED_VERSION}-py3-none-any.whl"

    result, uv_calls, cli_cwd, remaining = _run_smoke_script(
        tmp_path,
        [str(missing_wheel)],
    )

    assert result.returncode == 66
    assert f"wheel file does not exist: {missing_wheel}" in result.stderr
    assert uv_calls == []
    assert cli_cwd is None
    assert remaining == ()


def test_smoke_script_rejects_a_malformed_wheel_path(tmp_path: Path) -> None:
    """A non-wheel artifact fails before any project or environment access."""
    malformed_wheel = _wheel(tmp_path, suffix=".zip")

    result, uv_calls, cli_cwd, remaining = _run_smoke_script(
        tmp_path,
        [str(malformed_wheel)],
    )

    assert result.returncode == 65
    assert f"expected a .whl file: {malformed_wheel}" in result.stderr
    assert uv_calls == []
    assert cli_cwd is None
    assert remaining == ()


def test_smoke_script_propagates_invalid_wheel_install_failure(tmp_path: Path) -> None:
    """A corrupt wheel cannot be mistaken for a successful smoke check."""
    wheel = _wheel(tmp_path)

    result, uv_calls, cli_cwd, remaining = _run_smoke_script(
        tmp_path,
        [str(wheel)],
        install_fails=True,
    )

    assert result.returncode == 17
    assert len(uv_calls) == 3
    assert cli_cwd is None
    assert remaining == ()


def test_smoke_script_rejects_a_wrong_version_wheel(tmp_path: Path) -> None:
    """Installed metadata must match the source project's exact version."""
    wheel = _wheel(tmp_path)

    result, uv_calls, cli_cwd, remaining = _run_smoke_script(
        tmp_path,
        [str(wheel)],
        installed_version="9.9.9",
    )

    assert result.returncode == 65
    assert f"expected version {_EXPECTED_VERSION}, installed 9.9.9" in result.stderr
    assert len(uv_calls) == 3
    assert cli_cwd is None
    assert remaining == ()


def test_smoke_script_is_an_executable_posix_entry_point() -> None:
    """The packaged smoke script can run directly on supported CI hosts."""
    mode = _SMOKE_SCRIPT.stat().st_mode

    assert _SMOKE_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR
