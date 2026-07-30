"""CLI command for exact-directory local status."""

from __future__ import annotations

import typer  # noqa: TC002 - Typer resolves command annotations at registration

from workaholic.cli.errors import write_failure
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    JsonOption,
    NonInteractiveOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    instance_data,
    project_data,
    subject_data,
)
from workaholic.session import StatusRequest


def register_status_command(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register ``workaholic status`` against an explicit Session provider.

    Args:
        application: Root Typer application.
        session_provider: Command-scoped Session factory.

    """

    @application.command("status")
    def status(
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Show the selected exact-directory local Project."""
        del non_interactive
        try:
            result = acquire_session(session_provider).status(StatusRequest())
            data = {
                "mode": result.mode,
                "schema_version": result.schema_version,
                "instance": instance_data(result.instance),
                "project": project_data(result.project),
                "subject": subject_data(result.subject, result.grant),
            }
            human_result = f"Local project {result.project.key} is ready."
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
