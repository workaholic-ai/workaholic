"""CLI commands for persistent Project-scoped Tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from workaholic.cli.errors import write_failure, write_invalid_input
from workaholic.cli.options import (  # noqa: TC001 - Typer resolves aliases
    AllProjectsOption,
    CursorOption,
    IdempotencyKeyOption,
    JsonOption,
    LimitOption,
    NonInteractiveOption,
    ProjectOption,
)
from workaholic.cli.rendering import write_success
from workaholic.cli.runtime import SessionProvider, acquire_session
from workaholic.cli.serialization import task_data
from workaholic.session import (
    TaskCreateRequest,
    TaskGetRequest,
    TaskListRequest,
)

if TYPE_CHECKING:
    from workaholic.domain import Task

TaskTitleArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Title of the desired Task outcome.",
        metavar="TITLE",
    ),
]
TaskSelectorArgument = Annotated[
    str,
    typer.Argument(
        ...,
        help="Canonical Task UID or stable PROJECT-NUMBER key.",
        metavar="TASK",
    ),
]
ObjectiveOption = Annotated[
    str | None,
    typer.Option(
        ...,
        "--objective",
        help="Detailed desired outcome; defaults to TITLE.",
        metavar="TEXT",
        prompt=False,
    ),
]
PriorityOption = Annotated[
    int,
    typer.Option(
        ...,
        "--priority",
        help="Task priority from 0 through 100.",
        metavar="INTEGER",
        prompt=False,
        show_default=True,
    ),
]


def register_task_commands(
    application: typer.Typer,
    *,
    session_provider: SessionProvider,
) -> None:
    """Register Task commands against an explicit Session provider.

    Args:
        application: Task Typer command group.
        session_provider: Command-scoped Session factory.

    """

    @application.command("add")
    def add_task(  # noqa: PLR0913 - explicit public CLI option contract
        title: TaskTitleArgument,
        objective: ObjectiveOption = None,
        priority: PriorityOption = 50,
        idempotency_key: IdempotencyKeyOption = None,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Create one attributable Task in the selected Project."""
        del non_interactive
        try:
            request = TaskCreateRequest(
                title=title,
                objective=objective,
                priority=priority,
                idempotency_key=idempotency_key,
                project=project,
            )
        except ValidationError:
            write_invalid_input(
                "Task-create input is invalid.",
                json_mode=json_mode,
            )
        try:
            task = acquire_session(session_provider).create_task(request)
            data = {"task": task_data(task)}
            write_success(
                data if json_mode else _task_summary(task),
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("list")
    def list_tasks(  # noqa: PLR0913 - explicit public CLI option contract
        project: ProjectOption = None,
        all_projects: AllProjectsOption = False,  # noqa: FBT002 - Typer option
        cursor: CursorOption = None,
        limit: LimitOption = 100,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """List one ascending page of Tasks in the selected Project."""
        del non_interactive
        try:
            request = TaskListRequest(
                cursor=cursor,
                limit=limit,
                project=project,
                all_projects=all_projects,
            )
        except ValidationError:
            write_invalid_input(
                "Task-list input is invalid.",
                json_mode=json_mode,
            )
        try:
            page = acquire_session(session_provider).list_tasks(request)
            data = {
                "tasks": [task_data(task) for task in page.tasks],
                "next_cursor": page.next_cursor,
            }
            human_result = _task_page_summary(
                page.tasks,
                next_cursor=page.next_cursor,
            )
            write_success(
                data if json_mode else human_result,
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)

    @application.command("show")
    def show_task(
        task: TaskSelectorArgument,
        project: ProjectOption = None,
        json_mode: JsonOption = False,  # noqa: FBT002 - Typer option
        non_interactive: NonInteractiveOption = False,  # noqa: FBT002
    ) -> None:
        """Show one Task by canonical UID or stable Human key."""
        del non_interactive
        try:
            request = TaskGetRequest(task=task, project=project)
        except ValidationError:
            write_invalid_input(
                "Task selector is invalid.",
                json_mode=json_mode,
            )
        try:
            selected_task = acquire_session(session_provider).get_task(request)
            data = {"task": task_data(selected_task)}
            write_success(
                data if json_mode else _task_summary(selected_task),
                json_mode=json_mode,
            )
        except Exception as error:  # noqa: BLE001 - redact every boundary failure
            write_failure(error, json_mode=json_mode)


def _task_summary(task: Task) -> str:
    """Render one safe deterministic Human Task summary.

    Args:
        task: Validated domain Task returned by a Session.

    Returns:
        Stable single-line Task summary with JSON-escaped title text.

    """
    rendered_title = json.dumps(task.title, ensure_ascii=False)
    return f"{task.key}\t{task.state.value}\tpriority={task.priority}\t{rendered_title}"


def _task_page_summary(
    tasks: tuple[Task, ...],
    *,
    next_cursor: str | None,
) -> str:
    """Render one deterministic Human Task page.

    Args:
        tasks: Ordered Tasks returned by the Session.
        next_cursor: Optional opaque continuation cursor.

    Returns:
        Stable newline-delimited page summary.

    """
    lines = [_task_summary(task) for task in tasks]
    if not lines:
        lines.append("No tasks.")
    if next_cursor is not None:
        lines.append(f"Next cursor: {next_cursor}")
    return "\n".join(lines)
