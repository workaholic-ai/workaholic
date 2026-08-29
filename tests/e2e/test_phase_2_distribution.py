"""End-to-end acceptance for the Phase 2 source and wheel distribution."""

from __future__ import annotations

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
    from collections.abc import Iterator, Mapping, Sequence

_PROJECT_ROOT = Path(__file__).parents[2]
_COMMAND_TIMEOUT_SECONDS = 900
_GATE_ENVIRONMENT_KEYS = (
    "WORKAHOLIC_PHASE_0_GATE_RUNNING",
    "WORKAHOLIC_PHASE_1_GATE_RUNNING",
    "WORKAHOLIC_PHASE_2_GATE_RUNNING",
    "WORKAHOLIC_PHASE_3_GATE_RUNNING",
    "WORKAHOLIC_PHASE_4_GATE_RUNNING",
)

pytestmark = [
    pytest.mark.distribution,
    pytest.mark.e2e,
    pytest.mark.requires_network,
    pytest.mark.requires_uv,
    pytest.mark.skipif(
        any(os.environ.get(key) == "1" for key in _GATE_ENVIRONMENT_KEYS),
        reason="The outer clean-state test owns Phase 2 gate recursion.",
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


def _clean_environment(tmp_path: Path) -> dict[str, str]:
    """Return an environment isolated from operator and developer state.

    Args:
        tmp_path: Pytest-owned directory for external tool caches.

    Returns:
        Complete clean environment mapping.

    """
    environment = os.environ.copy()
    for variable in (
        "CONDA_PREFIX",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_DATA_DIR",
        "WORKAHOLIC_PROFILE",
        *_GATE_ENVIRONMENT_KEYS,
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "NO_COLOR": "1",
            "PRE_COMMIT_HOME": str(tmp_path.parent / "phase-two-pre-commit"),
            "UV_CACHE_DIR": str(tmp_path.parent / "phase-two-uv"),
            "UV_LINK_MODE": "copy",
        }
    )
    return environment


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
    return clone


@pytest.fixture
def phase_two_clone(tmp_path: Path) -> Iterator[Path]:
    """Yield and eagerly remove one clean clone to bound acceptance disk use.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Yields:
        Detached clone of the exact committed revision under test.

    """
    clone = _clone_current_revision(tmp_path)
    try:
        yield clone
    finally:
        # The exact clone is test-owned and may contain a large local environment.
        shutil.rmtree(clone, ignore_errors=True)


def _run_gate(
    clone: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the public Phase 2 acceptance command.

    Args:
        clone: Temporary repository root.
        environment: Clean child environment.

    Returns:
        Completed gate process.

    """
    return _run(
        [str(clone / "scripts" / "verify-phase-2.sh")],
        cwd=clone,
        environment=environment,
    )


def _commit_failing_test(clone: Path) -> None:
    """Commit one intentional negative test to the isolated clone.

    Args:
        clone: Temporary repository root.

    """
    fixture_path = clone / "tests" / "test_phase_two_injected_failure.py"
    fixture_path.write_text(
        '"""Intentional Phase 2 gate failure fixture."""\n\n\n'
        "def test_injected_phase_two_failure() -> None:\n"
        '    """Prove that the gate propagates pytest failure."""\n'
        '    raise AssertionError("injected Phase 2 failure")\n',
        encoding="utf-8",
    )
    for command in (
        ["git", "config", "user.name", "Phase 2 Test"],
        ["git", "config", "user.email", "phase-two-test@example.invalid"],
        ["git", "add", str(fixture_path.relative_to(clone))],
        ["git", "commit", "--quiet", "-m", "Add Phase 2 negative fixture"],
    ):
        result = _run(command, cwd=clone)
        _require_success(result, context=f"preparing fixture with {' '.join(command)}")


def _write_profiles(
    config_directory: Path,
    *,
    profiles: Mapping[str, Path],
) -> None:
    """Write one strict test-owned embedded profile registry.

    Args:
        config_directory: Destination configuration directory.
        profiles: Profile names mapped to distinct absolute data directories.

    Raises:
        ValueError: If the required local profile or unique paths are absent.

    """
    if "local" not in profiles or len(set(profiles.values())) != len(profiles):
        message = "Phase 2 profiles require distinct paths and a local default."
        raise ValueError(message)
    config_directory.mkdir(parents=True)
    lines = ["version = 1", 'default_profile = "local"']
    for name, data_directory in profiles.items():
        lines.extend(
            (
                "",
                f"[profiles.{name}]",
                'mode = "embedded"',
                f"data_directory = {json.dumps(str(data_directory))}",
            )
        )
    (config_directory / "profiles.toml").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _cli_environment(
    base_environment: Mapping[str, str],
    *,
    config_directory: Path,
    fallback_data_directory: Path,
) -> dict[str, str]:
    """Pin one CLI process to explicit test-owned state.

    Args:
        base_environment: Clean complete environment.
        config_directory: Absolute trusted configuration directory.
        fallback_data_directory: Absolute fallback local data directory.

    Returns:
        Complete child environment.

    """
    environment = dict(base_environment)
    environment["WORKAHOLIC_CONFIG_DIR"] = str(config_directory)
    environment["WORKAHOLIC_DATA_DIR"] = str(fallback_data_directory)
    return environment


def _run_cli(
    executable: Path,
    arguments: Sequence[str],
    *,
    workspace: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run one fresh CLI process with the stable JSON transport.

    Args:
        executable: Exact source or installed-wheel command.
        arguments: Public CLI arguments after the executable.
        workspace: Exact process working directory.
        environment: Complete pinned environment.

    Returns:
        Completed CLI process.

    """
    return _run(
        [str(executable), *arguments, "--json", "--non-interactive"],
        cwd=workspace,
        environment=environment,
    )


def _require_json_success(
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    """Decode one exact successful CLI envelope.

    Args:
        result: CLI process result.

    Returns:
        Successful data object.

    """
    _require_success(result, context="running the Phase 2 CLI")
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    assert set(envelope) == {"schema", "ok", "data"}
    assert envelope["schema"] == "workaholic.cli/v1"
    assert envelope["ok"] is True
    data = envelope["data"]
    assert isinstance(data, dict)
    return data


def _require_json_error(
    result: subprocess.CompletedProcess[str],
    *,
    status: int,
    code: str,
) -> None:
    """Require one stable safe CLI error boundary.

    Args:
        result: CLI process result.
        status: Expected process status.
        code: Expected application error code.

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


def _task_keys(payload: Mapping[str, object]) -> list[str]:
    """Return ordered Task keys from a validated list payload.

    Args:
        payload: Task-list success data.

    Returns:
        Ordered human Task keys.

    """
    tasks = payload["tasks"]
    assert isinstance(tasks, list)
    keys: list[str] = []
    for task in tasks:
        assert isinstance(task, dict)
        key = task["key"]
        assert isinstance(key, str)
        keys.append(key)
    return keys


def _run_multi_project_journey(
    executable: Path,
    *,
    root: Path,
    base_environment: Mapping[str, str],
) -> dict[str, object]:
    """Run and summarize the same observable journey for one distribution.

    Args:
        executable: Exact source or installed-wheel CLI path.
        root: Test-owned configuration, data, and Workspace root.
        base_environment: Clean complete process environment.

    Returns:
        Stable summary with generated identities and timestamps removed.

    """
    config_directory = root / "config"
    _write_profiles(
        config_directory,
        profiles={
            "local": root / "local-data",
            "isolated": root / "isolated-data",
        },
    )
    environment = _cli_environment(
        base_environment,
        config_directory=config_directory,
        fallback_data_directory=root / "fallback-data",
    )
    acme_workspace = root / "workspaces" / "acme"
    docs_workspace = root / "workspaces" / "docs"
    mirror_workspace = root / "workspaces" / "docs-mirror"
    unbound_workspace = root / "workspaces" / "unbound"
    acme_deep = acme_workspace / "src" / "service"
    docs_deep = docs_workspace / "guides" / "draft"
    mirror_deep = mirror_workspace / "notes" / "review"
    for workspace in (acme_deep, docs_deep, mirror_deep, unbound_workspace):
        workspace.mkdir(parents=True)

    up = _require_json_success(
        _run_cli(
            executable,
            [
                "up",
                "--project-key",
                "ACME",
                "--project-name",
                "Acme delivery",
            ],
            workspace=acme_workspace,
            environment=environment,
        )
    )
    docs = _require_json_success(
        _run_cli(
            executable,
            ["project", "create", "--key", "DOCS", "--name", "Documentation"],
            workspace=acme_deep,
            environment=environment,
        )
    )
    for workspace in (docs_workspace, mirror_workspace):
        _require_json_success(
            _run_cli(
                executable,
                ["project", "bind", "DOCS", str(workspace)],
                workspace=acme_deep,
                environment=environment,
            )
        )
    acme_task = _require_json_success(
        _run_cli(
            executable,
            ["task", "add", "Acme implementation"],
            workspace=acme_deep,
            environment=environment,
        )
    )
    docs_task = _require_json_success(
        _run_cli(
            executable,
            ["task", "add", "Documentation draft"],
            workspace=docs_deep,
            environment=environment,
        )
    )
    acme_list = _require_json_success(
        _run_cli(
            executable,
            ["task", "list"],
            workspace=acme_deep,
            environment=environment,
        )
    )
    docs_list = _require_json_success(
        _run_cli(
            executable,
            ["task", "list"],
            workspace=mirror_deep,
            environment=environment,
        )
    )
    all_list = _require_json_success(
        _run_cli(
            executable,
            ["task", "list", "--all-projects"],
            workspace=unbound_workspace,
            environment=environment,
        )
    )
    restarted_context = _require_json_success(
        _run_cli(
            executable,
            ["context"],
            workspace=docs_deep,
            environment=environment,
        )
    )

    acme_project = up["project"]
    docs_project = docs["project"]
    assert isinstance(acme_project, dict)
    assert isinstance(docs_project, dict)
    acme_task_object = acme_task["task"]
    docs_task_object = docs_task["task"]
    assert isinstance(acme_task_object, dict)
    assert isinstance(docs_task_object, dict)
    return {
        "projects": [
            (acme_project["key"], acme_project["name"]),
            (docs_project["key"], docs_project["name"]),
        ],
        "created_tasks": [
            (
                acme_task_object["number"],
                acme_task_object["key"],
                acme_task_object["title"],
            ),
            (
                docs_task_object["number"],
                docs_task_object["key"],
                docs_task_object["title"],
            ),
        ],
        "acme_list": _task_keys(acme_list),
        "docs_list": _task_keys(docs_list),
        "all_list": _task_keys(all_list),
        "next_cursor": all_list["next_cursor"],
        "restarted_project": restarted_context["project"]["key"],  # type: ignore[index]
        "schema_version": restarted_context["schema_version"],
    }


def _sync_and_build(
    clone: Path,
    environment: Mapping[str, str],
) -> Path:
    """Synchronize one source clone and return its single wheel.

    Args:
        clone: Fresh detached checkout.
        environment: Clean child environment.

    Returns:
        Built wheel artifact.

    """
    sync = _run(["uv", "sync", "--frozen"], cwd=clone, environment=environment)
    _require_success(sync, context="synchronizing the source clone")
    build = _run(["uv", "build"], cwd=clone, environment=environment)
    _require_success(build, context="building the source clone")
    wheels = tuple((clone / "dist").glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _install_wheel(
    wheel: Path,
    *,
    root: Path,
    environment: Mapping[str, str],
) -> Path:
    """Install one wheel into an isolated test-owned environment.

    Args:
        wheel: Exact built wheel.
        root: Destination environment root.
        environment: Clean child environment.

    Returns:
        Installed Workaholic executable path.

    """
    create = _run(
        ["uv", "venv", "--no-project", "--python", "3.14", str(root)],
        cwd=wheel.parent,
        environment=environment,
    )
    _require_success(create, context="creating the installed-wheel environment")
    install = _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(root / "bin" / "python"),
            "--strict",
            str(wheel),
        ],
        cwd=wheel.parent,
        environment=environment,
    )
    _require_success(install, context="installing the Phase 2 wheel")
    return root / "bin" / "workaholic"


def test_phase_two_gate_is_clean_fail_fast_and_self_contained(
    phase_two_clone: Path,
    tmp_path: Path,
) -> None:
    """The aggregate gate passes cleanly and rejects every contaminated start."""
    environment = _clean_environment(tmp_path)
    passed = _run_gate(phase_two_clone, environment)
    _require_success(passed, context="Phase 2 clean-state gate")
    assert "Verified Phase 2 multi-Project journey" in passed.stdout
    assert passed.stdout.endswith("Phase 2 clean-state acceptance gate passed.\n")

    shutil.rmtree(phase_two_clone / ".venv")
    shutil.rmtree(phase_two_clone / "dist")

    active_environment = dict(environment)
    active_environment["VIRTUAL_ENV"] = str(tmp_path / "outside-venv")
    active = _run_gate(phase_two_clone, active_environment)
    assert active.returncode == 69
    assert "deactivate the active virtual environment" in active.stderr

    for selector, path in (
        ("WORKAHOLIC_CONFIG_DIR", tmp_path / "outside-config"),
        ("WORKAHOLIC_DATA_DIR", tmp_path / "outside-data"),
    ):
        outside_environment = dict(environment)
        outside_environment[selector] = str(path)
        rejected = _run_gate(phase_two_clone, outside_environment)
        assert rejected.returncode == 69
        assert f"unset {selector}" in rejected.stderr

    (phase_two_clone / "dist").mkdir()
    prebuilt = _run_gate(phase_two_clone, environment)
    assert prebuilt.returncode == 69
    assert "remove pre-existing dist" in prebuilt.stderr
    shutil.rmtree(phase_two_clone / "dist")

    readme = phase_two_clone / "README.md"
    original_readme = readme.read_bytes()
    readme.write_bytes(original_readme + b"\n")
    dirty = _run_gate(phase_two_clone, environment)
    assert dirty.returncode == 69
    assert "verification requires a clean Git worktree" in dirty.stderr
    assert "README.md" in dirty.stderr
    readme.write_bytes(original_readme)

    _commit_failing_test(phase_two_clone)
    failing = _run_gate(phase_two_clone, environment)
    assert failing.returncode != 0
    assert "injected Phase 2 failure" in failing.stdout + failing.stderr
    assert not (phase_two_clone / "dist").exists()


def test_source_and_wheel_match_and_reject_phase_two_boundaries(  # noqa: PLR0915
    phase_two_clone: Path,
    tmp_path: Path,
) -> None:
    """Source and wheel agree while all Phase 2 trust boundaries fail closed."""
    environment = _clean_environment(tmp_path)
    wheel = _sync_and_build(phase_two_clone, environment)
    source_command = phase_two_clone / ".venv" / "bin" / "workaholic"
    installed_command = _install_wheel(
        wheel,
        root=tmp_path / "wheel-venv",
        environment=environment,
    )
    source_summary = _run_multi_project_journey(
        source_command,
        root=tmp_path / "source-journey",
        base_environment=environment,
    )
    wheel_summary = _run_multi_project_journey(
        installed_command,
        root=tmp_path / "wheel-journey",
        base_environment=environment,
    )
    assert (
        source_summary
        == wheel_summary
        == {
            "projects": [("ACME", "Acme delivery"), ("DOCS", "Documentation")],
            "created_tasks": [
                (1, "ACME-1", "Acme implementation"),
                (1, "DOCS-1", "Documentation draft"),
            ],
            "acme_list": ["ACME-1"],
            "docs_list": ["DOCS-1"],
            "all_list": ["ACME-1", "DOCS-1"],
            "next_cursor": None,
            "restarted_project": "DOCS",
            "schema_version": 5,
        }
    )

    smoke = _run(
        [str(phase_two_clone / "scripts" / "smoke-phase-2-wheel.sh"), str(wheel)],
        cwd=phase_two_clone,
        environment=environment,
    )
    _require_success(smoke, context="running the installed Phase 2 smoke")
    assert "Verified Phase 2 multi-Project journey" in smoke.stdout

    malformed_config = tmp_path / "malformed-config"
    malformed_config.mkdir()
    (malformed_config / "profiles.toml").write_text(
        "not valid TOML = [",
        encoding="utf-8",
    )
    malformed_environment = _cli_environment(
        environment,
        config_directory=malformed_config,
        fallback_data_directory=tmp_path / "malformed-fallback",
    )
    _require_json_error(
        _run_cli(
            source_command,
            ["project", "list"],
            workspace=tmp_path,
            environment=malformed_environment,
        ),
        status=3,
        code="PROFILE_INVALID",
    )

    remote_config = tmp_path / "remote-config"
    remote_config.mkdir()
    (remote_config / "profiles.toml").write_text(
        "\n".join(
            (
                "version = 1",
                'default_profile = "local"',
                "[profiles.local]",
                'mode = "remote"',
                f"data_directory = {json.dumps(str(tmp_path / 'remote-data'))}",
                "",
            )
        ),
        encoding="utf-8",
    )
    remote_environment = _cli_environment(
        environment,
        config_directory=remote_config,
        fallback_data_directory=tmp_path / "remote-fallback",
    )
    _require_json_error(
        _run_cli(
            source_command,
            ["project", "list"],
            workspace=tmp_path,
            environment=remote_environment,
        ),
        status=3,
        code="PROFILE_UNSUPPORTED",
    )

    boundary_root = tmp_path / "boundaries"
    config_directory = boundary_root / "config"
    local_data = boundary_root / "local-data"
    isolated_data = boundary_root / "isolated-data"
    _write_profiles(
        config_directory,
        profiles={"local": local_data, "isolated": isolated_data},
    )
    boundary_environment = _cli_environment(
        environment,
        config_directory=config_directory,
        fallback_data_directory=boundary_root / "fallback-data",
    )
    local_workspace = boundary_root / "local-workspace"
    local_deep = local_workspace / "src" / "service"
    isolated_workspace = boundary_root / "isolated-workspace"
    local_deep.mkdir(parents=True)
    isolated_workspace.mkdir(parents=True)
    _require_json_success(
        _run_cli(
            source_command,
            ["up", "--project-key", "ACME"],
            workspace=local_workspace,
            environment=boundary_environment,
        )
    )
    for title in ("First", "Second"):
        _require_json_success(
            _run_cli(
                source_command,
                ["task", "add", title],
                workspace=local_deep,
                environment=boundary_environment,
            )
        )

    first_page = _require_json_success(
        _run_cli(
            source_command,
            ["task", "list", "--all-projects", "--limit", "1"],
            workspace=tmp_path,
            environment=boundary_environment,
        )
    )
    cursor = first_page["next_cursor"]
    assert isinstance(cursor, str)
    _require_json_error(
        _run_cli(
            source_command,
            ["task", "list", "--project", "ACME", "--cursor", cursor],
            workspace=tmp_path,
            environment=boundary_environment,
        ),
        status=2,
        code="INVALID_INPUT",
    )

    hostile_workspace = local_deep / "hostile"
    hostile_workspace.mkdir()
    hostile_context = hostile_workspace / ".workaholic.env"
    hostile_context.write_text("this is not context\n", encoding="utf-8")
    hostile_before = hostile_context.read_bytes()
    _require_json_error(
        _run_cli(
            source_command,
            ["status"],
            workspace=hostile_workspace,
            environment=boundary_environment,
        ),
        status=3,
        code="CONTEXT_INVALID",
    )
    assert hostile_context.read_bytes() == hostile_before

    _require_json_success(
        _run_cli(
            source_command,
            ["up", "--project-key", "OTHER", "--profile", "isolated"],
            workspace=isolated_workspace,
            environment=boundary_environment,
        )
    )
    local_context_before = (local_workspace / ".workaholic.env").read_bytes()
    _require_json_error(
        _run_cli(
            source_command,
            [
                "project",
                "bind",
                "OTHER",
                str(local_workspace),
                "--profile",
                "isolated",
            ],
            workspace=isolated_workspace,
            environment=boundary_environment,
        ),
        status=4,
        code="WORKSPACE_BINDING_CONFLICT",
    )
    assert (local_workspace / ".workaholic.env").read_bytes() == local_context_before

    database = local_data / "local.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE store_metadata SET schema_version = 1")
        connection.commit()
    _require_json_error(
        _run_cli(
            source_command,
            ["status"],
            workspace=local_workspace,
            environment=boundary_environment,
        ),
        status=10,
        code="SCHEMA_UNSUPPORTED",
    )

    corrupt_wheel = tmp_path / wheel.name
    corrupt_wheel.write_text("not a wheel archive\n", encoding="utf-8")
    malformed_wheel = _run(
        [
            str(phase_two_clone / "scripts" / "smoke-phase-2-wheel.sh"),
            str(corrupt_wheel),
        ],
        cwd=phase_two_clone,
        environment=environment,
    )
    assert malformed_wheel.returncode != 0
    assert "Verified Phase 2" not in malformed_wheel.stdout
