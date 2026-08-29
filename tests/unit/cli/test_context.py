"""Unit tests for ``workaholic context``."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import (
    RecordingSession,
    SessionProviderSpy,
    context_result,
    project,
)
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.session import ContextRequest

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = CliRunner()
_CONTEXT_ERRORS = (
    ApplicationErrorCode.PROFILE_NOT_FOUND,
    ApplicationErrorCode.PROFILE_INVALID,
    ApplicationErrorCode.PROFILE_UNSUPPORTED,
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PROJECT_NOT_FOUND,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "context"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_context_json_emits_exact_safe_contract_and_forwards_request(
    tmp_path: Path,
) -> None:
    """Context JSON contains only effective identity and documented safe paths."""
    workspace = (tmp_path / "Documentation Ω").resolve()
    workspace.mkdir()
    selected_project = project(
        key="DOCS",
        name="Documentation Ω",
        identifier="prj_docs",
    )
    session = RecordingSession()
    session.context_result = context_result(
        selected_project=selected_project,
        profile="team",
        workspace_root=workspace,
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "context",
            "--profile",
            "team",
            "--project",
            "DOCS",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    data = require_object(
        require_success(_completed(result)),
        context="effective-context data",
    )
    assert data == {
        "mode": "embedded",
        "profile": "team",
        "schema_version": 5,
        "instance": {"id": "ins_local"},
        "project": {
            "id": "prj_docs",
            "key": "DOCS",
            "name": "Documentation Ω",
        },
        "workspace_root": str(workspace),
        "subject": {
            "id": "sub_local",
            "kind": "human",
            "display_name": "Local operator",
            "is_instance_admin": True,
            "project_role": "owner",
        },
        "context_source": str(workspace / ".workaholic.env"),
    }
    assert result.stderr == ""
    assert session.context_requests == [ContextRequest(profile="team", project="DOCS")]
    assert provider.call_count == 1
    rendered = result.stdout
    assert "profiles.toml" not in rendered
    assert "database" not in rendered
    assert "credential" not in rendered


def test_context_explicit_project_without_workspace_uses_null_paths() -> None:
    """Explicit Project selection without discovered context remains diagnosable."""
    session = RecordingSession()
    session.context_result = context_result(workspace_root=None)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["context", "--project", "ACME", "--json"],
    )

    data = require_object(
        require_success(_completed(result)),
        context="effective-context data",
    )
    assert data["workspace_root"] is None
    assert data["context_source"] is None


def test_context_human_output_is_deterministic() -> None:
    """Human context output is concise, stable, and limited to safe fields."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["context"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Profile: local\nProject: ACME (ACME)\nWorkspace: /work/acme\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize("code", _CONTEXT_ERRORS)
def test_context_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """Context preserves documented failure codes and exit categories."""
    session = RecordingSession()
    session.failures["context"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["context", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_context_rejects_invalid_selector_before_session_acquisition() -> None:
    """Malformed selectors become stable input failures without opening state."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["context", "--profile", "../private", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Context input is invalid."
    assert provider.call_count == 0
    assert session.context_requests == []


def test_context_redacts_unexpected_private_diagnostics() -> None:
    """Unexpected profile contents and storage paths never reach either stream."""
    private_detail = "/private/team.sqlite: token = 'secret'"
    session = RecordingSession()
    session.failures["context"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["context", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert result.stderr == ""


def test_context_help_and_non_interactive_are_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Help opens no Session and complete automation invocations never prompt."""
    provider = SessionProviderSpy(RecordingSession())
    help_result = _RUNNER.invoke(create_app(provider), ["context", "--help"])
    output = unstyle(help_result.stdout)

    assert help_result.exit_code == 0
    assert "--profile" in output
    assert "--project" in output
    assert "--json" in output
    assert "--non-interactive" in output
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("context --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        ["context", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
    assert provider.call_count == 1
