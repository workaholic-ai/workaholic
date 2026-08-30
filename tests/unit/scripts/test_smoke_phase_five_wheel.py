"""Tests for the installed-wheel Phase 5 identity smoke boundary."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[3]
_SMOKE_SCRIPT = _PROJECT_ROOT / "scripts" / "smoke-phase-5-wheel.sh"


def _run(tmp_path: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the smoke script with a test-owned temporary parent.

    Args:
        tmp_path: Pytest-owned root.
        arguments: Arguments supplied after the script path.

    Returns:
        Completed smoke process.

    """
    temporary = tmp_path / "temporary"
    temporary.mkdir(parents=True)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(temporary)
    return subprocess.run(
        [str(_SMOKE_SCRIPT), *arguments],
        check=False,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


def _wheel(tmp_path: Path, *, suffix: str = ".whl") -> Path:
    """Create one inert artifact path.

    Args:
        tmp_path: Fixture directory.
        suffix: Candidate artifact suffix.

    Returns:
        Created path.

    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"workaholic_ai-0.5.0a1-py3-none-any{suffix}"
    path.touch()
    return path


@pytest.mark.parametrize("arguments", [[], ["one.whl", "two.whl"]])
def test_smoke_requires_exactly_one_wheel(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Missing or ambiguous wheel selection fails before temporary state."""
    result = _run(tmp_path, arguments)

    assert result.returncode == 64
    assert "usage: scripts/smoke-phase-5-wheel.sh <wheel-path>" in result.stderr
    assert tuple((tmp_path / "temporary").iterdir()) == ()


def test_smoke_rejects_missing_and_nonwheel_artifacts(tmp_path: Path) -> None:
    """Artifact validation precedes environment creation and installation."""
    missing_root = tmp_path / "missing"
    malformed_root = tmp_path / "malformed-run"
    missing = _run(missing_root, [str(tmp_path / "absent.whl")])
    malformed = _run(
        malformed_root,
        [str(_wheel(tmp_path / "malformed", suffix=".zip"))],
    )

    assert missing.returncode == 66
    assert "wheel file does not exist" in missing.stderr
    assert malformed.returncode == 65
    assert "expected a .whl file" in malformed.stderr
    assert tuple((missing_root / "temporary").iterdir()) == ()
    assert tuple((malformed_root / "temporary").iterdir()) == ()


def test_smoke_declares_complete_phase_five_exit_contract() -> None:
    """The script owns state and proves every Phase 5 acceptance boundary."""
    source = _SMOKE_SCRIPT.read_text(encoding="utf-8")

    for contract in (
        "WORKAHOLIC_CONFIG_DIR",
        "WORKAHOLIC_CREDENTIAL_BACKEND=file",
        "WORKAHOLIC_DATA_DIR",
        "WORKAHOLIC_TOKEN_FILE",
        "unset \\\n  PYTHONHOME",
        "create-human",
        "create-agent",
        "project-viewer",
        "project-operator",
        '"viewer"',
        '"agent"',
        '"operator"',
        '"owner"',
        "cross_attempt_denied",
        "cross_project_denied",
        "PERMISSION_DENIED",
        "LEASE_LOST",
        "AUTHENTICATION_FAILED",
        "SCHEMA_UNSUPPORTED",
        '"INSERT INTO store_metadata VALUES (1, 4)"',
        "schema version 4 store changed after rejection",
        "token_revocation_denied",
        "disabled_subject_denied",
        "administrative audit is incomplete",
        "actor_token_id",
        "token_hash",
        "raw_token",
    ):
        assert contract in source


def test_smoke_uses_only_installed_cli_for_product_operations() -> None:
    """The wheel journey never imports source or private package modules."""
    source = _SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "\nfrom workaholic" not in source
    assert "\nimport workaholic" not in source
    assert "phase_five_command=$phase_five_environment/bin/workaholic" in source
    assert "subprocess.run(" in source
    assert '[str(command), *arguments, "--json", "--non-interactive"]' in source
    assert '"PYTHONNOUSERSITE": "1"' in source


def test_smoke_is_an_executable_posix_entry_point() -> None:
    """The wheel journey runs directly on supported acceptance hosts."""
    source = _SMOKE_SCRIPT.read_text(encoding="utf-8")
    mode = _SMOKE_SCRIPT.stat().st_mode

    assert source.startswith("#!/bin/sh\n")
    assert mode & stat.S_IXUSR
