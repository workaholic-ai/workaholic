"""CLI command for inspecting the effective trusted local context."""

from __future__ import annotations

import typer  # noqa: TC002 - Typer resolves command annotations at registration
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    JsonOption,
    NonInteractiveOption,
    ProfileOption,
    ProjectOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import context_data
from workaholic.session import ContextRequest


def register_context_command(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register ``workaholic context`` against an explicit Session provider.

    Args:
        application: Root Typer application.
        session_provider: Command-scoped Session factory.

    """

    @application.command("context")
    def context(
        profile: ProfileOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Show the effective profile, Project, actor, and safe Workspace paths."""
        del non_interactive
        try:
            request = ContextRequest(profile=profile, project=project)
        except ValidationError:
            write_invalid_input(
                "Context input is invalid.",
                json_mode=json_mode,
            )
        try:
            result = acquire_session(session_provider).context(request)
            data = context_data(result)
            workspace = (
                "none" if result.workspace_root is None else str(result.workspace_root)
            )
            human_result = (
                f"Profile: {result.profile}\n"
                f"Project: {result.project.key} ({result.project.name})\n"
                f"Workspace: {workspace}"
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
