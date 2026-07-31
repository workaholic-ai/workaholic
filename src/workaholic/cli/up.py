"""CLI command for embedded local Project bootstrap."""

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
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    instance_data,
    project_data,
    subject_data,
    workspace_data,
)
from workaholic.session import UpRequest

ProjectKeyOption = Annotated[
    str,
    typer.Option(
        ...,
        "--project-key",
        help="Immutable key for the local Project.",
        metavar="KEY",
        prompt=False,
    ),
]

ProjectNameOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--project-name",
        help="Human-readable name for the initial Project.",
        metavar="NAME",
        prompt=False,
    ),
]


def register_up_command(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register ``workaholic up`` against an explicit Session provider.

    Args:
        application: Root Typer application.
        session_provider: Command-scoped Session factory.

    """

    @application.command("up")
    def up(  # noqa: PLR0913 - explicit public CLI option contract
        project_key: ProjectKeyOption,
        project_name: ProjectNameOption = None,
        profile: ProfileOption = None,
        idempotency_key: IdempotencyKeyOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Initialize or reopen one exact-directory local Project."""
        del non_interactive
        try:
            request = UpRequest(
                project_key=project_key,
                project_name=project_name,
                profile=profile,
                idempotency_key=idempotency_key,
            )
        except ValidationError:
            write_invalid_input(
                "Bootstrap input is invalid.",
                json_mode=json_mode,
            )
        try:
            result = acquire_session(session_provider).up(request)
            current_directory = Path.cwd()
            data = {
                "instance": instance_data(result.instance),
                "project": project_data(result.project),
                "subject": subject_data(result.subject, result.grant),
                "workspace": workspace_data(
                    result.workspace,
                    current_directory=current_directory,
                ),
            }
            human_result = (
                f"Project {result.project.key} is ready in {data['workspace']['root']}."
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
