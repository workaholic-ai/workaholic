"""Workaholic AI command-line application factory and entry point."""

from importlib.metadata import version as distribution_version
from typing import TYPE_CHECKING, Annotated

import typer

from workaholic.cli.project import register_project_commands
from workaholic.cli.status import register_status_command
from workaholic.cli.task import register_task_commands
from workaholic.cli.up import register_up_command

if TYPE_CHECKING:
    from workaholic.cli.runtime import SessionProvider
    from workaholic.session import TaskSession

_DISTRIBUTION_NAME = "workaholic-ai"
_PROGRAM_NAME = "workaholic"


def _show_version(value: bool) -> None:
    """Print the installed distribution version when requested.

    Args:
        value: Whether the eager ``--version`` option was supplied.

    """
    if value:
        typer.echo(f"{_PROGRAM_NAME} {distribution_version(_DISTRIBUTION_NAME)}")
        raise typer.Exit(code=0)


VersionOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--version",
        callback=_show_version,
        help="Show the installed Workaholic AI version and exit.",
        is_eager=True,
    ),
]


def _root(
    ctx: typer.Context,
    version: VersionOption = False,
) -> None:
    """Run Workaholic AI commands.

    Args:
        ctx: Active CLI context used to render root help.
        version: Eager version flag handled by ``_show_version``.

    """
    del version
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def create_app(session_provider: SessionProvider) -> typer.Typer:
    """Create the command tree with one explicit Session provider.

    Args:
        session_provider: Command-scoped Session factory supplied by the
            composition root.

    Returns:
        Fully registered Typer application.

    Raises:
        TypeError: If ``session_provider`` is not callable.

    """
    candidate_provider: object = session_provider
    if not callable(candidate_provider):
        message = "CLI Session provider must be callable."
        raise TypeError(message)
    application = typer.Typer(
        name=_PROGRAM_NAME,
        help="Coordinate work between human operators and autonomous agents.",
        add_completion=False,
        invoke_without_command=True,
        no_args_is_help=False,
        pretty_exceptions_enable=False,
    )
    application.callback()(_root)
    register_up_command(application, session_provider=session_provider)
    register_status_command(application, session_provider=session_provider)

    project_application = typer.Typer(
        help="Inspect Projects authorized for the local operator.",
        add_completion=False,
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )
    register_project_commands(
        project_application,
        session_provider=session_provider,
    )
    application.add_typer(project_application, name="project")

    task_application = typer.Typer(
        help="Create and inspect persistent Tasks.",
        add_completion=False,
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )
    register_task_commands(
        task_application,
        session_provider=session_provider,
    )
    application.add_typer(task_application, name="task")
    return application


def _unconfigured_session() -> TaskSession:
    """Fail safely when the application factory has no composition root.

    Raises:
        RuntimeError: Always; production entry points use ``composition.main``.

    """
    message = "The local Session composition root is not configured."
    raise RuntimeError(message)


app = create_app(_unconfigured_session)


def main() -> None:
    """Run the Workaholic command-line application."""
    app(prog_name=_PROGRAM_NAME)
