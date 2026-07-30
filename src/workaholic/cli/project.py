"""CLI commands for Project administration and authorized reads."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    IdempotencyKeyOption,
    JsonOption,
    NonInteractiveOption,
    ProfileOption,
    ReplaceOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import context_data, grant_data, project_data
from workaholic.session import (
    ProjectBindRequest,
    ProjectCreateRequest,
    ProjectListRequest,
)

ProjectKeyOption = Annotated[
    str,
    typer.Option(
        ...,
        "--key",
        help="Immutable key for the new Project.",
        metavar="KEY",
        prompt=False,
    ),
]

ProjectNameOption = Annotated[
    str,
    typer.Option(
        ...,
        "--name",
        help="Human-readable name for the new Project.",
        metavar="NAME",
        prompt=False,
    ),
]

BoundProjectArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Immutable key of the Project to bind.",
        metavar="KEY",
    ),
]

WorkspacePathArgument = Annotated[
    Path | None,
    typer.Argument(
        ...,
        help="Existing Workspace directory; defaults to the current directory.",
        metavar="[PATH]",
    ),
]


def register_project_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Project commands against an explicit Session provider.

    Args:
        application: Project Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("create")
    def create_project(  # noqa: PLR0913 - explicit public CLI option contract
        key: ProjectKeyOption,
        name: ProjectNameOption,
        profile: ProfileOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create one named Project in an initialized profile."""
        del non_interactive
        try:
            request = ProjectCreateRequest(
                key=key,
                name=name,
                profile=profile,
                idempotency_key=idempotency_key,
            )
        except ValidationError:
            write_invalid_input(
                "Project creation input is invalid.",
                json_mode=json_mode,
            )
        try:
            result = acquire_session(session_provider).create_project(request)
            data = {
                "project": project_data(result.project),
                "grant": grant_data(result.grant),
            }
            human_result = (
                f"Project {result.project.key} ({result.project.name}) created."
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("bind")
    def bind_project(  # noqa: PLR0913 - explicit public CLI option contract
        project: BoundProjectArgument,
        path: WorkspacePathArgument = None,
        profile: ProfileOption = None,
        replace: ReplaceOption = False,  # noqa: FBT002 - Typer option
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Bind an existing Project to one verified Workspace directory."""
        del non_interactive
        try:
            request = ProjectBindRequest(
                project=project,
                path=path,
                profile=profile,
                replace=replace,
            )
        except ValidationError:
            write_invalid_input(
                "Project binding input is invalid.",
                json_mode=json_mode,
            )
        try:
            result = acquire_session(session_provider).bind_project(request)
            data = context_data(result)
            workspace = (
                "none" if result.workspace_root is None else str(result.workspace_root)
            )
            human_result = f"Project {result.project.key} bound to {workspace}."
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("list")
    def list_projects(
        profile: ProfileOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List Projects authorized for the local operator."""
        del non_interactive
        try:
            request = ProjectListRequest(profile=profile)
        except ValidationError:
            write_invalid_input(
                "Project-list input is invalid.",
                json_mode=json_mode,
            )
        try:
            projects = acquire_session(session_provider).list_projects(request)
            data = {
                "projects": [project_data(project) for project in projects],
            }
            human_result = (
                "\n".join(
                    f"{project.key}\t{project.name}\t{project.id}"
                    for project in projects
                )
                or "No projects."
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
