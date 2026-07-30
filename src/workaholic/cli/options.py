"""Reusable explicit Typer option aliases for Workaholic commands."""

from __future__ import annotations

from typing import Annotated

import typer

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
