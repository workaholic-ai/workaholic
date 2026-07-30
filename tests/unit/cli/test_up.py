"""Unit tests for ``workaholic up``."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import create_app
from workaholic.cli.serialization import workspace_data
from workaholic.session import UpRequest

if TYPE_CHECKING:
    from pathlib import Path

_RUNNER = CliRunner()
_UP_ERRORS = (
    ApplicationErrorCode.INVALID_INPUT,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.PROJECT_KEY_CONFLICT,
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "up"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_up_json_emits_exact_contract_and_forwards_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap JSON contains only public data and the exact request."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    application = create_app(provider)

    result = _RUNNER.invoke(
        application,
        [
            "up",
            "--project-key",
            "ACME",
            "--idempotency-key",
            "bootstrap-1",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    data = require_object(
        require_success(_completed(result)),
        context="up data",
    )
    assert data == {
        "instance": {"id": "ins_local"},
        "project": {"id": "prj_acme", "key": "ACME"},
        "subject": {
            "id": "sub_local",
            "kind": "human",
            "display_name": "Local operator",
            "is_instance_admin": True,
            "project_role": "owner",
        },
        "workspace": {
            "root": str(workspace.resolve()),
            "context_file": str(workspace.resolve() / ".workaholic.env"),
        },
    }
    assert session.up_requests == [
        UpRequest(
            project_key="ACME",
            idempotency_key="bootstrap-1",
        )
    ]
    assert provider.call_count == 1


def test_up_human_output_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default rendering gives one concise stable Human result."""
    workspace = tmp_path / "human-workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(
        create_app(provider),
        ["up", "--project-key", "ACME"],
    )

    assert result.exit_code == 0
    assert result.stdout == f"Project ACME is ready in {workspace.resolve()}.\n"
    assert result.stderr == ""


@pytest.mark.parametrize("code", _UP_ERRORS)
def test_up_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented bootstrap failure."""
    session = RecordingSession()
    session.failures["up"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["up", "--project-key", "ACME", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_up_redacts_unexpected_provider_failure() -> None:
    """Provider diagnostics never leak through the command boundary."""
    private_detail = "private provider diagnostic"

    def failing_provider() -> RecordingSession:
        """Raise one unexpected provider failure."""
        raise RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(failing_provider),
        ["up", "--project-key", "ACME", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
    assert private_detail not in result.stderr


@pytest.mark.parametrize(
    "idempotency_key",
    ["", "x" * 129],
)
def test_up_rejects_invalid_idempotency_key_before_session_acquisition(
    idempotency_key: str,
) -> None:
    """Request-model failures return INVALID_INPUT without invoking state."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "up",
            "--project-key",
            "ACME",
            "--idempotency-key",
            idempotency_key,
            "--json",
            "--non-interactive",
        ],
    )

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert detail["message"] == "Bootstrap input is invalid."
    assert provider.call_count == 0
    assert session.up_requests == []


def test_workspace_serialization_runtime_validates_current_directory() -> None:
    """Direct callers cannot provide an unvalidated current-directory value."""
    binding = RecordingSession().up_result.workspace

    with pytest.raises(TypeError, match="current directory must be a Path"):
        workspace_data(
            binding,
            current_directory=".",  # type: ignore[arg-type]
        )

    assert binding.workspace_root == "."


def test_up_help_is_complete_without_acquiring_a_session() -> None:
    """Help documents every public bootstrap option without side effects."""
    provider = SessionProviderSpy(RecordingSession())

    result = _RUNNER.invoke(create_app(provider), ["up", "--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--project-key" in output
    assert "[required]" in output
    assert "--idempotency-key" in output
    assert "--json" in output
    assert "--non-interactive" in output
    assert provider.call_count == 0


def test_up_non_interactive_never_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete non-interactive invocation never reads terminal input."""

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("up --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["up", "--project-key", "ACME", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
