"""Unit tests for ``workaholic project`` commands."""

from __future__ import annotations

import ast
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import (
    RecordingSession,
    SessionProviderSpy,
    project,
)
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import ProjectListRequest

_RUNNER = CliRunner()
_PROJECT_ERRORS = (
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)
_COMMAND_MODULES = ("up.py", "status.py", "project.py")
_FORBIDDEN_IMPORTS = (
    "workaholic.application",
    "workaholic.context",
    "workaholic.persistence",
    "workaholic.protocol",
    "workaholic.server",
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "project", "list"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_project_list_json_emits_exact_ordered_contract() -> None:
    """Project list preserves the Session's authoritative key ordering."""
    session = RecordingSession()
    session.projects_result = (
        project(),
        project(key="BETA", identifier="prj_beta"),
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["project", "list", "--json", "--non-interactive"],
        input=None,
    )

    data = require_object(
        require_success(_completed(result)),
        context="project-list data",
    )
    assert data == {
        "projects": [
            {"id": "prj_acme", "key": "ACME"},
            {"id": "prj_beta", "key": "BETA"},
        ]
    }
    assert result.stderr == ""
    assert session.project_list_requests == [ProjectListRequest()]
    assert provider.call_count == 1


def test_project_list_human_and_empty_results_are_deterministic() -> None:
    """Human output is stable for populated and empty authorized lists."""
    populated = RecordingSession()
    populated.projects_result = (
        project(),
        project(key="BETA", identifier="prj_beta"),
    )
    populated_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(populated)),
        ["project", "list"],
    )
    empty = RecordingSession()
    empty.projects_result = ()
    empty_result = _RUNNER.invoke(
        create_app(SessionProviderSpy(empty)),
        ["project", "list"],
    )

    assert populated_result.exit_code == 0
    assert populated_result.stdout == "ACME\tprj_acme\nBETA\tprj_beta\n"
    assert empty_result.exit_code == 0
    assert empty_result.stdout == "No projects.\n"


def test_project_list_empty_json_retains_required_array() -> None:
    """An empty result emits an explicit empty projects array."""
    session = RecordingSession()
    session.projects_result = ()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["project", "list", "--json"],
    )

    assert require_success(_completed(result)) == {"projects": []}


@pytest.mark.parametrize("code", _PROJECT_ERRORS)
def test_project_list_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented Project-list failure."""
    session = RecordingSession()
    session.failures["list_projects"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["project", "list", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_project_help_and_non_interactive_are_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group help does not acquire a Session and Project listing never prompts."""
    provider = SessionProviderSpy(RecordingSession())
    group_help = _RUNNER.invoke(create_app(provider), ["project", "--help"])
    command_help = _RUNNER.invoke(
        create_app(provider),
        ["project", "list", "--help"],
    )

    assert group_help.exit_code == 0
    assert "list" in unstyle(group_help.stdout)
    assert command_help.exit_code == 0
    assert "--json" in unstyle(command_help.stdout)
    assert "--non-interactive" in unstyle(command_help.stdout)
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("project list --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        ["project", "list", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
    assert provider.call_count == 1


def test_command_modules_depend_on_session_not_concrete_adapters() -> None:
    """Commands retain the required CLI-to-Session dependency boundary."""
    command_root = Path(__file__).parents[3] / "src" / "workaholic" / "cli"

    for filename in _COMMAND_MODULES:
        syntax = ast.parse(
            (command_root / filename).read_text(encoding="utf-8"),
            filename=filename,
        )
        imported: set[str] = set()
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imported
            for prefix in _FORBIDDEN_IMPORTS
        ), f"{filename} bypasses the Session boundary: {sorted(imported)}"
        assert "workaholic.session" in imported
