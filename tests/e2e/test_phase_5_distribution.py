"""End-to-end acceptance for the Phase 5 source and wheel distribution."""

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
    "WORKAHOLIC_PHASE_5_GATE_RUNNING",
)
_EXPECTED_SUMMARY = {
    "audit_event_types": [
        "instance_bootstrapped",
        "project_created",
        "project_grant_assigned",
        "subject_created",
        "subject_disabled",
        "token_issued",
        "token_revoked",
    ],
    "cross_attempt_denied": True,
    "cross_project_denied": True,
    "disabled_subject_denied": True,
    "human_attempt_id": None,
    "roles": ["viewer", "agent", "operator", "owner"],
    "schema_version": 5,
    "token_revocation_denied": True,
}
_FUTURE_GOLDEN_REASONS = (
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
        reason="The outer clean-state test owns Phase 5 gate recursion.",
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
        command: Complete argument vector.
        cwd: Child working directory.
        environment: Optional complete environment.

    Returns:
        Completed process with captured text output.

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
    """Require a successful external acceptance command.

    Args:
        result: Completed process.
        context: Safe operation label for assertion output.

    """
    assert result.returncode == 0, (
        f"{context} failed with status {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _clean_environment(tmp_path: Path) -> dict[str, str]:
    """Build an environment isolated from operator identity and state.

    Args:
        tmp_path: Pytest-owned cache parent.

    Returns:
        Complete sanitized environment.

    """
    environment = os.environ.copy()
    for variable in (
        "CONDA_PREFIX",
        "COVERAGE_FILE",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_CREDENTIAL_BACKEND",
        "WORKAHOLIC_DATA_DIR",
        "WORKAHOLIC_PROFILE",
        "WORKAHOLIC_TOKEN",
        "WORKAHOLIC_TOKEN_FILE",
        *_GATE_ENVIRONMENT_KEYS,
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "NO_COLOR": "1",
            "PRE_COMMIT_HOME": str(tmp_path.parent / "phase-five-pre-commit"),
            "UV_CACHE_DIR": str(tmp_path.parent / "phase-five-uv"),
            "UV_LINK_MODE": "copy",
        }
    )
    return environment


def _clone_current_revision(tmp_path: Path) -> Path:
    """Clone the exact committed revision without local object sharing.

    Args:
        tmp_path: Pytest-owned root.

    Returns:
        Detached clean checkout.

    """
    revision_result = _run(["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT)
    _require_success(revision_result, context="reading source revision")
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
    _require_success(clone_result, context="creating clean clone")
    checkout = _run(
        ["git", "checkout", "--detach", "--quiet", revision],
        cwd=clone,
    )
    _require_success(checkout, context="checking out source revision")
    return clone


@pytest.fixture
def phase_five_clone(tmp_path: Path) -> Iterator[Path]:
    """Yield and eagerly remove one clean Phase 5 checkout.

    Args:
        tmp_path: Pytest-owned root.

    Yields:
        Detached clone of the committed revision under test.

    """
    clone = _clone_current_revision(tmp_path)
    try:
        yield clone
    finally:
        # The clone is test-owned and may contain a large local environment.
        shutil.rmtree(clone, ignore_errors=True)


def _run_gate(
    clone: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the public Phase 5 aggregate command.

    Args:
        clone: Clean temporary checkout.
        environment: Sanitized child environment.

    Returns:
        Completed gate process.

    """
    return _run(
        [str(clone / "scripts" / "verify-phase-5.sh")],
        cwd=clone,
        environment=environment,
    )


def _summary(output: str) -> dict[str, object]:
    """Extract the unique installed-wheel summary object.

    Args:
        output: Aggregate gate standard output.

    Returns:
        Decoded summary.

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


def test_phase_five_gate_passes_from_clean_committed_clone(
    phase_five_clone: Path,
    tmp_path: Path,
) -> None:
    """The complete authenticated source-to-wheel gate is reproducible."""
    result = _run_gate(phase_five_clone, _clean_environment(tmp_path))

    _require_success(result, context="Phase 5 clean-state gate")
    for step in range(1, 7):
        assert f"[{step}/6]" in result.stdout
    assert _summary(result.stdout) == _EXPECTED_SUMMARY
    assert "Verified Phase 5 identity and authorization from workaholic 0.5.0a1." in (
        result.stdout
    )
    for reason in _FUTURE_GOLDEN_REASONS:
        assert reason in result.stdout
    assert result.stdout.endswith("Phase 5 clean-state acceptance gate passed.\n")


@pytest.mark.parametrize(
    ("selector", "value"),
    [
        ("VIRTUAL_ENV", "/operator/venv"),
        ("WORKAHOLIC_CONFIG_DIR", "/operator/config"),
        ("WORKAHOLIC_CREDENTIAL_BACKEND", "keyring"),
        ("WORKAHOLIC_DATA_DIR", "/operator/data"),
        ("WORKAHOLIC_PROFILE", "operator"),
        ("WORKAHOLIC_TOKEN", "private-token"),
        ("WORKAHOLIC_TOKEN_FILE", "/operator/token"),
    ],
)
def test_phase_five_gate_rejects_external_identity_state(
    phase_five_clone: Path,
    tmp_path: Path,
    selector: str,
    value: str,
) -> None:
    """Caller identity and storage selectors fail before environment creation."""
    environment = _clean_environment(tmp_path)
    environment[selector] = value

    result = _run_gate(phase_five_clone, environment)

    assert result.returncode == 69
    assert "phase-five:" in result.stderr
    assert not (phase_five_clone / ".venv").exists()
    assert not (phase_five_clone / "dist").exists()


def test_phase_five_gate_rejects_dirty_and_prebuilt_checkout(
    phase_five_clone: Path,
    tmp_path: Path,
) -> None:
    """Dirty tracked state and pre-existing output cannot enter acceptance."""
    environment = _clean_environment(tmp_path)
    (phase_five_clone / "dist").mkdir()
    prebuilt = _run_gate(phase_five_clone, environment)
    assert prebuilt.returncode == 69
    assert "remove pre-existing dist" in prebuilt.stderr
    shutil.rmtree(phase_five_clone / "dist")

    readme = phase_five_clone / "README.md"
    readme.write_bytes(readme.read_bytes() + b"\n")
    dirty = _run_gate(phase_five_clone, environment)
    assert dirty.returncode == 69
    assert "verification requires a clean Git worktree" in dirty.stderr
    assert "README.md" in dirty.stderr


def test_phase_five_wheel_smoke_rejects_malformed_wheel(tmp_path: Path) -> None:
    """A wheel suffix cannot bypass installer validation."""
    malformed = tmp_path / "workaholic_ai-0.5.0a1-py3-none-any.whl"
    malformed.write_bytes(b"not a wheel archive")

    result = _run(
        [str(_PROJECT_ROOT / "scripts" / "smoke-phase-5-wheel.sh"), str(malformed)],
        cwd=_PROJECT_ROOT,
        environment=_clean_environment(tmp_path),
    )

    assert result.returncode != 0
    assert "Verified Phase 5 identity and authorization" not in result.stdout
