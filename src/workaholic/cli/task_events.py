"""Snapshot and Human-follow TaskEvent history CLI command."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    JsonOption,
    NonInteractiveOption,
    ProjectOption,
    TaskSelectorArgument,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import (
    task_event_page_data,
    task_event_summary,
)
from workaholic.session import TaskEventPage, TaskEventsRequest

if TYPE_CHECKING:
    from workaholic.session import WorkaholicSession

_FOLLOW_POLL_SECONDS = 1.0

AfterOption = Annotated[
    int,
    typer.Option(
        ...,
        "--after",
        help="Read events strictly after this nonnegative Instance cursor.",
        min=0,
        metavar="CURSOR",
        prompt=False,
        show_default=True,
    ),
]
EventLimitOption = Annotated[
    int,
    typer.Option(
        ...,
        "--limit",
        help="Maximum number of TaskEvents to return per snapshot.",
        min=1,
        max=500,
        prompt=False,
        show_default=True,
    ),
]
FollowOption = Annotated[
    bool,
    typer.Option(
        ...,
        "--follow",
        help="Poll and stream new Human-readable TaskEvents until interrupted.",
        prompt=False,
    ),
]


def register_task_event_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register bounded TaskEvent snapshot and follow behavior.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("events")
    def task_events(  # noqa: PLR0913 - explicit public CLI contract
        task: TaskSelectorArgument,
        after: AfterOption = 0,
        limit: EventLimitOption = 100,
        follow: FollowOption = False,  # noqa: FBT002
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Read one Task's attributable ordered event history."""
        if follow and (json_mode or non_interactive):
            write_invalid_input(
                "Task-event follow is available only in interactive Human output.",
                json_mode=json_mode,
            )
        try:
            request = TaskEventsRequest.model_validate(
                {
                    "task": task,
                    "after": after,
                    "limit": limit,
                    "project": project,
                }
            )
        except ValidationError:
            write_invalid_input("Task-event input is invalid.", json_mode=json_mode)
        try:
            session = acquire_session(session_provider)
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)
        if follow:
            _follow_events(session, request)
            return
        page = _read_page_or_exit(session, request, json_mode=json_mode)
        write_success(
            task_event_page_data(page) if json_mode else _event_page_summary(page),
            json_mode=json_mode,
        )


def _follow_events(session: WorkaholicSession, request: TaskEventsRequest) -> None:
    """Poll one Session and emit every later event exactly once.

    Args:
        session: Command-scoped validated Session.
        request: Initial validated TaskEvent snapshot request.

    """
    current = request.after
    try:
        while True:
            page = _read_page_or_exit(
                session,
                request.model_copy(update={"after": current}),
                json_mode=False,
            )
            for event in page.events:
                typer.echo(task_event_summary(event))
            current = page.next_cursor
            _wait_for_events()
    except KeyboardInterrupt:
        return


def _read_page_or_exit(
    session: WorkaholicSession,
    request: TaskEventsRequest,
    *,
    json_mode: bool,
) -> TaskEventPage:
    """Read one exact page while safely normalizing Session failures.

    Args:
        session: Command-scoped validated Session.
        request: Exact snapshot request.
        json_mode: Whether to emit the automation error envelope.

    Returns:
        Validated TaskEvent snapshot page.

    Raises:
        typer.Exit: If the Session read fails or violates its result contract.

    """
    try:
        page = _validated_event_page(
            session.read_task_events(request),
            after=request.after,
        )
    except Exception as error:  # noqa: BLE001 - redact every boundary failure
        write_failure(error, json_mode=json_mode)
    return page


def _validated_event_page(value: object, *, after: int) -> TaskEventPage:
    """Require one correctly typed non-regressing Session event page.

    Args:
        value: Candidate Session return value.
        after: Cursor supplied to that exact Session read.

    Returns:
        Validated polling-safe event page.

    Raises:
        TypeError: If the Session violates its result or cursor contract.

    """
    if not isinstance(value, TaskEventPage) or value.next_cursor < after:
        raise TypeError
    return value


def _event_page_summary(page: TaskEventPage) -> str:
    """Render one deterministic Human TaskEvent snapshot.

    Args:
        page: Validated ordered event page.

    Returns:
        Newline-delimited events plus the resumable cursor.

    """
    lines = [task_event_summary(event) for event in page.events]
    if not lines:
        lines.append("No events.")
    lines.append(f"Next cursor: {page.next_cursor}")
    return "\n".join(lines)


def _wait_for_events() -> None:
    """Wait the stable bounded interval before the next Human follow poll."""
    time.sleep(_FOLLOW_POLL_SECONDS)
