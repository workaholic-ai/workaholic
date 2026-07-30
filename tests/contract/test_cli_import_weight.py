"""Fresh-process checks for the normal CLI import boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_PROJECT_ROOT = Path(__file__).parents[2]
_HEAVY_CLI_FIXTURE = _PROJECT_ROOT / "tests" / "contract" / "fixtures" / "heavy_cli"
_EXTERNAL_SERVER_PREFIXES = (
    "apscheduler",
    "asyncpg",
    "celery",
    "fastapi",
    "pg8000",
    "psycopg",
    "psycopg2",
    "psycopg_pool",
    "starlette",
    "uvicorn",
)
_PRODUCTION_FORBIDDEN_PREFIXES = (
    *_EXTERNAL_SERVER_PREFIXES,
    "workaholic.client",
    "workaholic.protocol",
    "workaholic.server",
)


def _probe_imported_modules(
    module_name: str,
    *,
    forbidden_prefixes: tuple[str, ...],
    cwd: Path,
    additional_import_path: Path | None = None,
) -> tuple[str, ...]:
    """Import one module in isolation and return forbidden loaded modules.

    Args:
        module_name: Module imported as the normal startup path.
        forbidden_prefixes: Module names or package prefixes prohibited at
            startup.
        cwd: Working directory outside the package under test.
        additional_import_path: Optional isolated fixture path prepended inside
            the child process.

    Returns:
        Sorted forbidden module names present after the import.

    Raises:
        AssertionError: If the child process or its JSON response is invalid.

    """
    encoded_module = json.dumps(module_name)
    encoded_prefixes = json.dumps(forbidden_prefixes)
    encoded_path = json.dumps(
        None if additional_import_path is None else str(additional_import_path)
    )
    probe = """
import importlib
import json
import sys

sys.dont_write_bytecode = True
additional_path = json.loads(sys.argv[1])
if additional_path is not None:
    sys.path.insert(0, additional_path)
module_name = json.loads(sys.argv[2])
prefixes = tuple(json.loads(sys.argv[3]))
importlib.import_module(module_name)
violations = sorted(
    loaded
    for loaded in sys.modules
    if any(loaded == prefix or loaded.startswith(prefix + ".") for prefix in prefixes)
)
print(json.dumps(violations, separators=(",", ":")))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            probe,
            encoded_path,
            encoded_module,
            encoded_prefixes,
        ],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            f"Isolated import probe for {module_name} exited with "
            f"status {result.returncode}."
        )
        raise AssertionError(message)

    try:
        decoded: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        message = f"Isolated import probe for {module_name} returned invalid JSON."
        raise AssertionError(message) from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        message = f"Isolated import probe for {module_name} returned invalid modules."
        raise AssertionError(message)
    return tuple(decoded)


def _assert_import_is_light(
    module_name: str,
    *,
    forbidden_prefixes: tuple[str, ...],
    cwd: Path,
    additional_import_path: Path | None = None,
) -> None:
    """Assert that a startup module does not eagerly load server dependencies.

    Args:
        module_name: Module representing a supported startup path.
        forbidden_prefixes: Module names or package prefixes prohibited at
            startup.
        cwd: Working directory outside the package under test.
        additional_import_path: Optional isolated fixture path.

    Raises:
        AssertionError: If the importer eagerly loads prohibited modules.

    """
    violations = _probe_imported_modules(
        module_name,
        forbidden_prefixes=forbidden_prefixes,
        cwd=cwd,
        additional_import_path=additional_import_path,
    )
    if violations:
        imported_modules = ", ".join(violations)
        message = (
            f"{module_name} eagerly imported prohibited module(s): {imported_modules}"
        )
        raise AssertionError(message)


@pytest.mark.parametrize(
    "module_name",
    [
        "workaholic.cli",
        "workaholic.cli.main",
        "workaholic.composition",
        "workaholic.__main__",
    ],
)
def test_normal_cli_import_does_not_load_server_or_postgres_dependencies(
    module_name: str,
    tmp_path: Path,
) -> None:
    """Normal CLI imports remain independent of server-only implementation."""
    _assert_import_is_light(
        module_name,
        forbidden_prefixes=_PRODUCTION_FORBIDDEN_PREFIXES,
        cwd=tmp_path,
    )


def test_import_weight_probe_identifies_importer_and_loaded_modules(
    tmp_path: Path,
) -> None:
    """The isolated heavy sample proves the startup detector can fail."""
    forbidden_prefixes = (
        *_EXTERNAL_SERVER_PREFIXES,
        "heavy_sample.server",
    )

    with pytest.raises(
        AssertionError,
        match=(
            r"heavy_sample\.cli\.main eagerly imported prohibited module\(s\): "
            r".*fastapi.*heavy_sample\.server.*psycopg"
        ),
    ):
        _assert_import_is_light(
            "heavy_sample.cli.main",
            forbidden_prefixes=forbidden_prefixes,
            cwd=tmp_path,
            additional_import_path=_HEAVY_CLI_FIXTURE,
        )
