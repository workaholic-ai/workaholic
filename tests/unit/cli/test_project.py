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
    context_result,
    grant,
    project,
)
from typer.testing import CliRunner, Result

from workaholic.application import (
    ApplicationError,
    ApplicationErrorCode,
    ProjectCreationResult,
)
from workaholic.cli.main import create_app
from workaholic.session import (
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
)

_RUNNER = CliRunner()
_PROJECT_ERRORS = (
    ApplicationErrorCode.PROFILE_NOT_FOUND,
    ApplicationErrorCode.PROFILE_INVALID,
    ApplicationErrorCode.PROFILE_UNSUPPORTED,
    ApplicationErrorCode.NOT_INITIALIZED,
    ApplicationErrorCode.PERMISSION_DENIED,
    ApplicationErrorCode.SCHEMA_UNSUPPORTED,
    ApplicationErrorCode.STORAGE_BUSY,
    ApplicationErrorCode.STORAGE_UNAVAILABLE,
    ApplicationErrorCode.INTERNAL_ERROR,
)
_CREATE_ERRORS = (
    *_PROJECT_ERRORS,
    ApplicationErrorCode.PROJECT_KEY_CONFLICT,
    ApplicationErrorCode.IDEMPOTENCY_CONFLICT,
)
_BIND_ERRORS = (
    *_PROJECT_ERRORS,
    ApplicationErrorCode.CONTEXT_INVALID,
    ApplicationErrorCode.PROJECT_NOT_FOUND,
    ApplicationErrorCode.WORKSPACE_BINDING_CONFLICT,
)
_COMMAND_MODULES = (
    "up.py",
    "status.py",
    "context.py",
    "project.py",
    "task.py",
)
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
            {"id": "prj_acme", "key": "ACME", "name": "ACME"},
            {"id": "prj_beta", "key": "BETA", "name": "BETA"},
        ]
    }
    assert result.stderr == ""
    assert session.project_list_requests == [ProjectListRequest()]
    assert provider.call_count == 1


def test_project_create_json_maps_unicode_input_and_exact_result() -> None:
    """Project creation preserves Unicode names and emits its Owner grant."""
    session = RecordingSession()
    created_project = project(
        key="DOCS",
        name="Documentation Ω",
        identifier="prj_docs",
    )
    session.project_creation_result = ProjectCreationResult(
        project=created_project,
        grant=grant(created_project),
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "project",
            "create",
            "--key",
            "DOCS",
            "--name",
            "Documentation Ω",
            "--profile",
            "team",
            "--idempotency-key",
            "create-docs-1",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    assert require_success(_completed(result)) == {
        "project": {
            "id": "prj_docs",
            "key": "DOCS",
            "name": "Documentation Ω",
        },
        "grant": {
            "subject_id": "sub_local",
            "project_id": "prj_docs",
            "role": "owner",
        },
    }
    assert result.stderr == ""
    assert session.project_create_requests == [
        ProjectCreateRequest(
            key="DOCS",
            name="Documentation Ω",
            profile="team",
            idempotency_key="create-docs-1",
        )
    ]
    assert provider.call_count == 1


def test_project_create_human_output_is_deterministic() -> None:
    """Project creation gives one stable concise Human result."""
    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(RecordingSession())),
        [
            "project",
            "create",
            "--key",
            "DOCS",
            "--name",
            "Documentation",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "Project DOCS (Documentation) created.\n"
    assert result.stderr == ""


def test_project_bind_maps_unicode_path_replace_and_exact_context(
    tmp_path: Path,
) -> None:
    """Binding preserves a Unicode path and explicit replacement intent."""
    workspace = tmp_path / "Documentation Ω"
    workspace.mkdir()
    selected_project = project(
        key="DOCS",
        name="Documentation",
        identifier="prj_docs",
    )
    session = RecordingSession()
    session.project_binding_result = context_result(
        selected_project=selected_project,
        profile="team",
        workspace_root=workspace.resolve(),
    )
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(
        create_app(provider),
        [
            "project",
            "bind",
            "DOCS",
            str(workspace),
            "--profile",
            "team",
            "--replace",
            "--json",
            "--non-interactive",
        ],
        input=None,
    )

    assert require_success(_completed(result)) == {
        "mode": "embedded",
        "profile": "team",
        "schema_version": 5,
        "instance": {"id": "ins_local"},
        "project": {
            "id": "prj_docs",
            "key": "DOCS",
            "name": "Documentation",
        },
        "workspace_root": str(workspace.resolve()),
        "subject": {
            "id": "sub_local",
            "kind": "human",
            "display_name": "Local operator",
            "is_instance_admin": True,
            "project_role": "owner",
        },
        "context_source": str(workspace.resolve() / ".workaholic.env"),
    }
    assert result.stderr == ""
    assert session.project_bind_requests == [
        ProjectBindRequest(
            project="DOCS",
            path=workspace,
            profile="team",
            replace=True,
        )
    ]
    assert provider.call_count == 1


def test_project_bind_defaults_path_and_replace_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted binding path remains explicit ``None`` for Session resolution."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    def fail_prompt(*_arguments: object, **_keywords: object) -> str:
        """Fail if Click attempts prompt interaction."""
        pytest.fail("project bind --non-interactive must not prompt")

    monkeypatch.setattr("click.termui.visible_prompt_func", fail_prompt)
    result = _RUNNER.invoke(
        create_app(provider),
        [
            "project",
            "bind",
            "DOCS",
            "--non-interactive",
        ],
        input=None,
    )

    assert result.exit_code == 0
    assert result.stdout == "Project ACME bound to /work/acme.\n"
    assert session.project_bind_requests == [
        ProjectBindRequest(project="DOCS", path=None)
    ]


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
    assert populated_result.stdout == "ACME\tACME\tprj_acme\nBETA\tBETA\tprj_beta\n"
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


def test_project_list_forwards_explicit_profile() -> None:
    """Project listing selects one initialized profile without Project context."""
    session = RecordingSession()

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["project", "list", "--profile", "team", "--json"],
    )

    assert result.exit_code == 0
    assert session.project_list_requests == [ProjectListRequest(profile="team")]


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


@pytest.mark.parametrize(
    ("operation", "command", "codes"),
    [
        (
            "create_project",
            (
                "project",
                "create",
                "--key",
                "DOCS",
                "--name",
                "Documentation",
            ),
            _CREATE_ERRORS,
        ),
        (
            "bind_project",
            ("project", "bind", "DOCS"),
            _BIND_ERRORS,
        ),
    ],
)
def test_project_mutations_map_documented_failure_exit_categories(
    operation: str,
    command: tuple[str, ...],
    codes: tuple[ApplicationErrorCode, ...],
) -> None:
    """Project mutations preserve every documented typed failure contract."""
    for code in codes:
        session = RecordingSession()
        session.failures[operation] = ApplicationError(
            code,
            f"Safe {code.value} message.",
        )

        result = _RUNNER.invoke(
            create_app(SessionProviderSpy(session)),
            [*command, "--json", "--non-interactive"],
        )

        detail = require_error(_completed(result), expected_code=code.value)
        assert detail["message"] == f"Safe {code.value} message."
        assert result.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        (
            "project",
            "create",
            "--key",
            "DOCS",
            "--name",
            " ",
            "--json",
        ),
        ("project", "bind", "invalid key", "--json"),
        ("project", "list", "--profile", "../private", "--json"),
    ],
)
def test_project_commands_reject_invalid_requests_before_session(
    command: tuple[str, ...],
) -> None:
    """Runtime request validation fails safely before acquiring state."""
    session = RecordingSession()
    provider = SessionProviderSpy(session)

    result = _RUNNER.invoke(create_app(provider), list(command))

    detail = require_error(_completed(result), expected_code="INVALID_INPUT")
    assert "input is invalid." in str(detail["message"])
    assert provider.call_count == 0


def test_project_mutation_redacts_unexpected_failure() -> None:
    """Unexpected mutation diagnostics and private paths never escape."""
    private_detail = "/private/profiles/team.toml contains secret-token"
    session = RecordingSession()
    session.failures["bind_project"] = RuntimeError(private_detail)

    result = _RUNNER.invoke(
        create_app(SessionProviderSpy(session)),
        ["project", "bind", "DOCS", "--json"],
    )

    detail = require_error(_completed(result), expected_code="INTERNAL_ERROR")
    assert detail["message"] == "An unexpected internal error occurred."
    assert private_detail not in result.stdout
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
    create_help = _RUNNER.invoke(
        create_app(provider),
        ["project", "create", "--help"],
    )
    bind_help = _RUNNER.invoke(
        create_app(provider),
        ["project", "bind", "--help"],
    )

    assert group_help.exit_code == 0
    assert "list" in unstyle(group_help.stdout)
    assert "create" in unstyle(group_help.stdout)
    assert "bind" in unstyle(group_help.stdout)
    assert command_help.exit_code == 0
    assert "--profile" in unstyle(command_help.stdout)
    assert "--json" in unstyle(command_help.stdout)
    assert "--non-interactive" in unstyle(command_help.stdout)
    assert "--name" in unstyle(create_help.stdout)
    assert "--idempotency-key" in unstyle(create_help.stdout)
    assert "--replace" in unstyle(bind_help.stdout)
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
