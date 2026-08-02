"""Reusable explicit Typer option aliases for Workaholic commands."""

from __future__ import annotations

from typing import Annotated

import typer

TaskSelectorArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Canonical Task UID or stable PROJECT-NUMBER key.",
        metavar="TASK",
    ),
]

JsonOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--json",
        help="Emit the versioned machine-readable JSON envelope.",
        prompt=False,
    ),
]

NonInteractiveOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--non-interactive",
        help="Never prompt or depend on terminal interaction.",
        prompt=False,
    ),
]

IdempotencyKeyOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--idempotency-key",
        help="Opaque caller key for safely replaying this mutation.",
        metavar="KEY",
        prompt=False,
    ),
]

ProfileOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--profile",
        help="Select one trusted local profile.",
        metavar="PROFILE",
        prompt=False,
    ),
]

ProjectOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--project",
        help="Select one Project by immutable key.",
        metavar="KEY",
        prompt=False,
    ),
]

AllProjectsOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--all-projects",
        help="Select Tasks across every authorized Project.",
        prompt=False,
    ),
]

ReplaceOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--replace",
        help="Replace an existing verified Workspace binding.",
        prompt=False,
    ),
]

CursorOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--cursor",
        help="Opaque continuation cursor returned by the previous page.",
        metavar="CURSOR",
        prompt=False,
    ),
]

LimitOption = Annotated[
    int,
    typer.Option(
        ...,
        "--limit",
        help="Maximum number of Tasks to return.",
        min=1,
        max=500,
        prompt=False,
        show_default=True,
    ),
]

ExpectedVersionOption = Annotated[
    int | None,
    typer.Option(
        ...,
        "--expected-version",
        help="Require the Task to have this positive current version.",
        metavar="INTEGER",
        prompt=False,
    ),
]

InputFileOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--input-file",
        help="Read bounded structured Task input from PATH, or stdin with '-'.",
        metavar="PATH|-",
        prompt=False,
    ),
]

AvailableAtOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--available-at",
        help="Set an RFC 3339 UTC availability timestamp.",
        metavar="TIMESTAMP",
        prompt=False,
    ),
]

ClearAvailableAtOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--clear-available-at",
        help="Clear the Task availability timestamp.",
        prompt=False,
    ),
]

ApprovalOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--approval",
        help="Set whether completion requires Human approval.",
        metavar="none|human",
        prompt=False,
    ),
]

TaskViewOption = Annotated[
    str,
    typer.Option(
        ...,
        "--view",
        help="Select one stored-state or derived Task view.",
        metavar="all|ready|scheduled|blocked|review|done|cancelled",
        prompt=False,
        show_default=True,
    ),
]


def option_was_supplied(ctx: typer.Context, name: str) -> bool:
    """Return whether Click obtained an option from the command line.

    Args:
        ctx: Active command context.
        name: Python parameter name registered with Click.

    Returns:
        Whether the source is the explicit command line rather than a default.

    """
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"
