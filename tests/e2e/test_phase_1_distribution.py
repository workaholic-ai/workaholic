"""End-to-end acceptance for the Phase 1 source and wheel distribution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_PROJECT_ROOT = Path(__file__).parents[2]
_COMMAND_TIMEOUT_SECONDS = 600
_GATE_ENVIRONMENT_KEYS = (
    "WORKAHOLIC_PHASE_0_GATE_RUNNING",
    "WORKAHOLIC_PHASE_1_GATE_RUNNING",
)
_QUICK_START_PATTERN = re.compile(
    r"## Quick start\n.*?```bash\n(?P<commands>.*?)\n```",
    flags=re.DOTALL,
)
_EXPECTED_QUICK_START_COMMANDS = (
    "uv sync --frozen",
    'export WORKAHOLIC_DATA_DIR="$(mktemp -d '
    '"${TMPDIR:-/tmp}/workaholic-quickstart.XXXXXX")"',
    "uv run workaholic up --project-key ACME",
    'uv run workaholic task add "First persistent task"',
    "uv run workaholic task list",
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_network,
    pytest.mark.requires_uv,
    pytest.mark.skipif(
        any(os.environ.get(key) == "1" for key in _GATE_ENVIRONMENT_KEYS),
        reason="The outer clean-state test owns Phase 1 gate recursion.",
    ),
]


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded external acceptance command.

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
    revision_result = _run(["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT)
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
        tmp_path: Pytest-owned directory for external tool caches and data.

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
        *_GATE_ENVIRONMENT_KEYS,
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "NO_COLOR": "1",
            "PRE_COMMIT_HOME": str(tmp_path.parent / "phase-one-pre-commit"),
            "UV_CACHE_DIR": str(tmp_path.parent / "phase-one-uv"),
            "UV_LINK_MODE": "copy",
            "WORKAHOLIC_DATA_DIR": str(tmp_path / "workaholic-data"),
        }
    )
    return environment


def _run_gate(
    clone: Path,
    tmp_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the public Phase 1 acceptance command.

    Args:
        clone: Temporary repository root.
        tmp_path: Pytest-owned directory for external tool state.
        environment: Optional environment override.

    Returns:
        Completed gate process.

    """
    child_environment = (
        dict(environment) if environment is not None else _clean_environment(tmp_path)
    )
    return _run(
        [str(clone / "scripts" / "verify-phase-1.sh")],
        cwd=clone,
        environment=child_environment,
    )


def _commit_fixture(clone: Path, relative_path: str, source: str) -> None:
    """Commit one intentional negative fixture to a clean clone.

    Args:
        clone: Temporary repository root.
        relative_path: Repository-relative fixture path.
        source: Complete UTF-8 fixture contents.

    """
    fixture_path = clone / relative_path
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(source, encoding="utf-8")
    for command in (
        ["git", "config", "user.name", "Phase 1 Test"],
        ["git", "config", "user.email", "phase-one-test@example.invalid"],
        ["git", "add", relative_path],
        ["git", "commit", "--quiet", "-m", "Add Phase 1 negative fixture"],
    ):
        result = _run(command, cwd=clone)
        _require_success(result, context=f"preparing fixture with {' '.join(command)}")


def _run_cli(
    clone: Path,
    arguments: Sequence[str],
    *,
    workspace: Path,
    data_directory: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the source CLI in a fresh process against explicit local state.

    Args:
        clone: Synced source checkout.
        arguments: Public CLI arguments after the executable.
        workspace: Exact current Workspace.
        data_directory: Test-owned local data directory.
        environment: Base clean environment.

    Returns:
        Completed CLI process.

    """
    child_environment = dict(environment)
    child_environment["WORKAHOLIC_DATA_DIR"] = str(data_directory)
    return _run(
        [
            str(clone / ".venv" / "bin" / "workaholic"),
            *arguments,
            "--json",
            "--non-interactive",
        ],
        cwd=workspace,
        environment=child_environment,
    )


def _require_json_error(
    result: subprocess.CompletedProcess[str],
    *,
    status: int,
    code: str,
) -> dict[str, object]:
    """Require one exact safe JSON error category.

    Args:
        result: CLI result to validate.
        status: Expected process status.
        code: Expected machine-readable application code.

    Returns:
        Decoded error detail mapping.

    """
    assert result.returncode == status
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"schema", "ok", "error"}
    assert envelope["schema"] == "workaholic.cli/v1"
    assert envelope["ok"] is False
    detail = envelope["error"]
    assert isinstance(detail, dict)
    assert detail["code"] == code
    assert detail["retryable"] is False
    return detail


def _require_json_success(
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    """Require one exact successful CLI envelope.

    Args:
        result: CLI result to validate.

    Returns:
        Decoded data mapping.

    """
    _require_success(result, context="running the source CLI")
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"schema", "ok", "data"}
    assert envelope["schema"] == "workaholic.cli/v1"
    assert envelope["ok"] is True
    data = envelope["data"]
    assert isinstance(data, dict)
    return data


def _sync_clone(clone: Path, environment: Mapping[str, str]) -> None:
    """Create the locked source environment in one clone.

    Args:
        clone: Fresh checkout to synchronize.
        environment: Clean child environment.

    """
    result = _run(
        ["uv", "sync", "--frozen"],
        cwd=clone,
        environment=environment,
    )
    _require_success(result, context="synchronizing the clean clone")


def _build_clone(clone: Path, environment: Mapping[str, str]) -> Path:
    """Build and return the clone's single wheel artifact.

    Args:
        clone: Synced checkout to build.
        environment: Clean child environment.

    Returns:
        Exact wheel artifact path.

    """
    result = _run(["uv", "build"], cwd=clone, environment=environment)
    _require_success(result, context="building the clean clone")
    wheels = tuple((clone / "dist").glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _file_digest(path: Path) -> str:
    """Return a stable digest for one file.

    Args:
        path: Existing file to read.

    Returns:
        SHA-256 hexadecimal digest.

    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase_one_gate_passes_from_a_fresh_clone(tmp_path: Path) -> None:
    """The aggregate command proves source and installed persistent journeys."""
    clone = _clone_current_revision(tmp_path)

    result = _run_gate(clone, tmp_path)

    _require_success(result, context="Phase 1 clean-state gate")
    assert "Verified Phase 1 persistent Task journey" in result.stdout
    assert result.stdout.endswith("Phase 1 clean-state acceptance gate passed.\n")


def test_readme_quick_start_passes_independently_in_a_fresh_clone(
    tmp_path: Path,
) -> None:
    """Every public quick-start command succeeds without the gate wrapper."""
    clone = _clone_current_revision(tmp_path)
    readme = (clone / "README.md").read_text(encoding="utf-8")
    quick_start_match = _QUICK_START_PATTERN.search(readme)
    assert quick_start_match is not None
    commands = tuple(quick_start_match.group("commands").splitlines())
    assert commands == _EXPECTED_QUICK_START_COMMANDS

    environment = _clean_environment(tmp_path)
    environment["WORKAHOLIC_PHASE_1_GATE_RUNNING"] = "1"
    environment["TMPDIR"] = str(tmp_path)
    result = _run(
        ["/bin/sh", "-eu", "-c", "\n".join(commands)],
        cwd=clone,
        environment=environment,
    )
    _require_success(result, context="README quick-start shell block")

    assert "ACME-1" in result.stdout
    assert "First persistent task" in result.stdout
    status_result = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=clone,
    )
    _require_success(status_result, context="checking README journey cleanliness")
    assert status_result.stdout == ""


def test_source_distribution_rejects_corrupt_boundaries_without_mutation(
    tmp_path: Path,
) -> None:
    """Malformed context, schema mismatch, and idempotency conflict fail closed."""
    clone = _clone_current_revision(tmp_path)
    environment = _clean_environment(tmp_path)
    environment["WORKAHOLIC_PHASE_1_GATE_RUNNING"] = "1"
    _sync_clone(clone, environment)

    malformed_workspace = tmp_path / "malformed-workspace"
    malformed_workspace.mkdir()
    malformed_context = malformed_workspace / ".workaholic.env"
    malformed_context.write_text("this is not context\n", encoding="utf-8")
    malformed_before = malformed_context.read_bytes()
    malformed = _run_cli(
        clone,
        ["status"],
        workspace=malformed_workspace,
        data_directory=tmp_path / "malformed-data",
        environment=environment,
    )
    _require_json_error(malformed, status=3, code="CONTEXT_INVALID")
    assert malformed_context.read_bytes() == malformed_before
    assert not (tmp_path / "malformed-data").exists()

    schema_workspace = tmp_path / "schema-workspace"
    schema_workspace.mkdir()
    schema_data = tmp_path / "schema-data"
    _require_json_success(
        _run_cli(
            clone,
            ["up", "--project-key", "SCHEMA"],
            workspace=schema_workspace,
            data_directory=schema_data,
            environment=environment,
        )
    )
    database = schema_data / "local.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE store_metadata SET schema_version = 2")
        connection.commit()
    schema_before = _file_digest(database)
    unsupported = _run_cli(
        clone,
        ["status"],
        workspace=schema_workspace,
        data_directory=schema_data,
        environment=environment,
    )
    _require_json_error(unsupported, status=10, code="SCHEMA_UNSUPPORTED")
    assert _file_digest(database) == schema_before

    conflict_workspace = tmp_path / "conflict-workspace"
    conflict_workspace.mkdir()
    conflict_data = tmp_path / "conflict-data"
    _require_json_success(
        _run_cli(
            clone,
            ["up", "--project-key", "CONFLICT"],
            workspace=conflict_workspace,
            data_directory=conflict_data,
            environment=environment,
        )
    )
    first = _run_cli(
        clone,
        ["task", "add", "Original", "--idempotency-key", "same-request"],
        workspace=conflict_workspace,
        data_directory=conflict_data,
        environment=environment,
    )
    first_task = _require_json_success(first)["task"]
    conflict = _run_cli(
        clone,
        ["task", "add", "Changed", "--idempotency-key", "same-request"],
        workspace=conflict_workspace,
        data_directory=conflict_data,
        environment=environment,
    )
    _require_json_error(conflict, status=4, code="IDEMPOTENCY_CONFLICT")
    listed = _require_json_success(
        _run_cli(
            clone,
            ["task", "list"],
            workspace=conflict_workspace,
            data_directory=conflict_data,
            environment=environment,
        )
    )
    assert listed == {"tasks": [first_task], "next_cursor": None}


def test_phase_one_gate_rejects_nonclean_starting_state(tmp_path: Path) -> None:
    """Active environments, build output, and dirty files invalidate evidence."""
    clone = _clone_current_revision(tmp_path)
    environment = _clean_environment(tmp_path)

    active_environment = dict(environment)
    active_environment["VIRTUAL_ENV"] = str(tmp_path / "active")
    active = _run_gate(
        clone,
        tmp_path,
        environment=active_environment,
    )
    assert active.returncode == 69
    assert "deactivate the active virtual environment" in active.stderr

    (clone / "dist").mkdir()
    prebuilt = _run_gate(clone, tmp_path, environment=environment)
    assert prebuilt.returncode == 69
    assert "remove pre-existing dist" in prebuilt.stderr
    shutil.rmtree(clone / "dist")

    (clone / "untracked.txt").write_text("local state\n", encoding="utf-8")
    dirty = _run_gate(clone, tmp_path, environment=environment)
    assert dirty.returncode == 69
    assert "verification requires a clean Git worktree" in dirty.stderr
    assert "untracked.txt" in dirty.stderr


def test_phase_one_gate_rejects_a_failing_test(tmp_path: Path) -> None:
    """A committed test failure stops the gate before artifact construction."""
    clone = _clone_current_revision(tmp_path)
    _commit_fixture(
        clone,
        "tests/test_phase_one_injected_failure.py",
        '"""Intentional Phase 1 gate failure fixture."""\n\n\n'
        "def test_injected_phase_one_failure() -> None:\n"
        '    """Prove that the gate propagates pytest failure."""\n'
        '    raise AssertionError("injected Phase 1 failure")\n',
    )

    result = _run_gate(clone, tmp_path)

    assert result.returncode != 0
    assert "injected Phase 1 failure" in result.stdout + result.stderr
    assert not (clone / "dist").exists()


def test_phase_one_wheel_smoke_rejects_a_malformed_wheel(tmp_path: Path) -> None:
    """The installed journey rejects a corrupt artifact before product use."""
    clone = _clone_current_revision(tmp_path)
    environment = _clean_environment(tmp_path)
    environment["WORKAHOLIC_PHASE_1_GATE_RUNNING"] = "1"
    _sync_clone(clone, environment)
    wheel = _build_clone(clone, environment)
    wheel.write_text("not a wheel archive\n", encoding="utf-8")

    result = _run(
        [str(clone / "scripts" / "smoke-phase-1-wheel.sh"), str(wheel)],
        cwd=clone,
        environment=environment,
    )

    assert result.returncode != 0
    assert "Verified Phase 1" not in result.stdout


def test_phase_one_wheel_never_uses_or_writes_source_checkout(
    tmp_path: Path,
) -> None:
    """Installed execution ignores hostile source-path inheritance."""
    clone = _clone_current_revision(tmp_path)
    environment = _clean_environment(tmp_path)
    environment["WORKAHOLIC_PHASE_1_GATE_RUNNING"] = "1"
    _sync_clone(clone, environment)
    wheel = _build_clone(clone, environment)
    source_init = clone / "src" / "workaholic" / "__init__.py"
    poison = b'raise RuntimeError("source checkout imported")\n'
    source_init.write_bytes(poison)
    environment["PYTHONPATH"] = str(clone / "src")
    environment["VIRTUAL_ENV"] = str(clone / ".venv")

    result = _run(
        [str(clone / "scripts" / "smoke-phase-1-wheel.sh"), str(wheel)],
        cwd=clone,
        environment=environment,
    )

    _require_success(result, context="running the isolated installed journey")
    assert "Verified Phase 1 persistent Task journey" in result.stdout
    assert source_init.read_bytes() == poison
    assert not (clone / ".workaholic.env").exists()
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=clone,
    )
    _require_success(status, context="checking installed-journey source writes")
    assert status.stdout == " M src/workaholic/__init__.py\n"
