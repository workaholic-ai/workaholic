"""Unit tests for ``workaholic status``."""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click import unstyle
from tests.golden import require_error, require_object, require_success
from tests.unit.cli.fakes import RecordingSession, SessionProviderSpy
from typer.testing import CliRunner, Result

from workaholic.application import ApplicationError, ApplicationErrorCode
from workaholic.cli.main import app as default_app
from workaholic.cli.main import create_app
from workaholic.cli.runtime import acquire_session
from workaholic.session import StatusRequest

_RUNNER = CliRunner()
_STATUS_ERRORS = (
    ApplicationErrorCode.CONTEXT_NOT_FOUND,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)


def _completed(result: Result) -> CompletedProcess[str]:
    """Convert one Typer result for shared golden assertions."""
    return CompletedProcess(
        args=("workaholic", "status"),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def test_status_json_emits_exact_contract_and_forwards_request() -> None:
    """Status JSON contains the selected local identity and authorization."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        ["status", "--json", "--non-interactive"],
        input=None,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    data = require_object(
        require_success(_completed(result)),
        context="status data",
    )
    assert data == {
        "mode": "embedded",
        "schema_version": 2,
        "instance": {"id": "ins_local"},
        "project": {"id": "prj_acme", "key": "ACME"},
        "subject": {
            "id": "sub_local",
            "kind": "human",
            "display_name": "Local operator",
            "is_instance_admin": True,
            "project_role": "owner",
        },
    }
    assert session.status_requests == [StatusRequest()]
    assert provider.call_count == 1


def test_status_human_output_is_deterministic() -> None:
    """Default status rendering gives one concise stable Human result."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        ["status"],
    )

    assert result.exit_code == 0
    assert result.stdout == "Local project ACME is ready.\n"
    assert result.stderr == ""


def test_create_app_rejects_non_callable_provider() -> None:
    """Application construction validates its explicit dependency boundary."""
    with pytest.raises(TypeError, match="provider must be callable"):
        create_app(None)  # type: ignore[arg-type]


def test_status_redacts_invalid_session_from_provider() -> None:
    """A provider contract breach becomes one safe operational failure."""

    def invalid_provider() -> object:
        """Return a deliberately invalid Session object."""
        return object()

    result = _RUNNER.invoke(
        create_app(invalid_provider),  # type: ignore[arg-type]
        ["status", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert result.stderr == ""


def test_acquire_session_rejects_non_callable_provider_directly() -> None:
    """The reusable runtime boundary validates direct callers as well."""
    with pytest.raises(TypeError, match="provider must be callable"):
        acquire_session(None)  # type: ignore[arg-type]


def test_unconfigured_factory_app_fails_safely_without_composition() -> None:
    """The bare CLI factory fixture fails safely without production composition."""
    result = _RUNNER.invoke(default_app, ["status", "--json", "--non-interactive"])

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert result.stderr == ""


@pytest.mark.parametrize("code", _STATUS_ERRORS)
def test_status_maps_every_documented_failure_to_json(
    code: ApplicationErrorCode,
) -> None:
    """The command preserves every documented status failure."""
    session = RecordingSession()
    session.failures["status"] = ApplicationError(
        code,
        f"Safe {code.value} message.",
    )

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["status", "--json", "--non-interactive"],
    )

    detail = require_error(_completed(result), expected_code=code.value)
    assert detail["message"] == f"Safe {code.value} message."
    assert result.stderr == ""


def test_status_help_and_non_interactive_are_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Help does not acquire a Session and non-interactive status never prompts."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)
    help_result = _RUNNER.invoke(create_app(provider), ["status", "--help"])

    assert help_result.exit_code == 0
    assert "--json" in unstyle(help_result.stdout)
    assert "--non-interactive" in unstyle(help_result.stdout)
    assert provider.call_count == 0

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("status --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    status_result = _RUNNER.invoke(
        create_app(provider),
        ["status", "--non-interactive"],
        input=None,
    )

    assert status_result.exit_code == 0
    assert provider.call_count == 1
