"""Tests for the installed-wheel Phase 1 persistent-journey smoke."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_SMOKE_SCRIPT = _PROJECT_ROOT / "scripts" / "smoke-phase-1-wheel.sh"
_EXPECTED_VERSION = "0.2.0a1"


def _write_executable(path: Path, source: str) -> None:
    """Write one executable test helper.

    Args:
        path: Destination path.
        source: Complete POSIX shell source.

    """
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_boundaries(
    binary_directory: Path,
    template_directory: Path,
) -> None:
    """Create deterministic uv, Python, and installed-CLI boundaries.

    Args:
        binary_directory: Directory prepended to the subprocess PATH.
        template_directory: Directory containing installed executable templates.

    """
    binary_directory.mkdir()
    template_directory.mkdir()
    _write_executable(
        template_directory / "python",
        """#!/bin/sh
set -eu
exec "$WORKAHOLIC_TEST_REAL_PYTHON" "$@"
""",
    )
    _write_executable(
        template_directory / "workaholic",
        """#!/bin/sh
set -eu
printf '%s|%s|%s|%s|%s\n' \
  "$PWD" "$*" "$WORKAHOLIC_DATA_DIR" \
  "${PYTHONPATH:-}" "${VIRTUAL_ENV:-}" \
  >> "$WORKAHOLIC_TEST_CLI_LOG"
if [ "${WORKAHOLIC_TEST_CLI_FAIL_ON:-}" = "$*" ]; then
  exit 10
fi
up_command='up --project-key ACME --idempotency-key phase-one-up'
up_command="$up_command --json --non-interactive"
add_command='task add First persistent task'
add_command="$add_command --idempotency-key phase-one-task --json --non-interactive"
conflict_command='task add Conflicting task'
conflict_command="$conflict_command --idempotency-key phase-one-task"
conflict_command="$conflict_command --json --non-interactive"
task='{"uid":"tsk_phase1","project_id":"prj_phase1","number":1'
task="${task}"',"key":"ACME-1","title":"First persistent task"'
task="${task}"',"objective":"First persistent task","state":"open"'
task="${task}"',"priority":50,"version":1,"created_by":"sub_phase1"'
task="${task}"',"created_at":"2026-07-30T12:00:00Z"'
task="${task}"',"updated_at":"2026-07-30T12:00:00Z"}'
case "$*" in
  "$up_command")
    mkdir -p "$WORKAHOLIC_DATA_DIR"
    : > "$PWD/.workaholic.env"
    printf '%s%s%s%s\n' \
      '{"schema":"workaholic.cli/v1","ok":true,"data":{' \
      '"instance":{"id":"ins_phase1"},"project":{"id":"prj_phase1","key":"ACME"},' \
      '"subject":{"id":"sub_phase1"},"workspace":{"root":"workspace",' \
      '"context_file":".workaholic.env"}}}'
    ;;
  "$add_command")
    printf '{"schema":"workaholic.cli/v1","ok":true,"data":{"task":%s}}\n' "$task"
    ;;
  "task list --json --non-interactive")
    printf '%s%s%s\n' \
      '{"schema":"workaholic.cli/v1","ok":true,"data":{"tasks":[' \
      "$task" \
      '],"next_cursor":null}}'
    ;;
  "task show ACME-1 --json --non-interactive" | \
  "task show tsk_phase1 --json --non-interactive")
    printf '{"schema":"workaholic.cli/v1","ok":true,"data":{"task":%s}}\n' "$task"
    ;;
  "$conflict_command")
    printf '%s%s%s\n' \
      '{"schema":"workaholic.cli/v1","ok":false,"error":{' \
      '"code":"IDEMPOTENCY_CONFLICT","message":"Conflict.",' \
      '"retryable":false}}'
    exit 4
    ;;
  *)
    exit 99
    ;;
esac
""",
    )
    _write_executable(
        binary_directory / "uv",
        """#!/bin/sh
set -eu
printf '%s|%s\n' "$PWD" "$*" >> "$WORKAHOLIC_TEST_UV_LOG"
case "$*" in
  "${WORKAHOLIC_TEST_UV_FAIL_PREFIX:-}"*)
    if [ -n "${WORKAHOLIC_TEST_UV_FAIL_PREFIX:-}" ]; then
      exit 17
    fi
    ;;
esac
case "${1:-}" in
  version)
    test "${2:-}" = "--short"
    printf '%s\n' "$WORKAHOLIC_TEST_PROJECT_VERSION"
    ;;
  venv)
    for workaholic_last_argument in "$@"; do :; done
    mkdir -p "$workaholic_last_argument/bin"
    cp "$WORKAHOLIC_TEST_TEMPLATE_DIR/python" "$workaholic_last_argument/bin/python"
    cp \
      "$WORKAHOLIC_TEST_TEMPLATE_DIR/workaholic" \
      "$workaholic_last_argument/bin/workaholic"
    ;;
  pip)
    ;;
  *)
    exit 98
    ;;
esac
""",
    )


def _run_smoke(
    tmp_path: Path,
    arguments: list[str],
    *,
    uv_fail_prefix: str | None = None,
    cli_fail_on: str | None = None,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[str],
    list[str],
    tuple[Path, ...],
]:
    """Run the smoke through deterministic external boundaries.

    Args:
        tmp_path: Pytest-owned temporary directory.
        arguments: Arguments supplied after the smoke script path.
        uv_fail_prefix: Optional fake uv argument prefix to reject.
        cli_fail_on: Optional exact installed CLI argument string to reject.

    Returns:
        Process result, uv calls, CLI calls, and remaining temporary paths.

    """
    binary_directory = tmp_path / "bin"
    template_directory = tmp_path / "templates"
    temporary_directory = tmp_path / "temporary"
    caller_directory = tmp_path / "caller"
    tmp_path.mkdir(parents=True, exist_ok=True)
    temporary_directory.mkdir()
    caller_directory.mkdir()
    _write_fake_boundaries(binary_directory, template_directory)
    uv_log = tmp_path / "uv.log"
    cli_log = tmp_path / "cli.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{binary_directory}{os.pathsep}{environment.get('PATH', '')}",
            "PYTHONPATH": str(_PROJECT_ROOT / "src"),
            "TMPDIR": str(temporary_directory),
            "VIRTUAL_ENV": str(tmp_path / "inherited-environment"),
            "WORKAHOLIC_TEST_CLI_LOG": str(cli_log),
            "WORKAHOLIC_TEST_PROJECT_VERSION": _EXPECTED_VERSION,
            "WORKAHOLIC_TEST_REAL_PYTHON": sys.executable,
            "WORKAHOLIC_TEST_TEMPLATE_DIR": str(template_directory),
            "WORKAHOLIC_TEST_UV_LOG": str(uv_log),
        }
    )
    if uv_fail_prefix is not None:
        environment["WORKAHOLIC_TEST_UV_FAIL_PREFIX"] = uv_fail_prefix
    if cli_fail_on is not None:
        environment["WORKAHOLIC_TEST_CLI_FAIL_ON"] = cli_fail_on

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
    cli_calls = (
        cli_log.read_text(encoding="utf-8").splitlines() if cli_log.exists() else []
    )
    return result, uv_calls, cli_calls, tuple(temporary_directory.iterdir())


def _wheel(tmp_path: Path, *, suffix: str = ".whl") -> Path:
    """Create one inert wheel-path fixture.

    Args:
        tmp_path: Pytest-owned temporary directory.
        suffix: Candidate artifact suffix.

    Returns:
        Created fixture path.

    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / f"workaholic_ai-{_EXPECTED_VERSION}-py3-none-any{suffix}"
    wheel.touch()
    return wheel


def test_smoke_persists_one_task_across_isolated_cli_processes(
    tmp_path: Path,
) -> None:
    """The happy path validates persistence, replay, lookup, and isolation."""
    wheel = _wheel(tmp_path)

    result, uv_calls, cli_calls, remaining = _run_smoke(tmp_path, [str(wheel)])

    assert result.returncode == 0
    assert result.stdout == (
        f"Verified Phase 1 persistent Task journey from workaholic "
        f"{_EXPECTED_VERSION}.\n"
    )
    assert result.stderr == ""
    assert len(uv_calls) == 3
    assert uv_calls[0] == f"{_PROJECT_ROOT}|version --short"
    assert "|venv --no-project --python 3.14 " in uv_calls[1]
    assert "|pip install --python " in uv_calls[2]
    assert f" --strict {wheel}" in uv_calls[2]
    assert len(cli_calls) == 7
    workspaces = {call.partition("|")[0] for call in cli_calls}
    assert len(workspaces) == 1
    assert not next(iter(workspaces)).startswith(str(_PROJECT_ROOT))
    for call in cli_calls:
        fields = call.split("|")
        assert "workaholic-phase-one-wheel." in fields[2]
        assert fields[3:] == ["", ""]
    assert remaining == ()


@pytest.mark.parametrize("arguments", [[], ["one.whl", "two.whl"]])
def test_smoke_requires_exactly_one_wheel(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Missing or ambiguous wheel selection fails before external calls."""
    result, uv_calls, cli_calls, remaining = _run_smoke(tmp_path, arguments)

    assert result.returncode == 64
    assert "usage: scripts/smoke-phase-1-wheel.sh <wheel-path>" in result.stderr
    assert uv_calls == cli_calls == []
    assert remaining == ()


def test_smoke_rejects_missing_and_malformed_wheels(tmp_path: Path) -> None:
    """Wheel path validation happens before uv or temporary-state creation."""
    missing = _run_smoke(
        tmp_path / "missing",
        [str(tmp_path / "missing.whl")],
    )
    malformed_path = _wheel(tmp_path / "malformed", suffix=".zip")
    malformed = _run_smoke(
        tmp_path / "malformed-run",
        [str(malformed_path)],
    )

    assert missing[0].returncode == 66
    assert "wheel file does not exist" in missing[0].stderr
    assert malformed[0].returncode == 65
    assert "expected a .whl file" in malformed[0].stderr
    assert missing[1:3] == ([], [])
    assert malformed[1:3] == ([], [])
    assert missing[3] == malformed[3] == ()


def test_smoke_propagates_environment_and_cli_failures(tmp_path: Path) -> None:
    """A failed installer boundary or product command cannot report success."""
    wheel = _wheel(tmp_path)
    uv_failed = _run_smoke(
        tmp_path / "uv-failure",
        [str(wheel)],
        uv_fail_prefix="pip install --python ",
    )
    cli_failed = _run_smoke(
        tmp_path / "cli-failure",
        [str(wheel)],
        cli_fail_on=("task list --json --non-interactive"),
    )

    assert uv_failed[0].returncode != 0
    assert cli_failed[0].returncode == 10
    assert "Verified Phase 1" not in uv_failed[0].stdout
    assert "Verified Phase 1" not in cli_failed[0].stdout
    assert uv_failed[3] == cli_failed[3] == ()


def test_smoke_is_an_executable_posix_entry_point() -> None:
    """The wheel journey can run directly on supported acceptance hosts."""
    mode = _SMOKE_SCRIPT.stat().st_mode

    assert _SMOKE_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR
