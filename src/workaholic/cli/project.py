"""CLI commands for authorized Project reads."""

from __future__ import annotations

import typer  # noqa: TC002 - Typer resolves command annotations at registration

from workaholic.cli.errors import write_failure
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    JsonOption,
    NonInteractiveOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import project_data
from workaholic.session import ProjectListRequest


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

    @application.command("list")
    def list_projects(
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List Projects authorized for the local operator."""
        del non_interactive
        try:
            projects = acquire_session(session_provider).list_projects(
                ProjectListRequest()
            )
            data = {
                "projects": [project_data(project) for project in projects],
            }
            human_result = (
                "\n".join(f"{project.key}\t{project.id}" for project in projects)
                or "No projects."
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
