"""End-to-end acceptance for the Phase 3 source and wheel distribution."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

_PROJECT_ROOT = Path(__file__).parents[2]
_COMMAND_TIMEOUT_SECONDS = 1200
_GATE_ENVIRONMENT_KEYS = (
    "WORKAHOLIC_PHASE_0_GATE_RUNNING",
    "WORKAHOLIC_PHASE_1_GATE_RUNNING",
    "WORKAHOLIC_PHASE_2_GATE_RUNNING",
    "WORKAHOLIC_PHASE_3_GATE_RUNNING",
    "WORKAHOLIC_PHASE_4_GATE_RUNNING",
)
_EXPECTED_WHEEL_SUMMARY = {
    "approved_version": 5,
    "errors": [
        "VERSION_CONFLICT",
        "DEPENDENCY_CYCLE",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_TRANSITION",
        "UNSATISFIABLE_DEPENDENCY",
        "RESULT_INVALID",
        "SCHEMA_UNSUPPORTED",
    ],
    "human_attempt_id": None,
    "prerequisite_version": 4,
    "ready_after_prerequisite": ["ACME-2"],
    "reviewed_events": [
        "task_created",
        "task_updated",
        "task_updated",
        "result_submitted",
        "review_approved",
        "task_completed",
    ],
    "schema_version": 4,
}
_FUTURE_GOLDEN_REASONS = (
    "Phase 4: missing agent claims, leases, heartbeats, and result submission.",
    "Phase 6: missing authenticated server, RemoteSession, and shared-team workflow.",
    "Phase 7: missing JSON, SQLite, and PostgreSQL adapter conformance.",
    "Phase 9: missing release-candidate publication and clean uvx acceptance.",
)

pytestmark = [
    pytest.mark.distribution,
    pytest.mark.e2e,
    pytest.mark.requires_network,
    pytest.mark.requires_uv,
    pytest.mark.skipif(
        any(os.environ.get(key) == "1" for key in _GATE_ENVIRONMENT_KEYS),
        reason="The outer clean-state test owns Phase 3 gate recursion.",
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
        tmp_path: Pytest-owned directory for external caches.

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
            "PRE_COMMIT_HOME": str(tmp_path.parent / "phase-three-pre-commit"),
            "UV_CACHE_DIR": str(tmp_path.parent / "phase-three-uv"),
            "UV_LINK_MODE": "copy",
        }
    )
    return environment


def _clone_current_revision(tmp_path: Path) -> Path:
    """Clone the exact committed revision without local object sharing.

    Args:
        tmp_path: Pytest-owned temporary directory.

    Returns:
        Detached clean checkout of the tested revision.

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
def phase_three_clone(tmp_path: Path) -> Iterator[Path]:
    """Yield and eagerly remove one Phase 3 clean clone.

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
    """Run the public Phase 3 acceptance command.

    Args:
        clone: Temporary repository root.
        environment: Clean child environment.

    Returns:
        Completed gate process.

    """
    return _run(
        [str(clone / "scripts" / "verify-phase-3.sh")],
        cwd=clone,
        environment=environment,
    )


def _wheel_summary(output: str) -> dict[str, object]:
    """Extract the single canonical wheel-journey summary.

    Args:
        output: Aggregate gate standard output.

    Returns:
        Decoded summary object.

    Raises:
        AssertionError: If the output has no unique JSON summary line.

    """
    candidates = [
        line
        for line in output.splitlines()
        if line.startswith("{") and line.endswith("}")
    ]
    assert len(candidates) == 1, candidates
    value = json.loads(candidates[0])
    assert isinstance(value, dict)
    return value


def test_phase_three_gate_passes_from_a_clean_committed_clone(
    phase_three_clone: Path,
    tmp_path: Path,
) -> None:
    """The aggregate source and installed-wheel exit gate is reproducible."""
    environment = _clean_environment(tmp_path)
    result = _run_gate(phase_three_clone, environment)

    _require_success(result, context="Phase 3 clean-state gate")
    for step in range(1, 7):
        assert f"[{step}/6]" in result.stdout
    assert _wheel_summary(result.stdout) == _EXPECTED_WHEEL_SUMMARY
    assert "Verified Phase 3 Human lifecycle from workaholic 0.4.0a1." in (
        result.stdout
    )
    for reason in _FUTURE_GOLDEN_REASONS:
        assert reason in result.stdout
    assert result.stdout.endswith("Phase 3 clean-state acceptance gate passed.\n")


def test_phase_three_gate_rejects_external_state_before_execution(
    phase_three_clone: Path,
    tmp_path: Path,
) -> None:
    """Caller-owned state cannot redirect or contaminate the acceptance run."""
    environment = _clean_environment(tmp_path)
    outside_config = tmp_path / "outside-config"
    outside_data = tmp_path / "outside-data"
    outside_config.mkdir()
    outside_data.mkdir()
    config_sentinel = outside_config / "profiles.toml"
    data_sentinel = outside_data / "local.db"
    config_sentinel.write_text("operator configuration\n", encoding="utf-8")
    data_sentinel.write_bytes(b"operator data")

    for selector, value in (
        ("VIRTUAL_ENV", str(tmp_path / "operator-venv")),
        ("WORKAHOLIC_CONFIG_DIR", str(outside_config)),
        ("WORKAHOLIC_DATA_DIR", str(outside_data)),
        ("WORKAHOLIC_PROFILE", "operator"),
    ):
        contaminated = dict(environment)
        contaminated[selector] = value
        result = _run_gate(phase_three_clone, contaminated)
        assert result.returncode == 69
        assert "phase-three:" in result.stderr
        assert not (phase_three_clone / ".venv").exists()
        assert not (phase_three_clone / "dist").exists()

    (phase_three_clone / "dist").mkdir()
    prebuilt = _run_gate(phase_three_clone, environment)
    assert prebuilt.returncode == 69
    assert "remove pre-existing dist" in prebuilt.stderr
    shutil.rmtree(phase_three_clone / "dist")

    readme = phase_three_clone / "README.md"
    original_readme = readme.read_bytes()
    readme.write_bytes(original_readme + b"\n")
    dirty = _run_gate(phase_three_clone, environment)
    assert dirty.returncode == 69
    assert "verification requires a clean Git worktree" in dirty.stderr
    assert "README.md" in dirty.stderr

    assert config_sentinel.read_text(encoding="utf-8") == "operator configuration\n"
    assert data_sentinel.read_bytes() == b"operator data"


def test_phase_three_wheel_smoke_rejects_a_malformed_wheel(tmp_path: Path) -> None:
    """A file with a wheel suffix cannot cross the installation boundary."""
    malformed_wheel = tmp_path / "workaholic_ai-0.4.0a1-py3-none-any.whl"
    malformed_wheel.write_bytes(b"not a wheel archive")
    environment = _clean_environment(tmp_path)

    result = _run(
        [
            str(_PROJECT_ROOT / "scripts" / "smoke-phase-3-wheel.sh"),
            str(malformed_wheel),
        ],
        cwd=_PROJECT_ROOT,
        environment=environment,
    )

    assert result.returncode != 0
    assert "Verified Phase 3 Human lifecycle" not in result.stdout
