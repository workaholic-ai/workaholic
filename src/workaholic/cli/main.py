"""Workaholic AI command-line entry point."""

from importlib.metadata import version as distribution_version
from typing import Annotated

import typer

_DISTRIBUTION_NAME = "workaholic-ai"
_PROGRAM_NAME = "workaholic"

app = typer.Typer(
    name=_PROGRAM_NAME,
    help="Coordinate work between human operators and autonomous agents.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)


def _show_version(value: bool) -> None:
    """Print the installed distribution version when requested.

    Args:
        value: Whether the eager ``--version`` option was supplied.
    """
    if value:
        typer.echo(f"{_PROGRAM_NAME} {distribution_version(_DISTRIBUTION_NAME)}")
        raise typer.Exit()


VersionOption = Annotated[
    bool,
    typer.Option(
        "--version",
        callback=_show_version,
        help="Show the installed Workaholic AI version and exit.",
        is_eager=True,
    ),
]


@app.callback()
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


def main() -> None:
    """Run the Workaholic command-line application."""
    app(prog_name=_PROGRAM_NAME)
